"""行业分析 —— 能力层

逐个分析申万一级行业（31个），每个行业：搜索新闻 → 构造prompt → 调用LLM。
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
from clash_proxy import rotate_proxy, restore_auto

logger = logging.getLogger(__name__)

DELAY_MIN = 5
DELAY_MAX = 10


# ── 搜索引擎：今日头条 ──

CLASH_PROXY = "http://127.0.0.1:7890"

_session: cffi_req.Session | None = None

MAX_EMPTY_RETRIES = 3


def _get_session() -> cffi_req.Session:
    global _session
    if _session is None:
        _session = cffi_req.Session(impersonate="chrome120", proxy=CLASH_PROXY)
    return _session


def _reset_session():
    """换IP后重建session"""
    global _session
    if _session is not None:
        _session.close()
        _session = None


def _parse_date_str(date_str: str) -> datetime | None:
    """解析中文相对/绝对日期字符串为 datetime"""
    if not date_str:
        return None
    now = datetime.now()
    m = re.search(r'(\d+)\s*分钟前', date_str)
    if m:
        return now - timedelta(minutes=min(int(m.group(1)), 5256000))
    m = re.search(r'(\d+)\s*小时前', date_str)
    if m:
        return now - timedelta(hours=min(int(m.group(1)), 87600))
    m = re.search(r'(\d+)\s*天前', date_str)
    if m:
        return now - timedelta(days=min(int(m.group(1)), 3650))
    if '刚刚' in date_str:
        return now
    if '昨天' in date_str:
        return now - timedelta(days=1)
    if '前天' in date_str:
        return now - timedelta(days=2)
    m = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', date_str)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_str)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r'(\d{1,2})月(\d{1,2})日', date_str)
    if m:
        try:
            return datetime(now.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def _search_toutiao(query: str, timeout: int = 15) -> list[dict]:
    """头条搜索"""
    session = _get_session()
    resp = session.get(
        "https://so.toutiao.com/search",
        params={"keyword": query, "pd": "information", "dvpf": "pc", "count": 20},
        timeout=timeout,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select("[data-i]")

    if not items:
        page_text = soup.get_text(strip=True)[:300]
        logger.warning(f"[头条-{query}] 页面无 [data-i] 元素 | status={resp.status_code} len={len(resp.text)} | {page_text}")

    results = []
    for item in items:
        a = item.select_one(".result-content a") or item.select_one("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        text = item.get_text(strip=True)
        summary = text[len(title):].strip() if title and text.startswith(title) else text

        date_str = ""
        time_el = item.select_one("span[class*='time'], span[class*='date'], .text-time")
        if time_el:
            date_str = time_el.get_text(strip=True)
        if not date_str:
            for pattern in [r'\d+分钟前', r'\d+小时前', r'\d+天前', r'昨天', r'前天',
                            r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', r'\d{1,2}月\d{1,2}日']:
                m = re.search(pattern, text)
                if m:
                    date_str = m.group(0)
                    break

        results.append({
            "title": title, "summary": summary[:200],
            "date_str": date_str, "date": _parse_date_str(date_str),
        })
    return results


def _search(query: str, timeout: int = 15) -> list[dict]:
    """搜索头条，失败时轮换IP重试"""
    empty_retries = 0
    is_first = True

    while True:
        try:
            results = _search_toutiao(query, timeout)
            if results:
                suffix = "" if is_first else " (切换后)"
                logger.info(f"[头条-{query}] {len(results)} 条{suffix}")
                return results
            empty_retries += 1
            if empty_retries > MAX_EMPTY_RETRIES:
                logger.warning(f"[头条-{query}] 连续 {MAX_EMPTY_RETRIES} 次空结果，跳过")
                return []
            logger.warning(f"[头条-{query}] 0 条结果 ({empty_retries}/{MAX_EMPTY_RETRIES})，轮换IP重试...")
        except Exception as e:
            logger.warning(f"[头条-{query}] 请求失败: {e}，轮换IP重试...")

        try:
            new_node = rotate_proxy()
        except RuntimeError:
            raise RuntimeError(f"[{query}] 所有代理节点均不可用")

        _reset_session()
        logger.info(f"[代理切换] -> {new_node}，等待 {DELAY_MIN}-{DELAY_MAX}s 后重试 [{query}]")
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        is_first = False


def _dedup_news(items: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for item in items:
        if item["title"] not in seen:
            seen.add(item["title"])
            unique.append(item)
    return unique


def _sort_by_date(items: list[dict]) -> list[dict]:
    epoch = datetime(2000, 1, 1)
    return sorted(items, key=lambda x: x.get("date") or epoch, reverse=True)


def _format_news(items: list[dict], max_count: int = 30) -> str:
    items = items[:max_count]
    lines = []
    for i, item in enumerate(items):
        date_tag = f"[{item['date_str']}] " if item.get("date_str") else ""
        lines.append(f"{i+1}. {date_tag}{item['title']}\n{item['summary']}")
    return "\n\n".join(lines)


def _collect_news(queries: list[str], max_count: int = 30) -> str:
    """多关键词搜索头条，合并去重"""
    all_news = []
    for q in queries:
        results = _search(q)
        all_news.extend(results)
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    unique = _dedup_news(all_news)
    unique = _sort_by_date(unique)
    if unique:
        logger.info(f"去重后 {len(unique)} 条，取前 {min(len(unique), max_count)} 条")
    return _format_news(unique, max_count)


def _call_llm(prompt: str, llm: LLMConfig, max_retries: int = 3) -> dict | None:
    """调用 LLM，返回解析后的 JSON dict"""
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
    logger.error(f"LLM 调用 {max_retries} 次均失败")
    return None


# ── 行业分析 ──

INDUSTRY_PROMPT = """你是一位专业的行业研究员。根据以下【{name}】行业的最新新闻，完成行业分析。今日：{today}。

