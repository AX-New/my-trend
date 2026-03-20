"""盘中热度监控 + 板块新闻采集 —— 调度层

用法：
  python -m hot.main                      # 盘中热度快照（默认前200）
  python -m hot.main --sector             # 板块新闻采集（涨幅前20板块）
  python -m hot.main --all                # 全部执行
  python -m hot.main --top 100            # 只采集前100
  python -m hot.main --sector-top 10      # 只采集涨幅前10板块新闻
  python -m hot.main -c config.yaml       # 指定配置

定时建议（盘中每小时）：
  crontab: 40 9 * * 1-5; 30 10-11 * * 1-5; 30 13-14 * * 1-5
"""

import argparse
import logging
import time
from datetime import datetime

from config import load_config
from database import Database, batch_upsert, batch_insert_ignore
from hot.models import IntradayHeatSnapshot, SectorNews
from heat.models import HeatChangeTop
from hot.fetcher import (
    fetch_realtime_hot_rank,
    fetch_sector_list,
    fetch_sector_news,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hot")

DELAY = 1  # 接口间隔（秒）


# ── 盘中热度快照 ──

def run_heat_snapshot(session, top_n: int = 200):
    """采集实时人气排名，与上一次快照对比计算排名变化"""
    start = time.time()
    # 快照时间取整到小时（如 14:30 → 14:00）
    now = datetime.now()
    snapshot_time = now.replace(minute=0, second=0, microsecond=0)

    logger.info(f"[热度快照] 采集前{top_n}名，快照时间: {snapshot_time}")

    # 1. 采集当前排名
    current = fetch_realtime_hot_rank(top_n)
    if not current:
        logger.warning("[热度快照] 未采集到数据")
        return

    logger.info(f"[热度快照] 采集到 {len(current)} 条")

    # 2. 查上一次快照（同日更早的，或昨日最后一次）
    from sqlalchemy import text
    prev_map = {}
    prev_rows = session.execute(text("""
        SELECT stock_code, `rank`
        FROM intraday_heat_snapshot
        WHERE snapshot_time = (
            SELECT MAX(snapshot_time) FROM intraday_heat_snapshot
            WHERE snapshot_time < :now
        )
    """), {"now": snapshot_time}).fetchall()
    for row in prev_rows:
        prev_map[row[0]] = row[1]

    logger.info(f"[热度快照] 上次快照 {len(prev_map)} 条可对比")

    # 3. 构建入库数据
    rows = []
    alerts = []  # 热度飙升的票
    for item in current:
        code = item["stock_code"]
        cur_rank = item["rank"]
        prev_rank = prev_map.get(code)
        rank_change = (prev_rank - cur_rank) if prev_rank is not None else None

        rows.append({
            "stock_code": code,
            "stock_name": item["stock_name"],
            "snapshot_time": snapshot_time,
            "rank": cur_rank,
            "prev_rank": prev_rank,
            "rank_change": rank_change,
            "new_price": item["new_price"],
            "change_rate": item["change_rate"],
            "deal_amount": None,
            "volume_ratio": None,
            "turnover_rate": None,
        })

        # 热度飙升信号：排名上升>50 且 涨幅 3-8%（有空间但没涨停）
        chg = item.get("change_rate") or 0
        if rank_change is not None and rank_change > 50 and 3 <= chg <= 8:
            alerts.append(item | {"rank_change": rank_change, "prev_rank": prev_rank})

    # 4. 入库
    affected = batch_upsert(
        session, IntradayHeatSnapshot, rows,
        update_cols=["rank", "prev_rank", "rank_change", "new_price",
                     "change_rate", "volume_ratio", "turnover_rate", "deal_amount"],
    )
    elapsed = time.time() - start
    logger.info(f"[热度快照] 完成: {len(rows)} 条, affected {affected}, 耗时 {elapsed:.1f}s")

    # 5. 输出热度飙升信号 + 写入 heat_change_top
    if alerts:
        alerts_sorted = sorted(alerts, key=lambda x: x["rank_change"], reverse=True)
        logger.info(f"[热度快照] ===== 热度飙升信号 ({len(alerts_sorted)} 只) =====")

        today = now.date()
        hct_records = []
        for a in alerts_sorted:
            logger.info(
                f"  {a['stock_code']} {a['stock_name']} "
                f"排名: {a['prev_rank']}→{a['rank']}(+{a['rank_change']}) "
                f"涨幅: {a['change_rate']:.1f}%"
            )
            hct_records.append({
                "stock_code": a["stock_code"],
                "stock_name": a["stock_name"],
                "date": today,
                "time_point": snapshot_time.strftime("%H:%M"),
                "rank_today": a["rank"],
                "rank_yesterday": a["prev_rank"],
                "rank_change": a["rank_change"],
                "new_price": a["new_price"],
                "change_rate": a["change_rate"],
                "volume_ratio": None,
                "turnover_rate": None,
            })

        # insert_ignore: 每只股票每天只记录首次入选，重跑不覆盖
        hct_affected = batch_insert_ignore(session, HeatChangeTop, hct_records)
        logger.info(f"[热度快照] 信号入库: {hct_affected}/{len(hct_records)} 条写入 heat_change_top")
    else:
        logger.info("[热度快照] 本次无热度飙升信号")

    return alerts


# ── 板块新闻采集 ──

def run_sector_news(session, sector_top: int = 20):
    """采集涨幅前N的概念板块 + 行业板块新闻"""
    start = time.time()
    total_saved = 0

    for sector_type, label in [("concept", "概念"), ("industry", "行业")]:
        logger.info(f"[板块新闻] 获取{label}板块列表...")
        sectors = fetch_sector_list(sector_type)
        if not sectors:
            logger.warning(f"[板块新闻] {label}板块列表为空")
            continue

        # 按涨幅排序取前N
        sectors.sort(key=lambda x: x.get("change_rate") or 0, reverse=True)
        top_sectors = sectors[:sector_top]

        logger.info(
            f"[板块新闻] {label}板块 Top{sector_top}: "
            + ", ".join(f"{s['sector_name']}({s['change_rate']:+.1f}%)"
                        for s in top_sectors[:5])
            + "..."
        )

        for i, sector in enumerate(top_sectors):
            name = sector["sector_name"]
            code = sector.get("sector_code", "")
            news = fetch_sector_news(name, sector_type)
            if news:
                # 补充板块代码
                for n in news:
                    n["sector_code"] = code
                saved = batch_insert_ignore(session, SectorNews, news)
                total_saved += saved
                if saved > 0:
                    logger.info(f"  [{name}] {len(news)} 篇, 新增 {saved}")
            else:
                logger.debug(f"  [{name}] 无新闻")

            if (i + 1) % 10 == 0:
                logger.info(f"[板块新闻] {label}进度 {i+1}/{len(top_sectors)}")
            time.sleep(DELAY)

    elapsed = time.time() - start
    logger.info(f"[板块新闻] 完成: 新增 {total_saved} 篇, 耗时 {elapsed:.1f}s")


# ── 主入口 ──

def main():
    parser = argparse.ArgumentParser(description="盘中热度监控 + 板块新闻")
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--all", action="store_true", help="执行全部任务")
    parser.add_argument("--sector", action="store_true", help="只采集板块新闻")
    parser.add_argument("--top", type=int, default=200, help="热度快照采集前N名(默认200)")
    parser.add_argument("--sector-top", type=int, default=20, help="板块新闻采集涨幅前N(默认20)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        if args.sector and not args.all:
            # 只采集板块新闻
            logger.info("========== 板块新闻采集开始 ==========")
            run_sector_news(session, args.sector_top)
            logger.info("========== 板块新闻采集完成 ==========")
        elif args.all:
            # 全部执行
            logger.info("========== 盘中热度监控开始 ==========")
            run_heat_snapshot(session, args.top)
            run_sector_news(session, args.sector_top)
            logger.info("========== 盘中热度监控完成 ==========")
        else:
            # 默认：热度快照
            logger.info("========== 热度快照开始 ==========")
            run_heat_snapshot(session, args.top)
            logger.info("========== 热度快照完成 ==========")
    finally:
        session.close()


if __name__ == "__main__":
    main()
