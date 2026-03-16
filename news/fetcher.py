"""新闻采集 —— 能力层

搜索引擎：今日头条（主） + 搜狗新闻（备），curl_cffi 绕反爬。
每个函数是单次调用，调度层负责循环和间隔控制。
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_req

from config import StockInfo

logger = logging.getLogger(__name__)


@dataclass
class RawArticle:
    """抓取到的原始文章"""
    source: str
    category: str
    title: str
    url: str
    content: str
    language: str
    published_at: datetime | None
    stock_code: str = ""
    dup_count: int = 1


# ── 共享 session ──

_session: cffi_req.Session | None = None


def _get_session() -> cffi_req.Session:
    """复用 session：保持 cookie + TLS 指纹一致"""
    global _session
    if _session is None:
        _session = cffi_req.Session(impersonate="chrome120")
    return _session


# ── 今日头条搜索 ──

def _fetch_toutiao(query: str, timeout: int = 15) -> list[RawArticle]:
    """今日头条资讯搜索"""
    try:
        session = _get_session()
        resp = session.get(
            "https://so.toutiao.com/search",
            params={"keyword": query, "pd": "information", "dvpf": "pc"},
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.debug(f"[头条-{query}] 请求失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    for item in soup.select("[data-i]"):
        title_a = item.select_one(".result-content a") or item.select_one("a")
        if not title_a:
            continue
        title = title_a.get_text(strip=True)
        link = title_a.get("href", "").strip()
        if not title or not link:
            continue
        summary = item.get_text(strip=True)[:2000]
        articles.append(RawArticle(
            source="今日头条", category="stock", title=title,
            url=link, content=summary, language="zh",
            published_at=None, stock_code="",
        ))
    return articles


# ── 搜狗新闻搜索（备用） ──

def _fetch_sogou(query: str, timeout: int = 15) -> list[RawArticle]:
    """搜狗新闻搜索"""
    try:
        session = _get_session()
        resp = session.get(
            "https://news.sogou.com/news",
            params={"query": query, "mode": 1},
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.debug(f"[搜狗新闻-{query}] 请求失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    for item in soup.select(".vrwrap"):
        h3 = item.select_one("h3 a")
        if not h3:
            continue
        title = h3.get_text(strip=True)
        link = h3.get("href", "").strip()
        if not title or not link:
            continue
        summary_el = item.select_one(".txt-info, .space-txt, .star-wiki")
        summary = summary_el.get_text(strip=True) if summary_el else ""
        articles.append(RawArticle(
            source="搜狗新闻", category="stock", title=title,
            url=link, content=summary[:2000], language="zh",
            published_at=None, stock_code="",
        ))
    return articles


# ── 对外统一接口 ──

def fetch_news_search(query: str, timeout: int = 15) -> list[RawArticle]:
    """搜索新闻：头条优先，失败时 fallback 搜狗"""
    articles = _fetch_toutiao(query, timeout)
    if articles:
        logger.info(f"[头条-{query}] 抓取到 {len(articles)} 篇")
        return articles

    articles = _fetch_sogou(query, timeout)
    logger.info(f"[搜狗新闻-{query}] 抓取到 {len(articles)} 篇")
    return articles


# ── 个股搜索词生成 ──

def _lookup_stock_name(code: str) -> str | None:
    """从 my_stock.stock_basic 查股票简称"""
    import pymysql
    try:
        conn = pymysql.connect(
            host="localhost", user="root", password="root", db="my_stock",
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM stock_basic WHERE symbol = %s LIMIT 1", (code,)
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.warning(f"[stock_basic] 查询 {code} 失败: {e}")
        return None


def generate_stock_queries(stock: StockInfo) -> list[str]:
    """公司名 + 单关键词"""
    name = stock.name or _lookup_stock_name(stock.code) or stock.code
    return [
        name,               # 综合新闻
        f"{name} 项目",      # 项目动态
        f"{name} 利润",      # 盈利情况
        f"{name} 前景",      # 发展前景
    ]


# ── 去重 ──

def _normalize_title(title: str) -> str:
    title = re.sub(r'\s*[-–—|]\s*\S+$', '', title)
    title = re.sub(r'[^\w]', '', title)
    return title.lower()


def dedup_articles(articles: list[RawArticle], threshold: float = 0.85) -> list[RawArticle]:
    """同日内标题相似度 >threshold 的合并，保留内容最长的"""
    if not articles:
        return []

    today = datetime.now().date()
    day_buckets: dict[str, list[RawArticle]] = {}
    for art in articles:
        day = art.published_at.date() if art.published_at else today
        day_buckets.setdefault(day.isoformat(), []).append(art)

    result = []
    for day_key, day_articles in day_buckets.items():
        groups: list[tuple[RawArticle, str, int]] = []
        seen_urls: set[str] = set()

        for art in day_articles:
            if art.url in seen_urls:
                continue
            seen_urls.add(art.url)

            norm = _normalize_title(art.title)
            if not norm:
                continue

            matched_idx = -1
            for i, (rep, rep_norm, count) in enumerate(groups):
                if SequenceMatcher(None, norm, rep_norm).ratio() > threshold:
                    matched_idx = i
                    break

            if matched_idx >= 0:
                rep, rep_norm, count = groups[matched_idx]
                if len(art.content) > len(rep.content):
                    groups[matched_idx] = (art, norm, count + 1)
                else:
                    groups[matched_idx] = (rep, rep_norm, count + 1)
            else:
                groups.append((art, norm, 1))

        for rep, _, count in groups:
            rep.dup_count = count
            result.append(rep)

    logger.info(f"去重: {len(articles)} → {len(result)} 篇")
    return result
