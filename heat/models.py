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


