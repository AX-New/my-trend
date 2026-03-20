"""交易日历工具 — 实时判断是否交易日

调用 AkShare 获取交易日历，带内存缓存（一次请求管一天）。
所有盘中模块（heat_live / hot）在入口处调用，非交易日直接跳过。

用法：
    from trading_day import is_trading_day, last_trading_day

    if not is_trading_day():
        print("非交易日，跳过")
        return

    yesterday = last_trading_day()  # 上一个交易日
"""

import logging
from datetime import datetime, date

import akshare as ak

logger = logging.getLogger(__name__)

# 内存缓存：同一进程内只请求一次
_cache_dates: set[date] | None = None
_cache_list: list[date] | None = None


def _load_calendar():
    """加载交易日历到缓存"""
    global _cache_dates, _cache_list
    if _cache_dates is not None:
        return

    try:
        df = ak.tool_trade_date_hist_sina()
        dates = [d.date() if hasattr(d, 'date') else d for d in df['trade_date']]
        _cache_list = sorted(dates)
        _cache_dates = set(_cache_list)
        logger.info(f"[交易日历] 加载完成，{len(_cache_dates)} 个交易日")
    except Exception as e:
        logger.warning(f"[交易日历] AkShare 获取失败: {e}，fallback 工作日判断")
        _cache_dates = set()
        _cache_list = []


def is_trading_day(d: date | str | None = None) -> bool:
    """判断指定日期是否为交易日

    Args:
        d: 日期，默认今天。支持 date 对象或 "YYYY-MM-DD"/"YYYYMMDD" 字符串
    """
    _load_calendar()

    if d is None:
        d = datetime.now().date()
    elif isinstance(d, str):
        d = d.replace("-", "")
        d = datetime.strptime(d, "%Y%m%d").date()

    if _cache_dates:
        return d in _cache_dates

    # fallback：工作日视为交易日
    return d.weekday() < 5


def last_trading_day(d: date | None = None) -> date | None:
    """获取指定日期之前的最近一个交易日

    Args:
        d: 基准日期，默认今天
    """
    _load_calendar()

    if d is None:
        d = datetime.now().date()

    if not _cache_list:
        # fallback：往前找工作日
        from datetime import timedelta
        check = d - timedelta(days=1)
        while check.weekday() >= 5:
            check -= timedelta(days=1)
        return check

    # 二分查找 < d 的最大交易日
    import bisect
    idx = bisect.bisect_left(_cache_list, d)
    if idx > 0:
        return _cache_list[idx - 1]
    return None
