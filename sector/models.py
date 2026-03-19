"""板块分析结果表"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, Date,
    UniqueConstraint, Index,
)
from database import Base


class SectorAnalysis(Base):
    """东财板块每日分析结果

    每个板块每个交易日一条记录，逐板块 LLM 分析。
    """
    __tablename__ = "sector_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_code = Column(String(20), nullable=False, comment="东财板块代码")
    sector_name = Column(String(100), comment="板块名称")
    date = Column(Date, nullable=False, comment="交易日期")
    sentiment = Column(String(10), comment="情绪判断: 乐观/中性/悲观")
    summary = Column(Text, comment="综合评价摘要")
    score = Column(Float, comment="综合评分 0-100，50为中性")
    detail = Column(Text, comment="各维度分析结论（JSON）")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("sector_code", "date", name="uq_sa_code_date"),
        Index("idx_sa_date", "date"),
        {"comment": "东财板块逐板块每日分析，含涨跌驱动、成分股表现、资金流向、趋势判断"},
    )
