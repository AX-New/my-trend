"""热度数据采集 —— 能力层

核心接口：
  - fetch_popularity_page()     单页人气排名（东方财富选股 API）
  - fetch_hot_rank_detail_em()  个股 366 天历史趋势+粉丝
  - fetch_hot_keyword_em()      个股热门关键词
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


# ── 工具函数 ──

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


# ── 人气排名（东方财富选股 API） ──

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
    """请求单页人气排名数据"""
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


# ── AkShare 接口 ──

def fetch_hot_rank_detail_em(symbol: str) -> list[dict]:
    """东方财富-个股历史趋势+粉丝特征（366天）"""
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


