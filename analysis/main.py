"""基本面分析调度层

用法：
  python -m analysis.main --global              # 国际形势分析
  python -m analysis.main --domestic            # 国内形势分析
  python -m analysis.main --stock 601789        # 个股基本面分析
  python -m analysis.main --stock 601789,000858 # 多只个股
  python -m analysis.main --all                 # 国际+国内+个股（stocks.txt）
  python -m analysis.main --all-stocks          # 全量个股（la_pick），支持断点续跑
  python -m analysis.main --retry               # 重跑今天失败的股票
  python -m analysis.main --all-stocks --no-resume  # 强制全量重跑，不跳过已完成
"""

import argparse
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from config import load_config, load_stocks

from database import Database, batch_upsert
from analysis.models import NewsAnalysis, AnalysisFailure
from analysis.analyzer import (
    analyze_global, analyze_domestic,
    _collect_news, _call_llm, STOCK_PROMPT, LLMConfig,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("analysis")

DELAY = 1
MAX_LLM_WORKERS = 3


def _get_done_codes(session, today) -> set[str]:
    """查询今天已完成基本面分析的股票代码"""
    rows = session.query(NewsAnalysis.stock_code).filter(
        NewsAnalysis.analysis_type == "stock",
        NewsAnalysis.date == today,
    ).all()
    return {r[0] for r in rows}


def _record_failure(session, code: str, name: str, error: str):
    """记录基本面分析失败"""
    today = date.today()
    existing = session.query(AnalysisFailure).filter(
        AnalysisFailure.stock_code == code,
        AnalysisFailure.date == today,
        AnalysisFailure.stage == "stock",
    ).first()
    if existing:
        existing.retried = (existing.retried or 0) + 1
        existing.error = str(error)[:500]
    else:
        session.add(AnalysisFailure(
            stock_code=code, stock_name=name,
            date=today, stage="stock", error=str(error)[:500],
        ))
    session.commit()


def _resolve_failure(session, code: str):
    """标记基本面失败已解决"""
    today = date.today()
    session.query(AnalysisFailure).filter(
        AnalysisFailure.stock_code == code,
        AnalysisFailure.date == today,
        AnalysisFailure.stage == "stock",
        AnalysisFailure.resolved == 0,
    ).update({"resolved": 1})
    session.commit()


def _get_failed_codes(session, today) -> list[tuple[str, str]]:
    """获取今天未解决的基本面失败记录 [(code, name), ...]"""
    rows = session.query(
        AnalysisFailure.stock_code,
        AnalysisFailure.stock_name,
    ).filter(
        AnalysisFailure.date == today,
        AnalysisFailure.stage == "stock",
        AnalysisFailure.resolved == 0,
    ).distinct().all()
    return [(r[0], r[1] or r[0]) for r in rows]


def _save(session, analysis_type: str, code: str, name: str,
          data: dict, article_count: int = 0):
    """保存分析结果，提取 sentiment/summary/score 后剩余字段存 detail"""
    sentiment = data.get("sentiment", "")
    summary = data.get("summary", "")
    score = data.get("score", 0.0)
    detail = {k: v for k, v in data.items() if k not in ("sentiment", "summary", "score")}

    row = {
        "analysis_type": analysis_type,
        "stock_code": code,
        "stock_name": name,
        "date": date.today(),
        "article_count": article_count,
        "sentiment": sentiment,
        "summary": summary,
        "score": score,
        "detail": json.dumps(detail, ensure_ascii=False),
    }
    affected = batch_upsert(
        session, NewsAnalysis, [row],
        update_cols=["stock_name", "article_count", "sentiment",
                     "summary", "score", "detail"],
    )
    logger.info(f"[{analysis_type}] {code or '—'} | {sentiment} | score={score:.0f} | affected={affected}")


def run_global(session, llm):
    """国际形势分析"""
    logger.info("========== 国际形势分析 ==========")
    data = analyze_global(llm)
    if data:
        _save(session, "global", "", "", data)
        logger.info(f"  {data.get('summary', '')}")
    else:
        logger.warning("国际形势分析失败")


def run_domestic(session, llm):
    """国内形势分析"""
    logger.info("========== 国内形势分析 ==========")
    data = analyze_domestic(llm)
    if data:
        _save(session, "domestic", "", "", data)
        logger.info(f"  {data.get('summary', '')}")
    else:
        logger.warning("国内形势分析失败")



def _worker_id() -> str:
    """返回当前线程的 worker 编号，如 worker_0"""
    return threading.current_thread().name


def _llm_task(code: str, name: str, prompt: str, article_count: int,
              llm: LLMConfig, semaphore: threading.Semaphore):
    """线程任务：LLM 基本面分析"""
    wid = _worker_id()
    logger.info(f"[{wid}] [{code}] 开始基本面 LLM 分析")
    try:
        result = _call_llm(prompt, llm)
        logger.info(f"[{wid}] [{code}] 基本面 LLM 完成")
        return {"code": code, "name": name, "data": result, "count": article_count}
    finally:
        semaphore.release()


def run_stocks(session, llm, codes_names: list[tuple[str, str]], resume: bool = True):
    """个股基本面分析：主线程搜索新闻，LLM 丢线程池并发。

    resume=True 时跳过今天已完成的股票（断点续跑）。
    """
    # 断点续跑：跳过已完成的
    if resume:
        done = _get_done_codes(session, date.today())
        if done:
            before = len(codes_names)
            codes_names = [(c, n) for c, n in codes_names if c not in done]
            skipped = before - len(codes_names)
            logger.info(f"[基本面] 断点续跑：跳过 {skipped} 只已完成，剩余 {len(codes_names)} 只")
            if not codes_names:
                logger.info("[基本面] 全部已完成，无需重跑")
                return

    total = len(codes_names)
    logger.info(f"========== 基本面分析（{total} 只，LLM并发={MAX_LLM_WORKERS}）==========")

    semaphore = threading.Semaphore(MAX_LLM_WORKERS)
    executor = ThreadPoolExecutor(max_workers=MAX_LLM_WORKERS, thread_name_prefix="worker")
    futures = {}

    for i, (code, name) in enumerate(codes_names):
        logger.info(f"[{code} {name}]（{i+1}/{total}）搜索新闻...")

        queries = [name, f"{name} 项目", f"{name} 利润", f"{name} 行业前景", f"{name} 产品"]
        articles = _collect_news(queries, max_count=30)

        if articles:
            article_count = len(articles.strip().split("\n\n"))
            prompt = STOCK_PROMPT.format(
                code=code, name=name, articles=articles, today=date.today(),
            )
            semaphore.acquire()
            fut = executor.submit(_llm_task, code, name, prompt, article_count, llm, semaphore)
            futures[fut] = (code, name)
            logger.info(f"[{code}] 新闻 {article_count} 条，已提交 LLM")
        else:
            logger.warning(f"[{code}] 无新闻")
            _record_failure(session, code, name, "搜索无结果")

        time.sleep(DELAY)

        # 定期收割已完成的 future
        for fut in [f for f in futures if f.done()]:
            _handle_stock_future(session, fut, futures.pop(fut))

    logger.info(f"搜索完毕，等待 {len(futures)} 个 LLM 任务完成...")
    for fut in as_completed(futures):
        _handle_stock_future(session, fut, futures[fut])

    executor.shutdown(wait=False)
    logger.info("========== 基本面分析完成 ==========")


def _handle_stock_future(session, fut, meta):
    """处理基本面 LLM future"""
    code, name = meta
    try:
        result = fut.result()
    except Exception as e:
        logger.error(f"[{code}] 基本面 LLM 异常: {e}")
        _record_failure(session, code, name, str(e))
        return

    data = result["data"]
    if data:
        _save(session, "stock", code, name, data, result["count"])
        _resolve_failure(session, code)
        logger.info(f"[{code}] 基本面: {data.get('summary', '')[:80]}")
    else:
        logger.warning(f"[{code}] 基本面分析失败")
        _record_failure(session, code, name, "LLM返回空结果")


def _lookup_name(code: str) -> str:
    """查股票名"""
    import pymysql
    try:
        conn = pymysql.connect(
            host="localhost", user="root", password="root", db="my_stock",
        )
        cur = conn.cursor()
        cur.execute("SELECT name FROM stock_basic WHERE symbol = %s LIMIT 1", (code,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else code
    except Exception:
        return code


def _load_all_stocks() -> list[tuple[str, str]]:
    """从 my_stock.la_pick 读取选股池，去重后返回 [(code, name), ...]"""
    import pymysql
    conn = pymysql.connect(host="localhost", user="root", password="root", db="my_stock")
    cur = conn.cursor()
    cur.execute(
        "SELECT code, name FROM ("
        "  SELECT DISTINCT SUBSTRING_INDEX(ts_code, '.', 1) AS code, stock_name AS name"
        "  FROM la_pick"
        ") t ORDER BY code"
    )
    rows = cur.fetchall()
    conn.close()
    logger.info(f"[la_pick] 读取到 {len(rows)} 只股票")
    return [(r[0], r[1]) for r in rows]


def main():
    parser = argparse.ArgumentParser(description="新闻分析")
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--global", dest="do_global", action="store_true",
                        help="国际形势分析")
    parser.add_argument("--domestic", action="store_true",
                        help="国内形势分析")
    parser.add_argument("--stock", type=str, default=None,
                        help="个股分析，多只用逗号分隔")
    parser.add_argument("--all", action="store_true",
                        help="全部分析（国际+国内+stocks.txt个股）")
    parser.add_argument("--all-stocks", action="store_true",
                        help="全量个股分析（stock_basic 全部在市股票）")
    parser.add_argument("--retry", action="store_true",
                        help="重跑今天失败的股票")
    parser.add_argument("--no-resume", action="store_true",
                        help="不跳过已完成的（强制全量重跑）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    resume = not args.no_resume

    try:
        if args.retry:
            today = date.today()
            failed = _get_failed_codes(session, today)
            if failed:
                logger.info(f"重跑 {len(failed)} 只基本面失败")
                run_stocks(session, cfg.llm, failed, resume=False)
            else:
                logger.info("今天没有失败记录")
        elif args.all_stocks:
            run_global(session, cfg.llm)
            time.sleep(DELAY)
            run_domestic(session, cfg.llm)
            time.sleep(DELAY)
            codes_names = _load_all_stocks()
            if codes_names:
                run_stocks(session, cfg.llm, codes_names, resume=resume)
        elif args.all:
            run_global(session, cfg.llm)
            time.sleep(DELAY)
            run_domestic(session, cfg.llm)
            time.sleep(DELAY)
            stocks = load_stocks(cfg)
            if stocks:
                codes_names = [(s.code, s.name or _lookup_name(s.code)) for s in stocks]
                run_stocks(session, cfg.llm, codes_names, resume=resume)
        else:
            if args.do_global:
                run_global(session, cfg.llm)
            if args.domestic:
                run_domestic(session, cfg.llm)
            if args.stock:
                codes = [c.strip() for c in args.stock.split(",") if c.strip()]
                codes_names = [(c, _lookup_name(c)) for c in codes]
                run_stocks(session, cfg.llm, codes_names, resume=resume)

            if not args.do_global and not args.domestic and not args.stock:
                parser.print_help()
    finally:
        session.close()


if __name__ == "__main__":
    main()
