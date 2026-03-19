"""热度变化分析 —— 对比今日 vs 昨日人气排名，输出 Top20 飙升股

用法：
  python -m heat.analyze              # 分析当前热度变化
  python -m heat.analyze -c config.yaml
"""

import argparse
import logging
import time
from datetime import datetime

from sqlalchemy import text

from config import load_config
from database import Database, batch_upsert, batch_insert_ignore
from heat.models import PopularityRank, HeatChangeTop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("heat.analyze")

TOP_N = 20


def run_analyze(session):
    """计算今日 vs 昨日排名变化，取 Top20 写入 heat_change_top"""
    start = time.time()
    today = datetime.now().date()
    time_point = datetime.now().strftime("%H:%M")

    # 查上一个有数据的交易日（不一定是昨天，可能跨周末）
    last_date = session.execute(
        text("SELECT MAX(date) FROM popularity_rank WHERE date < :today"),
        {"today": today},
    ).scalar()

    if not last_date:
        logger.warning("[分析] 无历史数据可对比")
        return

    # 检查今日是否有数据
    today_count = session.execute(
        text("SELECT COUNT(*) FROM popularity_rank WHERE date = :today"),
        {"today": today},
    ).scalar()

    if not today_count:
        logger.warning("[分析] 今日尚无人气排名数据，跳过")
        return

    # 查询当天已入选的股票，后续排除
    existing = session.execute(
        text("SELECT stock_code FROM heat_change_top WHERE date = :today"),
        {"today": today},
    ).fetchall()
    existing_codes = {r[0] for r in existing}

    logger.info(
        f"[分析] 对比：今日 {today}（{today_count} 条）vs 昨日 {last_date}，"
        f"时间点 {time_point}，已入选 {len(existing_codes)} 只"
    )

    # JOIN 今日和昨日排名，计算变化，排除已入选股票，取 Top20 新股
    # rank_change = 昨日排名 - 今日排名，正数表示排名上升（更受关注）
    if existing_codes:
        # 动态构造 NOT IN 子句（参数化防注入）
        placeholders = ",".join(f":ex_{i}" for i in range(len(existing_codes)))
        exclude_clause = f"AND t.stock_code NOT IN ({placeholders})"
        params = {f"ex_{i}": code for i, code in enumerate(existing_codes)}
    else:
        exclude_clause = ""
        params = {}

    sql = text(f"""
        SELECT
            t.stock_code, t.stock_name,
            t.`rank` AS rank_today,
            y.`rank` AS rank_yesterday,
            (y.`rank` - t.`rank`) AS rank_change,
            t.new_price, t.change_rate, t.volume_ratio, t.turnover_rate
        FROM popularity_rank t
        JOIN popularity_rank y
            ON t.stock_code = y.stock_code AND y.date = :yesterday
        WHERE t.date = :today
          AND y.`rank` IS NOT NULL
          AND t.`rank` IS NOT NULL
          {exclude_clause}
        ORDER BY rank_change DESC
        LIMIT :top_n
    """)

    params.update({"today": today, "yesterday": last_date, "top_n": TOP_N})
    rows = session.execute(sql, params).fetchall()

    if not rows:
        logger.warning("[分析] 无排名变化数据")
        return

    logger.info(f"[分析] Top{TOP_N} 热度飙升股：")
    records = []
    for r in rows:
        logger.info(
            f"  {r.stock_code} {r.stock_name}: "
            f"排名 {r.rank_yesterday}→{r.rank_today}（↑{r.rank_change}），"
            f"涨幅 {r.change_rate}%"
        )
        records.append({
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "date": today,
            "time_point": time_point,
            "rank_today": r.rank_today,
            "rank_yesterday": r.rank_yesterday,
            "rank_change": r.rank_change,
            "new_price": r.new_price,
            "change_rate": r.change_rate,
            "volume_ratio": r.volume_ratio,
            "turnover_rate": r.turnover_rate,
        })

    # insert_ignore: 每只股票每天只记录首次入选，重跑不覆盖
    affected = batch_insert_ignore(session, HeatChangeTop, records)

    elapsed = time.time() - start
    logger.info(
        f"[分析] 完成：新增 {affected}/{len(records)} 条，"
        f"当日累计 {len(existing_codes) + affected} 只，耗时 {elapsed:.1f}s"
    )


def main():
    parser = argparse.ArgumentParser(description="热度变化分析")
    parser.add_argument("-c", "--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        logger.info("========== 热度变化分析开始 ==========")
        run_analyze(session)
        logger.info("========== 热度变化分析完成 ==========")
    finally:
        session.close()


if __name__ == "__main__":
    main()
