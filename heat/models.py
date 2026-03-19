"""热度数据表"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Date, Index,
    UniqueConstraint,
)
from database import Base


class PopularityRank(Base):
    """东方财富人气排名每日快照（全市场 ~5500 只）"""
    __tablename__ = "popularity_rank"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(50), nullable=False)
    date = Column(Date, nullable=False, comment="交易日期")
    rank = Column(Integer, nullable=False, comment="人气排名")
    new_price = Column(Float, comment="最新价")
    change_rate = Column(Float, comment="涨跌幅%")
    volume_ratio = Column(Float, comment="量比")
    turnover_rate = Column(Float, comment="换手率%")
    volume = Column(Integer, comment="成交量")
    deal_amount = Column(Float, comment="成交额")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "date", name="uq_pop_stock_date"),
        Index("idx_pop_date", "date"),
        Index("idx_pop_rank", "rank"),
    )


class EmHotRankDetail(Base):
    """东方财富个股历史趋势+粉丝特征（366天回溯）"""
    __tablename__ = "em_hot_rank_detail"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False, comment="数据时间")
    rank = Column(Integer, comment="排名")
    new_fans = Column(Integer, comment="新晋粉丝")
    hardcore_fans = Column(Integer, comment="铁杆粉丝")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "timestamp", name="uq_ehrd_code_ts"),
        Index("idx_ehrd_code", "stock_code"),
    )


class EmHotKeyword(Base):
    """东方财富个股热门关键词（Top100 概念关联）"""
    __tablename__ = "em_hot_keyword"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False, comment="数据时间")
    concept_name = Column(String(100))
    concept_code = Column(String(50))
    heat = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "timestamp", "concept_code",
                         name="uq_ehk_code_ts_concept"),
        Index("idx_ehk_code", "stock_code"),
    )


class HeatChangeTop(Base):
    """盘中热度变化 Top20 快照

    每个时间点记录排名上升最多的 Top20 股票。
    同一天有多个时间点快照，用于追踪盘中热度演变。
    """
    __tablename__ = "heat_change_top"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, comment="股票代码")
    stock_name = Column(String(50), nullable=False, comment="股票名称")
    date = Column(Date, nullable=False, comment="交易日期")
    time_point = Column(String(10), nullable=False, comment="快照时间点，如 09:30")
    rank_today = Column(Integer, nullable=False, comment="当前排名")
    rank_yesterday = Column(Integer, comment="昨日排名")
    rank_change = Column(Integer, comment="排名变化（正数=上升）")
    new_price = Column(Float, comment="最新价")
    change_rate = Column(Float, comment="涨跌幅%")
    volume_ratio = Column(Float, comment="量比")
    turnover_rate = Column(Float, comment="换手率%")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "date", "time_point",
                         name="uq_hct_code_date_tp"),
        Index("idx_hct_date_tp", "date", "time_point"),
        Index("idx_hct_rank_change", "rank_change"),
    )


class HeatStockMinute(Base):
    """热度 Top 股票分钟K线（收盘后采集，用于回测）

    每日收盘后，对当日所有出现在 heat_change_top 的股票，
    采集完整 1 分钟 K 线数据，用于分析热度飙升股的盘中走势规律。
    """
    __tablename__ = "heat_stock_minute"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, comment="股票代码")
    stock_name = Column(String(50), nullable=False, comment="股票名称")
    trade_date = Column(Date, nullable=False, comment="交易日期")
    time = Column(DateTime, nullable=False, comment="分钟时间戳")
    open = Column(Float, comment="开盘价")
    high = Column(Float, comment="最高价")
    low = Column(Float, comment="最低价")
    close = Column(Float, comment="收盘价")
    volume = Column(Float, comment="成交量")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "time", name="uq_hsm_code_time"),
        Index("idx_hsm_date", "trade_date"),
        Index("idx_hsm_code", "stock_code"),
    )
