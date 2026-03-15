"""东方财富热度每日监控

每日采集：
  1. stock_hot_rank_em              — 人气榜 Top100
  2. stock_hot_up_em                — 飙升榜 Top100
  3. stock_hot_rank_detail_realtime_em — 个股实时排名变动
  4. stock_hot_keyword_em           — 个股热门关键词

初始化（--init）额外采集：
  5. stock_hot_rank_detail_em       — 个股历史趋势+粉丝（366天回溯，仅首次）

用法：
  python monitor.py --init         # 首次运行，含历史回溯
  python monitor.py                # 每日常规采集
  python monitor.py -c config.yaml # 指定配置文件
"""

import argparse
import logging
from datetime import datetime

import requests as _req

from config import load_config
from database import Database
from models import (
    EmHotRank, EmHotUp, EmHotRankDetail, EmHotRankRealtime, EmHotKeyword,
)
from akshare_fetcher import (
    fetch_hot_rank_em, fetch_hot_up_em,
    fetch_hot_rank_detail_em, fetch_hot_rank_realtime_em,
    fetch_hot_keyword_em, _to_ak_symbol,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("monitor")


# ── 代理处理 ──

def _disable_proxy():
    """AkShare 用 requests，国内接口不需要系统代理"""
    _orig = _req.Session.__init__

    def _patched(self, *a, **kw):
        _orig(self, *a, **kw)
        self.trust_env = False

    _req.Session.__init__ = _patched
    return _orig


def _restore_proxy(orig):
    _req.Session.__init__ = orig


# ── 入库 ──

def _save_snapshot(session, model, items, today):
    """快照式：删当天旧数据，重新插入"""
    session.query(model).filter_by(date=today).delete()
    session.add_all([model(date=today, **item) for item in items])
    session.commit()


def _save_dedup(session, model, items, key_fields):
    """去重追加：预查已有 key，只插新记录"""
    if not items:
        return 0
    codes = list(set(item["stock_code"] for item in items))
    key_columns = [getattr(model, f) for f in key_fields]
    existing = set(
        session.query(*key_columns).filter(model.stock_code.in_(codes)).all()
    )
    saved = 0
    for item in items:
        key = tuple(item.get(f) for f in key_fields)
        if key not in existing:
            session.add(model(**item))
            saved += 1
    if saved:
        session.commit()
    return saved


# ── 单股批量采集 ──

def _fetch_per_stock(codes, label, fetch_fn):
    """对一批股票调用单股接口，合并结果"""
    results = []
    for i, code in enumerate(codes):
        symbol = _to_ak_symbol(code)
        if (i + 1) % 20 == 0:
            logger.info(f"[{label}] 进度 {i+1}/{len(codes)}")
        data = fetch_fn(symbol)
        if data:
            results.extend(data)
    return results


# ── 主流程 ──

def run_monitor(session, init: bool = False):
    today = datetime.now().date()

    # 1. 人气榜 Top100
    rank_items = fetch_hot_rank_em()
    if rank_items:
        _save_snapshot(session, EmHotRank, rank_items, today)
        logger.info(f"[人气榜] 存入 {len(rank_items)} 条")

    # 2. 飙升榜 Top100
    up_items = fetch_hot_up_em()
    if up_items:
        _save_snapshot(session, EmHotUp, up_items, today)
        logger.info(f"[飙升榜] 存入 {len(up_items)} 条")

    # 合并去重，确定单股采集对象
    all_codes = {}
    for item in (rank_items or []):
        all_codes[item["stock_code"]] = item.get("stock_name", "")
    for item in (up_items or []):
        all_codes[item["stock_code"]] = item.get("stock_name", "")

    if not all_codes:
        logger.warning("人气榜和飙升榜均为空，跳过单股采集")
        return

    codes = list(all_codes.keys())
    logger.info(f"单股采集: {len(codes)} 只（rank+up 去重）")

    # 3. 初始化：历史趋势 366 天回溯（仅 --init）
    if init:
        data = _fetch_per_stock(codes, "历史趋势", fetch_hot_rank_detail_em)
        saved = _save_dedup(
            session, EmHotRankDetail, data,
            key_fields=["stock_code", "timestamp"],
        )
        logger.info(f"[历史趋势] 新增 {saved}/{len(data)} 条")

    # 4. 实时排名变动
    data = _fetch_per_stock(codes, "实时排名", fetch_hot_rank_realtime_em)
    saved = _save_dedup(
        session, EmHotRankRealtime, data,
        key_fields=["stock_code", "timestamp"],
    )
    logger.info(f"[实时排名] 新增 {saved}/{len(data)} 条")

    # 5. 热门关键词
    data = _fetch_per_stock(codes, "热门关键词", fetch_hot_keyword_em)
    saved = _save_dedup(
        session, EmHotKeyword, data,
        key_fields=["stock_code", "timestamp", "concept_code"],
    )
    logger.info(f"[热门关键词] 新增 {saved}/{len(data)} 条")


def main():
    parser = argparse.ArgumentParser(description="东方财富热度每日监控")
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    parser.add_argument(
        "--init", action="store_true",
        help="初始化：额外采集历史趋势（366天回溯，仅首次需要）",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()

    orig = _disable_proxy()
    try:
        session = db.get_session()
        mode = "初始化" if args.init else "每日"
        logger.info(f"========== 热度监控开始（{mode}） ==========")
        run_monitor(session, init=args.init)
        logger.info("========== 热度监控完成 ==========")
        session.close()
    finally:
        _restore_proxy(orig)


if __name__ == "__main__":
    main()
