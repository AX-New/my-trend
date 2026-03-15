"""数据库连接和通用写入方法

设计原则：
  - 不使用显式事务（无 rollback），每次写入独立提交
  - 支持重复写入：通过 MySQL ON DUPLICATE KEY UPDATE / INSERT IGNORE 保证幂等
  - 分批提交，中途崩溃不丢已提交数据
  - Base 定义在此，各包的 models.py 导入 Base 注册自己的表
"""

import logging

from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from config import DatabaseConfig

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, db_config: DatabaseConfig):
        self.engine = create_engine(
            db_config.url,
            echo=False,
            pool_size=5,
            pool_recycle=3600,
        )
        self._session_factory = sessionmaker(bind=self.engine)

    def init_tables(self):
        """创建已导入的 models 对应的表（已存在则跳过）"""
        Base.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        return self._session_factory()


def batch_upsert(session, model, rows: list[dict],
                 update_cols: list[str], chunk_size: int = 500) -> int:
    """批量 upsert（INSERT ... ON DUPLICATE KEY UPDATE）

    依赖模型的唯一约束判断冲突。重复记录更新 update_cols 指定的字段。
    分批提交，每批独立，中途崩溃不丢已提交数据。
    """
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        stmt = mysql_insert(model).values(chunk)
        update = {col: stmt.inserted[col] for col in update_cols}
        stmt = stmt.on_duplicate_key_update(**update)
        result = session.execute(stmt)
        session.commit()
        total += result.rowcount
    return total


def batch_insert_ignore(session, model, rows: list[dict],
                        chunk_size: int = 500) -> int:
    """批量 INSERT IGNORE（跳过重复记录）

    依赖模型的唯一约束判断冲突。重复记录直接跳过。
    分批提交，每批独立。
    """
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        stmt = mysql_insert(model).values(chunk).prefix_with('IGNORE')
        result = session.execute(stmt)
        session.commit()
        total += result.rowcount
    return total
