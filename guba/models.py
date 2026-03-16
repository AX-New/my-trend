"""股吧情绪表"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Date, Index, UniqueConstraint,
)
from database import Base


class GubaSentiment(Base):
    """股吧情绪得分（每日每股一条）

    score = Σ(label × weight) / Σ(weight)
    label: LLM 分类（1=看多 / -1=看空 / 0=中性）
    weight: 按阅读量分位数分 1-5 档
    """
    __tablename__ = "guba_sentiment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    date = Column(Date, nullable=False, comment="采集日期")
    score = Column(Float, nullable=False, comment="加权情绪得分 0-100，50为中性")
    post_count = Column(Integer, comment="当日帖子数")
    bull_count = Column(Integer, comment="看多帖子数")
    bear_count = Column(Integer, comment="看空帖子数")
    neutral_count = Column(Integer, comment="中性帖子数")
    total_read = Column(Integer, comment="总阅读量")
    total_comment = Column(Integer, comment="总评论数")
    page1_timespan = Column(Float, comment="第一页帖子时间跨度（小时），越短越活跃")
    tier = Column(String(20), comment="采样区间（市场抽样时使用）")
    popularity_rank = Column(Integer, comment="人气排名（市场抽样时记录）")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "date", name="uq_gs_code_date"),
        Index("idx_gs_date", "date"),
        Index("idx_gs_score", "score"),
    )


class GubaPostDetail(Base):
    """股吧帖子明细（每次分析先删旧数据再写入，只保留最新一次）"""
    __tablename__ = "guba_post_detail"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False)
    date = Column(Date, nullable=False, comment="采集日期")
    title = Column(String(500), nullable=False, comment="帖子标题")
    click_count = Column(Integer, comment="阅读数")
    comment_count = Column(Integer, comment="评论数")
    forward_count = Column(Integer, comment="转发数")
    publish_time = Column(DateTime, comment="发布时间")
    label = Column(Integer, comment="LLM 情绪标签：1=看多 / -1=看空 / 0=中性")
    weight = Column(Integer, comment="阅读量分档权重 1-5")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_gpd_code_date", "stock_code", "date"),
        Index("idx_gpd_label", "label"),
    )
