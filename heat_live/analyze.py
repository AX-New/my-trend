"""盘中热度变化分析 — 读 live 表 + popularity_rank，dict 匹配

今日数据：从 popularity_rank_live 全量读入 dict
昨日数据：从 popularity_rank 最新日期全量读入 dict
Python dict 匹配计算 rank_change，不用 SQL JOIN
Top20 写入 heat_change_top，日期用 datetime.now().date()

用法：
  python -m heat_live.analyze
  python -m heat_live.analyze -c config.yaml
"""

import argparse
import logging
import time
from datetime import datetime

from sqlalchemy import text

from config import load_config
from database import Database, batch_insert_ignore
from heat.models import PopularityRankLive, HeatChangeTop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("heat_live.analyze")

TOP_N = 20


def run(session):
    """从 live 表和 popularity_rank 分别读取，dict 匹配计算 Top20"""
    start = time.time()
    time_point = datetime.now().strftime("%H:%M")
    today = datetime.now().date()

    # ── 1. 读取 popularity_rank_live（今日盘中） ──
    live_rows = session.execute(
        text("SELECT stock_code, stock_name, `rank`, new_price, "
             "change_rate, volume_ratio, turnover_rate "
             "FROM popularity_rank_live"),
    ).fetchall()

    if not live_rows:
        logger.warning("[分析] popularity_rank_live 无数据，请先运行 heat_live.main")
        return

    today_map = {}
    for r in live_rows:
        today_map[r.stock_code] = {
            "stock_name": r.stock_name,
            "rank": r.rank,
            "new_price": r.new_price,
            "change_rate": r.change_rate,
            "volume_ratio": r.volume_ratio,
            "turnover_rate": r.turnover_rate,
        }

    # ── 2. 读取 popularity_rank 最新日期（昨日收盘快照） ──
    last_date = session.execute(
        text("SELECT MAX(date) FROM popularity_rank"),
    ).scalar()

    if not last_date:
        logger.warning("[分析] popularity_rank 无历史数据")
        return

    yesterday_rows = session.execute(
        text("SELECT stock_code, `rank` FROM popularity_rank WHERE date = :d"),
        {"d": last_date},
    ).fetchall()

    yesterday_map = {r.stock_code: r.rank for r in yesterday_rows}

    # ── 3. 排除当天已入选的股票 ──
    existing = session.execute(
        text("SELECT stock_code FROM heat_change_top WHERE date = :d"),
        {"d": today},
    ).fetchall()
    existing_codes = {r[0] for r in existing}

    logger.info(
        f"[分析] 对比：今日 live（{len(today_map)} 条）"
        f"vs 昨日 {last_date}（{len(yesterday_map)} 条），"
        f"时间点 {time_point}，已入选 {len(existing_codes)} 只"
    )

    # ── 4. dict 匹配：计算 rank_change ──
    candidates = []
    for code, info in today_map.items():
        if code in existing_codes:
            continue
        yesterday_rank = yesterday_map.get(code)
        if yesterday_rank is None:
            continue
        rank_change = yesterday_rank - info["rank"]
        candidates.append({
            "stock_code": code,
            "stock_name": info["stock_name"],
            "rank_today": info["rank"],
            "rank_yesterday": yesterday_rank,
            "rank_change": rank_change,
            "new_price": info["new_price"],
            "change_rate": info["change_rate"],
            "volume_ratio": info["volume_ratio"],
            "turnover_rate": info["turnover_rate"],
        })

    candidates.sort(key=lambda x: x["rank_change"], reverse=True)
    top_n = candidates[:TOP_N]

    if not top_n:
        logger.warning("[分析] 无排名变化数据")
        return

    # ── 5. 日志 + 写入 heat_change_top ──
    logger.info(f"[分析] Top{TOP_N} 热度飙升股：")
    records = []
    for r in top_n:
        logger.info(
            f"  {r['stock_code']} {r['stock_name']}: "
            f"排名 {r['rank_yesterday']}→{r['rank_today']}（↑{r['rank_change']}），"
            f"涨幅 {r['change_rate']}%"
        )
        records.append({
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "date": today,
            "time_point": time_point,
            "rank_today": r["rank_today"],
            "rank_yesterday": r["rank_yesterday"],
            "rank_change": r["rank_change"],
            "new_price": r["new_price"],
            "change_rate": r["change_rate"],
            "volume_ratio": r["volume_ratio"],
            "turnover_rate": r["turnover_rate"],
        })

    affected = batch_insert_ignore(session, HeatChangeTop, records)

    elapsed = time.time() - start
    logger.info(
        f"[分析] 完成：新增 {affected}/{len(records)} 条，"
        f"当日累计 {len(existing_codes) + affected} 只，耗时 {elapsed:.1f}s"
    )


def main():
    parser = argparse.ArgumentParser(description="盘中热度变化分析")
    parser.add_argument("-c", "--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        from trading_day import is_trading_day
        if not is_trading_day():
            logger.info("========== 非交易日，跳过盘中热度分析 ==========")
            return
        logger.info("========== 盘中热度分析开始 ==========")
        run(session)
        logger.info("========== 盘中热度分析完成 ==========")
    finally:
        session.close()


if __name__ == "__main__":
    main()
