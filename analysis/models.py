"""分析结果表"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Date, Index,
    UniqueConstraint,
)
from database import Base


class AnalysisFailure(Base):
    """分析失败记录，用于断点续跑和失败重试"""
    __tablename__ = "analysis_failure"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, comment="股票代码")
    stock_name = Column(String(50), comment="股票简称")
    date = Column(Date, nullable=False, comment="分析日期")
    stage = Column(String(20), nullable=False, comment="失败阶段: search/llm/guba")
    error = Column(Text, comment="错误信息")
    retried = Column(Integer, default=0, comment="已重试次数")
    resolved = Column(Integer, default=0, comment="是否已解决: 0=未解决 1=已解决")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "date", "stage", name="uq_af_code_date_stage"),
        Index("idx_af_date_resolved", "date", "resolved"),
    )


class NewsAnalysis(Base):
    """新闻 LLM 综合分析

    analysis_type:
      - global: 国际形势
      - domestic: 国内形势
      - stock: 个股基本面（stock_code 为具体代码）
    """
    __tablename__ = "news_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_type = Column(String(20), nullable=False, comment="分析类型: global/domestic/stock")
    stock_code = Column(String(20), nullable=False, default="", comment="股票代码（stock类型时填写）")
    stock_name = Column(String(50), comment="股票简称")
    date = Column(Date, nullable=False, comment="分析日期")
    article_count = Column(Integer, comment="参与分析的新闻数量")
    sentiment = Column(String(10), comment="情绪判断")
    summary = Column(Text, comment="综合评价摘要")
    score = Column(Float, comment="综合评分 0-100，50为中性")
    detail = Column(Text, comment="各维度分析结论（JSON）")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("analysis_type", "stock_code", "date", name="uq_na_type_code_date"),
        Index("idx_na_date", "date"),
        Index("idx_na_type", "analysis_type"),
    )
