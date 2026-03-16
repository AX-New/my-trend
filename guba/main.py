"""股吧情绪调度层

用法：
  python -m guba.main --stock 600519              # 单股情绪分析
  python -m guba.main --stock 600519,000858       # 多股
  python -m guba.main --market                    # 市场情绪采样（从人气排名分区间取40只）
  python -m guba.main --all                       # 全市场（la_pick），支持断点续跑
"""

import argparse
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from config import load_config
from database import Database, batch_upsert
from guba.models import GubaSentiment, GubaPostDetail
from guba.fetcher import (
    fetch_guba_posts, fetch_page1_timespan,
    classify_posts, calc_sentiment, _assign_weights,
)
from analysis.main import (
    _start_or_resume_batch, _advance_cursor, _inc_fail, _finish_run,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("guba")

DELAY = 1  # 限流间隔（秒）
MAX_LLM_WORKERS = 10  # LLM 并发线程上限


def _save_post_details(session, code: str, date, posts, labels, weights):
    """保存帖子明细：只保留当天阅读量最高的3条帖子

    先删除该股票该日旧数据，再写入 Top3 热门帖子。
    """
    # 删除旧明细
    deleted = (
        session.query(GubaPostDetail)
        .filter(GubaPostDetail.stock_code == code, GubaPostDetail.date == date)
        .delete()
    )
    session.commit()
    if deleted:
        logger.info(f"[{code}] 删除旧明细 {deleted} 条")

    # 按阅读量取 Top3 当天帖子
    today_str = date.strftime("%Y-%m-%d")
    top3_indices = []
    for i, p in enumerate(posts):
        if p["publish_time"].startswith(today_str):
            top3_indices.append(i)
    # 按阅读量降序，取前3
    top3_indices.sort(key=lambda i: posts[i]["click_count"], reverse=True)
    top3_indices = top3_indices[:3]

    rows = []
    for i in top3_indices:
        p = posts[i]
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
            "label": labels[i],
            "weight": weights[i],
        })

    from database import batch_insert_ignore
    saved = batch_insert_ignore(session, GubaPostDetail, rows)
    logger.info(f"[{code}] 写入明细 {saved} 条")


def run_stock(session, llm_cfg, codes: list[str]):
    """单股/多股情绪分析：抓帖子 → LLM 分类 → 计算得分 → 入库（串行）"""
    start = time.time()
    today = datetime.now().date()
    results = []

    for i, code in enumerate(codes):
        logger.info(f"[{code}] 开始采集（{i+1}/{len(codes)}）")

        # 1. 取第一页时间跨度（发帖频率指标）
        timespan = fetch_page1_timespan(code)

        # 2. 抓最近 24 小时帖子
        posts = fetch_guba_posts(code)
        if not posts:
            logger.info(f"[{code}] 无帖子，跳过")
            time.sleep(DELAY)
            continue

        # 3. LLM 分类
        titles = [p["title"] for p in posts]
        logger.info(f"[{code}] {len(titles)} 条帖子，调用 LLM 分类...")
        labels = classify_posts(titles, llm_cfg)

        # 4. 计算权重和情绪得分
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
        ts_str = f"{timespan:.1f}h" if timespan is not None else "N/A"
        logger.info(
            f"[{code}] 情绪={sentiment['score']:.1f}  "
            f"多={bull} 空={bear} 中={neutral}  "
            f"阅读={sentiment['total_read']} 评论={sentiment['total_comment']}  "
            f"首页跨度={ts_str}"
        )

        # 5. 保存帖子明细（先删旧再写新）
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
            "page1_timespan": timespan,
        })

        time.sleep(DELAY)

    # 汇总入库
    if results:
        affected = batch_upsert(
            session, GubaSentiment, results,
            update_cols=["score", "post_count", "bull_count", "bear_count",
                         "neutral_count", "total_read", "total_comment",
                         "page1_timespan"],
        )
        elapsed = time.time() - start
        logger.info(f"[情绪分析] 完成：{len(results)} 只股票，affected {affected}，耗时 {elapsed:.1f}s")
    else:
        logger.warning("[情绪分析] 无有效结果")


# ═══════════════════════════════════════════
# 市场情绪采样（并发 LLM）
# ═══════════════════════════════════════════

SAMPLE_TIERS = {
    "top":    (1, 10),
    "mid_a":  (91, 100),
    "mid_b":  (491, 500),
    "tail":   (991, 1000),
}


