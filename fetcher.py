"""新闻抓取模块，支持 RSS / JSON API / 搜索聚合"""

import json
import logging
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dataclasses import dataclass
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
    stock_code: str = ""  # 关联股票代码，个股新闻时填写
    dup_count: int = 1  # 相似文章数（含自身），即热度权重


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


# ──────────────────────────────────────────────
#  正文提取：访问文章 URL 抓取全文
# ──────────────────────────────────────────────

# 内容短于此阈值的文章会尝试抓取全文
_MIN_CONTENT_LEN = 200


def _extract_full_text(url: str, network: NetworkConfig) -> str | None:
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


def enrich_content(articles: list['RawArticle'], network: NetworkConfig,
                   max_workers: int = 5) -> None:
    """对内容过短的文章并发抓取全文，原地更新 content"""
    short = [a for a in articles if len(a.content) < _MIN_CONTENT_LEN and a.url]
    if not short:
        return

    logger.info(f"[正文提取] {len(short)}/{len(articles)} 篇内容过短，尝试抓取全文")

    def _fetch_one(art: 'RawArticle'):
        text = _extract_full_text(art.url, network)
        if text:
            art.content = text

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_fetch_one, short))

    enriched = sum(1 for a in short if len(a.content) >= _MIN_CONTENT_LEN)
    logger.info(f"[正文提取] 成功补充 {enriched}/{len(short)} 篇")


# ──────────────────────────────────────────────
#  通用抓取：RSS / 新浪 JSON API
# ──────────────────────────────────────────────

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
    logger.info(f"[{source.name}] 抓取到 {len(articles)} 篇文章")
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
    logger.info(f"[{source.name}] 抓取到 {len(articles)} 篇文章")
    return articles


def fetch_source(source: SourceConfig, network: NetworkConfig) -> list[RawArticle]:
    if not source.enabled:
        return []
    if source.type == "json_api":
        return fetch_sina_api(source, network)
    return fetch_rss(source, network)


# ──────────────────────────────────────────────
#  个股搜索：按股票代码从多个源搜索
# ──────────────────────────────────────────────

def _eastmoney_code(code: str) -> str:
    code = code.strip()
    if code.upper().startswith(("SH", "SZ")):
        return code.upper()
    return f"SH{code}" if code.startswith("6") else f"SZ{code}"


def _fetch_google_news(query: str, network: NetworkConfig, language: str = "zh") -> list[RawArticle]:
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
    for entry in feed.entries[:15]:  # 每个查询最多取15条
        pub_time = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                pub_time = datetime(*entry.published_parsed[:6])
            except Exception:
                pass
        articles.append(RawArticle(
            source=f"GoogleNews",
            category="stock",
            title=entry.get("title", "").strip(),
            url=entry.get("link", "").strip(),
            content=_strip_html(entry.get("summary", ""))[:2000],
            language=language,
            published_at=pub_time,
        ))
    logger.info(f"[GoogleNews-{query}] 抓取到 {len(articles)} 篇")
    return articles


def _fetch_stock_sina(query: str, network: NetworkConfig) -> list[RawArticle]:
    """新浪财经 - JSON API 搜索"""
    source = SourceConfig(
        name=f"新浪财经",
        category="stock",
        url=f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k={urllib.parse.quote(query)}&num=20&page=1",
        language="zh",
        type="json_api",
    )
    return fetch_sina_api(source, network)


def _fetch_stock_eastmoney(query: str, network: NetworkConfig) -> list[RawArticle]:
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


# ──────────────────────────────────────────────
#  去重与聚合
# ──────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """标题归一化：去除标点、空格、来源标注，用于相似度比较"""
    # 去掉 Google News 常见的 " - 来源名" 后缀
    title = re.sub(r'\s*[-–—|]\s*\S+$', '', title)
    # 去标点和空格
    title = re.sub(r'[^\w]', '', title)
    return title.lower()


def dedup_articles(articles: list[RawArticle], threshold: float = 0.85) -> list[RawArticle]:
    """按日期分组，同一天内标题相似的合并，保留内容最长的，记录重复数为热度。跨天不去重。"""
    if not articles:
        return []

    # 按发布日期分桶，无日期的用 today
    today = datetime.now().date()
    day_buckets: dict[str, list[RawArticle]] = {}
    for art in articles:
        day = art.published_at.date() if art.published_at else today
        day_key = day.isoformat()
        day_buckets.setdefault(day_key, []).append(art)

    result = []
    for day_key, day_articles in day_buckets.items():
        # 每天独立去重
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

        merged = sum(g[2] for g in groups) - len(groups)
        if merged:
            logger.info(f"[{day_key}] 去重: {len(day_articles)} → {len(groups)} 篇（合并 {merged}）")

    logger.info(f"去重总计: {len(articles)} → {len(result)} 篇")
    return result


