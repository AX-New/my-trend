"""数据库模型定义"""

from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Date, Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Article(Base):
    """新闻文章表：保留 3 天滚动窗口，URL 去重"""
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100), nullable=False, comment="来源名称")
    category = Column(String(20), nullable=False, comment="板块: news/finance/forum/stock")
    stock_code = Column(String(20), comment="关联股票代码，常规新闻为空")
    title = Column(String(500), nullable=False, comment="标题")
    url = Column(String(2048), nullable=False, comment="原文链接")
    content = Column(Text, comment="原文内容/摘要")
    dup_count = Column(Integer, default=1, comment="相似文章数量（热度权重）")
    language = Column(String(10), default="en", comment="原文语言")
    published_at = Column(DateTime, comment="发布时间")
    created_at = Column(DateTime, default=datetime.now, comment="入库时间")

    __table_args__ = (
        Index("idx_category", "category"),
        Index("idx_stock_code", "stock_code"),
        Index("idx_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<Article {self.id}: {self.title[:30]}>"


class StockDaily(Base):
    """股票每日指标：声量、情绪、热度，用于时间序列分析"""
    __tablename__ = "stock_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, comment="股票代码")
    date = Column(Date, nullable=False, comment="统计日期")
    mention_count = Column(Integer, default=0, comment="当日新增文章数（声量）")
    positive_count = Column(Integer, default=0, comment="正面文章数")
    negative_count = Column(Integer, default=0, comment="负面文章数")
    neutral_count = Column(Integer, default=0, comment="中性文章数")
    sentiment_score = Column(Float, default=0.0, comment="综合情绪分 -1.0~1.0")
    heat_score = Column(Float, default=0.0, comment="热度指数")
    top_sources = Column(String(500), comment="来源分布")
    key_events = Column(Text, comment="关键事件")
    summary = Column(Text, comment="当日综合摘要")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "date", name="uq_stock_date"),
        Index("idx_sd_stock_code", "stock_code"),
        Index("idx_sd_date", "date"),
    )

    def __repr__(self):
        return f"<StockDaily {self.stock_code} {self.date}: mention={self.mention_count}>"


class PopularityRank(Base):
    """东方财富人气排名每日快照"""
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
# AkShare 热度数据表
# ═══════════════════════════════════════════

class EmHotRank(Base):
    """东方财富人气榜 Top100 每日快照"""
    __tablename__ = "em_hot_rank"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, comment="采集日期")
    rank = Column(Integer, comment="当前排名")
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(50))
    new_price = Column(Float, comment="最新价")
    change_amount = Column(Float, comment="涨跌额")
    change_rate = Column(Float, comment="涨跌幅%")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("date", "stock_code", name="uq_ehr_date_code"),
        Index("idx_ehr_date", "date"),
    )


class EmHotUp(Base):
    """东方财富飙升榜 Top100 每日快照"""
    __tablename__ = "em_hot_up"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, comment="采集日期")
    rank = Column(Integer, comment="当前排名")
    rank_change = Column(Integer, comment="排名较昨日变动")
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(50))
    new_price = Column(Float, comment="最新价")
    change_amount = Column(Float, comment="涨跌额")
    change_rate = Column(Float, comment="涨跌幅%")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("date", "stock_code", name="uq_ehu_date_code"),
        Index("idx_ehu_date", "date"),
    )


class EmHotRankDetail(Base):
    """东方财富个股历史趋势+粉丝特征"""
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


class EmHotRankRealtime(Base):
    """东方财富个股实时排名变动"""
    __tablename__ = "em_hot_rank_realtime"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False, comment="数据时间")
    rank = Column(Integer, comment="排名")
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
    concept_name = Column(String(100), comment="概念名称")
    concept_code = Column(String(50), comment="概念代码")
    heat = Column(Float, comment="热度")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "timestamp", "concept_code",
                         name="uq_ehk_code_ts_concept"),
        Index("idx_ehk_code", "stock_code"),
    )


class EmHotRankLatest(Base):
    """东方财富个股最新排名详情"""
    __tablename__ = "em_hot_rank_latest"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    date = Column(Date, nullable=False, comment="采集日期")
    data_json = Column(Text, comment="排名详情 JSON")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "date", name="uq_ehrl_code_date"),
        Index("idx_ehrl_code", "stock_code"),
    )


class EmHotRankRelate(Base):
    """东方财富个股相关股票"""
    __tablename__ = "em_hot_rank_relate"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False, comment="数据时间")
    related_code = Column(String(20), nullable=False, comment="相关股票代码")
    change_rate = Column(Float, comment="涨跌幅%")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "timestamp", "related_code",
                         name="uq_ehrrel_code_ts_rel"),
        Index("idx_ehrrel_code", "stock_code"),
    )


class XqHotRank(Base):
    """雪球热度排行（关注/讨论/交易三榜合一）"""
    __tablename__ = "xq_hot_rank"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, comment="采集日期")
    type = Column(String(20), nullable=False, comment="follow/tweet/deal")
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(50))
    count = Column(Integer, comment="关注/讨论/交易数")
    new_price = Column(Float, comment="最新价")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("date", "type", "stock_code",
                         name="uq_xqhr_date_type_code"),
        Index("idx_xqhr_date", "date"),
        Index("idx_xqhr_type", "type"),
    )
