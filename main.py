"""my-trend 入口：抓取金融新闻 → LLM 分析 → 存入 MySQL"""

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from config import load_config, AppConfig, StockInfo
from database import Database
from models import (
    Article, PopularityRank,
    EmHotRank, EmHotUp, EmHotRankDetail, EmHotRankRealtime,
    EmHotKeyword, EmHotRankLatest, EmHotRankRelate, XqHotRank,
)
from fetcher import (
    fetch_source, fetch_stock_news_batch, fetch_popularity_rank,
    generate_stock_queries, enrich_content,
)
from akshare_fetcher import (
    fetch_hot_rank_em, fetch_hot_up_em,
    fetch_hot_follow_xq, fetch_hot_tweet_xq, fetch_hot_deal_xq,
    fetch_stock_details,
)
from analyzer import analyze_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("my-trend")


def load_stocks(cfg: AppConfig, cli_stocks: str = None) -> list[StockInfo]:
    """加载股票列表，从 stocks.txt 解析代码、名称、行业"""
    if cli_stocks:
        return [StockInfo(code=c.strip()) for c in cli_stocks.split(",") if c.strip()]

    if cfg.stocks_file:
        path = Path(cfg.stocks_file)
        if not path.is_absolute():
            path = Path(__file__).parent / path
        if path.exists():
            stocks = []
            current_industry = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                # 解析行业分类: # ── 白酒 ──
                m = re.match(r'^#\s*──\s*(.+?)\s*──', line)
                if m:
                    current_industry = m.group(1)
                    continue
                # 解析股票: 600519  # 贵州茅台
                parts = line.split("#", 1)
                code = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else ""
                if code:
                    stocks.append(StockInfo(
                        code=code, name=name, industry=current_industry,
                    ))
            logger.info(f"从 {path} 加载 {len(stocks)} 只股票")
            return stocks
        logger.warning(f"股票文件不存在: {path}")

    return [StockInfo(code=c) for c in (cfg.stocks or [])]


def get_stock_batch(all_stocks: list[StockInfo], per_run: int, run_index: int) -> list[StockInfo]:
    """轮转取当前批次的股票"""
    total = len(all_stocks)
    if per_run >= total:
        return all_stocks
    start = (run_index * per_run) % total
    end = start + per_run
    if end <= total:
        return all_stocks[start:end]
    return all_stocks[start:] + all_stocks[:end - total]


# 全局运行计数器，用于轮转
_run_counter = 0


def _safe_float(val):
    """安全转 float，停牌股票字段可能为 '-' 或 None"""
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


def save_popularity_rank(session, items: list[dict]):
    """批量写入人气排名数据，同日同股票 upsert"""
    today = datetime.now().date()
    saved = 0

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

        fields = {
            "rank": item.get("POPULARITY_RANK", 0),
            "stock_name": item.get("SECURITY_NAME_ABBR", ""),
            "new_price": _safe_float(item.get("NEW_PRICE")),
            "change_rate": _safe_float(item.get("CHANGE_RATE")),
            "volume_ratio": _safe_float(item.get("VOLUME_RATIO")),
            "turnover_rate": _safe_float(item.get("TURNOVERRATE")),
            "volume": _safe_int(item.get("VOLUME")),
            "deal_amount": _safe_float(item.get("DEAL_AMOUNT")),
        }

        existing = session.query(PopularityRank).filter_by(
            stock_code=code, date=rank_date,
        ).first()

        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
        else:
            record = PopularityRank(stock_code=code, date=rank_date, **fields)
            session.add(record)
            saved += 1

    try:
        session.commit()
        logger.info(f"[人气排名] 写入 {saved} 条新记录，更新 {len(items) - saved} 条")
    except Exception as e:
        session.rollback()
        logger.error(f"[人气排名] 写入失败: {e}")


def run_akshare_pipeline(db: Database, cfg: AppConfig, all_stocks: list[StockInfo]):
    """AkShare 热度数据采集与存储（10 个接口）"""
    if not cfg.akshare.enabled:
        return

    import json
    import os

    # AkShare 用 requests，会读系统代理（Windows 注册表），国内接口不需要
    import requests as _req
    _orig_init = _req.Session.__init__

    def _no_proxy_init(self, *a, **kw):
        _orig_init(self, *a, **kw)
        self.trust_env = False

    _req.Session.__init__ = _no_proxy_init

    session = db.get_session()
    today = datetime.now().date()

    try:
        # ── 批量接口 ──

        # 1. 东方财富人气榜 Top100
        rank_items = fetch_hot_rank_em()
        if rank_items:
            session.query(EmHotRank).filter_by(date=today).delete()
            session.add_all([EmHotRank(date=today, **item) for item in rank_items])
            session.commit()
            logger.info(f"[AK人气榜] 存入 {len(rank_items)} 条")

        # 2. 东方财富飙升榜 Top100
        up_items = fetch_hot_up_em()
        if up_items:
            session.query(EmHotUp).filter_by(date=today).delete()
            session.add_all([EmHotUp(date=today, **item) for item in up_items])
            session.commit()
            logger.info(f"[AK飙升榜] 存入 {len(up_items)} 条")

        # 3-5. 雪球三榜（关注/讨论/交易）
        for type_name, fetch_func in [
            ("follow", fetch_hot_follow_xq),
            ("tweet", fetch_hot_tweet_xq),
            ("deal", fetch_hot_deal_xq),
        ]:
            xq_items = fetch_func()
            if xq_items:
                session.query(XqHotRank).filter(
                    XqHotRank.date == today, XqHotRank.type == type_name,
                ).delete()
                session.add_all([XqHotRank(date=today, **item) for item in xq_items])
                session.commit()
                logger.info(f"[雪球{type_name}] 存入 {len(xq_items)} 条")

        # ── 单股接口（6-10）──
        detail_codes = _get_detail_codes(cfg, rank_items, all_stocks)
        if detail_codes:
            logger.info(f"[AK单股] 开始采集 {len(detail_codes)} 只股票的详情")
            details = fetch_stock_details(detail_codes)
            _save_akshare_details(session, details, today)

    except Exception as e:
        session.rollback()
        logger.error(f"[AkShare] 采集失败: {e}")
    finally:
        session.close()
        # 恢复 requests 原始行为
        _req.Session.__init__ = _orig_init


