"""
数据库模型定义和自动迁移脚本
"""
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import enum
from pydantic_settings import BaseSettings
from typing import Optional

# 定义配置类
class Settings(BaseSettings):
    MYSQL_ROOT_PASSWORD: str
    MYSQL_DATABASE: str
    MYSQL_HOST: str = "mysql-server"
    MYSQL_PORT: int = 3306

    class Config:
        env_file = ".env"

settings = Settings()

# 创建数据库连接字符串
DATABASE_URL = f"mysql+pymysql://root:{settings.MYSQL_ROOT_PASSWORD}@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE}?charset=utf8mb4"

# 创建引擎
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # 连接前测试连接
    pool_recycle=3600,   # 1小时后回收连接
    echo=False
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 声明基类
Base = declarative_base()


# 审核状态枚举
class AuditStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ExamQuestion 模型：存储最终生成的试题（支持单选/多选/判断/主观/填空）
class ExamQuestion(Base):
    __tablename__ = "exam_questions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    question = Column(Text, nullable=False, comment="题目内容")
    options = Column(Text, nullable=False, comment="选项（JSON格式，主观题/填空可为空对象）")
    answer = Column(Text, nullable=False, comment="正确答案（单选A、多选A,B,C、判断正确/错误、主观题/填空为文本）")
    explanation = Column(Text, nullable=True, comment="解析")
    source_document = Column(String(255), nullable=True, comment="来源文档名")
    question_type = Column(String(32), nullable=True, comment="试题类型：single_choice/multiple_choice/true_false/subjective/fill_blank")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")

    # 关联审核任务
    audit_tasks = relationship("AuditTask", back_populates="question", cascade="all, delete-orphan")


# AuditTask 模型：存储人工审核状态
class AuditTask(Base):
    __tablename__ = "audit_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    question_id = Column(Integer, ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=True, comment="试题ID（审核通过后关联）")
    status = Column(SQLEnum(AuditStatus), default=AuditStatus.PENDING, nullable=False, comment="审核状态")
    comment = Column(Text, nullable=True, comment="审核意见")
    processed_at = Column(DateTime, nullable=True, comment="处理时间")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")

    # 关联试题（审核通过后）
    question = relationship("ExamQuestion", back_populates="audit_tasks")


def _migrate_exam_questions():
    """为已存在的 exam_questions 表增加 question_type、将 answer 改为 TEXT（若尚未修改）"""
    from sqlalchemy import text
    conn = engine.connect()
    try:
        # 检查是否有 question_type 列（以 MySQL 为例）
        r = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exam_questions' AND COLUMN_NAME = 'question_type'"
        ))
        if r.scalar() == 0:
            conn.execute(text("ALTER TABLE exam_questions ADD COLUMN question_type VARCHAR(32) NULL COMMENT '试题类型'"))
            conn.commit()
            print("✅ 已添加 exam_questions.question_type 列")
        # 将 answer 从 VARCHAR(10) 改为 TEXT（若当前为 varchar）
        r = conn.execute(text(
            "SELECT DATA_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exam_questions' AND COLUMN_NAME = 'answer'"
        ))
        row = r.fetchone()
        if row and (row[0] or "").lower() in ("varchar", "char"):
            conn.execute(text("ALTER TABLE exam_questions MODIFY COLUMN answer TEXT NOT NULL COMMENT '正确答案'"))
            conn.commit()
            print("✅ 已将 exam_questions.answer 改为 TEXT")
    except Exception as e:
        print(f"⚠️ 数据库迁移跳过或失败（可手动执行 ALTER TABLE）: {e}")
        conn.rollback()
    finally:
        conn.close()


def init_database():
    """
    初始化数据库，自动创建表（如果不存在），并执行必要迁移
    在服务启动时调用此函数
    """
    try:
        Base.metadata.create_all(bind=engine)
        _migrate_exam_questions()
        print("✅ 数据库表初始化成功")
        return True
    except Exception as e:
        print(f"❌ 数据库表初始化失败: {str(e)}")
        return False


def get_db():
    """
    获取数据库会话的依赖注入函数
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
