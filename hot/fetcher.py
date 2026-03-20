"""盘中热度 + 板块新闻采集 —— 能力层

核心接口：
  - fetch_realtime_hot_rank()        盘中实时人气排名前N
  - fetch_sector_list()              概念/行业板块列表（含涨跌幅）
  - fetch_sector_news()              单个板块的实时新闻
"""

import hashlib
import logging
from datetime import datetime

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)


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


def _clean_code(code: str) -> str:
    """SZ000001 → 000001"""
    code = str(code).strip()
    if code.upper().startswith(("SH", "SZ", "BJ")):
        return code[2:]
    return code


def _content_hash(text: str) -> str:
    """标题 MD5 用于去重"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ── 盘中实时人气排名 ──

def fetch_realtime_hot_rank(top_n: int = 200) -> list[dict]:
    """东方财富实时人气排名（盘中可用）

    返回前 top_n 名的股票列表，每条包含 rank/code/name/price/change_rate。
    注意：ak.stock_hot_rank_em() 只返回前100，如需更多需分页。
    """
    try:
        df = ak.stock_hot_rank_em()
        if df is None or df.empty:
            return []

        results = []
        for _, row in df.head(top_n).iterrows():
            results.append({
                "stock_code": _clean_code(str(row.get("代码", ""))),
                "stock_name": str(row.get("股票名称", "")),
                "rank": _safe_int(row.get("当前排名")),
                "new_price": _safe_float(row.get("最新价")),
                "change_rate": _safe_float(row.get("涨跌幅")),
            })
        return results
    except Exception as e:
        logger.error(f"[实时热度] 采集失败: {e}")
        return []


# ── 板块列表 ──

def fetch_sector_list(sector_type: str = "concept") -> list[dict]:
    """获取板块列表及今日涨跌幅

    sector_type: concept(概念板块) / industry(行业板块)
    """
    try:
        if sector_type == "concept":
            df = ak.stock_board_concept_name_em()
        else:
            df = ak.stock_board_industry_name_em()

        if df is None or df.empty:
            return []

        results = []
        for _, row in df.iterrows():
            results.append({
                "sector_name": str(row.get("板块名称", "")),
                "sector_code": str(row.get("板块代码", "")),
                "change_rate": _safe_float(row.get("涨跌幅")),
                "turnover_rate": _safe_float(row.get("换手率")),
                "total_amount": _safe_float(row.get("总成交额")),
                "up_count": _safe_int(row.get("上涨家数")),
                "down_count": _safe_int(row.get("下跌家数")),
            })
        return results
    except Exception as e:
        logger.error(f"[板块列表-{sector_type}] 采集失败: {e}")
        return []


# ── 板块新闻 ──

def fetch_sector_news(sector_name: str, sector_type: str = "concept") -> list[dict]:
    """采集单个板块的实时新闻（东方财富板块资讯）

    sector_name: 板块名称（如"人工智能"、"半导体"）
    返回新闻列表，每条含 title/content/url/news_time/source
    """
    try:
        # 概念板块资讯
        if sector_type == "concept":
            df = ak.stock_board_concept_info_em(symbol=sector_name)
        else:
            df = ak.stock_board_industry_info_em(symbol=sector_name)

        if df is None or df.empty:
            return []

        results = []
        for _, row in df.iterrows():
            title = str(row.get("标题", "")).strip()
            if not title:
                continue
            results.append({
                "sector_type": sector_type,
                "sector_name": sector_name,
                "title": title,
                "content": str(row.get("内容", ""))[:2000],
                "url": str(row.get("链接", "")),
                "source": str(row.get("来源", "")),
                "news_time": _parse_news_time(row.get("发布时间") or row.get("日期")),
                "content_hash": _content_hash(title),
            })
        return results
    except Exception as e:
        logger.debug(f"[板块新闻-{sector_name}] 采集失败: {e}")
        return []


def _parse_news_time(val) -> datetime | None:
    """解析新闻时间"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    s = str(val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
