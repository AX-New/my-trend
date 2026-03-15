"""AkShare 股票热度数据采集模块

支持 10 个 A 股热度接口：
  批量接口（无需指定股票）：
    1. stock_hot_rank_em      — 东方财富人气榜 Top100
    2. stock_hot_up_em        — 东方财富飙升榜 Top100
    3. stock_hot_follow_xq    — 雪球关注排行（全市场）
    4. stock_hot_tweet_xq     — 雪球讨论排行（全市场）
    5. stock_hot_deal_xq      — 雪球交易排行（全市场）
  单股接口（需指定股票代码）：
    6. stock_hot_rank_detail_em         — 个股历史趋势+粉丝
    7. stock_hot_rank_detail_realtime_em — 个股实时排名变动
    8. stock_hot_keyword_em             — 个股热门关键词
    9. stock_hot_rank_latest_em         — 个股最新排名详情
   10. stock_hot_rank_relate_em         — 个股相关股票
"""

import logging
from datetime import datetime

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def _to_ak_symbol(code: str) -> str:
    """股票代码转 AkShare 格式: 600519 → SH600519, 000858 → SZ000858"""
    code = code.strip()
    if code.startswith("6"):
        return f"SH{code}"
    return f"SZ{code}"


def _clean_code(code) -> str:
    """清理股票代码，移除 SH/SZ 前缀"""
    code = str(code).strip()
    if code.upper().startswith(("SH", "SZ")):
        return code[2:]
    return code


