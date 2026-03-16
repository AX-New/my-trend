"""基本面分析调度层

用法：
  python -m analysis.main --global              # 国际形势分析
  python -m analysis.main --domestic            # 国内形势分析
  python -m analysis.main --stock 601789        # 个股基本面分析
  python -m analysis.main --stock 601789,000858 # 多只个股
  python -m analysis.main --all                 # 国际+国内+个股（stocks.txt）
  python -m analysis.main --all-stocks          # 全量个股（stock_basic 全市场），支持断点续跑
  python -m analysis.main --all-la              # 选股池（la_pick），支持断点续跑
  python -m analysis.main --retry               # 重跑最近一次失败的股票
  python -m analysis.main --all-stocks --no-resume  # 强制全量重跑
"""

import argparse
import json
import logging
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

from config import load_config, load_stocks

from database import Database, batch_upsert
from analysis.models import NewsAnalysis, AnalysisFailure, AnalysisRun
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

DELAY = 5
MAX_LLM_WORKERS = 3
# 连续搜索失败退避：连续 N 只无新闻则暂停，避免被限流
EMPTY_STREAK_THRESHOLD = 2   # 连续空结果触发退避
BACKOFF_SECONDS = 300        # 退避时长（秒）

# 来源标识：用主机名区分不同机器
_SOURCE = platform.node()[:50]


# ── run 管理（游标模式）──

def _gen_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def _find_active_run(session, task_type: str = "analysis") -> AnalysisRun | None:
    """查找本机未完成的运行"""
    return session.query(AnalysisRun).filter(
        AnalysisRun.status == "running",
        AnalysisRun.source == _SOURCE,
        AnalysisRun.task_type == task_type,
    ).order_by(AnalysisRun.started_at.desc()).first()


def _create_run(session, total: int, task_type: str = "analysis") -> AnalysisRun:
    run = AnalysisRun(
        run_id=_gen_run_id(),
        task_type=task_type,
        status="running",
        total_count=total,
        done_cursor=0,
        fail_count=0,
        source=_SOURCE,
    )
    session.add(run)
    session.commit()
    logger.info(f"[RUN] 新建 {run.run_id}（{task_type}，{total} 只，来源={_SOURCE}）")
    return run


def _advance_cursor(session, run: AnalysisRun, new_cursor: int):
    """推进游标到 new_cursor"""
    run.done_cursor = new_cursor
    session.commit()


def _inc_fail(session, run: AnalysisRun):
    run.fail_count = (run.fail_count or 0) + 1
    session.commit()


def _finish_run(session, run: AnalysisRun):
    run.status = "completed"
    run.finished_at = datetime.now()
    session.commit()
    logger.info(f"[RUN] {run.run_id} 完成：cursor={run.done_cursor}/{run.total_count} fail={run.fail_count}")


def _start_or_resume_batch(session, total: int,
                           task_type: str = "analysis",
                           force_new: bool = False) -> AnalysisRun:
    """恢复未完成的 run 或创建新 run"""
    if not force_new:
        active = _find_active_run(session, task_type)
        if active:
            logger.info(f"[RUN] 恢复 {active.run_id}（cursor={active.done_cursor}/{active.total_count}）")
            return active

    # 中断旧 run
    old_runs = session.query(AnalysisRun).filter(
        AnalysisRun.status == "running",
        AnalysisRun.source == _SOURCE,
        AnalysisRun.task_type == task_type,
    ).all()
    for r in old_runs:
        r.status = "interrupted"
        r.finished_at = datetime.now()
    session.commit()

    return _create_run(session, total, task_type)


# ── 失败记录 ──

def _record_failure(session, run_id: str, code: str, name: str, error: str):
    existing = session.query(AnalysisFailure).filter(
        AnalysisFailure.stock_code == code,
        AnalysisFailure.run_id == run_id,
        AnalysisFailure.stage == "stock",
    ).first()
    if existing:
        existing.retried = (existing.retried or 0) + 1
        existing.error = str(error)[:500]
    else:
        session.add(AnalysisFailure(
            stock_code=code, stock_name=name, run_id=run_id,
            date=date.today(), stage="stock", error=str(error)[:500],
        ))
    session.commit()


