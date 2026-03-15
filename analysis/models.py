"""分析结果表"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Date, Index,
    UniqueConstraint,
)
from database import Base


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
        return f"<StockDaily {self.stock_code} {self.date}>"
