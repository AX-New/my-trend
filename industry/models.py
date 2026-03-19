"""行业分析结果表"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Date,
    UniqueConstraint, Index,
)
from database import Base


class IndustryAnalysis(Base):
    """申万行业每日分析结果

    每个行业每个交易日一条记录，逐行业 LLM 分析。
    """
    __tablename__ = "industry_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    industry_code = Column(String(20), nullable=False, comment="申万行业代码")
    industry_name = Column(String(50), comment="行业名称")
    date = Column(Date, nullable=False, comment="交易日期")
    sentiment = Column(String(10), comment="情绪判断: 乐观/中性/悲观")
    summary = Column(Text, comment="综合评价摘要")
    score = Column(Float, comment="综合评分 0-100，50为中性")
    detail = Column(Text, comment="各维度分析结论（JSON）")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("industry_code", "date", name="uq_ia_code_date"),
        Index("idx_ia_date", "date"),
        {"comment": "申万行业逐行业每日分析，含行情趋势、资金流向、驱动因素、配置建议"},
    )
