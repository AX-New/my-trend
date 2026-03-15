"""新闻采集 —— 能力层，无限流

每个函数是单次调用，调度层负责循环和间隔控制。
"""

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

import feedparser
import httpx
import trafilatura

from config import SourceConfig, NetworkConfig, StockInfo

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


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


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _make_client(network: NetworkConfig, browser: bool = False) -> httpx.Client:
    headers = {"User-Agent": _BROWSER_UA if browser else network.user_agent}
    return httpx.Client(
        timeout=network.timeout,
        proxy=network.proxy or None,
        follow_redirects=True,
        headers=headers,
    )


# ── 正文提取 ──

_MIN_CONTENT_LEN = 200


def extract_full_text(url: str, network: NetworkConfig) -> str | None:
    """访问文章 URL，用 trafilatura 提取正文"""
    try:
        with _make_client(network, browser=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = trafilatura.extract(resp.text, include_comments=False)
            if text and len(text) > _MIN_CONTENT_LEN:
                return text[:5000]
    except Exception as e:
        logger.debug(f"[正文提取] {url[:80]} 失败: {e}")
    return None


# ── RSS / JSON API ──

def fetch_rss(source: SourceConfig, network: NetworkConfig) -> list[RawArticle]:
    """从 RSS 源抓取文章列表"""
    try:
        with _make_client(network) as client:
            resp = client.get(source.url)
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"[{source.name}] 请求失败: {e}")
        return []

    feed = feedparser.parse(resp.text)
    articles = []
    for entry in feed.entries:
        pub_time = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                pub_time = datetime(*entry.published_parsed[:6])
            except Exception:
                pass
        content = ""
        if hasattr(entry, "summary"):
            content = entry.summary
        elif hasattr(entry, "content") and entry.content:
            content = entry.content[0].get("value", "")
        content = _strip_html(content)

        articles.append(RawArticle(
            source=source.name,
            category=source.category,
            title=entry.get("title", "").strip(),
            url=entry.get("link", "").strip(),
            content=content[:2000],
            language=source.language,
            published_at=pub_time,
        ))
    logger.info(f"[{source.name}] 抓取到 {len(articles)} 篇")
    return articles


def fetch_sina_api(source: SourceConfig, network: NetworkConfig) -> list[RawArticle]:
    """从新浪财经 JSON API 抓取文章"""
    try:
        with _make_client(network) as client:
            resp = client.get(source.url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"[{source.name}] 请求失败: {e}")
        return []

    articles = []
    items = data.get("result", {}).get("data", [])
    for item in items:
        pub_time = None
        ctime = item.get("ctime", "")
        if ctime:
            try:
                pub_time = datetime.strptime(ctime, "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        articles.append(RawArticle(
            source=source.name,
            category=source.category,
            title=item.get("title", "").strip(),
            url=item.get("url", "").strip(),
            content=item.get("intro", "").strip()[:2000],
            language=source.language,
            published_at=pub_time,
        ))
    logger.info(f"[{source.name}] 抓取到 {len(articles)} 篇")
    return articles


def fetch_source(source: SourceConfig, network: NetworkConfig) -> list[RawArticle]:
    if not source.enabled:
        return []
    if source.type == "json_api":
        return fetch_sina_api(source, network)
    return fetch_rss(source, network)


# ── 个股搜索（单次调用） ──

def fetch_google_news(query: str, network: NetworkConfig,
                      language: str = "zh") -> list[RawArticle]:
    """Google News RSS 搜索（需要 proxy）"""
    if language == "zh":
        params = urllib.parse.urlencode({
            "q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans",
        })
    else:
        params = urllib.parse.urlencode({
            "q": query, "hl": "en", "gl": "US", "ceid": "US:en",
        })
    url = f"https://news.google.com/rss/search?{params}"

    try:
        with _make_client(network) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"[GoogleNews-{query}] 请求失败: {e}")
        return []

    feed = feedparser.parse(resp.text)
    articles = []
    for entry in feed.entries[:15]:
        pub_time = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                pub_time = datetime(*entry.published_parsed[:6])
            except Exception:
                pass
        articles.append(RawArticle(
            source="GoogleNews",
            category="stock",
            title=entry.get("title", "").strip(),
            url=entry.get("link", "").strip(),
            content=_strip_html(entry.get("summary", ""))[:2000],
            language=language,
            published_at=pub_time,
        ))
    logger.info(f"[GoogleNews-{query}] 抓取到 {len(articles)} 篇")
    return articles


def fetch_stock_eastmoney(query: str, network: NetworkConfig) -> list[RawArticle]:
    """东方财富搜索 API (JSONP)"""
    articles = []
    try:
        param = json.dumps({
            "uid": "",
            "keyword": query,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 20,
                }
            },
        }, separators=(",", ":"))
        url = f"https://search-api-web.eastmoney.com/search/jsonp?cb=cb&param={urllib.parse.quote(param)}"

        with _make_client(network, browser=True) as client:
            client.headers["Referer"] = "https://so.eastmoney.com/"
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text.strip()
            json_str = re.sub(r'^cb\(|\);?$', '', text)
            data = json.loads(json_str)

        cms = data.get("result", {}).get("cmsArticleWebOld", [])
        items = cms if isinstance(cms, list) else cms.get("list", [])
        for item in items[:15]:
            title = item.get("title", "")
            art_url = item.get("url", "") or item.get("mediaUrl", "")
            if not title or not art_url:
                continue
            pub_time = None
            date_str = item.get("date", "")
            if date_str:
                try:
                    pub_time = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
            articles.append(RawArticle(
                source="东方财富",
                category="stock",
                title=_strip_html(title),
                url=art_url.strip(),
                content=_strip_html(item.get("content", ""))[:2000],
                language="zh",
                published_at=pub_time,
            ))
    except Exception as e:
        logger.warning(f"[东方财富-{query}] 失败: {e}")

    logger.info(f"[东方财富-{query}] 抓取到 {len(articles)} 篇")
    return articles


def fetch_stock_sina(query: str, network: NetworkConfig) -> list[RawArticle]:
    """新浪财经搜索"""
    source = SourceConfig(
        name="新浪财经",
        category="stock",
        url=f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k={urllib.parse.quote(query)}&num=20&page=1",
        language="zh",
        type="json_api",
    )
    return fetch_sina_api(source, network)


def generate_stock_queries(stock: StockInfo) -> list[str]:
    """股票代码 + 公司名，各搜一次"""
    queries = [stock.code]
    if stock.name:
        queries.append(stock.name)
    return queries


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