def _resolve_failure(session, run_id: str, code: str):
    session.query(AnalysisFailure).filter(
        AnalysisFailure.stock_code == code,
        AnalysisFailure.run_id == run_id,
        AnalysisFailure.stage == "stock",
        AnalysisFailure.resolved == 0,
    ).update({"resolved": 1})
    session.commit()


def _get_failed_codes(session, run_id: str) -> list[tuple[str, str]]:
    rows = session.query(
        AnalysisFailure.stock_code,
        AnalysisFailure.stock_name,
    ).filter(
        AnalysisFailure.run_id == run_id,
        AnalysisFailure.stage == "stock",
        AnalysisFailure.resolved == 0,
    ).distinct().all()
    return [(r[0], r[1] or r[0]) for r in rows]


# ── 保存结果 ──

def _save(session, analysis_type: str, code: str, name: str,
          data: dict, article_count: int = 0, run_id: str = None):
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
        "run_id": run_id,
    }
    affected = batch_upsert(
        session, NewsAnalysis, [row],
        update_cols=["stock_name", "article_count", "sentiment",
                     "summary", "score", "detail", "run_id"],
    )
    logger.info(f"[{analysis_type}] {code or '—'} | {sentiment} | score={score:.0f} | affected={affected}")


# ── 国际/国内 ──

def run_global(session, llm):
    logger.info("========== 国际形势分析 ==========")
    data = analyze_global(llm)
    if data:
        _save(session, "global", "", "", data)
        logger.info(f"  {data.get('summary', '')}")
    else:
        logger.warning("国际形势分析失败")


def run_domestic(session, llm):
    logger.info("========== 国内形势分析 ==========")
    data = analyze_domestic(llm)
    if data:
        _save(session, "domestic", "", "", data)
        logger.info(f"  {data.get('summary', '')}")
    else:
        logger.warning("国内形势分析失败")


# ── 个股基本面 ──

def _llm_task(code, name, prompt, article_count, llm, semaphore):
    """线程任务：调用 LLM 分析，完成后释放信号量"""
    tid = threading.current_thread().name
    logger.info(f"[{tid}] [{code}] 开始 LLM 分析")
    try:
        data = _call_llm(prompt, llm)
        logger.info(f"[{tid}] [{code}] LLM 完成")
        return {"code": code, "name": name, "data": data, "count": article_count}
    finally:
        semaphore.release()