def generate_stock_queries(stock: StockInfo) -> list[str]:
    """股票代码 + 公司名，各搜一次"""
    queries = [stock.code]
    if stock.name:
        queries.append(stock.name)
    return queries


# 每个搜索词要查的源
_QUERY_FETCHERS = [
    ("GoogleNews", _fetch_google_news),
    ("东方财富", _fetch_stock_eastmoney),
]


def fetch_stock_news(
    code: str,
    queries: list[str],
    network: NetworkConfig,
    max_per_stock: int = 20,
) -> list[RawArticle]:
    """对一只股票的所有搜索词，多源抓取 → 去重 → 截断"""
    all_articles = []

    for query in queries:
        for name, fetcher in _QUERY_FETCHERS:
            try:
                all_articles.extend(fetcher(query, network))
            except Exception as e:
                logger.warning(f"[{name}-{query}] 失败: {e}")

        # 新浪只用原始代码搜一次（关键词搜效果差）
        if query == code:
            try:
                all_articles.extend(_fetch_stock_sina(query, network))
            except Exception as e:
                logger.warning(f"[新浪-{query}] 失败: {e}")

    # 标记股票代码
    for art in all_articles:
        art.stock_code = code

    # 去重 + 截断
    deduped = dedup_articles(all_articles)
    if len(deduped) > max_per_stock:
        deduped = deduped[:max_per_stock]
        logger.info(f"[{code}] 截断到 {max_per_stock} 篇")

    logger.info(f"[{code}] 最终 {len(deduped)} 篇（原始 {len(all_articles)}）")
    return deduped


def fetch_stock_news_batch(
    codes: list[str],
    queries_map: dict[str, list[str]],
    network: NetworkConfig,
    max_workers: int = 5,
    max_per_stock: int = 20,
) -> list[RawArticle]:
    """并发搜索多只股票"""
    all_articles = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                fetch_stock_news, code, queries_map.get(code, [code]),
                network, max_per_stock,
            ): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                all_articles.extend(future.result())
            except Exception as e:
                logger.error(f"[{code}] 批量搜索失败: {e}")
    logger.info(f"本批次 {len(codes)} 只股票，共 {len(all_articles)} 篇")
    return all_articles


# ──────────────────────────────────────────────
#  人气排名：东方财富选股 API
# ──────────────────────────────────────────────

_POPULARITY_STY = (
    "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NEW_PRICE,"
    "CHANGE_RATE,VOLUME_RATIO,HIGH_PRICE,LOW_PRICE,PRE_CLOSE_PRICE,"
    "VOLUME,DEAL_AMOUNT,TURNOVERRATE,POPULARITY_RANK"
)

_POPULARITY_FILTERS = [
    "(POPULARITY_RANK>0)(POPULARITY_RANK<=1000)",   # top 1000
    "(POPULARITY_RANK>1000)",                        # 1000 名之后
]


def _fetch_popularity_page(client: httpx.Client, filter_str: str, page: int, page_size: int) -> dict:
    """请求单页人气排名数据"""
    url = (
        "https://data.eastmoney.com/dataapi/xuangu/list"
        f"?st=CHANGE_RATE&sr=-1&ps={page_size}&p={page}"
        f"&sty={_POPULARITY_STY}"
        f"&filter={filter_str}"
        "&source=SELECT_SECURITIES&client=WEB&hyversion=v2"
    )
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_popularity_rank(network: NetworkConfig, page_size: int = 50) -> list[dict]:
    """
    从东方财富选股 API 采集全市场人气排名数据。
    分两段 filter 调用（<=1000 和 >1000），循环分页，合并结果。
    约 110 次请求，覆盖 ~5500 只股票。
    """
    all_items = []

    with _make_client(network, browser=True) as client:
        for filter_str in _POPULARITY_FILTERS:
            page = 1
            while True:
                try:
                    data = _fetch_popularity_page(client, filter_str, page, page_size)
                except Exception as e:
                    logger.warning(f"[人气排名] filter={filter_str} page={page} 失败: {e}")
                    break

                items = data.get("result", {}).get("data", [])
                if not items:
                    break
                all_items.extend(items)
                logger.debug(f"[人气排名] {filter_str} page={page}, 本页 {len(items)} 条")

                if not data.get("result", {}).get("nextpage"):
                    break
                page += 1

    logger.info(f"[人气排名] 共采集 {len(all_items)} 条排名数据")
    return all_items
