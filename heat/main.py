"""热度数据调度层 —— 1s 间隔限流

用法：
  python -m heat.main                  # 每日人气排名（全市场 ~5500 只）
  python -m heat.main --keyword        # Top100 热门关键词
  python -m heat.main --init           # 回溯历史趋势（366天，首次运行）
  python -m heat.main -c config.yaml   # 指定配置
"""

import argparse
import logging
import time
from datetime import datetime

import requests as _req
from sqlalchemy import text

from config import load_config, load_stocks
from database import Database, batch_upsert, batch_insert_ignore
from heat.models import PopularityRank, EmHotRankDetail, EmHotKeyword, BaiduHotSearch
from heat.fetcher import (
    fetch_popularity_page, fetch_hot_rank_detail_em,
    fetch_hot_keyword_em, fetch_hot_search_baidu,
    POPULARITY_FILTERS, _to_ak_symbol,
)

# my_stock 库连接（stock_basic 表）
STOCK_DB_URL = "mysql+pymysql://root:root@localhost:3306/my_stock?charset=utf8mb4"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("heat")

DELAY = 1  # 调度层限流间隔（秒）
INIT_BATCH = 100  # --init 每批处理股票数


def _safe_float(val):
    if val is None or val == "-" or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    if val is None or val == "-" or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ── 人气排名：分页采集 + 1s 间隔 ──

