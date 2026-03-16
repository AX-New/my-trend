"""股吧数据采集 —— 能力层

核心接口：
  - fetch_guba_posts()   抓取个股股吧当日帖子（标题+阅读+评论）
  - classify_posts()     LLM 批量分类帖子情绪（1=看多 / -1=看空 / 0=中性）
  - calc_sentiment()     根据分类结果 + 阅读量分档权重，计算加权情绪得分
"""

import json
import logging
import re
from datetime import datetime

from curl_cffi import requests as cffi_req

from config import LLMConfig

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ═══════════════════════════════════════════
# 帖子采集
# ═══════════════════════════════════════════

def fetch_guba_posts(stock_code: str, max_pages: int = 3) -> list[dict]:
    """抓取个股股吧最近 24 小时的帖子

    返回 [{title, click_count, comment_count, forward_count, publish_time}, ...]
    用 curl_cffi 绕过反爬，逐页抓取直到帖子超出 24 小时范围为止。
    """
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(hours=24)
    all_posts = []

    for page in range(1, max_pages + 1):
        if page == 1:
            url = f"https://guba.eastmoney.com/list,{stock_code}.html"
        else:
            url = f"https://guba.eastmoney.com/list,{stock_code},f_{page}.html"

        try:
            resp = cffi_req.get(url, impersonate="chrome", timeout=15)
            if resp.status_code != 200:
                logger.warning(f"[股吧] {stock_code} page={page} HTTP {resp.status_code}")
                break
        except Exception as e:
            logger.warning(f"[股吧] {stock_code} page={page} 请求失败: {e}")
            break

        match = re.search(r"var article_list=(\{.*?\});", resp.text, re.S)
        if not match:
            logger.warning(f"[股吧] {stock_code} page={page} 未找到帖子数据")
            break

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.warning(f"[股吧] {stock_code} page={page} JSON 解析失败")
            break

        posts = data.get("re", [])
        if not posts:
            break

        page_hit = 0
        has_old = False
        for p in posts:
            pub_time_str = p.get("post_publish_time", "")
            if not pub_time_str:
                continue
            try:
                pub_time = datetime.strptime(pub_time_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

            if pub_time < cutoff:
                has_old = True
                continue

            page_hit += 1
            all_posts.append({
                "title": p.get("post_title", "").strip(),
                "click_count": p.get("post_click_count", 0) or 0,
                "comment_count": p.get("post_comment_count", 0) or 0,
                "forward_count": p.get("post_forward_count", 0) or 0,
                "publish_time": pub_time_str,
            })

        # 已出现超过 24 小时的帖子，后续页不会再有更新的
        if has_old or page_hit == 0:
            break

    # 按阅读量降序，取前 50 条（优先保留高关注帖子）
    all_posts.sort(key=lambda p: p["click_count"], reverse=True)
    if len(all_posts) > 50:
        all_posts = all_posts[:50]

    logger.info(f"[股吧] {stock_code} 最近24h帖子 {len(all_posts)} 条")
    return all_posts


def fetch_page1_timespan(stock_code: str) -> float | None:
    """取股吧第一页帖子，计算最新帖与最旧帖的时间跨度（小时）

    跨度越短 = 发帖越密集 = 关注度越高。
    例如第一页跨度 2 小时说明非常活跃，跨度 48 小时说明冷门。
    返回 None 表示数据不足。
    """
    url = f"https://guba.eastmoney.com/list,{stock_code}.html"
    try:
        resp = cffi_req.get(url, impersonate="chrome", timeout=15)
        if resp.status_code != 200:
            return None
    except Exception:
        return None

    match = re.search(r"var article_list=(\{.*?\});", resp.text, re.S)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    times = []
    for p in data.get("re", []):
        pub_str = p.get("post_publish_time", "")
        if not pub_str:
            continue
        try:
            times.append(datetime.strptime(pub_str, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue

    if len(times) < 2:
        return None

    span = (max(times) - min(times)).total_seconds() / 3600
    logger.info(f"[股吧] {stock_code} 第一页时间跨度 {span:.1f}h（{len(times)} 条帖子）")
    return round(span, 1)


# ═══════════════════════════════════════════
# LLM 情绪分类
# ═══════════════════════════════════════════

_CLASSIFY_PROMPT = """你是一个股票情绪分类器。以下是某股票股吧今日的帖子标题列表。
请逐条判断每条帖子的情绪倾向：
  1 = 看多（乐观、利好、加仓、看涨）
  -1 = 看空（悲观、利空、割肉、看跌）
  0 = 中性（讨论、提问、技术分析、无明确倾向）

只返回一个 JSON 数组，长度必须和帖子数量一致，例如：[1, -1, 0, 1, 0]
不要返回任何其他文字。

帖子标题：
{titles}"""


def classify_posts(titles: list[str], llm_cfg: LLMConfig) -> list[int]:
    """调用 LLM 对帖子标题批量分类，返回 [1, -1, 0, ...] 列表"""
    if not titles:
        return []

    import httpx

    # 构造带编号的标题列表
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    prompt = _CLASSIFY_PROMPT.format(titles=numbered)

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{llm_cfg.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {llm_cfg.api_key}"},
                    json={
                        "model": llm_cfg.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": llm_cfg.max_tokens,
                        "temperature": 0.1,
                    },
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()

            # 提取 JSON 数组（兼容 LLM 可能包裹在 markdown code block 中）
            json_match = re.search(r"\[[\s\S]*?\]", content)
            if not json_match:
                logger.error(f"[LLM] 返回格式异常: {content[:200]}")
                return [0] * len(titles)

            labels = json.loads(json_match.group())

            # 校验长度和取值
            if len(labels) != len(titles):
                logger.warning(f"[LLM] 返回 {len(labels)} 条，期望 {len(titles)} 条，截断/补齐")
                labels = labels[:len(titles)]
                labels.extend([0] * (len(titles) - len(labels)))

            # 确保取值在 {-1, 0, 1}
            labels = [x if x in (-1, 0, 1) else 0 for x in labels]
            return labels

        except Exception as e:
            logger.warning(f"[LLM] 分类失败 (第{attempt}次): {e}")
            if attempt < max_retries:
                import time
                time.sleep(5 * attempt)

    logger.error(f"[LLM] 分类 {max_retries} 次均失败，放弃")
    return [0] * len(titles)


# ═══════════════════════════════════════════
# 情绪计算
# ═══════════════════════════════════════════

def _assign_weights(click_counts: list[int]) -> list[int]:
    """按阅读量分位数分成 1-5 档权重

    将当日所有帖子的阅读量排序，按 20%/40%/60%/80% 分位切分：
      0-20%  → 权重 1
      20-40% → 权重 2
      40-60% → 权重 3
      60-80% → 权重 4
      80-100% → 权重 5
    """
    if not click_counts:
        return []

    n = len(click_counts)
    if n == 1:
        return [3]  # 只有一条帖子给中间权重

    # 按阅读量排序，记录原始索引
    sorted_indices = sorted(range(n), key=lambda i: click_counts[i])
    weights = [0] * n
    for rank, idx in enumerate(sorted_indices):
        percentile = rank / n
        if percentile < 0.2:
            weights[idx] = 1
        elif percentile < 0.4:
            weights[idx] = 2
        elif percentile < 0.6:
            weights[idx] = 3
        elif percentile < 0.8:
            weights[idx] = 4
        else:
            weights[idx] = 5

    return weights


def calc_sentiment(posts: list[dict], labels: list[int]) -> dict:
    """根据 LLM 分类结果和阅读量权重，计算加权情绪得分

    返回 {score, bull_count, bear_count, neutral_count,
           post_count, total_read, total_comment}
    """
    n = len(posts)
    if n == 0 or len(labels) != n:
        return None

    click_counts = [p["click_count"] for p in posts]
    weights = _assign_weights(click_counts)

    bull_count = sum(1 for l in labels if l == 1)
    bear_count = sum(1 for l in labels if l == -1)
    neutral_count = sum(1 for l in labels if l == 0)

    # 加权情绪得分：先算 -1~+1，再映射到 0~100（50为中性）
    weighted_sum = sum(labels[i] * weights[i] for i in range(n))
    weight_total = sum(weights)
    raw = weighted_sum / weight_total if weight_total > 0 else 0.0
    score = round((raw + 1) * 50, 1)  # -1→0, 0→50, +1→100

    return {
        "score": score,
        "post_count": n,
        "bull_count": bull_count,
        "bear_count": bear_count,
        "neutral_count": neutral_count,
        "total_read": sum(click_counts),
        "total_comment": sum(p["comment_count"] for p in posts),
    }