def _safe_float(val):
    if val is None or val == "-" or val == "":
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    if val is None or val == "-" or val == "":
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_timestamp(val):
    """解析时间字段，支持多种格式"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    s = str(val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ═══════════════════════════════════════════
# 批量接口
# ═══════════════════════════════════════════

def fetch_hot_rank_em() -> list[dict]:
    """东方财富-人气榜 Top100（AkShare 失败时降级直调 emappdata）"""
    try:
        df = ak.stock_hot_rank_em()
        if df is None or df.empty:
            raise ValueError("返回空数据")
        results = []
        for _, row in df.iterrows():
            results.append({
                "rank": _safe_int(row.get("当前排名")),
                "stock_code": _clean_code(row.get("代码", "")),
                "stock_name": str(row.get("股票名称", "")),
                "new_price": _safe_float(row.get("最新价")),
                "change_amount": _safe_float(row.get("涨跌额")),
                "change_rate": _safe_float(row.get("涨跌幅")),
            })
        logger.info(f"[AK人气榜] 获取 {len(results)} 条")
        return results
    except Exception as e:
        logger.warning(f"[AK人气榜] AkShare 失败，降级直调 emappdata: {e}")
        return _fetch_rank_em_fallback()


def fetch_hot_up_em() -> list[dict]:
    """东方财富-飙升榜 Top100（AkShare 失败时降级直调 emappdata）"""
    try:
        df = ak.stock_hot_up_em()
        if df is None or df.empty:
            raise ValueError("返回空数据")
        results = []
        for _, row in df.iterrows():
            results.append({
                "rank": _safe_int(row.get("当前排名")),
                "rank_change": _safe_int(row.get("排名较昨日变动")),
                "stock_code": _clean_code(row.get("代码", "")),
                "stock_name": str(row.get("股票名称", "")),
                "new_price": _safe_float(row.get("最新价")),
                "change_amount": _safe_float(row.get("涨跌额")),
                "change_rate": _safe_float(row.get("涨跌幅")),
            })
        logger.info(f"[AK飙升榜] 获取 {len(results)} 条")
        return results
    except Exception as e:
        logger.warning(f"[AK飙升榜] AkShare 失败，降级直调 emappdata: {e}")
        return _fetch_up_em_fallback()


# ── emappdata 直调降级（跳过 push2 价格补全）──

_EMAPPDATA_PAYLOAD = {
    "appId": "appId01",
    "globalId": "786e4c21-70dc-435a-93bb-38",
    "marketType": "",
    "pageNo": 1,
    "pageSize": 100,
}


def _emappdata_post(url: str, extra: dict = None) -> list[dict]:
    """直调 emappdata API"""
    import requests
    payload = {**_EMAPPDATA_PAYLOAD, **(extra or {})}
    s = requests.Session()
    s.trust_env = False
    resp = s.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _fetch_rank_em_fallback() -> list[dict]:
    """降级：直调 emappdata 获取人气榜（无价格数据）"""
    try:
        data = _emappdata_post(
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
        )
        results = []
        for item in data:
            results.append({
                "rank": item.get("rk"),
                "stock_code": _clean_code(item.get("sc", "")),
                "stock_name": "",
                "new_price": None,
                "change_amount": None,
                "change_rate": None,
            })
        logger.info(f"[AK人气榜·降级] 获取 {len(results)} 条（无价格）")
        return results
    except Exception as e:
        logger.error(f"[AK人气榜·降级] 也失败了: {e}")
        return []


def _fetch_up_em_fallback() -> list[dict]:
    """降级：直调 emappdata 获取飙升榜（无价格数据）"""
    try:
        data = _emappdata_post(
            "https://emappdata.eastmoney.com/stockrank/getAllHisRcList",
        )
        results = []
        for item in data:
            results.append({
                "rank": item.get("rk"),
                "rank_change": item.get("hrc"),
                "stock_code": _clean_code(item.get("sc", "")),
                "stock_name": "",
                "new_price": None,
                "change_amount": None,
                "change_rate": None,
            })
        logger.info(f"[AK飙升榜·降级] 获取 {len(results)} 条（无价格）")
        return results
    except Exception as e:
        logger.error(f"[AK飙升榜·降级] 也失败了: {e}")
        return []


def fetch_hot_follow_xq(symbol: str = "最热门") -> list[dict]:
    """雪球-关注排行"""
    return _fetch_xq("follow", ak.stock_hot_follow_xq, symbol)


def fetch_hot_tweet_xq(symbol: str = "最热门") -> list[dict]:
    """雪球-讨论排行"""
    return _fetch_xq("tweet", ak.stock_hot_tweet_xq, symbol)


def fetch_hot_deal_xq(symbol: str = "最热门") -> list[dict]:
    """雪球-交易排行"""
    return _fetch_xq("deal", ak.stock_hot_deal_xq, symbol)


def _fetch_xq(type_name: str, func, symbol: str) -> list[dict]:
    """雪球排行通用逻辑"""
    label = {"follow": "关注", "tweet": "讨论", "deal": "交易"}[type_name]
    try:
        df = func(symbol=symbol)
        if df is None or df.empty:
            logger.warning(f"[雪球{label}] 返回空数据")
            return []
        results = []
        seen = set()
        for _, row in df.iterrows():
            code = _clean_code(row.get("股票代码", ""))
            if code in seen:
                continue
            seen.add(code)
            results.append({
                "type": type_name,
                "stock_code": code,
                "stock_name": str(row.get("股票简称", "")),
                "count": _safe_int(row.get("关注")),
                "new_price": _safe_float(row.get("最新价")),
            })
        logger.info(f"[雪球{label}] 获取 {len(results)} 条")
        return results
    except Exception as e:
        logger.error(f"[雪球{label}] 采集失败: {e}")
        return []


# ═══════════════════════════════════════════
# 单股接口
# ═══════════════════════════════════════════

def fetch_hot_rank_detail_em(symbol: str) -> list[dict]:
    """东方财富-个股历史趋势+粉丝特征"""
    try:
        df = ak.stock_hot_rank_detail_em(symbol=symbol)
        if df is None or df.empty:
            return []
        code = symbol[2:]
        results = []
        for _, row in df.iterrows():
            results.append({
                "stock_code": code,
                "timestamp": _parse_timestamp(row.get("时间")),
                "rank": _safe_int(row.get("排名")),
                "new_fans": _safe_int(row.get("新晋粉丝")),
                "hardcore_fans": _safe_int(row.get("铁杆粉丝")),
            })
        return results
    except Exception as e:
        logger.debug(f"[AK历史趋势] {symbol}: {e}")
        return []


def fetch_hot_rank_realtime_em(symbol: str) -> list[dict]:
    """东方财富-个股实时排名变动"""
    try:
        df = ak.stock_hot_rank_detail_realtime_em(symbol=symbol)
        if df is None or df.empty:
            return []
        code = symbol[2:]
        results = []
        for _, row in df.iterrows():
            results.append({
                "stock_code": code,
                "timestamp": _parse_timestamp(row.get("时间")),
                "rank": _safe_int(row.get("排名")),
            })
        return results
    except Exception as e:
        logger.debug(f"[AK实时排名] {symbol}: {e}")
        return []


def fetch_hot_keyword_em(symbol: str) -> list[dict]:
    """东方财富-个股热门关键词"""
    try:
        df = ak.stock_hot_keyword_em(symbol=symbol)
        if df is None or df.empty:
            return []
        code = symbol[2:]
        results = []
        for _, row in df.iterrows():
            results.append({
                "stock_code": code,
                "timestamp": _parse_timestamp(row.get("时间")),
                "concept_name": str(row.get("概念名称", "")),
                "concept_code": str(row.get("概念代码", "")),
                "heat": _safe_float(row.get("热度")),
            })
        return results
    except Exception as e:
        logger.debug(f"[AK热门关键词] {symbol}: {e}")
        return []


def fetch_hot_rank_latest_em(symbol: str) -> dict | None:
    """东方财富-个股最新排名详情（key-value 对）"""
    try:
        df = ak.stock_hot_rank_latest_em(symbol=symbol)
        if df is None or df.empty:
            return None
        code = symbol[2:]
        data = {}
        for _, row in df.iterrows():
            key = str(row.get("item", ""))
            val = row.get("value", "")
            if key:
                data[key] = str(val) if val is not None else ""
        return {"stock_code": code, "data": data}
    except Exception as e:
        logger.debug(f"[AK最新排名] {symbol}: {e}")
        return None


def fetch_hot_rank_relate_em(symbol: str) -> list[dict]:
    """东方财富-个股相关股票"""
    try:
        df = ak.stock_hot_rank_relate_em(symbol=symbol)
        if df is None or df.empty:
            return []
        code = symbol[2:]
        results = []
        for _, row in df.iterrows():
            results.append({
                "stock_code": code,
                "timestamp": _parse_timestamp(row.get("时间")),
                "related_code": _clean_code(row.get("相关股票代码", "")),
                "change_rate": _safe_float(row.get("涨跌幅")),
            })
        return results
    except Exception as e:
        logger.debug(f"[AK相关股票] {symbol}: {e}")
        return []


# ═══════════════════════════════════════════
# 批量采集单股接口
# ═══════════════════════════════════════════

def fetch_stock_details(codes: list[str]) -> dict:
    """批量采集所有单股热度数据

    参数: codes — 纯数字代码列表，如 ["600519", "000858"]
    返回: {"detail": [...], "realtime": [...], "keyword": [...],
           "latest": [...], "relate": [...]}
    """
    result = {
        "detail": [], "realtime": [], "keyword": [],
        "latest": [], "relate": [],
    }

    for i, code in enumerate(codes):
        symbol = _to_ak_symbol(code)
        logger.debug(f"[AK单股] ({i+1}/{len(codes)}) {symbol}")

        for key, func in [
            ("detail", fetch_hot_rank_detail_em),
            ("realtime", fetch_hot_rank_realtime_em),
            ("keyword", fetch_hot_keyword_em),
            ("relate", fetch_hot_rank_relate_em),
        ]:
            data = func(symbol)
            if data:
                result[key].extend(data)

        latest = fetch_hot_rank_latest_em(symbol)
        if latest:
            result["latest"].append(latest)

    logger.info(
        f"[AK单股汇总] {len(codes)} 只: "
        f"历史{len(result['detail'])} 实时{len(result['realtime'])} "
        f"关键词{len(result['keyword'])} 最新{len(result['latest'])} "
        f"相关{len(result['relate'])}"
    )
    return result
