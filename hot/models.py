"""盘中热度监控 + 板块新闻数据表

两张表：
  - intraday_heat_snapshot: 盘中热度快照（每小时抓一次前200名）
  - sector_news: 板块/概念实时新闻（东方财富板块新闻）
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Text, Index,
    UniqueConstraint,
)
from database import Base


class IntradayHeatSnapshot(Base):
    """盘中热度快照

    每小时采集一次东财人气排名前200，用于捕捉热度突变信号。
    同一只股票同一天可以有多条记录（不同时间点的快照）。
    """
    __tablename__ = "intraday_heat_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, comment="股票代码")
    stock_name = Column(String(50), nullable=False, comment="股票名称")
    snapshot_time = Column(DateTime, nullable=False, comment="快照时间（精确到小时）")
    rank = Column(Integer, nullable=False, comment="当前人气排名")
    prev_rank = Column(Integer, comment="上一次快照排名（空=首次出现）")
    rank_change = Column(Integer, comment="排名变化（正=上升/变好）")
    new_price = Column(Float, comment="最新价")
    change_rate = Column(Float, comment="涨跌幅%")
    volume_ratio = Column(Float, comment="量比")
    turnover_rate = Column(Float, comment="换手率%")
    deal_amount = Column(Float, comment="成交额")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("stock_code", "snapshot_time", name="uq_ihs_code_time"),
        Index("idx_ihs_time", "snapshot_time"),
        Index("idx_ihs_rank", "rank"),
        Index("idx_ihs_rank_change", "rank_change"),
    )


class SectorNews(Base):
    """板块/概念实时新闻

    东方财富概念板块和行业板块的实时新闻，
    与 news_analysis（个股基本面分析，今日头条源）完全分开。
    """
    __tablename__ = "sector_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_type = Column(String(20), nullable=False, comment="板块类型: concept/industry")
    sector_name = Column(String(50), nullable=False, comment="板块名称")
    sector_code = Column(String(20), comment="板块代码")
    title = Column(String(500), nullable=False, comment="新闻标题")
    content = Column(Text, comment="新闻摘要/正文")
    url = Column(String(500), comment="新闻链接")
    source = Column(String(50), comment="来源")
    news_time = Column(DateTime, comment="新闻发布时间")
    content_hash = Column(String(32), nullable=False, comment="标题MD5，去重用")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_sn_hash"),
        Index("idx_sn_sector", "sector_type", "sector_name"),
        Index("idx_sn_time", "news_time"),
    )
