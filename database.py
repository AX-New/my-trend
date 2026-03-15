"""数据库连接和初始化"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import Base
from config import DatabaseConfig


class Database:
    """封装数据库连接，提供 session 工厂和建表功能"""

    def __init__(self, db_config: DatabaseConfig):
        self.engine = create_engine(
            db_config.url,
            echo=False,
            pool_size=5,
            pool_recycle=3600,
        )
        self._session_factory = sessionmaker(bind=self.engine)

    def init_tables(self):
        """自动创建所有表（已存在则跳过）"""
        Base.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        return self._session_factory()
