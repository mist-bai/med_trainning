from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings
from pydantic import BaseModel
import redis.asyncio as redis  # 修改这里：使用 redis 自带的异步功能
import pymysql
from qdrant_client import QdrantClient
from typing import Optional, List
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import uuid

# 导入数据库和工作流模块
from database import init_database, get_db, ExamQuestion, AuditTask, AuditStatus
from workflow import workflow_app, WorkflowState, save_question_from_audit, delete_audit_question_data

# 1. 定义配置类，Pydantic 会自动从环境变量或 .env 中读取同名变量
class Settings(BaseSettings):
    MYSQL_ROOT_PASSWORD: str
    MYSQL_DATABASE: str
    MYSQL_HOST: str = "mysql-server"
    
    REDIS_HOST: str = "host.docker.internal"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    
    QDRANT_HOST: str = "qdrant-server"
    QDRANT_PORT: int = 6333
    #QDRANT_API_KEY: Optional[str] = None
    
    DEEPSEEK_API_KEY: str

    class Config:
        env_file = ".env"

settings = Settings()


# 启动时初始化数据库
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行
    print("🚀 正在初始化数据库...")
    init_database()
    yield
    # 关闭时执行（如果需要）


app = FastAPI(title="医药培训平台", lifespan=lifespan)

# 配置 CORS，允许前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/test_connections")
async def test_connections():
    results = {}

    # 1. 测试 Redis
    try:
        # 这里的 redis 已经指向 redis.asyncio
        redis_client = redis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}",
            password=settings.REDIS_PASSWORD,
            encoding="utf-8",
            decode_responses=True
        )
        await redis_client.ping()
        results["redis"] = "✅ 已连通"
        await redis_client.close()
    except Exception as e:
        results["redis"] = f"❌ 失败: {str(e)}"

    # 2. 测试 MySQL
    try:
        conn = pymysql.connect(
            host=settings.MYSQL_HOST,
            user="root",
            password=settings.MYSQL_ROOT_PASSWORD,
            database=settings.MYSQL_DATABASE
        )
        conn.close()
        results["mysql"] = "✅ 已连通"
    except Exception as e:
        results["mysql"] = f"❌ 失败: {str(e)}"

    # 3. 测试 Qdrant
    try:
        client = QdrantClient(
            host=settings.QDRANT_HOST, 
            port=settings.QDRANT_PORT
            #api_key=settings.QDRANT_API_KEY
        )
        client.get_collections()
        results["qdrant"] = "✅ 已连通"
    except Exception as e:
        results["qdrant"] = f"❌ 失败: {str(e)}"

    return results


# ==================== 试题生成工作流 API ====================

class GenerateQuestionRequest(BaseModel):
    query: str = "医药合规法规条款"  # 检索查询词


class GenerateQuestionResponse(BaseModel):
    task_id: str
    audit_task_id: int
    message: str
    generated_question: Optional[dict] = None


