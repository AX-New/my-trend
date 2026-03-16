"""新闻采集调度层 —— 1s 间隔限流

用法：
  python -m news.main --stocks 600519           # 指定股票
  python -m news.main --all-stocks              # 全部股票（不分批）
"""

import argparse
import hashlib
import logging
import time
from datetime import datetime, timedelta

from config import load_config, load_stocks, AppConfig, StockInfo
from database import Database, batch_insert_ignore
from news.models import Article
from news.fetcher import (
    fetch_news_search, generate_stock_queries,
    dedup_articles, RawArticle,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("news")

DELAY = 1


def cleanup_old_articles(session, days: int = 7):
    """清理过期文章"""
    cutoff = datetime.now() - timedelta(days=days)
    count = session.query(Article).filter(Article.created_at < cutoff).delete()
    if count:
        session.commit()
        logger.info(f"[清理] 删除 {count} 条超过 {days} 天的文章")


def _fetch_stock_news(code: str, queries: list[str]) -> list[RawArticle]:
    """对一只股票的所有搜索词搜索新闻（每次间隔 1s）"""
    all_articles = []
    for query in queries:
        all_articles.extend(fetch_news_search(query))
        time.sleep(DELAY)

    for art in all_articles:
        art.stock_code = code

    deduped = dedup_articles(all_articles)
    if len(deduped) > 20:
        deduped = deduped[:20]
    logger.info(f"[{code}] 最终 {len(deduped)} 篇（原始 {len(all_articles)}）")
    return deduped


def get_stock_batch(all_stocks: list[StockInfo], per_run: int,
                    run_index: int) -> list[StockInfo]:
    """轮转取当前批次的股票"""
    total = len(all_stocks)
    if per_run >= total:
        return all_stocks
    start = (run_index * per_run) % total
    end = start + per_run
    if end <= total:
        return all_stocks[start:end]
    return all_stocks[start:] + all_stocks[:end - total]


_run_counter = 0


def run_pipeline(cfg: AppConfig, db: Database, all_stocks: list[StockInfo]):
    """单次采集流程"""
    global _run_counter
    logger.info("========== 新闻采集开始 ==========")
    session = db.get_session()

    cleanup_old_articles(session)

    all_articles: list[RawArticle] = []
    if all_stocks:
        batch = get_stock_batch(
            all_stocks, cfg.scheduler.stocks_per_run, _run_counter,
        )
        _run_counter += 1
        logger.info(
            f"本轮第 {_run_counter} 批 ({len(batch)}/{len(all_stocks)}): "
            f"{', '.join(s.code for s in batch[:5])}{'...' if len(batch) > 5 else ''}"
        )
        for stock in batch:
            queries = generate_stock_queries(stock)
            all_articles.extend(_fetch_stock_news(stock.code, queries))

    article_rows = []
    for raw in all_articles:
        if not raw.url:
            continue
        article_rows.append({
            "source": raw.source,
            "category": raw.category,
            "stock_code": raw.stock_code or None,
            "title": raw.title,
            "url": raw.url,
            "url_hash": hashlib.sha256(raw.url.encode()).hexdigest(),
            "content": raw.content,
            "language": raw.language,
            "published_at": raw.published_at,
        })
        if len(article_rows) >= cfg.scheduler.max_articles_per_run:
            break

    new_count = batch_insert_ignore(session, Article, article_rows)
    session.close()
    logger.info(f"========== 新闻采集完成：新增 {new_count} 篇 ==========")


def main():
    parser = argparse.ArgumentParser(description="新闻采集")
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--stocks", type=str, default=None)
    parser.add_argument("--all-stocks", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()

    all_stocks = load_stocks(cfg, args.stocks)
    if args.all_stocks and all_stocks:
        cfg.scheduler.stocks_per_run = len(all_stocks)

    run_pipeline(cfg, db, all_stocks)


if __name__ == "__main__":
    main()
