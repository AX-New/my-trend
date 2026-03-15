"""热度数据表"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Date, Index,
    UniqueConstraint,
)
from database import Base


class PopularityRank(Base):
    """东方财富人气排名每日快照（核心）"""
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


# ═══════════════════════════════════════════
# AkShare 热度数据表（代码保留备用，核心只用 EmHotRankDetail）
# ═══════════════════════════════════════════

class EmHotRankDetail(Base):
    """东方财富个股历史趋势+粉丝特征（核心）"""
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


class EmHotRank(Base):
    """东方财富人气榜 Top100 每日快照（备用）"""
    __tablename__ = "em_hot_rank"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    rank = Column(Integer)
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(50))
    new_price = Column(Float)
    change_amount = Column(Float)
    change_rate = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("date", "stock_code", name="uq_ehr_date_code"),
        Index("idx_ehr_date", "date"),
    )


class EmHotUp(Base):
    """东方财富飙升榜 Top100 每日快照（备用）"""
    __tablename__ = "em_hot_up"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    rank = Column(Integer)
    rank_change = Column(Integer)
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(50))
    new_price = Column(Float)
    change_amount = Column(Float)
    change_rate = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("date", "stock_code", name="uq_ehu_date_code"),
        Index("idx_ehu_date", "date"),
    )


class EmHotRankRealtime(Base):
    """东方财富个股实时排名变动（备用）"""
    __tablename__ = "em_hot_rank_realtime"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    rank = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "timestamp", name="uq_ehrr_code_ts"),
        Index("idx_ehrr_code", "stock_code"),
    )


class EmHotKeyword(Base):
    """东方财富个股热门关键词"""
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


class EmHotRankLatest(Base):
    """东方财富个股最新排名详情（备用）"""
    __tablename__ = "em_hot_rank_latest"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    date = Column(Date, nullable=False)
    data_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "date", name="uq_ehrl_code_date"),
        Index("idx_ehrl_code", "stock_code"),
    )


class EmHotRankRelate(Base):
    """东方财富个股相关股票（备用）"""
    __tablename__ = "em_hot_rank_relate"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    related_code = Column(String(20), nullable=False)
    change_rate = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "timestamp", "related_code",
                         name="uq_ehrrel_code_ts_rel"),
        Index("idx_ehrrel_code", "stock_code"),
    )


class XqHotRank(Base):
    """雪球热度排行（备用）"""
    __tablename__ = "xq_hot_rank"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    type = Column(String(20), nullable=False)
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(50))
    count = Column(Integer)
    new_price = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("date", "type", "stock_code",
                         name="uq_xqhr_date_type_code"),
        Index("idx_xqhr_date", "date"),
        Index("idx_xqhr_type", "type"),
    )


class BaiduHotSearch(Base):
    """百度热搜排行（24小时资讯，A股/港股/美股 Top12）"""
    __tablename__ = "baidu_hot_search"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, comment="采集日期")
    market = Column(String(10), nullable=False, comment="市场: A股/港股/美股")
    rank = Column(Integer, nullable=False, comment="热搜排名 1-12")
    stock_name = Column(String(50), nullable=False, comment="股票名称")
    change_rate = Column(String(20), comment="涨跌幅（原始字符串，如 +0.45%）")
    heat = Column(Integer, comment="综合热度")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("date", "market", "rank",
                         name="uq_bhs_date_market_rank"),
        Index("idx_bhs_date", "date"),
        Index("idx_bhs_market", "market"),
    )