@app.post("/api/generate-question", response_model=GenerateQuestionResponse)
async def generate_question(request: GenerateQuestionRequest):
    """
    触发试题生成工作流
    工作流会：检索 -> 生成 -> 等待人工审核
    """
    try:
        # 创建初始状态
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        initial_state: WorkflowState = {
            "query": request.query,
            "retrieved_clauses": [],
            "generated_question": None,
            "audit_task_id": None,
            "approval_status": None,
            "audit_comment": None,
            "source_document": None
        }
        
        # 运行工作流（会执行到人工审核节点并暂停）
        result = workflow_app.invoke(initial_state, config)
        
        audit_task_id = result.get("audit_task_id")
        generated_question = result.get("generated_question")
        
        if not audit_task_id:
            raise HTTPException(status_code=500, detail="工作流执行失败，未创建审核任务")
        
        return GenerateQuestionResponse(
            task_id=config["configurable"]["thread_id"],
            audit_task_id=audit_task_id,
            message="题目已生成，等待人工审核",
            generated_question=generated_question
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成题目失败: {str(e)}")


# ==================== 审核管理 API ====================

class AuditRequest(BaseModel):
    status: str  # "approved" 或 "rejected"
    comment: Optional[str] = None


class AuditTaskResponse(BaseModel):
    id: int
    question_id: Optional[int]
    status: str
    comment: Optional[str]
    processed_at: Optional[str]
    created_at: str
    question: Optional[dict] = None


@app.get("/api/audit-tasks", response_model=List[AuditTaskResponse])
async def get_audit_tasks(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    获取审核任务列表
    可以按状态筛选：pending, approved, rejected
    """
    query = db.query(AuditTask)
    
    if status:
        query = query.filter(AuditTask.status == status)
    
    tasks = query.order_by(AuditTask.created_at.desc()).all()
    
    result = []
    for task in tasks:
        question_data = None
        if task.question_id:
            # 已审核通过，从数据库读取
            question = db.query(ExamQuestion).filter(ExamQuestion.id == task.question_id).first()
            if question:
                import json
                question_data = {
                    "id": question.id,
                    "question": question.question,
                    "options": json.loads(question.options),
                    "answer": question.answer,
                    "explanation": question.explanation,
                    "source_document": question.source_document
                }
        elif task.status == AuditStatus.PENDING:
            # 待审核，从 Redis 读取
            import redis
            import json
            from workflow import redis_client
            try:
                redis_key = f"audit_task:{task.id}:question_data"
                question_data_str = redis_client.get(redis_key)
                if question_data_str:
                    question_data = json.loads(question_data_str)
            except Exception as e:
                print(f"从 Redis 读取题目数据失败: {str(e)}")
        
        result.append(AuditTaskResponse(
            id=task.id,
            question_id=task.question_id,
            status=task.status.value,
            comment=task.comment,
            processed_at=task.processed_at.isoformat() if task.processed_at else None,
            created_at=task.created_at.isoformat(),
            question=question_data
        ))
    
    return result


@app.get("/api/audit-tasks/{task_id}", response_model=AuditTaskResponse)
async def get_audit_task(task_id: int, db: Session = Depends(get_db)):
    """
    获取单个审核任务详情
    """
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    
    if not task:
        raise HTTPException(status_code=404, detail="审核任务不存在")
    
    question_data = None
    if task.question_id:
        # 已审核通过，从数据库读取
        question = db.query(ExamQuestion).filter(ExamQuestion.id == task.question_id).first()
        if question:
            import json
            question_data = {
                "id": question.id,
                "question": question.question,
                "options": json.loads(question.options),
                "answer": question.answer,
                "explanation": question.explanation,
                "source_document": question.source_document
            }
    elif task.status == AuditStatus.PENDING:
        # 待审核，从 Redis 读取
        import json
        from workflow import redis_client
        try:
            redis_key = f"audit_task:{task.id}:question_data"
            question_data_str = redis_client.get(redis_key)
            if question_data_str:
                question_data = json.loads(question_data_str)
        except Exception as e:
            print(f"从 Redis 读取题目数据失败: {str(e)}")
    
    return AuditTaskResponse(
        id=task.id,
        question_id=task.question_id,
        status=task.status.value,
        comment=task.comment,
        processed_at=task.processed_at.isoformat() if task.processed_at else None,
        created_at=task.created_at.isoformat(),
        question=question_data
    )


@app.post("/api/audit-tasks/{task_id}/review")
async def review_audit_task(
    task_id: int,
    request: AuditRequest,
    db: Session = Depends(get_db)
):
    """
    审核题目（批准或拒绝）
    如果批准，会自动保存题目到 ExamQuestion 表
    """
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    
    if not task:
        raise HTTPException(status_code=404, detail="审核任务不存在")
    
    if task.status != AuditStatus.PENDING:
        raise HTTPException(status_code=400, detail="该任务已经审核过了")
    
    # 如果批准，先保存题目
    if request.status == "approved":
        question_id = save_question_from_audit(task_id, request.comment)
        if not question_id:
            raise HTTPException(status_code=500, detail="保存题目失败，请检查 Redis 中是否还有题目数据")
        return {
            "message": f"审核任务 {task_id} 已批准，题目已保存（ID: {question_id}）",
            "question_id": question_id
        }
    elif request.status == "rejected":
        # 拒绝时，更新审核任务状态
        task.status = AuditStatus.REJECTED
        task.comment = request.comment
        from datetime import datetime
        task.processed_at = datetime.utcnow()
        db.commit()
        
        # 删除 Redis 中的临时数据
        delete_audit_question_data(task_id)
        
        return {"message": f"审核任务 {task_id} 已拒绝"}
    else:
        raise HTTPException(status_code=400, detail="状态必须是 'approved' 或 'rejected'")


# ==================== 试题查询 API ====================

class QuestionResponse(BaseModel):
    id: int
    question: str
    options: dict
    answer: str
    explanation: Optional[str]
    source_document: Optional[str]
    created_at: str


@app.get("/api/questions", response_model=List[QuestionResponse])
async def get_questions(
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """
    获取已审核通过的试题列表
    """
    questions = db.query(ExamQuestion).offset(skip).limit(limit).all()
    
    result = []
    for q in questions:
        import json
        result.append(QuestionResponse(
            id=q.id,
            question=q.question,
            options=json.loads(q.options),
            answer=q.answer,
            explanation=q.explanation,
            source_document=q.source_document,
            created_at=q.created_at.isoformat()
        ))
    
    return result


@app.get("/api/questions/{question_id}", response_model=QuestionResponse)
async def get_question(question_id: int, db: Session = Depends(get_db)):
    """
    获取单个试题详情
    """
    question = db.query(ExamQuestion).filter(ExamQuestion.id == question_id).first()
    
    if not question:
        raise HTTPException(status_code=404, detail="试题不存在")
    
    import json
    return QuestionResponse(
        id=question.id,
        question=question.question,
        options=json.loads(question.options),
        answer=question.answer,
        explanation=question.explanation,
        source_document=question.source_document,
        created_at=question.created_at.isoformat()
    )


# ==================== 新增接口：智能体和审核管理 ====================

class AgentStartRequest(BaseModel):
    query: str = "医药合规法规条款"  # 检索查询词


class AgentStartResponse(BaseModel):
    success: bool
    message: str
    audit_task_id: Optional[int] = None
    generated_question: Optional[dict] = None


@app.post("/agent/start", response_model=AgentStartResponse)
async def agent_start(request: AgentStartRequest):
    """
    触发智能体开始运行
    等同于 /api/generate-question，提供更简洁的接口
    """
    try:
        # 创建初始状态
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        initial_state: WorkflowState = {
            "query": request.query,
            "retrieved_clauses": [],
            "generated_question": None,
            "audit_task_id": None,
            "approval_status": None,
            "audit_comment": None,
            "source_document": None
        }
        
        # 运行工作流（会执行到人工审核节点并暂停）
        result = workflow_app.invoke(initial_state, config)
        
        audit_task_id = result.get("audit_task_id")
        generated_question = result.get("generated_question")
        
        if not audit_task_id:
            return AgentStartResponse(
                success=False,
                message="工作流执行失败，未创建审核任务",
                audit_task_id=None,
                generated_question=None
            )
        
        return AgentStartResponse(
            success=True,
            message="题目已生成，等待人工审核",
            audit_task_id=audit_task_id,
            generated_question=generated_question
        )
    except Exception as e:
        return AgentStartResponse(
            success=False,
            message=f"生成题目失败: {str(e)}",
            audit_task_id=None,
            generated_question=None
        )


class AuditListItem(BaseModel):
    id: int
    question: dict
    created_at: str


@app.get("/audit/list", response_model=List[AuditListItem])
async def audit_list(db: Session = Depends(get_db)):
    """
    获取待审核的试题列表
    """
    tasks = db.query(AuditTask).filter(
        AuditTask.status == AuditStatus.PENDING
    ).order_by(AuditTask.created_at.desc()).all()
    
    result = []
    for task in tasks:
        question_data = None
        # 从 Redis 读取题目数据
        import json
        from workflow import redis_client
        try:
            redis_key = f"audit_task:{task.id}:question_data"
            question_data_str = redis_client.get(redis_key)
            if question_data_str:
                question_data = json.loads(question_data_str)
        except Exception as e:
            print(f"从 Redis 读取题目数据失败: {str(e)}")
        
        if question_data:
            result.append(AuditListItem(
                id=task.id,
                question=question_data,
                created_at=task.created_at.isoformat()
            ))
    
    return result


class AuditActionRequest(BaseModel):
    status: str  # "approved" 或 "rejected"
    comment: Optional[str] = None


class AuditActionResponse(BaseModel):
    success: bool
    message: str
    question_id: Optional[int] = None


@app.post("/audit/action/{task_id}", response_model=AuditActionResponse)
async def audit_action(
    task_id: int,
    request: AuditActionRequest,
    db: Session = Depends(get_db)
):
    """
    提交审核结果（通过或驳回），并触发 LangGraph 继续向下运行
    """
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    
    if not task:
        return AuditActionResponse(
            success=False,
            message="审核任务不存在",
            question_id=None
        )
    
    if task.status != AuditStatus.PENDING:
        return AuditActionResponse(
            success=False,
            message="该任务已经审核过了",
            question_id=None
        )
    
    # 如果批准，先保存题目
    if request.status == "approved":
        question_id = save_question_from_audit(task_id, request.comment)
        if not question_id:
            return AuditActionResponse(
                success=False,
                message="保存题目失败，请检查 Redis 中是否还有题目数据",
                question_id=None
            )
        return AuditActionResponse(
            success=True,
            message=f"审核任务 {task_id} 已批准，题目已保存（ID: {question_id}）",
            question_id=question_id
        )
    elif request.status == "rejected":
        # 拒绝时，更新审核任务状态
        task.status = AuditStatus.REJECTED
        task.comment = request.comment
        from datetime import datetime
        task.processed_at = datetime.utcnow()
        db.commit()
        
        # 删除 Redis 中的临时数据
        delete_audit_question_data(task_id)
        
        return AuditActionResponse(
            success=True,
            message=f"审核任务 {task_id} 已拒绝",
            question_id=None
        )
    else:
        return AuditActionResponse(
            success=False,
            message="状态必须是 'approved' 或 'rejected'",
            question_id=None
        )