def _sample_stocks(session) -> list[tuple[str, str, str, int]]:
    """从最新 popularity_rank 按区间采样"""
    from sqlalchemy import func
    from heat.models import PopularityRank

    latest_date = session.query(func.max(PopularityRank.date)).scalar()
    if not latest_date:
        logger.warning("[采样] popularity_rank 表无数据")
        return []
    logger.info(f"[采样] 使用人气排名日期: {latest_date}")

    sampled = []
    for tier_name, (lo, hi) in SAMPLE_TIERS.items():
        rows = (
            session.query(
                PopularityRank.stock_code,
                PopularityRank.stock_name,
                PopularityRank.rank,
            )
            .filter(
                PopularityRank.date == latest_date,
                PopularityRank.rank >= lo,
                PopularityRank.rank <= hi,
            )
            .order_by(PopularityRank.rank)
            .limit(10)
            .all()
        )
        logger.info(f"[采样] {tier_name} (rank {lo}-{hi}): {len(rows)} 只")
        for code, name, rank in rows:
            sampled.append((code, name, tier_name, rank))

    return sampled


def _llm_task(code: str, posts: list[dict], llm_cfg, semaphore: threading.Semaphore):
    """线程任务：LLM 分类 + 计算情绪得分"""
    try:
        titles = [p["title"] for p in posts]
        labels = classify_posts(titles, llm_cfg)
        click_counts = [p["click_count"] for p in posts]
        weights = _assign_weights(click_counts)
        sentiment = calc_sentiment(posts, labels)
        return {
            "code": code, "posts": posts,
            "labels": labels, "weights": weights, "sentiment": sentiment,
        }
    finally:
        semaphore.release()


def run_market(session, llm_cfg):
    """市场情绪采样：分区间取股票 → 顺序抓帖子 → 并发 LLM 分类 → 入库"""
    start = time.time()
    today = datetime.now().date()

    sampled = _sample_stocks(session)
    if not sampled:
        logger.warning("[市场情绪] 无采样数据，请确认当日已执行人气排名采集")
        return

    logger.info(f"[市场情绪] 采样 {len(sampled)} 只股票，开始抓取...")

    semaphore = threading.Semaphore(MAX_LLM_WORKERS)
    executor = ThreadPoolExecutor(max_workers=MAX_LLM_WORKERS)
    futures = {}
    timespans = {}

    for i, (code, name, tier, rank) in enumerate(sampled):
        logger.info(f"[{code} {name}] 抓取帖子（{i+1}/{len(sampled)}，{tier} rank={rank}）")

        timespans[code] = fetch_page1_timespan(code)
        posts = fetch_guba_posts(code)
        if not posts:
            logger.info(f"[{code}] 无帖子，跳过")
            time.sleep(DELAY)
            continue

        logger.info(f"[{code}] {len(posts)} 条帖子，提交 LLM 分类...")
        semaphore.acquire()
        future = executor.submit(_llm_task, code, posts, llm_cfg, semaphore)
        futures[future] = (code, name, tier, rank)
        time.sleep(DELAY)

    logger.info(f"[市场情绪] 帖子抓取完毕，等待 {len(futures)} 个 LLM 任务完成...")
    results = []
    for future in as_completed(futures):
        code, name, tier, rank = futures[future]
        try:
            result = future.result()
        except Exception as e:
            logger.error(f"[{code}] LLM 任务异常: {e}")
            continue

        sentiment = result["sentiment"]
        if sentiment is None:
            logger.warning(f"[{code}] 情绪计算失败")
            continue

        bull = sentiment["bull_count"]
        bear = sentiment["bear_count"]
        neutral = sentiment["neutral_count"]
        ts = timespans.get(code)
        ts_str = f"{ts:.1f}h" if ts is not None else "N/A"
        logger.info(
            f"[{code} {name}] {tier} 情绪={sentiment['score']:.1f}  "
            f"多={bull} 空={bear} 中={neutral}  "
            f"帖={sentiment['post_count']} 阅读={sentiment['total_read']}  "
            f"首页跨度={ts_str}"
        )

        _save_post_details(
            session, code, today,
            result["posts"], result["labels"], result["weights"],
        )

        results.append({
            "stock_code": code, "date": today,
            "score": sentiment["score"], "post_count": sentiment["post_count"],
            "bull_count": bull, "bear_count": bear, "neutral_count": neutral,
            "total_read": sentiment["total_read"],
            "total_comment": sentiment["total_comment"],
            "page1_timespan": ts, "tier": tier, "popularity_rank": rank,
        })

    executor.shutdown(wait=False)

    if results:
        affected = batch_upsert(
            session, GubaSentiment, results,
            update_cols=["score", "post_count", "bull_count", "bear_count",
                         "neutral_count", "total_read", "total_comment",
                         "page1_timespan", "tier", "popularity_rank"],
        )
        elapsed = time.time() - start
        logger.info(
            f"[市场情绪] 完成：{len(results)}/{len(sampled)} 只，"
            f"affected {affected}，耗时 {elapsed:.1f}s"
        )

        tier_scores = {}
        for r in results:
            t = r["tier"]
            if t not in tier_scores:
                tier_scores[t] = []
            tier_scores[t].append(r["score"])
        for t, scores in tier_scores.items():
            avg = sum(scores) / len(scores)
            logger.info(f"  {t}: 平均情绪={avg:.1f}（{len(scores)} 只）")
    else:
        logger.warning("[市场情绪] 无有效结果")


