"""热度 Top 股票分钟K线采集 —— 收盘后运行

采集当日所有出现在 heat_change_top 中的股票的 1 分钟 K 线，
用于分析热度飙升股的盘中走势规律。

用法：
  python -m heat.minute              # 采集今日 Top 股票分钟线
  python -m heat.minute -c config.yaml
"""

import argparse
import logging
import time
from datetime import datetime

import akshare as ak
from sqlalchemy import text

from config import load_config
from database import Database, batch_upsert
from heat.models import HeatChangeTop, HeatStockMinute

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("heat.minute")

DELAY = 1  # 每只股票间隔（秒），防限流


def _to_minute_symbol(stock_code: str) -> str:
    """股票代码转 akshare 分钟线格式：600519 → sh600519"""
    code = stock_code.strip()
    if code.startswith("6") or code.startswith("9"):
        return f"sh{code}"
    return f"sz{code}"


def run_minute(session):
    """采集今日热度 Top 股票的 1 分钟 K 线"""
    start = time.time()
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")

    # 查询今日所有出现在 heat_change_top 中的股票（去重）
    sql = text("""
        SELECT DISTINCT stock_code, stock_name
        FROM heat_change_top
        WHERE date = :today
        ORDER BY stock_code
    """)
    stocks = session.execute(sql, {"today": today}).fetchall()

    if not stocks:
        logger.warning("[分钟线] 今日无热度 Top 股票数据")
        return

    logger.info(f"[分钟线] 今日 {len(stocks)} 只热度 Top 股票待采集")

    all_rows = []
    success = 0
    fail = 0

    for i, (code, name) in enumerate(stocks):
        symbol = _to_minute_symbol(code)
        try:
            df = ak.stock_zh_a_minute(symbol=symbol, period="1")
            if df is not None and not df.empty:
                # 过滤只保留当日数据
                df_today = df[df["day"].astype(str).str.startswith(today_str)]
                for _, row in df_today.iterrows():
                    all_rows.append({
                        "stock_code": code,
                        "stock_name": name,
                        "trade_date": today,
                        "time": row["day"],
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "volume": float(row.get("volume", 0)),
                    })
                success += 1
                logger.debug(f"[分钟线] {code} {name}：{len(df_today)} 条")
            else:
                fail += 1
                logger.warning(f"[分钟线] {code} {name}：无数据")
        except Exception as e:
            fail += 1
            logger.warning(f"[分钟线] {code} {name} 失败: {e}")

        if (i + 1) % 10 == 0:
            logger.info(
                f"[分钟线] 进度 {i+1}/{len(stocks)}"
                f"（成功 {success}，失败 {fail}，累计 {len(all_rows)} 条）"
            )
        time.sleep(DELAY)

    if all_rows:
        affected = batch_upsert(
            session, HeatStockMinute, all_rows,
            update_cols=["stock_name", "open", "high", "low", "close", "volume"],
        )
        elapsed = time.time() - start
        logger.info(
            f"[分钟线] 完成：{success} 只成功，{fail} 只失败，"
            f"共 {len(all_rows)} 条，affected {affected}，耗时 {elapsed:.1f}s"
        )
    else:
        logger.warning("[分钟线] 未采集到数据")


def main():
    parser = argparse.ArgumentParser(description="热度 Top 股票分钟K线采集")
    parser.add_argument("-c", "--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        logger.info("========== 分钟K线采集开始 ==========")
        run_minute(session)
        logger.info("========== 分钟K线采集完成 ==========")
    finally:
        session.close()


if __name__ == "__main__":
    main()
