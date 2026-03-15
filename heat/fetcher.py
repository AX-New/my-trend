"""热度数据采集 —— 能力层，无限流

核心接口：
  - fetch_popularity_page()     单页人气排名（东方财富选股 API）
  - fetch_hot_rank_detail_em()  个股 366 天历史趋势+粉丝

备用接口（代码保留）：
  - fetch_hot_rank_em / fetch_hot_up_em
  - fetch_hot_rank_realtime_em / fetch_hot_keyword_em
  - fetch_hot_rank_latest_em / fetch_hot_rank_relate_em
  - fetch_hot_follow_xq / fetch_hot_tweet_xq / fetch_hot_deal_xq
"""

import logging
from datetime import datetime

import akshare as ak
import httpx
import pandas as pd

from config import NetworkConfig

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def _to_ak_symbol(code: str) -> str:
    """600519 → SH600519, 000858 → SZ000858"""
    code = code.strip()
    return f"SH{code}" if code.startswith("6") else f"SZ{code}"


def _clean_code(code) -> str:
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
# 人气排名（东方财富选股 API）—— 核心
# ═══════════════════════════════════════════

_POPULARITY_STY = (
    "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NEW_PRICE,"
    "CHANGE_RATE,VOLUME_RATIO,HIGH_PRICE,LOW_PRICE,PRE_CLOSE_PRICE,"
    "VOLUME,DEAL_AMOUNT,TURNOVERRATE,POPULARITY_RANK"
)

POPULARITY_FILTERS = [
    "(POPULARITY_RANK>0)(POPULARITY_RANK<=1000)",
    "(POPULARITY_RANK>1000)",
]


def fetch_popularity_page(network: NetworkConfig, filter_str: str,
                          page: int, page_size: int = 50) -> dict:
    """请求单页人气排名数据（能力层，单次调用）"""
    url = (
        "https://data.eastmoney.com/dataapi/xuangu/list"
        f"?st=CHANGE_RATE&sr=-1&ps={page_size}&p={page}"
        f"&sty={_POPULARITY_STY}"
        f"&filter={filter_str}"
        "&source=SELECT_SECURITIES&client=WEB&hyversion=v2"
    )
    with httpx.Client(
        timeout=network.timeout,
        follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA},
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


# ═══════════════════════════════════════════
# AkShare 接口 —— 核心: detail_em，其余备用
# ═══════════════════════════════════════════

def fetch_hot_rank_detail_em(symbol: str) -> list[dict]:
    """东方财富-个股历史趋势+粉丝特征（366天）—— 核心"""
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


# ── 以下为备用接口，代码保留 ──

def fetch_hot_rank_em() -> list[dict]:
    """东方财富-人气榜 Top100（备用）"""
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
    """东方财富-飙升榜 Top100（备用）"""
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


# ── emappdata 直调降级 ──

_EMAPPDATA_PAYLOAD = {
    "appId": "appId01",
    "globalId": "786e4c21-70dc-435a-93bb-38",
    "marketType": "",
    "pageNo": 1,
    "pageSize": 100,
}


def _emappdata_post(url: str, extra: dict = None) -> list[dict]:
    import requests
    payload = {**_EMAPPDATA_PAYLOAD, **(extra or {})}
    s = requests.Session()
    s.trust_env = False
    resp = s.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _fetch_rank_em_fallback() -> list[dict]:
    try:
        data = _emappdata_post(
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
        )
        results = [{
            "rank": item.get("rk"),
            "stock_code": _clean_code(item.get("sc", "")),
            "stock_name": "", "new_price": None,
            "change_amount": None, "change_rate": None,
        } for item in data]
        logger.info(f"[AK人气榜·降级] 获取 {len(results)} 条")
        return results
    except Exception as e:
        logger.error(f"[AK人气榜·降级] 也失败了: {e}")
        return []


def _fetch_up_em_fallback() -> list[dict]:
    try:
        data = _emappdata_post(
            "https://emappdata.eastmoney.com/stockrank/getAllHisRcList",
        )
        results = [{
            "rank": item.get("rk"),
            "rank_change": item.get("hrc"),
            "stock_code": _clean_code(item.get("sc", "")),
            "stock_name": "", "new_price": None,
            "change_amount": None, "change_rate": None,
        } for item in data]
        logger.info(f"[AK飙升榜·降级] 获取 {len(results)} 条")
        return results
    except Exception as e:
        logger.error(f"[AK飙升榜·降级] 也失败了: {e}")
        return []


def fetch_hot_follow_xq(symbol: str = "最热门") -> list[dict]:
    """雪球-关注排行（备用）"""
    return _fetch_xq("follow", ak.stock_hot_follow_xq, symbol)


def fetch_hot_tweet_xq(symbol: str = "最热门") -> list[dict]:
    """雪球-讨论排行（备用）"""
    return _fetch_xq("tweet", ak.stock_hot_tweet_xq, symbol)


def fetch_hot_deal_xq(symbol: str = "最热门") -> list[dict]:
    """雪球-交易排行（备用）"""
    return _fetch_xq("deal", ak.stock_hot_deal_xq, symbol)


def _fetch_xq(type_name: str, func, symbol: str) -> list[dict]:
    label = {"follow": "关注", "tweet": "讨论", "deal": "交易"}[type_name]
    try:
        df = func(symbol=symbol)
        if df is None or df.empty:
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


def fetch_hot_rank_realtime_em(symbol: str) -> list[dict]:
    """东方财富-个股实时排名变动（备用）"""
    try:
        df = ak.stock_hot_rank_detail_realtime_em(symbol=symbol)
        if df is None or df.empty:
            return []
        code = symbol[2:]
        return [{
            "stock_code": code,
            "timestamp": _parse_timestamp(row.get("时间")),
            "rank": _safe_int(row.get("排名")),
        } for _, row in df.iterrows()]
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
        return [{
            "stock_code": code,
            "timestamp": _parse_timestamp(row.get("时间")),
            "concept_name": str(row.get("概念名称", "")),
            "concept_code": str(row.get("概念代码", "")),
            "heat": _safe_float(row.get("热度")),
        } for _, row in df.iterrows()]
    except Exception as e:
        logger.debug(f"[AK热门关键词] {symbol}: {e}")
        return []


def fetch_hot_rank_latest_em(symbol: str) -> dict | None:
    """东方财富-个股最新排名详情（备用）"""
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
    """东方财富-个股相关股票（备用）"""
    try:
        df = ak.stock_hot_rank_relate_em(symbol=symbol)
        if df is None or df.empty:
            return []
        code = symbol[2:]
        return [{
            "stock_code": code,
            "timestamp": _parse_timestamp(row.get("时间")),
            "related_code": _clean_code(row.get("相关股票代码", "")),
            "change_rate": _safe_float(row.get("涨跌幅")),
        } for _, row in df.iterrows()]
    except Exception as e:
        logger.debug(f"[AK相关股票] {symbol}: {e}")
        return []