def _get_detail_codes(cfg: AppConfig, rank_items: list[dict],
                      all_stocks: list[StockInfo]) -> list[str]:
    """确定单股接口要采集的股票列表"""
    top_n = cfg.akshare.detail_top_n
    if cfg.akshare.detail_source == "rank_top" and rank_items:
        return [item["stock_code"] for item in rank_items[:top_n]]
    # rank_top 但人气榜为空时，fallback 到 stocks_file
    if all_stocks:
        return [s.code for s in all_stocks[:top_n]]
    return []


def _save_akshare_details(session, details: dict, today):
    """保存单股热度详情数据"""
    import json

    # 6. 历史趋势+粉丝
    saved = _save_detail_batch(
        session, EmHotRankDetail, details["detail"],
        key_fields=["stock_code", "timestamp"],
    )
    if saved:
        logger.info(f"[AK历史趋势] 新增 {saved} 条")

    # 7. 实时排名变动
    saved = _save_detail_batch(
        session, EmHotRankRealtime, details["realtime"],
        key_fields=["stock_code", "timestamp"],
    )
    if saved:
        logger.info(f"[AK实时排名] 新增 {saved} 条")

    # 8. 热门关键词
    saved = _save_detail_batch(
        session, EmHotKeyword, details["keyword"],
        key_fields=["stock_code", "timestamp", "concept_code"],
    )
    if saved:
        logger.info(f"[AK热门关键词] 新增 {saved} 条")

    # 9. 最新排名详情（upsert）
    saved = 0
    for item in details["latest"]:
        code = item["stock_code"]
        data_json = json.dumps(item["data"], ensure_ascii=False)
        existing = session.query(EmHotRankLatest).filter_by(
            stock_code=code, date=today,
        ).first()
        if existing:
            existing.data_json = data_json
        else:
            session.add(EmHotRankLatest(
                stock_code=code, date=today, data_json=data_json,
            ))
            saved += 1
    session.commit()
    if saved:
        logger.info(f"[AK最新排名] 新增 {saved} 条")

    # 10. 相关股票
    saved = _save_detail_batch(
        session, EmHotRankRelate, details["relate"],
        key_fields=["stock_code", "timestamp", "related_code"],
    )
    if saved:
        logger.info(f"[AK相关股票] 新增 {saved} 条")


def _save_detail_batch(session, model, items: list[dict],
                       key_fields: list[str]) -> int:
    """批量保存详情数据，通过预加载已有 key 去重"""
    if not items:
        return 0

    # 预查已有记录的 key 集合
    codes = list(set(item["stock_code"] for item in items))
    key_columns = [getattr(model, f) for f in key_fields]
    existing_keys = set(
        session.query(*key_columns)
        .filter(model.stock_code.in_(codes))
        .all()
    )

    saved = 0
    for item in items:
        key = tuple(item.get(f) for f in key_fields)
        if key not in existing_keys:
            session.add(model(**item))
            saved += 1

    if saved:
        session.commit()
    return saved


def cleanup_old_articles(session, days: int = 7):
    """清理超过指定天数的文章"""
    cutoff = datetime.now() - timedelta(days=days)
    count = session.query(Article).filter(Article.created_at < cutoff).delete()
    session.commit()
    if count:
        logger.info(f"[清理] 删除 {count} 条超过 {days} 天的文章")