## 新闻列表（按时间倒序，带 [时间] 标记）
{articles}

## 分析任务

### 1. 近期概况（recent）
提取**近一个月**的行业重要事件，概括最新动态和短期催化因素。新闻不足时可放宽到三个月并注明。

### 2. 行业分析
- **政策动态（policy）**：产业政策、监管变化、补贴扶持
- **供需格局（supply_demand）**：产能变化、需求趋势、价格走势
- **龙头动向（leaders）**：行业龙头公司的重大动作、业绩变化
- **风险提示（risk）**：负面消息、政策风险、周期性风险

### 3. 关键事件（key_events，最多3条）
- 优先近一个月，同类只保留最新
- 不同事件覆盖不同维度，含具体数据
- date 从新闻提取，不确定填 "unknown"；不够3条可少填

### 4. 维度打分（scores）
对5个维度打分（0-100，50=中性），各附一句理由（30字内）：
- **policy**（政策面，权重25%）：近期政策是否利好本行业
- **supply_demand**（供需面，权重25%）：供需格局是否改善
- **profitability**（盈利面，权重20%）：行业整体盈利趋势
- **risk_level**（安全程度，权重15%）：越高=越安全
- **catalyst**（近期催化，权重15%）：近一个月有无重大利好/利空

**score** = policy×0.25 + supply_demand×0.25 + profitability×0.20 + risk_level×0.15 + catalyst×0.15

## JSON 输出（严格遵守，不要输出其他内容，总输出不得超过2000个字符）

{{"recent": "近期概况100字", "policy": "政策动态", "supply_demand": "供需格局", "leaders": "龙头动向", "risk": "风险提示", "sentiment": "乐观|中性|悲观", "summary": "综合评价含关键数据200字", "scores": {{"policy": {{"score": 50, "reason": "理由"}}, "supply_demand": {{"score": 50, "reason": "理由"}}, "profitability": {{"score": 50, "reason": "理由"}}, "risk_level": {{"score": 50, "reason": "理由"}}, "catalyst": {{"score": 50, "reason": "理由"}}}}, "score": 50, "key_events": [{{"event": "事件描述含数据200字内", "sentiment": "乐观|悲观|中性", "impact": "影响", "date": "YYYY-MM-DD"}}]}}"""


def analyze_industry(name: str, llm: LLMConfig) -> tuple[dict | None, int]:
    """行业分析，返回 (result, article_count)"""
    queries = [f"{name}行业", f"{name} 政策 产能 龙头 前景"]
    articles = _collect_news(queries, max_count=30)
    if not articles:
        return None, 0
    count = len(articles.strip().split("\n\n"))
    prompt = INDUSTRY_PROMPT.format(name=name, articles=articles, today=date.today())
    return _call_llm(prompt, llm), count