# ═══════════════════════════════════════════
# 全市场（游标断点续跑）
# ═══════════════════════════════════════════

def _load_all_stocks() -> list[tuple[str, str]]:
    """从 my_stock.la_pick 读取选股池，按 code 排序（固定顺序）"""
    import pymysql
    conn = pymysql.connect(host="localhost", user="root", password="root", db="my_stock")
    cur = conn.cursor()
    cur.execute(
        "SELECT code, name FROM ("
        "  SELECT DISTINCT SUBSTRING_INDEX(ts_code, '.', 1) AS code, stock_name AS name"
        "  FROM la_pick"
        ") t ORDER BY code"
    )
    rows = cur.fetchall()
    conn.close()
    logger.info(f"[la_pick] 读取到 {len(rows)} 只股票")
    return [(r[0], r[1]) for r in rows]


def run_all(session, llm_cfg, force_new: bool = False):
    """全市场股吧情绪：逐只串行（抓帖子+LLM），游标断点续跑。

    游标模式：stock 列表按 code 排序（固定顺序），cursor 记录已处理到第几只。
    中断后从 cursor 位置继续，不存储完成列表。
    """
    all_stocks = _load_all_stocks()
    if not all_stocks:
        logger.warning("[全市场] la_pick 无股票")
        return

    total = len(all_stocks)
    run = _start_or_resume_batch(session, total, task_type="guba", force_new=force_new)

    # 从 cursor 位置开始
    start_idx = run.cursor or 0
    if start_idx >= total:
        logger.info(f"[RUN {run.run_id}] 全部已完成")
        _finish_run(session, run)
        return
    if start_idx > 0:
        logger.info(f"[RUN {run.run_id}] 从第 {start_idx} 只继续，剩余 {total - start_idx} 只")

    start = time.time()
    today = datetime.now().date()
    total_saved = 0

    for i in range(start_idx, total):
        code, name = all_stocks[i]
        logger.info(f"[{code} {name}]（{i+1}/{total}）")

        timespan = fetch_page1_timespan(code)
        posts = fetch_guba_posts(code)

        if posts:
            titles = [p["title"] for p in posts]
            labels = classify_posts(titles, llm_cfg)
            click_counts = [p["click_count"] for p in posts]
            weights = _assign_weights(click_counts)
            sentiment = calc_sentiment(posts, labels)

            if sentiment:
                _save_post_details(session, code, today, posts, labels, weights)

                row = {
                    "stock_code": code, "date": today,
                    "score": sentiment["score"],
                    "post_count": sentiment["post_count"],
                    "bull_count": sentiment["bull_count"],
                    "bear_count": sentiment["bear_count"],
                    "neutral_count": sentiment["neutral_count"],
                    "total_read": sentiment["total_read"],
                    "total_comment": sentiment["total_comment"],
                    "page1_timespan": timespan,
                }
                batch_upsert(
                    session, GubaSentiment, [row],
                    update_cols=["score", "post_count", "bull_count", "bear_count",
                                 "neutral_count", "total_read", "total_comment",
                                 "page1_timespan"],
                )
                total_saved += 1
                logger.info(f"[{code}] 情绪={sentiment['score']:.1f}")
            else:
                _inc_fail(session, run)
        else:
            if (i + 1) % 50 == 0:
                logger.info(f"[全市场] 进度 {i+1}/{total}")

        # 推进游标
        _advance_cursor(session, run, i + 1)
        time.sleep(DELAY)

    _finish_run(session, run)
    elapsed = time.time() - start
    logger.info(
        f"[全市场] 完成：{total_saved}/{total} 只有效，"
        f"耗时 {elapsed:.0f}s（{elapsed/3600:.1f}h）"
    )


def main():
    parser = argparse.ArgumentParser(description="股吧情绪分析")
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--no-resume", action="store_true",
                        help="强制新建 run，不恢复中断的运行（仅 --all）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stock", type=str,
                       help="股票代码，多只用逗号分隔")
    group.add_argument("--market", action="store_true",
                       help="市场情绪采样（从人气排名分区间取40只）")
    group.add_argument("--all", action="store_true",
                       help="全市场股吧情绪（la_pick），支持断点续跑")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()
    session = db.get_session()

    try:
        if args.market:
            logger.info("========== 市场情绪采样开始 ==========")
            run_market(session, cfg.llm)
            logger.info("========== 市场情绪采样完成 ==========")
        elif args.all:
            logger.info("========== 全市场股吧情绪开始 ==========")
            run_all(session, cfg.llm, force_new=args.no_resume)
            logger.info("========== 全市场股吧情绪完成 ==========")
        else:
            codes = [c.strip() for c in args.stock.split(",") if c.strip()]
            logger.info(f"========== 股吧情绪分析开始（{len(codes)} 只）==========")
            run_stock(session, cfg.llm, codes)
            logger.info("========== 股吧情绪分析完成 ==========")
    finally:
        session.close()


if __name__ == "__main__":
    main()