def run_stocks(session, llm, codes_names: list[tuple[str, str]],
               run: AnalysisRun = None):
    """个股基本面分析：串行搜索新闻，LLM 并发（滑动窗口）。

    主线程按顺序搜索新闻，搜到后提交 LLM 到线程池（Semaphore 控制并发上限）。
    搜索完一只就推进游标（LLM 结果异步回收）。

    run 不为 None 时走游标断点续跑（批量模式）：
      - codes_names 已按 code 排序（固定顺序）
      - 从 run.done_cursor 位置开始
    run 为 None 时直接跑全部（--stock 单股模式）。
    """
    run_id = run.run_id if run else None

    # 批量模式：从 cursor 位置截断
    if run and run.done_cursor > 0:
        skipped = run.done_cursor
        codes_names = codes_names[skipped:]
        logger.info(f"[RUN {run_id}] 从第 {skipped} 只继续，剩余 {len(codes_names)} 只")
        if not codes_names:
            logger.info(f"[RUN {run_id}] 全部已完成")
            _finish_run(session, run)
            return

    total = len(codes_names)
    base_cursor = run.done_cursor if run else 0
    logger.info(f"========== 基本面分析（{total} 只，LLM并发={MAX_LLM_WORKERS}）==========")

    semaphore = threading.Semaphore(MAX_LLM_WORKERS)
    executor = ThreadPoolExecutor(max_workers=MAX_LLM_WORKERS, thread_name_prefix="worker")
    futures = {}
    empty_streak = 0  # 连续搜索无结果计数
    stopped_early = False  # 是否因限流等原因提前退出

    for i, (code, name) in enumerate(codes_names):
        global_i = base_cursor + i + 1
        logger.info(f"[{code} {name}]（{global_i}/{run.total_count if run else total}）搜索新闻...")

        queries = [name, f"{name} 项目 利润 行业前景 产品"]
        articles = _collect_news(queries, max_count=40)

        if articles:
            empty_streak = 0
            article_count = len(articles.strip().split("\n\n"))
            prompt = STOCK_PROMPT.format(
                code=code, name=name, articles=articles, today=date.today(),
            )
            semaphore.acquire()
            fut = executor.submit(_llm_task, code, name, prompt, article_count, llm, semaphore)
            futures[fut] = (code, name)
            logger.info(f"[{code}] 新闻 {article_count} 条，已提交 LLM")
        else:
            empty_streak += 1
            logger.warning(f"[{code}] 无新闻（连续空 {empty_streak}）")
            if run:
                _record_failure(session, run_id, code, name, "搜索无结果")
                _inc_fail(session, run)

            # 连续空结果：疑似被限流，停止搜索，等下次续跑
            if empty_streak >= EMPTY_STREAK_THRESHOLD:
                logger.warning(
                    f"连续 {empty_streak} 只无新闻，疑似限流，停止搜索"
                )
                stopped_early = True
                break

        # 推进游标：这只已搜索完（LLM 异步处理）
        if run:
            _advance_cursor(session, run, base_cursor + i + 1)

        time.sleep(DELAY)

        # 收割已完成的 future
        for fut in [f for f in futures if f.done()]:
            _handle_result(session, fut, futures.pop(fut), run_id, run)

    # 等待剩余 LLM 任务完成
    logger.info(f"搜索完毕，等待 {len(futures)} 个 LLM 任务完成...")
    for fut in as_completed(futures):
        _handle_result(session, fut, futures[fut], run_id, run)

    executor.shutdown(wait=False)

    if run:
        if stopped_early:
            logger.info(f"[RUN {run_id}] 提前退出，保持 running 状态，下次续跑")
        else:
            _finish_run(session, run)

    logger.info("========== 基本面分析完成 ==========")


def _handle_result(session, fut, meta, run_id, run):
    """处理 LLM future 结果"""
    code, name = meta
    try:
        result = fut.result()
    except Exception as e:
        logger.error(f"[{code}] LLM 异常: {e}")
        if run:
            _record_failure(session, run_id, code, name, str(e))
            _inc_fail(session, run)
        return

    data = result["data"]
    if data:
        _save(session, "stock", code, name, data, result["count"], run_id=run_id)
        if run:
            _resolve_failure(session, run_id, code)
        logger.info(f"[{code}] 基本面: {data.get('summary', '')[:80]}")
    else:
        logger.warning(f"[{code}] LLM 返回空结果")
        if run:
            _record_failure(session, run_id, code, name, "LLM返回空结果")
            _inc_fail(session, run)


# ── 辅助 ──

