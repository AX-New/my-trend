"""热度股票分钟K线采集 —— 收盘后运行

采集热度相关股票的 1 分钟 K 线，用于回测分析。
支持多种采集模式，自动排除当天已采集的股票。

用法：
  python -m heat.minute                # 默认：heat_change_top 入选股
  python -m heat.minute --top100       # 人气排名 Top100
  python -m heat.minute --market       # 市场热度采样（Top100 + heat_change_top 合集）
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

DELAY = 1.5  # AkShare 限流间隔（秒）
BATCH_SAVE = 50  # 每采集 N 只股票批量入库一次，防中途崩溃丢数据


def _to_minute_symbol(stock_code: str) -> str:
    """股票代码转 akshare 分钟线格式：600519 → sh600519"""
    code = stock_code.strip()
    if code.startswith("6") or code.startswith("9"):
        return f"sh{code}"
    return f"sz{code}"


def _get_already_fetched(session, today) -> set:
    """查询当天已采集过分钟线的股票代码"""
    rows = session.execute(
        text("SELECT DISTINCT stock_code FROM heat_stock_minute WHERE trade_date = :today"),
        {"today": today},
    ).fetchall()
    return {r[0] for r in rows}


def _get_heat_change_stocks(session, today) -> list[tuple]:
    """获取当天 heat_change_top 入选股票"""
    rows = session.execute(
        text("SELECT DISTINCT stock_code, stock_name FROM heat_change_top WHERE date = :today ORDER BY stock_code"),
        {"today": today},
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _get_top_n_stocks(session, today, n=100) -> list[tuple]:
    """获取当天人气排名 Top N 股票"""
    rows = session.execute(
        text("SELECT stock_code, stock_name FROM popularity_rank WHERE date = :today ORDER BY `rank` ASC LIMIT :n"),
        {"today": today, "n": n},
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _fetch_and_save(session, stocks: list[tuple], today, today_str: str):
    """采集股票列表的 1 分钟 K 线并入库

    Args:
        stocks: [(stock_code, stock_name), ...]
        today: date 对象
        today_str: "YYYY-MM-DD" 格式
    """
    all_rows = []
    success = 0
    fail = 0
    total_saved = 0

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
            else:
                fail += 1
                logger.warning(f"[分钟线] {code} {name}：无数据")
        except Exception as e:
            fail += 1
            logger.warning(f"[分钟线] {code} {name} 失败: {e}")

        # 定期入库，防止中途崩溃丢数据
        if (i + 1) % BATCH_SAVE == 0 and all_rows:
            affected = batch_upsert(
                session, HeatStockMinute, all_rows,
                update_cols=["stock_name", "open", "high", "low", "close", "volume"],
            )
            total_saved += affected
            logger.info(
                f"[分钟线] 进度 {i+1}/{len(stocks)}"
                f"（成功 {success}，失败 {fail}，本批 {affected} 条）"
            )
            all_rows = []

        time.sleep(DELAY)

    # 剩余数据入库
    if all_rows:
        affected = batch_upsert(
            session, HeatStockMinute, all_rows,
            update_cols=["stock_name", "open", "high", "low", "close", "volume"],
        )
        total_saved += affected

    return success, fail, total_saved


def run_minute(session, mode="default"):
    """采集分钟K线主逻辑

    Args:
        mode: "default"=heat_change_top, "top100"=人气Top100, "market"=合集
    """
    start = time.time()
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")

    # 收集目标股票列表
    if mode == "top100":
        raw_stocks = _get_top_n_stocks(session, today, n=100)
        source = "人气Top100"
    elif mode == "market":
        # 合集：heat_change_top + Top100，去重
        heat_stocks = _get_heat_change_stocks(session, today)
        top_stocks = _get_top_n_stocks(session, today, n=100)
        seen = set()
        raw_stocks = []
        for code, name in heat_stocks + top_stocks:
            if code not in seen:
                seen.add(code)
                raw_stocks.append((code, name))
        source = f"市场采样（heat_change {len(heat_stocks)} + top100 {len(top_stocks)}）"
    else:
        raw_stocks = _get_heat_change_stocks(session, today)
        source = "heat_change_top"

    if not raw_stocks:
        logger.warning(f"[分钟线] {source}：无目标股票")
        return

    # 排除当天已采集的
    already = _get_already_fetched(session, today)
    stocks = [(code, name) for code, name in raw_stocks if code not in already]

    logger.info(
        f"[分钟线] 模式={source}，目标 {len(raw_stocks)} 只，"
        f"已采集 {len(already)} 只，待采集 {len(stocks)} 只"
    )

    if not stocks:
        logger.info("[分钟线] 所有股票已采集，无需重复")
        return

    # 采集并入库
    success, fail, total_saved = _fetch_and_save(session, stocks, today, today_str)

    elapsed = time.time() - start
    logger.info(
        f"[分钟线] 完成：{success} 只成功，{fail} 只失败，"
        f"共 {total_saved} 条入库，耗时 {elapsed:.1f}s"
    )


def main():
    parser = argparse.ArgumentParser(description="热度股票分钟K线采集")
    parser.add_argument("-c", "--config", default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--top100", action="store_true",
                       help="采集人气排名 Top100 股票")
    group.add_argument("--market", action="store_true",
                       help="采集市场热度采样（Top100 + heat_change_top 合集）")
    args = parser.parse_args()

    # 确定模式
    if args.top100:
        mode = "top100"
    elif args.market:
        mode = "market"
    else:
        mode = "default"

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        logger.info(f"========== 分钟K线采集开始（{mode}） ==========")
        run_minute(session, mode=mode)
        logger.info(f"========== 分钟K线采集完成（{mode}） ==========")
    finally:
        session.close()


if __name__ == "__main__":
    main()
