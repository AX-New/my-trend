"""盘中人气排名采集 → popularity_rank_live

与 heat.main 完全独立，物理隔离：
  - 日期：固定用 datetime.now().date()，不依赖 API 的 MAX_TRADE_DATE
  - 目标表：popularity_rank_live（每次 TRUNCATE 后全量写入）
  - 不影响 popularity_rank 的历史数据

用法：
  python -m heat_live.main
  python -m heat_live.main -c config.yaml
"""

import argparse
import logging
import time
from datetime import datetime

from sqlalchemy import text

from config import load_config
from database import Database, batch_upsert
from heat.models import PopularityRankLive
from heat.fetcher import fetch_popularity_page, POPULARITY_FILTERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("heat_live")

DELAY = 1


def _safe_float(v):
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _safe_int(v):
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def run(session, cfg):
    """采集全市场人气排名 → popularity_rank_live（TRUNCATE + 写入）"""
    start = time.time()
    all_rows = []
    today = datetime.now().date()
    total_pages = 0

    for filter_str in POPULARITY_FILTERS:
        page = 1
        filter_count = 0
        logger.info(f"[盘中排名] 开始采集 filter={filter_str}")
        while True:
            try:
                data = fetch_popularity_page(
                    cfg.network, filter_str, page, cfg.popularity.page_size,
                )
            except Exception as e:
                logger.warning(f"[盘中排名] {filter_str} page={page} 失败: {e}")
                break

            items = data.get("result", {}).get("data", [])
            if not items:
                break

            for item in items:
                code = item.get("SECURITY_CODE", "")
                if not code:
                    continue
                all_rows.append({
                    "stock_code": code,
                    "date": today,
                    "rank": item.get("POPULARITY_RANK", 0),
                    "stock_name": item.get("SECURITY_NAME_ABBR", ""),
                    "new_price": _safe_float(item.get("NEW_PRICE")),
                    "change_rate": _safe_float(item.get("CHANGE_RATE")),
                    "volume_ratio": _safe_float(item.get("VOLUME_RATIO")),
                    "turnover_rate": _safe_float(item.get("TURNOVERRATE")),
                    "volume": _safe_int(item.get("VOLUME")),
                    "deal_amount": _safe_float(item.get("DEAL_AMOUNT")),
                })

            filter_count += len(items)
            total_pages += 1
            logger.info(
                f"[盘中排名] {filter_str} page={page}，"
                f"本页 {len(items)} 条，累计 {filter_count} 条"
            )

            if not data.get("result", {}).get("nextpage"):
                break
            page += 1
            time.sleep(DELAY)

        logger.info(f"[盘中排名] filter={filter_str} 完成，共 {page} 页 {filter_count} 条")

    if not all_rows:
        logger.warning("[盘中排名] 未采集到数据")
        return

    session.execute(text("TRUNCATE TABLE popularity_rank_live"))
    session.commit()

    affected = batch_upsert(
        session, PopularityRankLive, all_rows,
        update_cols=["rank", "stock_name", "new_price", "change_rate",
                     "volume_ratio", "turnover_rate", "volume", "deal_amount"],
    )
    elapsed = time.time() - start
    logger.info(
        f"[盘中排名] 完成：{len(all_rows)} 条 → popularity_rank_live，"
        f"{total_pages} 页，affected {affected}，耗时 {elapsed:.1f}s"
    )


def main():
    parser = argparse.ArgumentParser(description="盘中人气排名采集")
    parser.add_argument("-c", "--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        logger.info("========== 盘中排名采集开始 ==========")
        run(session, cfg)
        logger.info("========== 盘中排名采集完成 ==========")
    finally:
        session.close()


if __name__ == "__main__":
    main()
