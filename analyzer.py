"""LLM 分析模块，调用 OpenAI 兼容接口对文章做摘要和情感分析"""

import json
import logging

import httpx

from config import LLMConfig

logger = logging.getLogger(__name__)

# 分析提示词
KEYWORD_GEN_PROMPT = """你是一个金融搜索专家。给定一个股票代码或名称，生成 5-8 个多样化的搜索关键词，用于全面搜索该股票的相关新闻。

要求：
- 覆盖多个维度：业绩/财报、行业动态、政策监管、机构观点、技术面、竞争格局
- 每个关键词简洁，2-6个字
- 直接返回 JSON 数组，不要其他内容

股票: {stock}

返回格式: ["关键词1", "关键词2", ...]"""


ANALYSIS_PROMPT = """你是一个专业的金融舆情分析师。请对以下新闻文章进行分析，返回 JSON 格式结果。

文章标题: {title}
文章内容: {content}

请返回以下 JSON（不要包含其他内容）:
{{
    "summary": "50-100字的中文摘要，概括核心要点",
    "sentiment": "positive 或 negative 或 neutral",
    "keywords": "3-5个关键词，逗号分隔"
}}"""

BATCH_ANALYSIS_PROMPT = """你是一个专业的金融舆情分析师。以下是股票 {stock_code} 今天新出现的 {count} 篇相关新闻。
请综合分析后返回 JSON（不要包含其他内容）。

新闻列表：
{articles_text}

返回格式：
{{
    "summary": "100-200字综合摘要，概括今日该股票的核心舆情动态",
    "sentiment_score": 0.0 到 1.0 之间的数值（0=极度负面, 0.5=中性, 1.0=极度正面）,
    "positive_count": 正面新闻数,
    "negative_count": 负面新闻数,
    "neutral_count": 中性新闻数,
    "key_events": "关键事件，分号分隔",
    "heat_score": 0.0 到 10.0 之间的热度评分（综合考虑数量、重要性、情绪强度）
}}"""


def expand_stock_queries(
    stock: str,
    llm_config: LLMConfig,
    network_timeout: int = 30,
    proxy: str = "",
) -> list[str]:
    """调用 LLM 为股票生成多维度搜索关键词（备用，当前未使用）"""

    prompt = KEYWORD_GEN_PROMPT.format(stock=stock)
    payload = {
        "model": llm_config.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 256,
        "temperature": 0.5,
    }
    headers = {
        "Authorization": f"Bearer {llm_config.api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=network_timeout, proxy=proxy or None) as client:
            resp = client.post(
                f"{llm_config.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        text = data["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        queries = json.loads(text)
        if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
            result = [stock] + [f"{stock} {q}" for q in queries[:7]]
            logger.info(f"[{stock}] 扩展搜索词: {result}")
            return result
    except Exception as e:
        logger.warning(f"[{stock}] query expansion 失败: {e}")

    return [stock]


def analyze_article(
    title: str,
    content: str,
    llm_config: LLMConfig,
    network_timeout: int = 60,
    proxy: str = "",
) -> dict | None:
    """调用 LLM 分析单篇文章，返回 {summary, sentiment, keywords} 或 None"""

    prompt = ANALYSIS_PROMPT.format(title=title, content=content[:1500])
    payload = {
        "model": llm_config.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": llm_config.max_tokens,
        "temperature": llm_config.temperature,
    }
    headers = {
        "Authorization": f"Bearer {llm_config.api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=network_timeout, proxy=proxy or None) as client:
            resp = client.post(
                f"{llm_config.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"LLM 请求失败: {e}")
        return None

    # 解析 LLM 返回内容
    try:
        text = data["choices"][0]["message"]["content"].strip()
        # 处理可能被 markdown 包裹的 JSON
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        return {
            "summary": result.get("summary", ""),
            "sentiment": result.get("sentiment", "neutral"),
            "keywords": result.get("keywords", ""),
        }
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f"LLM 返回解析失败: {e}, 原文: {text[:200] if 'text' in dir() else 'N/A'}")
        return None