def _lookup_name(code: str, cfg=None) -> str:
    import pymysql
    try:
        kw = {"host": "localhost", "user": "root", "password": "root", "db": "my_stock"}
        if cfg:
            db = cfg.database
            kw = {"host": db.host, "port": db.port, "user": db.user, "password": db.password, "db": "my_stock"}
        conn = pymysql.connect(**kw)
        cur = conn.cursor()
        cur.execute("SELECT name FROM stock_basic WHERE symbol = %s LIMIT 1", (code,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else code
    except Exception:
        return code


def _load_all_stocks(cfg) -> list[tuple[str, str]]:
    """从 my_stock.stock_basic 读取全市场上市股票，按 symbol 排序"""
    import pymysql
    db = cfg.database
    conn = pymysql.connect(host=db.host, port=db.port, user=db.user, password=db.password, db="my_stock")
    cur = conn.cursor()
    cur.execute(
        "SELECT symbol, name FROM stock_basic"
        " WHERE list_status = 'L'"
        " ORDER BY symbol"
    )
    rows = cur.fetchall()
    conn.close()
    logger.info(f"[stock_basic] 读取到 {len(rows)} 只股票")
    return [(r[0], r[1]) for r in rows]


def _load_la_stocks(cfg) -> list[tuple[str, str]]:
    """从 my_stock.la_pick 读取最新日期的选股池，按 code 排序（固定顺序，游标依赖此顺序）"""
    import pymysql
    db = cfg.database
    conn = pymysql.connect(host=db.host, port=db.port, user=db.user, password=db.password, db="my_stock")
    cur = conn.cursor()
    cur.execute(
        "SELECT code, name FROM ("
        "  SELECT DISTINCT SUBSTRING_INDEX(ts_code, '.', 1) AS code, stock_name AS name"
        "  FROM la_pick"
        "  WHERE eval_date = (SELECT MAX(eval_date) FROM la_pick)"
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
                        help="个股分析，多只用逗号分隔（不记录 run）")
    parser.add_argument("--all", action="store_true",
                        help="stocks.txt个股分析，记录 run")
    parser.add_argument("--all-stocks", action="store_true",
                        help="全量个股分析（stock_basic 全市场），记录 run，支持断点续跑")
    parser.add_argument("--all-la", action="store_true",
                        help="选股池分析（la_pick），记录 run，支持断点续跑")
    parser.add_argument("--retry", type=str, default=None, nargs="?", const="latest",
                        help="重跑失败的股票，可指定 run_id（默认最近一次）")
    parser.add_argument("--no-resume", action="store_true",
                        help="强制新建 run，不恢复中断的运行")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        if args.retry is not None:
            if args.retry == "latest":
                last_run = session.query(AnalysisRun).order_by(
                    AnalysisRun.started_at.desc()
                ).first()
                if not last_run:
                    logger.info("没有历史运行记录")
                    return
                retry_run_id = last_run.run_id
            else:
                retry_run_id = args.retry

            failed = _get_failed_codes(session, retry_run_id)
            if failed:
                logger.info(f"重跑 run={retry_run_id} 中 {len(failed)} 只失败")
                run_stocks(session, cfg.llm, failed)
            else:
                logger.info(f"run={retry_run_id} 没有失败记录")

        elif args.all_stocks:
            codes_names = _load_all_stocks(cfg)
            if codes_names:
                run = _start_or_resume_batch(session, len(codes_names),
                                             task_type="all_stocks",
                                             force_new=args.no_resume)
                run_stocks(session, cfg.llm, codes_names, run=run)

        elif args.all_la:
            codes_names = _load_la_stocks(cfg)
            if codes_names:
                run = _start_or_resume_batch(session, len(codes_names),
                                             task_type="all_la",
                                             force_new=args.no_resume)
                run_stocks(session, cfg.llm, codes_names, run=run)

        elif args.all:
            stocks = load_stocks(cfg)
            if stocks:
                codes_names = sorted(
                    [(s.code, s.name or _lookup_name(s.code, cfg)) for s in stocks],
                    key=lambda x: x[0],
                )
                run = _start_or_resume_batch(session, len(codes_names),
                                             force_new=args.no_resume)
                run_stocks(session, cfg.llm, codes_names, run=run)

        else:
            if args.do_global:
                run_global(session, cfg.llm)
            if args.domestic:
                run_domestic(session, cfg.llm)
            if args.stock:
                codes = [c.strip() for c in args.stock.split(",") if c.strip()]
                codes_names = [(c, _lookup_name(c, cfg)) for c in codes]
                run_stocks(session, cfg.llm, codes_names)

            if not args.do_global and not args.domestic and not args.stock:
                parser.print_help()
    finally:
        session.close()


if __name__ == "__main__":
    main()
