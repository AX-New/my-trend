"""股吧情绪调度层

用法：
  python -m guba.main --stock 600519              # 单股情绪分析
  python -m guba.main --stock 600519,000858       # 多股
"""

import argparse
import logging
import time
from datetime import datetime

from config import load_config
from database import Database, batch_upsert
from guba.models import GubaSentiment, GubaPostDetail
from guba.fetcher import (
    fetch_guba_posts, classify_posts, calc_sentiment, _assign_weights,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("guba")

DELAY = 1  # 限流间隔（秒）


def _save_post_details(session, code: str, date, posts, labels, weights):
    """保存帖子明细：先删除该股票该日旧数据，再写入新数据（无事务）"""
    # 删除旧明细
    deleted = (
        session.query(GubaPostDetail)
        .filter(GubaPostDetail.stock_code == code, GubaPostDetail.date == date)
        .delete()
    )
    session.commit()
    if deleted:
        logger.info(f"[{code}] 删除旧明细 {deleted} 条")

    # 写入新明细
    rows = []
    for p, label, weight in zip(posts, labels, weights):
        pub_time = None
        try:
            pub_time = datetime.strptime(p["publish_time"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        rows.append({
            "stock_code": code,
            "date": date,
            "title": p["title"][:500],
            "click_count": p["click_count"],
            "comment_count": p["comment_count"],
            "forward_count": p["forward_count"],
            "publish_time": pub_time,
            "label": label,
            "weight": weight,
        })

    from database import batch_insert_ignore
    saved = batch_insert_ignore(session, GubaPostDetail, rows)
    logger.info(f"[{code}] 写入明细 {saved} 条")


def run_stock(session, llm_cfg, codes: list[str]):
    """单股/多股情绪分析：抓帖子 → LLM 分类 → 计算得分 → 入库"""
    start = time.time()
    today = datetime.now().date()
    results = []

    for i, code in enumerate(codes):
        logger.info(f"[{code}] 开始采集（{i+1}/{len(codes)}）")

        # 1. 抓最近 24 小时帖子
        posts = fetch_guba_posts(code)
        if not posts:
            logger.info(f"[{code}] 无帖子，跳过")
            time.sleep(DELAY)
            continue

        # 2. LLM 分类
        titles = [p["title"] for p in posts]
        logger.info(f"[{code}] {len(titles)} 条帖子，调用 LLM 分类...")
        labels = classify_posts(titles, llm_cfg)

        # 3. 计算权重和情绪得分
        click_counts = [p["click_count"] for p in posts]
        weights = _assign_weights(click_counts)
        sentiment = calc_sentiment(posts, labels)
        if sentiment is None:
            logger.warning(f"[{code}] 情绪计算失败")
            time.sleep(DELAY)
            continue

        # 打印每条帖子的分类结果
        label_map = {1: "看多", -1: "看空", 0: "中性"}
        for p, label, w in zip(posts, labels, weights):
            tag = label_map.get(label, "中性")
            logger.info(
                f"  [{tag:2s}] W{w} 阅读:{p['click_count']:>6d}  "
                f"评论:{p['comment_count']:>3d}  {p['title'][:60]}"
            )

        bull = sentiment["bull_count"]
        bear = sentiment["bear_count"]
        neutral = sentiment["neutral_count"]
        logger.info(
            f"[{code}] 情绪得分={sentiment['score']:+.4f}  "
            f"看多={bull} 看空={bear} 中性={neutral}  "
            f"阅读={sentiment['total_read']} 评论={sentiment['total_comment']}"
        )

        # 4. 保存帖子明细（先删旧再写新）
        _save_post_details(session, code, today, posts, labels, weights)

        results.append({
            "stock_code": code,
            "date": today,
            "score": sentiment["score"],
            "post_count": sentiment["post_count"],
            "bull_count": bull,
            "bear_count": bear,
            "neutral_count": neutral,
            "total_read": sentiment["total_read"],
            "total_comment": sentiment["total_comment"],
        })

        time.sleep(DELAY)

    # 5. 汇总入库
    if results:
        affected = batch_upsert(
            session, GubaSentiment, results,
            update_cols=["score", "post_count", "bull_count", "bear_count",
                         "neutral_count", "total_read", "total_comment"],
        )
        elapsed = time.time() - start
        logger.info(f"[情绪分析] 完成：{len(results)} 只股票，affected {affected}，耗时 {elapsed:.1f}s")
    else:
        logger.warning("[情绪分析] 无有效结果")


def main():
    parser = argparse.ArgumentParser(description="股吧情绪分析")
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--stock", type=str, required=True,
                        help="股票代码，多只用逗号分隔")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    codes = [c.strip() for c in args.stock.split(",") if c.strip()]
    logger.info(f"========== 股吧情绪分析开始（{len(codes)} 只）==========")

    try:
        run_stock(session, cfg.llm, codes)
    finally:
        session.close()

    logger.info("========== 股吧情绪分析完成 ==========")


if __name__ == "__main__":
    main()