def run_pipeline(cfg: AppConfig, db: Database, all_stocks: list[StockInfo], no_analyze: bool = False):
    """执行一次完整的抓取-分析-存储流程"""
    global _run_counter
    logger.info("========== 开始执行采集任务 ==========")
    session = db.get_session()
    new_count = 0
    analyzed_count = 0

    # 0. 清理过期文章
    cleanup_old_articles(session)

    # 1. 采集常规数据源
    all_articles = []
    for source in cfg.sources:
        all_articles.extend(fetch_source(source, cfg.network))

    # 1.5 采集人气排名（独立 session，避免与后续操作冲突）
    if cfg.popularity.enabled:
        rank_session = db.get_session()
        try:
            rank_items = fetch_popularity_rank(
                cfg.network,
                page_size=cfg.popularity.page_size,
            )
            if rank_items:
                save_popularity_rank(rank_session, rank_items)
        except Exception as e:
            logger.error(f"[人气排名] 采集失败: {e}")
        finally:
            rank_session.close()

    # 1.6 AkShare 热度数据（10 个接口）
    run_akshare_pipeline(db, cfg, all_stocks)

    # 2. 采集个股新闻（轮转分批 + 确定性关键词 + 并发）
    if all_stocks:
        batch = get_stock_batch(
            all_stocks, cfg.scheduler.stocks_per_run, _run_counter,
        )
        _run_counter += 1
        codes_display = [s.code for s in batch]
        logger.info(
            f"本轮处理第 {_run_counter} 批股票 ({len(batch)}/{len(all_stocks)}): "
            f"{', '.join(codes_display[:5])}{'...' if len(batch) > 5 else ''}"
        )

        queries_map = {}
        for stock in batch:
            queries_map[stock.code] = generate_stock_queries(stock)

        stock_articles = fetch_stock_news_batch(
            [s.code for s in batch], queries_map, cfg.network,
            max_workers=cfg.scheduler.max_workers,
        )
        all_articles.extend(stock_articles)

    # 2.5 正文提取：对内容过短的文章抓取全文
    enrich_content(all_articles, cfg.network, max_workers=cfg.scheduler.max_workers)

    # 3. 逐条处理，每条独立提交
    for raw in all_articles:
        if not raw.url:
            continue

        try:
            exists = session.query(Article.id).filter_by(url=raw.url).first()
            if exists:
                continue

            article = Article(
                source=raw.source,
                category=raw.category,
                stock_code=raw.stock_code or None,
                title=raw.title,
                url=raw.url,
                content=raw.content,
                language=raw.language,
                published_at=raw.published_at,
            )

            if not no_analyze and (raw.title or raw.content):
                result = analyze_article(
                    title=raw.title,
                    content=raw.content,
                    llm_config=cfg.llm,
                    network_timeout=cfg.network.timeout,
                    proxy=cfg.network.proxy,
                )
                if result:
                    article.summary = result["summary"]
                    article.sentiment = result["sentiment"]
                    article.keywords = result["keywords"]
                    article.analyzed_at = datetime.now()
                    analyzed_count += 1

            session.add(article)
            session.commit()
            new_count += 1

            if new_count >= cfg.scheduler.max_articles_per_run:
                logger.info(f"达到单次上限 {cfg.scheduler.max_articles_per_run}，停止采集")
                break

        except Exception as e:
            session.rollback()
            logger.warning(f"处理文章失败: {raw.url} - {e}")
            continue

    session.close()
    logger.info(
        f"========== 任务完成：新增 {new_count} 篇，"
        f"分析 {analyzed_count} 篇 =========="
    )


def main():
    parser = argparse.ArgumentParser(description="my-trend 金融舆情采集分析")
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument(
        "--category",
        choices=["news", "finance", "forum", "stock"],
        default=None,
        help="只采集指定板块",
    )
    parser.add_argument("--stocks", type=str, default=None, help="股票代码，逗号分隔")
    parser.add_argument(
        "--all-stocks", action="store_true",
        help="单次模式下处理全部股票（不分批）",
    )
    parser.add_argument(
        "--no-analyze", action="store_true",
        help="跳过 LLM 分析，只抓取入库",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.category:
        cfg.sources = [s for s in cfg.sources if s.category == args.category]
        logger.info(f"筛选板块: {args.category}，共 {len(cfg.sources)} 个数据源")

    all_stocks = load_stocks(cfg, args.stocks)

    # --once --all-stocks: 一次跑完所有股票
    if args.once and args.all_stocks and all_stocks:
        cfg.scheduler.stocks_per_run = len(all_stocks)

    if all_stocks:
        total = len(all_stocks)
        per_run = cfg.scheduler.stocks_per_run
        rounds = (total + per_run - 1) // per_run
        logger.info(
            f"共 {total} 只股票，每轮 {per_run} 只，"
            f"需 {rounds} 轮完成全部覆盖"
        )

    db = Database(cfg.database)
    logger.info("初始化数据库...")
    db.init_tables()

    if args.once:
        run_pipeline(cfg, db, all_stocks, no_analyze=args.no_analyze)
    else:
        scheduler = BlockingScheduler()
        scheduler.add_job(
            run_pipeline,
            "interval",
            args=[cfg, db, all_stocks, args.no_analyze],
            minutes=cfg.scheduler.interval_minutes,
            next_run_time=datetime.now(),
        )
        logger.info(
            f"定时调度已启动，每 {cfg.scheduler.interval_minutes} 分钟执行一次，"
            f"Ctrl+C 退出"
        )
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("调度器已停止")


if __name__ == "__main__":
    main()
