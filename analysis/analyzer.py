"""LLM 分析 —— 能力层

三类分析：国际形势、国内形势、个股基本面。
"""

import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, date

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_req
from openai import OpenAI

from config import LLMConfig

logger = logging.getLogger(__name__)

DELAY_MIN = 3
DELAY_MAX = 8

# ── 日期解析 ──


def _parse_date_str(date_str: str) -> datetime | None:
    """解析中文相对/绝对日期字符串为 datetime"""
    if not date_str:
        return None
    now = datetime.now()
    # X分钟前
    m = re.search(r'(\d+)\s*分钟前', date_str)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    # X小时前
    m = re.search(r'(\d+)\s*小时前', date_str)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    # X天前
    m = re.search(r'(\d+)\s*天前', date_str)
    if m:
        return now - timedelta(days=int(m.group(1)))
    if '刚刚' in date_str:
        return now
    if '昨天' in date_str:
        return now - timedelta(days=1)
    if '前天' in date_str:
        return now - timedelta(days=2)
    # YYYY-MM-DD or YYYY/MM/DD
    m = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', date_str)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # YYYY年MM月DD日
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_str)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # MM-DD or MM月DD日 (assume current year)
    m = re.search(r'(\d{1,2})月(\d{1,2})日', date_str)
    if m:
        try:
            return datetime(now.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


# ── 搜索引擎：头条（主）+ 百度新闻（备） ──

_session: cffi_req.Session | None = None


def _get_session() -> cffi_req.Session:
    global _session
    if _session is None:
        _session = cffi_req.Session(impersonate="chrome120")
        try:
            _session.get("https://www.baidu.com/", timeout=10)
        except Exception:
            pass
    return _session


def _search_toutiao(query: str, timeout: int = 15) -> list[dict]:
    """头条搜索，返回 [{"title": ..., "summary": ..., "date_str": ..., "date": ...}, ...]"""
    try:
        session = _get_session()
        resp = session.get(
            "https://so.toutiao.com/search",
            params={"keyword": query, "pd": "information", "dvpf": "pc"},
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"[头条-{query}] 失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select("[data-i]"):
        a = item.select_one(".result-content a") or item.select_one("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        text = item.get_text(strip=True)
        summary = text[len(title):].strip() if title and text.startswith(title) else text

        # 提取时间信息
        date_str = ""
        time_el = item.select_one("span[class*='time'], span[class*='date'], .text-time")
        if time_el:
            date_str = time_el.get_text(strip=True)
        if not date_str:
            # 从全文提取时间模式
            for pattern in [r'\d+分钟前', r'\d+小时前', r'\d+天前', r'昨天', r'前天',
                            r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', r'\d{1,2}月\d{1,2}日']:
                m = re.search(pattern, text)
                if m:
                    date_str = m.group(0)
                    break

        results.append({
            "title": title,
            "summary": summary[:200],
            "date_str": date_str,
            "date": _parse_date_str(date_str),
        })
    return results


def _search_baidu(query: str, timeout: int = 15) -> list[dict]:
    """百度新闻搜索，返回 [{"title": ..., "summary": ..., "date_str": ..., "date": ...}, ...]"""
    try:
        session = _get_session()
        resp = session.get(
            "https://www.baidu.com/s",
            params={"wd": query, "tn": "news", "rtt": 1},
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"[百度-{query}] 失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select(".result-op, .result"):
        title_el = item.select_one("h3 a") or item.select_one(".news-title a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title:
            continue
        summary_el = item.select_one(".c-summary, .c-abstract, .c-span-last")
        summary = summary_el.get_text(strip=True)[:200] if summary_el else ""

        # 提取时间信息
        date_str = ""
        time_el = item.select_one(".c-color-gray2, .c-color-gray, .news-source span:last-child")
        if time_el:
            date_str = time_el.get_text(strip=True)
        if not date_str:
            text = item.get_text(strip=True)
            for pattern in [r'\d+分钟前', r'\d+小时前', r'\d+天前', r'昨天', r'前天',
                            r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', r'\d{1,2}月\d{1,2}日']:
                m = re.search(pattern, text)
                if m:
                    date_str = m.group(0)
                    break

        results.append({
            "title": title,
            "summary": summary,
            "date_str": date_str,
            "date": _parse_date_str(date_str),
        })
    return results


def _search(query: str, timeout: int = 15) -> list[dict]:
    """搜索新闻：头条优先，返回0条时 fallback 百度"""
    results = _search_toutiao(query, timeout)
    if results:
        logger.info(f"[头条-{query}] {len(results)} 条")
        return results

    results = _search_baidu(query, timeout)
    logger.info(f"[百度-{query}] {len(results)} 条")
    return results


def _dedup_news(items: list[dict]) -> list[dict]:
    """按标题去重"""
    seen = set()
    unique = []
    for item in items:
        if item["title"] not in seen:
            seen.add(item["title"])
            unique.append(item)
    return unique


def _sort_by_date(items: list[dict]) -> list[dict]:
    """按日期排序（最新在前），无日期的排最后"""
    epoch = datetime(2000, 1, 1)
    return sorted(items, key=lambda x: x.get("date") or epoch, reverse=True)


def _format_news(items: list[dict], max_count: int = 30) -> str:
    """格式化新闻列表为编号文本，包含日期信息"""
    items = items[:max_count]
    lines = []
    for i, item in enumerate(items):
        date_tag = f"[{item['date_str']}] " if item.get("date_str") else ""
        lines.append(f"{i+1}. {date_tag}{item['title']}\n{item['summary']}")
    return "\n\n".join(lines)


def _collect_news(queries: list[str], max_count: int = 30) -> str:
    """多关键词搜索 → 去重 → 按时间排序 → 拼接文本（随机 3-8s 间隔）"""
    all_news = []
    for q in queries:
        all_news.extend(_search(q))
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    unique = _dedup_news(all_news)
    unique = _sort_by_date(unique)
    logger.info(f"去重后 {len(unique)} 条，取前 {min(len(unique), max_count)} 条")
    return _format_news(unique, max_count)



def _call_llm(prompt: str, llm: LLMConfig, max_retries: int = 3) -> dict | None:
    """调用 LLM，返回解析后的 JSON dict，失败自动重试"""
    client = OpenAI(base_url=llm.base_url, api_key=llm.api_key, timeout=120)
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=llm.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=llm.max_tokens,
                temperature=llm.temperature,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
        except Exception as e:
            logger.warning(f"LLM 调用失败 (第{attempt}次): {e}")
            if attempt < max_retries:
                time.sleep(5 * attempt)
    logger.error(f"LLM 调用 {max_retries} 次均失败，放弃")
    return None


# ── 国际形势分析 ──

GLOBAL_QUERIES = ["全球经济", "美联储", "国际局势", "原油 黄金"]

GLOBAL_PROMPT = """你是一位专业的宏观经济研究员。请根据以下最新全球资讯，分析当前国际形势对A股市场的影响。

## 资讯列表
{articles}

## 分析要求

请从以下维度分析，每个维度 1-2 句关键结论：

1. **地缘政治**：当前国际局势（战争、冲突、外交）对市场的影响
2. **货币政策**：美联储/主要央行政策动向，对流动性和汇率的影响
3. **大宗商品**：原油、黄金等价格走势及对通胀的影响
4. **外围市场**：美股、欧股、亚太市场走势对A股的传导

## 输出格式

请严格按以下 JSON 格式输出，不要输出其他内容：

{{"geopolitics": "地缘政治结论", "monetary": "货币政策结论", "commodity": "大宗商品结论", "overseas": "外围市场结论", "sentiment": "利好|中性|利空", "summary": "一段话总结当前国际形势对A股的整体影响（100字以内）", "score": 50}}

score 为百分制评分（0-100），50分为中性：
- 75-100：明显利好A股
- 50-75：偏正面
- 50：中性
- 25-50：偏负面
- 0-25：明显利空A股"""


def analyze_global(llm: LLMConfig) -> dict | None:
    """国际形势分析"""
    articles = _collect_news(GLOBAL_QUERIES)
    if not articles:
        return None
    prompt = GLOBAL_PROMPT.format(articles=articles)
    return _call_llm(prompt, llm)


# ── 国内形势分析 ──

DOMESTIC_QUERIES = ["A股 政策", "中国经济", "央行 货币", "产业政策 A股"]

DOMESTIC_PROMPT = """你是一位专业的宏观经济研究员。请根据以下最新国内资讯，分析当前国内形势对A股市场的影响。

## 资讯列表
{articles}

## 分析要求

请从以下维度分析，每个维度 1-2 句关键结论：

1. **宏观经济**：GDP、PMI、就业等经济数据表现，经济复苏情况
2. **货币财政**：央行货币政策、财政政策、流动性状况
3. **产业政策**：重点产业扶持、监管变化、改革动向
4. **市场资金**：北向资金、公募基金、两融等资金面状况

## 输出格式

请严格按以下 JSON 格式输出，不要输出其他内容：

{{"macro": "宏观经济结论", "monetary": "货币财政结论", "industry": "产业政策结论", "capital": "市场资金结论", "sentiment": "利好|中性|利空", "summary": "一段话总结当前国内形势对A股的整体影响（100字以内）", "score": 50}}

score 为百分制评分（0-100），50分为中性：
- 75-100：明显利好A股
- 50-75：偏正面
- 50：中性
- 25-50：偏负面
- 0-25：明显利空A股"""


def analyze_domestic(llm: LLMConfig) -> dict | None:
    """国内形势分析"""
    articles = _collect_news(DOMESTIC_QUERIES)
    if not articles:
        return None
    prompt = DOMESTIC_PROMPT.format(articles=articles)
    return _call_llm(prompt, llm)


# ── 个股基本面分析 ──

STOCK_PROMPT = """你是一位专业的股票基本面研究员。根据以下【{name}（{code}）】的新闻，完成分析。今日：{today}。

## 新闻列表（按时间倒序，带 [时间] 标记）
{articles}

## 分析任务

### 1. 近期概况（recent）
提取**近一个月**的重要事件，概括最新动态和短期催化因素。新闻不足时可放宽到三个月并注明。

### 2. 基本面分析
- **经营动态（operating）**：业务趋势、重大项目、合作
- **财务状况（financial）**：营收利润增速、现金流，引用具体数字和同比
- **行业地位（position）**：竞争力、市占率、核心壁垒
- **风险提示（risk）**：负面消息、经营/政策/治理风险

### 3. 关键事件（key_events，最多3条）
- 优先近一个月，同类只保留最新（如多份财报只取最新一期）
- 不同事件覆盖不同维度，含具体数据
- date 从新闻提取，不确定填 "unknown"；不够3条可少填

### 4. 维度打分（scores）
对5个维度打分（0-100，50=中性），各附一句理由（30字内）：
- **growth**（经营成长，权重25%）：增长趋势、新项目订单
- **profitability**（盈利质量，权重25%）：利润增速、毛利率、现金流
- **competitive**（行业竞争，权重20%）：行业地位、护城河
- **risk_level**（安全程度，权重15%）：越高=越安全，有退市/诉讼/巨亏风险应低于30
- **catalyst**（近期催化，权重15%）：近一个月有无利好/利空催化

**score** = growth×0.25 + profitability×0.25 + competitive×0.20 + risk_level×0.15 + catalyst×0.15

## JSON 输出（严格遵守，不要输出其他内容）

{{"recent": "近期概况100字", "operating": "经营动态", "financial": "财务状况", "position": "行业地位", "risk": "风险提示", "sentiment": "乐观|中性|悲观", "summary": "综合评价含关键数据200字", "scores": {{"growth": {{"score": 50, "reason": "理由"}}, "profitability": {{"score": 50, "reason": "理由"}}, "competitive": {{"score": 50, "reason": "理由"}}, "risk_level": {{"score": 50, "reason": "理由"}}, "catalyst": {{"score": 50, "reason": "理由"}}}}, "score": 50, "key_events": [{{"event": "事件描述含数据200字内", "sentiment": "乐观|悲观|中性", "impact": "影响", "date": "YYYY-MM-DD"}}]}}"""


def analyze_stock(code: str, name: str, llm: LLMConfig) -> tuple[dict | None, int]:
    """个股基本面分析，返回 (result, article_count)"""
    queries = [name, f"{name} 项目", f"{name} 利润", f"{name} 行业前景", f"{name} 产品"]
    articles = _collect_news(queries, max_count=30)
    if not articles:
        return None, 0
    count = len(articles.strip().split("\n\n"))
    prompt = STOCK_PROMPT.format(code=code, name=name, articles=articles, today=date.today())
    return _call_llm(prompt, llm), count
