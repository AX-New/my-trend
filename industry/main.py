"""行业分析调度层

逐个分析申万一级行业（31个），串行搜索新闻 + 并发 LLM。

用法：
  python -m industry.main                        # 全部行业
  python -m industry.main --name 煤炭             # 单个行业
"""

import argparse
import json
import logging
import logging.handlers
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pymysql

from config import load_config
from database import Database, batch_upsert
from industry.models import IndustryAnalysis
from industry.analyzer import analyze_industry, _call_llm, _collect_news, INDUSTRY_PROMPT, restore_auto

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_log_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_console = logging.StreamHandler()
_console.setFormatter(_log_fmt)
_file = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "industry.log", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8",
)
_file.setFormatter(_log_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_console, _file])
logger = logging.getLogger("industry")

DELAY = 5
MAX_LLM_WORKERS = 3

# my_stock 数据库（读取行业列表）
MY_STOCK_DB = {
    "host": "127.0.0.1", "port": 3307,
    "user": "root", "password": "root",
    "db": "my_stock", "charset": "utf8mb4",
}


def _load_industries() -> list[dict]:
    """从 index_classify 读取申万一级行业列表"""
    conn = pymysql.connect(**MY_STOCK_DB)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT index_code as code, industry_name as name
                FROM index_classify WHERE level = 'L1'
                ORDER BY index_code
            """)
            return cur.fetchall()
    finally:
        conn.close()


def _save(session, code: str, name: str, data: dict, article_count: int):
    """保存单个行业分析结果"""
    sentiment = data.get("sentiment", "")
    summary = data.get("summary", "")
    score = data.get("score", 0.0)
    detail = {k: v for k, v in data.items() if k not in ("sentiment", "summary", "score")}

    row = {
        "industry_code": code,
        "industry_name": name,
        "date": date.today(),
        "sentiment": sentiment,
        "summary": summary,
        "score": score,
        "detail": json.dumps(detail, ensure_ascii=False),
    }
    batch_upsert(
        session, IndustryAnalysis, [row],
        update_cols=["industry_name", "sentiment", "summary", "score", "detail"],
    )


def _llm_task(name, prompt, article_count, llm, semaphore):
    """线程任务：调用 LLM"""
    tid = threading.current_thread().name
    logger.info(f"[{tid}] [{name}] 开始 LLM 分析")
    try:
        data = _call_llm(prompt, llm)
        logger.info(f"[{tid}] [{name}] LLM 完成")
        return {"name": name, "data": data, "count": article_count}
    finally:
        semaphore.release()


def run_all(session, llm, industries: list[dict]):
    """批量分析：串行搜索新闻，并发 LLM"""
    total = len(industries)
    success = 0
    fail = 0

    logger.info(f"========== 行业分析（{total} 个，LLM并发={MAX_LLM_WORKERS}）==========")

    semaphore = threading.Semaphore(MAX_LLM_WORKERS)
    executor = ThreadPoolExecutor(max_workers=MAX_LLM_WORKERS, thread_name_prefix="ind")
    futures = {}

    for i, ind in enumerate(industries, 1):
        code = ind["code"]
        name = ind["name"]
        logger.info(f"[{i}/{total}] {name} 搜索新闻...")

        queries = [f"{name}行业", f"{name} 政策 产能 龙头 前景"]
        try:
            articles = _collect_news(queries, max_count=30)
        except RuntimeError as e:
            logger.error(f"[{name}] 所有代理节点耗尽: {e}")
            fail += 1
            break

        if articles:
            article_count = len(articles.strip().split("\n\n"))
            prompt = INDUSTRY_PROMPT.format(name=name, articles=articles, today=date.today())
            semaphore.acquire()
            fut = executor.submit(_llm_task, name, prompt, article_count, llm, semaphore)
            futures[fut] = (code, name)
            logger.info(f"[{name}] 新闻 {article_count} 条，已提交 LLM")
        else:
            logger.warning(f"[{name}] 搜索无结果，跳过")
            fail += 1

        time.sleep(DELAY)

        # 收割已完成的 future
        for fut in [f for f in futures if f.done()]:
            code_f, name_f = futures.pop(fut)
            try:
                result = fut.result()
                data = result["data"]
                if data:
                    _save(session, code_f, name_f, data, result["count"])
                    logger.info(f"[{name_f}] {data.get('sentiment')} | score={data.get('score', 0):.0f} | {data.get('summary', '')[:60]}")
                    success += 1
                else:
                    logger.warning(f"[{name_f}] LLM 返回空")
                    fail += 1
            except Exception as e:
                logger.error(f"[{name_f}] 异常: {e}")
                fail += 1

    # 等待剩余 LLM
    logger.info(f"搜索完毕，等待 {len(futures)} 个 LLM 任务...")
    for fut in as_completed(futures):
        code_f, name_f = futures[fut]
        try:
            result = fut.result()
            data = result["data"]
            if data:
                _save(session, code_f, name_f, data, result["count"])
                logger.info(f"[{name_f}] {data.get('sentiment')} | score={data.get('score', 0):.0f} | {data.get('summary', '')[:60]}")
                success += 1
            else:
                logger.warning(f"[{name_f}] LLM 返回空")
                fail += 1
        except Exception as e:
            logger.error(f"[{name_f}] 异常: {e}")
            fail += 1

    executor.shutdown(wait=False)
    restore_auto()
    logger.info(f"========== 行业分析完成: {success}/{total} 成功，{fail} 失败 ==========")


def main():
    parser = argparse.ArgumentParser(description="申万行业逐个分析")
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--name", type=str, default=None, help="单个行业名称，如 煤炭")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        industries = _load_industries()
        if not industries:
            logger.warning("无行业数据")
            return

        if args.name:
            target = [i for i in industries if i["name"] == args.name]
            if not target:
                logger.error(f"未找到行业: {args.name}")
                return
            ind = target[0]
            data, count = analyze_industry(ind["name"], cfg.llm)
            if data:
                _save(session, ind["code"], ind["name"], data, count)
                logger.info(f"[{ind['name']}] {data.get('summary', '')}")
        else:
            run_all(session, cfg.llm, industries)
    finally:
        session.close()


if __name__ == "__main__":
    main()
