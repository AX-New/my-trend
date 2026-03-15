"""新闻采集调度层 —— 1s 间隔限流

用法：
  python -m news.main                           # 个股资讯（默认）
  python -m news.main --stocks 600519           # 指定股票
  python -m news.main --category news           # 指定板块
  python -m news.main --all-stocks              # 全部股票（不分批）
  python -m news.main --global                  # 24小时全球资讯
"""

import argparse
import hashlib
import logging
import time
from datetime import datetime, timedelta

from config import load_config, load_stocks, AppConfig, StockInfo
from database import Database, batch_insert_ignore
from news.models import Article, GlobalNews
from news.fetcher import (
    fetch_source, fetch_google_news, fetch_stock_eastmoney,
    fetch_stock_sina, extract_full_text,
    generate_stock_queries, dedup_articles, RawArticle,
    fetch_global_news_em,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("news")

DELAY = 1  # 调度层限流间隔（秒）


def cleanup_old_articles(session, days: int = 7):
    """清理过期文章"""
    cutoff = datetime.now() - timedelta(days=days)
    count = session.query(Article).filter(Article.created_at < cutoff).delete()
    if count:
        session.commit()
        logger.info(f"[清理] 删除 {count} 条超过 {days} 天的文章")


def _fetch_stock_news(code: str, queries: list[str], network) -> list[RawArticle]:
    """对一只股票的所有搜索词，多源抓取（每次调用间隔 1s）"""
    all_articles = []

    for query in queries:
        all_articles.extend(fetch_google_news(query, network))
        time.sleep(DELAY)
        all_articles.extend(fetch_stock_eastmoney(query, network))
        time.sleep(DELAY)
        if query == code:
            all_articles.extend(fetch_stock_sina(query, network))
            time.sleep(DELAY)

    for art in all_articles:
        art.stock_code = code

    deduped = dedup_articles(all_articles)
    if len(deduped) > 20:
        deduped = deduped[:20]
    logger.info(f"[{code}] 最终 {len(deduped)} 篇（原始 {len(all_articles)}）")
    return deduped


def _enrich_content(articles: list[RawArticle], network, max_count: int = 50):
    """对内容过短的文章抓取全文，每次间隔 1s"""
    short = [a for a in articles if len(a.content) < 200 and a.url][:max_count]
    if not short:
        return
    logger.info(f"[正文提取] {len(short)} 篇内容过短，尝试抓取全文")
    enriched = 0
    for art in short:
        text = extract_full_text(art.url, network)
        if text:
            art.content = text
            enriched += 1
        time.sleep(DELAY)
    logger.info(f"[正文提取] 成功补充 {enriched}/{len(short)} 篇")


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

    # 0. 清理过期文章
    cleanup_old_articles(session)

    # 1. 采集常规数据源（RSS/API，每源间隔 1s）
    all_articles: list[RawArticle] = []
    for source in cfg.sources:
        all_articles.extend(fetch_source(source, cfg.network))
        time.sleep(DELAY)

    # 2. 采集个股新闻（轮转分批，每只间隔 1s）
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
            all_articles.extend(_fetch_stock_news(stock.code, queries, cfg.network))

    # 3. 正文提取（内容过短的，每篇间隔 1s）
    _enrich_content(all_articles, cfg.network)

    # 4. 批量写入（INSERT IGNORE，url_hash 去重）
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


def run_global(db: Database):
    """采集24小时全球资讯（东方财富 stock_info_global_em）"""
    logger.info("========== 24小时资讯采集开始 ==========")
    session = db.get_session()

    rows = fetch_global_news_em()
    if rows:
        new_count = batch_insert_ignore(session, GlobalNews, rows)
        logger.info(f"========== 24小时资讯完成：采集 {len(rows)} 条，新增 {new_count} 条 ==========")
    else:
        logger.warning("========== 24小时资讯：未采集到数据 ==========")

    session.close()


def main():
    parser = argparse.ArgumentParser(description="新闻采集")
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--category", choices=["news", "finance", "forum", "stock"])
    parser.add_argument("--stocks", type=str, default=None)
    parser.add_argument("--all-stocks", action="store_true")
    parser.add_argument("--global", dest="global_news", action="store_true",
                        help="24小时全球资讯")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg.database)
    db.init_tables()

    if args.global_news:
        run_global(db)
    else:
        if args.category:
            cfg.sources = [s for s in cfg.sources if s.category == args.category]
            logger.info(f"筛选板块: {args.category}，共 {len(cfg.sources)} 个数据源")

        all_stocks = load_stocks(cfg, args.stocks)
        if args.all_stocks and all_stocks:
            cfg.scheduler.stocks_per_run = len(all_stocks)

        run_pipeline(cfg, db, all_stocks)


if __name__ == "__main__":
    main()