def run_popularity(session, cfg):
    """采集全市场人气排名，分页循环，每页间隔 1s"""
    start = time.time()
    all_rows = []
    today = datetime.now().date()
    total_pages = 0

    for filter_str in POPULARITY_FILTERS:
        page = 1
        filter_count = 0
        logger.info(f"[人气排名] 开始采集 filter={filter_str}")
        while True:
            try:
                data = fetch_popularity_page(
                    cfg.network, filter_str, page, cfg.popularity.page_size,
                )
            except Exception as e:
                logger.warning(f"[人气排名] {filter_str} page={page} 失败: {e}")
                break

            items = data.get("result", {}).get("data", [])
            if not items:
                break

            for item in items:
                code = item.get("SECURITY_CODE", "")
                if not code:
                    continue
                rank_date = today
                max_trade = item.get("MAX_TRADE_DATE")
                if max_trade:
                    try:
                        rank_date = datetime.strptime(max_trade[:10], "%Y-%m-%d").date()
                    except Exception:
                        pass
                all_rows.append({
                    "stock_code": code,
                    "date": rank_date,
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
            logger.info(f"[人气排名] {filter_str} page={page}，本页 {len(items)} 条，累计 {filter_count} 条")

            if not data.get("result", {}).get("nextpage"):
                break
            page += 1
            time.sleep(DELAY)

        logger.info(f"[人气排名] filter={filter_str} 完成，共 {page} 页 {filter_count} 条")

    if all_rows:
        affected = batch_upsert(
            session, PopularityRank, all_rows,
            update_cols=["rank", "stock_name", "new_price", "change_rate",
                         "volume_ratio", "turnover_rate", "volume", "deal_amount"],
        )
        elapsed = time.time() - start
        logger.info(
            f"[人气排名] 完成：共 {len(all_rows)} 条，{total_pages} 页，"
            f"affected {affected}，耗时 {elapsed:.1f}s"
        )
    else:
        logger.warning("[人气排名] 未采集到数据")


# ── 关键词采集：Top100 逐只采集 + 1s 间隔 ──

def run_keyword(session):
    """采集当日人气排名 Top100 的热门关键词"""
    start = time.time()
    today = datetime.now().date()
    top_codes = (
        session.query(PopularityRank.stock_code, PopularityRank.stock_name)
        .filter(PopularityRank.date == today)
        .order_by(PopularityRank.rank)
        .limit(100)
        .all()
    )
    if not top_codes:
        logger.warning("[关键词] 今日无人气排名数据，请先运行人气排名采集")
        return

    codes = [(r[0], r[1]) for r in top_codes]
    logger.info(f"[关键词] Top{len(codes)} 股票关键词采集开始")

    all_data = []
    success = 0
    empty = 0
    for i, (code, name) in enumerate(codes):
        symbol = _to_ak_symbol(code)
        data = fetch_hot_keyword_em(symbol)
        if data:
            all_data.extend(data)
            success += 1
            logger.debug(f"[关键词] {code} {name}：{len(data)} 个关键词")
        else:
            empty += 1
        if (i + 1) % 20 == 0:
            logger.info(
                f"[关键词] 进度 {i+1}/{len(codes)}"
                f"（成功 {success}，空 {empty}，累计 {len(all_data)} 条）"
            )
        time.sleep(DELAY)

    elapsed = time.time() - start
    if all_data:
        saved = batch_insert_ignore(session, EmHotKeyword, all_data)
        logger.info(
            f"[关键词] 完成：{success} 只有数据，{empty} 只为空，"
            f"新增 {saved}/{len(all_data)} 条，耗时 {elapsed:.1f}s"
        )
    else:
        logger.warning(f"[关键词] 未采集到数据，耗时 {elapsed:.1f}s")


# ── 历史趋势回溯：逐只采集 + 1s 间隔 ──

def _load_stock_basic() -> dict[str, str]:
    """从 my_stock.stock_basic 读取全部在市股票，返回 {symbol: name}"""
    from sqlalchemy import create_engine
    engine = create_engine(STOCK_DB_URL, echo=False)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT symbol, name FROM stock_basic WHERE list_status = 'L' ORDER BY symbol")
        ).fetchall()
    engine.dispose()
    return {row[0]: row[1] for row in rows}


def run_init(session):
    """--init: 回溯全市场股票的 366 天历史趋势，并回写单日排名"""
    start = time.time()

    # ── 第一步：从 my_stock.stock_basic 获取股票列表 ──
    logger.info("[init] 从 my_stock.stock_basic 读取在市股票...")
    all_codes = _load_stock_basic()

    if not all_codes:
        logger.warning("[init] stock_basic 无在市股票数据")
        return

    codes = list(all_codes.keys())
    total_batches = (len(codes) + INIT_BATCH - 1) // INIT_BATCH
    logger.info(f"[init] 共 {len(codes)} 只在市股票，分 {total_batches} 批（每批 {INIT_BATCH} 只）")

    # ── 第二步：分批采集历史趋势 ──
    total_detail_saved = 0
    all_rank_rows = []  # 收集用于回写 popularity_rank 的数据

    for batch_idx in range(0, len(codes), INIT_BATCH):
        batch_codes = codes[batch_idx:batch_idx + INIT_BATCH]
        batch_num = batch_idx // INIT_BATCH + 1
        logger.info(f"[init] ── 第 {batch_num}/{total_batches} 批开始，{len(batch_codes)} 只股票 ──")

        batch_detail = []
        batch_success = 0
        batch_empty = 0

        for i, code in enumerate(batch_codes):
            symbol = _to_ak_symbol(code)
            name = all_codes.get(code, "")
            data = fetch_hot_rank_detail_em(symbol)

            if data:
                batch_detail.extend(data)
                batch_success += 1
                # 提取每日排名，用于回写 popularity_rank
                for d in data:
                    ts = d.get("timestamp")
                    rank = d.get("rank")
                    if ts and rank is not None:
                        rank_date = ts.date() if hasattr(ts, "date") else ts
                        all_rank_rows.append({
                            "stock_code": d["stock_code"],
                            "stock_name": name,
                            "date": rank_date,
                            "rank": rank,
                        })
            else:
                batch_empty += 1

            if (i + 1) % 20 == 0:
                logger.info(
                    f"[init] 批内进度 {i+1}/{len(batch_codes)}"
                    f"（成功 {batch_success}，空 {batch_empty}）"
                )
            time.sleep(DELAY)

        # 本批趋势数据入库
        if batch_detail:
            saved = batch_insert_ignore(session, EmHotRankDetail, batch_detail)
            total_detail_saved += saved
            logger.info(
                f"[init] 第 {batch_num} 批趋势数据：采集 {len(batch_detail)} 条，"
                f"新增 {saved} 条（成功 {batch_success} 只，空 {batch_empty} 只）"
            )
        else:
            logger.warning(f"[init] 第 {batch_num} 批：无趋势数据")

    step2_elapsed = time.time() - start
    logger.info(f"[init] 趋势采集完成，共新增 {total_detail_saved} 条，耗时 {step2_elapsed:.1f}s")

    # ── 第三步：回写单日排名 ──
    if not all_rank_rows:
        logger.warning("[init] 无排名数据可回写")
        return

    # 按日期统计
    dates = {r["date"] for r in all_rank_rows}
    logger.info(
        f"[init] 开始回写单日排名：{len(all_rank_rows)} 条，"
        f"覆盖 {len(dates)} 天（{min(dates)} ~ {max(dates)}）"
    )

    RANK_CHUNK = 1000
    total_rank_affected = 0
    for i in range(0, len(all_rank_rows), RANK_CHUNK):
        chunk = all_rank_rows[i:i + RANK_CHUNK]
        affected = batch_upsert(
            session, PopularityRank, chunk,
            update_cols=["rank"],
        )
        total_rank_affected += affected
        logger.info(
            f"[init] 回写进度 {min(i + RANK_CHUNK, len(all_rank_rows))}/{len(all_rank_rows)}，"
            f"affected {affected}"
        )

    total_elapsed = time.time() - start
    logger.info(
        f"[init] 单日排名回写完成，共 affected {total_rank_affected}，"
        f"总耗时 {total_elapsed:.1f}s"
    )


# ── 百度热搜：A股/港股/美股 Top12 ──

def run_baidu(session):
    """采集百度热搜 Top12，A股+港股+美股，今日+1小时两个维度"""
    start = time.time()
    today = datetime.now().strftime("%Y%m%d")

    # 只采集"今日"维度入库（"1小时"数据波动大，意义不大）
    all_rows = []
    for market in ["A股", "港股", "美股"]:
        data = fetch_hot_search_baidu(market=market, time="今日", date=today)
        all_rows.extend(data)
        time.sleep(DELAY)

    if all_rows:
        affected = batch_upsert(
            session, BaiduHotSearch, all_rows,
            update_cols=["stock_name", "change_rate", "heat"],
        )
        elapsed = time.time() - start
        logger.info(
            f"[百度热搜] 完成：{len(all_rows)} 条（A股+港股+美股），"
            f"affected {affected}，耗时 {elapsed:.1f}s"
        )
    else:
        logger.warning("[百度热搜] 未采集到数据")


# ── 代理处理 ──

def _disable_proxy():
    _orig = _req.Session.__init__
    def _patched(self, *a, **kw):
        _orig(self, *a, **kw)
        self.trust_env = False
    _req.Session.__init__ = _patched
    return _orig


def _restore_proxy(orig):
    _req.Session.__init__ = orig


def main():
    parser = argparse.ArgumentParser(description="热度数据采集")
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--init", action="store_true",
                        help="回溯历史趋势（366天，仅首次）")
    parser.add_argument("--keyword", action="store_true",
                        help="采集 Top100 热门关键词")
    parser.add_argument("--baidu", action="store_true",
                        help="百度热搜 Top12（A股/港股/美股）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    orig = _disable_proxy()
    try:
        if args.init:
            logger.info("========== 热度回溯开始 ==========")
            run_init(session)
            logger.info("========== 热度回溯完成 ==========")
        elif args.keyword:
            logger.info("========== 关键词采集开始 ==========")
            run_keyword(session)
            logger.info("========== 关键词采集完成 ==========")
        elif args.baidu:
            logger.info("========== 百度热搜采集开始 ==========")
            run_baidu(session)
            logger.info("========== 百度热搜采集完成 ==========")
        else:
            logger.info("========== 人气排名采集开始 ==========")
            run_popularity(session, cfg)
            logger.info("========== 人气排名采集完成 ==========")
    finally:
        session.close()
        _restore_proxy(orig)


if __name__ == "__main__":
    main()
