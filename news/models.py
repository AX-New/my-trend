"""新闻文章表"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Index, UniqueConstraint,
)
from database import Base


class Article(Base):
    """新闻文章表：7 天滚动窗口，url_hash 唯一去重"""
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100), nullable=False, comment="来源名称")
    category = Column(String(20), nullable=False, comment="板块: news/finance/forum/stock")
    stock_code = Column(String(20), comment="关联股票代码，常规新闻为空")
    title = Column(String(500), nullable=False, comment="标题")
    url = Column(String(2048), nullable=False, comment="原文链接")
    url_hash = Column(String(64), nullable=False, comment="url 的 SHA256，用于唯一去重")
    content = Column(Text, comment="原文内容/摘要")
    dup_count = Column(Integer, default=1, comment="相似文章数量（热度权重）")
    language = Column(String(10), default="en", comment="原文语言")
    published_at = Column(DateTime, comment="发布时间")
    created_at = Column(DateTime, default=datetime.now, comment="入库时间")

    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_article_url_hash"),
        Index("idx_category", "category"),
        Index("idx_stock_code", "stock_code"),
        Index("idx_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<Article {self.id}: {self.title[:30]}>"


class GlobalNews(Base):
    """24小时全球资讯（东方财富 stock_info_global_em）"""
    __tablename__ = "global_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False, comment="标题")
    summary = Column(Text, comment="摘要")
    url = Column(String(2048), nullable=False, comment="原文链接")
    url_hash = Column(String(64), nullable=False, comment="url 的 SHA256，用于唯一去重")
    published_at = Column(DateTime, comment="发布时间")
    created_at = Column(DateTime, default=datetime.now, comment="入库时间")

    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_global_news_url_hash"),
        Index("idx_gn_published", "published_at"),
    )
