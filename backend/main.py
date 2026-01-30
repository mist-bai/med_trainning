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
import json
from io import BytesIO
from fastapi.responses import StreamingResponse

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
    query: str = "医药合规法规条款"
    question_count: int = 1
    question_type: str = "single_choice"


class GenerateQuestionResponse(BaseModel):
    task_id: str
    audit_task_id: int
    audit_task_ids: Optional[List[int]] = None
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
        count = max(1, min(10, getattr(request, "question_count", 1)))
        qtype = (getattr(request, "question_type", "single_choice") or "single_choice").strip()
        if qtype not in QUESTION_TYPE_VALUES:
            qtype = "single_choice"
        initial_state: WorkflowState = {
            "question_context": request.query.strip() or "医药合规法规条款",
            "query": request.query.strip() or "医药合规法规条款",
            "retrieved_clauses": [],
            "retrieved_docs": [],
            "question_count": count,
            "question_type": qtype,
            "generated_question": None,
            "generated_questions": None,
            "audit_task_id": None,
            "audit_task_ids": None,
            "approval_status": None,
            "audit_comment": None,
            "source_document": None,
            "rejection_reason": None,
        }
        
        result = workflow_app.invoke(initial_state, config)
        audit_task_id = result.get("audit_task_id")
        audit_task_ids = result.get("audit_task_ids") or ([audit_task_id] if audit_task_id else [])
        generated_question = result.get("generated_question")
        
        if not audit_task_id:
            raise HTTPException(status_code=500, detail="工作流执行失败，未创建审核任务")
        
        return GenerateQuestionResponse(
            task_id=config["configurable"]["thread_id"],
            audit_task_id=audit_task_id,
            audit_task_ids=audit_task_ids,
            message=f"已生成 {len(audit_task_ids)} 道题目，等待人工审核",
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

# 试题类型枚举（与前端一致）
QUESTION_TYPE_VALUES = ("single_choice", "multiple_choice", "true_false", "subjective", "fill_blank")
# 试题类型 -> 中文（列表与导出用）
QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "true_false": "判断题",
    "subjective": "主观题",
    "fill_blank": "填空题",
}


class AgentStartRequest(BaseModel):
    query: str = "医药合规法规条款"  # 用户输入的关键词或知识点
    question_count: int = 1  # 生成试题数量（1-10）
    question_type: str = "single_choice"  # 试题类型：single_choice/multiple_choice/true_false/subjective/fill_blank


class AgentStartResponse(BaseModel):
    success: bool
    message: str
    audit_task_id: Optional[int] = None
    audit_task_ids: Optional[List[int]] = None  # 多题时返回全部任务 ID
    generated_question: Optional[dict] = None


@app.post("/agent/start", response_model=AgentStartResponse)
async def agent_start(request: AgentStartRequest):
    """
    触发智能体开始运行
    等同于 /api/generate-question，提供更简洁的接口
    """
    try:
        count = max(1, min(10, getattr(request, "question_count", 1)))
        qtype = (getattr(request, "question_type", "single_choice") or "single_choice").strip()
        if qtype not in QUESTION_TYPE_VALUES:
            qtype = "single_choice"
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        initial_state: WorkflowState = {
            "question_context": request.query.strip() or "医药合规法规条款",
            "query": request.query.strip() or "医药合规法规条款",
            "retrieved_clauses": [],
            "retrieved_docs": [],
            "question_count": count,
            "question_type": qtype,
            "generated_question": None,
            "generated_questions": None,
            "audit_task_id": None,
            "audit_task_ids": None,
            "approval_status": None,
            "audit_comment": None,
            "source_document": None,
            "rejection_reason": None,
        }
        
        result = workflow_app.invoke(initial_state, config)
        audit_task_id = result.get("audit_task_id")
        audit_task_ids = result.get("audit_task_ids") or ([audit_task_id] if audit_task_id else [])
        generated_question = result.get("generated_question")
        
        if not audit_task_id:
            return AgentStartResponse(
                success=False,
                message="工作流执行失败，未创建审核任务",
                audit_task_id=None,
                audit_task_ids=None,
                generated_question=None
            )
        msg = f"已生成 {len(audit_task_ids)} 道题目，等待人工审核"
        return AgentStartResponse(
            success=True,
            message=msg,
            audit_task_id=audit_task_id,
            audit_task_ids=audit_task_ids,
            generated_question=generated_question
        )
    except Exception as e:
        return AgentStartResponse(
            success=False,
            message=f"生成题目失败: {str(e)}",
            audit_task_id=None,
            audit_task_ids=None,
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
    new_audit_task_id: Optional[int] = None  # 拒绝并重新生成时返回新任务 ID


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
        rejection_reason = request.comment or "未填写原因"
        task.status = AuditStatus.REJECTED
        task.comment = rejection_reason
        from datetime import datetime
        task.processed_at = datetime.utcnow()
        db.commit()
        
        # 删除 Redis 中的临时数据
        delete_audit_question_data(task_id)
        
        # 自动触发 DeepSeek 根据拒绝原因重新生成题目
        new_audit_task_id = None
        try:
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            initial_state: WorkflowState = {
                "question_context": "医药合规法规条款",
                "query": "医药合规法规条款",
                "retrieved_clauses": [],
                "retrieved_docs": [],
                "question_count": 1,
                "question_type": "single_choice",
                "generated_question": None,
                "generated_questions": None,
                "audit_task_id": None,
                "audit_task_ids": None,
                "approval_status": None,
                "audit_comment": None,
                "source_document": None,
                "rejection_reason": rejection_reason,
            }
            result = workflow_app.invoke(initial_state, config)
            new_audit_task_id = result.get("audit_task_id")
        except Exception as e:
            print(f"拒绝后自动重新生成题目失败: {e}")
        
        return AuditActionResponse(
            success=True,
            message=f"审核任务 {task_id} 已拒绝" + (
                f"，已根据拒绝原因提交重新生成（新任务 #{new_audit_task_id}）" if new_audit_task_id else "（重新生成失败，请手动点击「生成新题目」）"
            ),
            question_id=None,
            new_audit_task_id=new_audit_task_id,
        )
    else:
        return AuditActionResponse(
            success=False,
            message="状态必须是 'approved' 或 'rejected'",
            question_id=None,
            new_audit_task_id=None,
        )


# ==================== 审核通过试题列表与导出 ====================

class ApprovedQuestionItem(BaseModel):
    id: int
    question: dict
    question_type: Optional[str] = None  # single_choice / multiple_choice / true_false / subjective / fill_blank
    audit_comment: Optional[str] = None
    processed_at: Optional[str] = None
    created_at: str


@app.get("/questions/approved", response_model=List[ApprovedQuestionItem])
async def list_approved_questions(db: Session = Depends(get_db)):
    """
    获取审核通过的试题列表（含审批意见、审核时间）
    """
    tasks = (
        db.query(AuditTask)
        .filter(
            AuditTask.status == AuditStatus.APPROVED,
            AuditTask.question_id.isnot(None),
        )
        .order_by(AuditTask.processed_at.desc())
        .all()
    )
    result = []
    for task in tasks:
        q = db.query(ExamQuestion).filter(ExamQuestion.id == task.question_id).first()
        if not q:
            continue
        options = {}
        try:
            options = json.loads(q.options) if isinstance(q.options, str) else (q.options or {})
        except Exception:
            pass
        q_type = getattr(q, "question_type", None) or "single_choice"
        result.append(
            ApprovedQuestionItem(
                id=q.id,
                question={
                    "question": q.question,
                    "options": options,
                    "answer": q.answer,
                    "explanation": q.explanation or "",
                },
                question_type=q_type,
                audit_comment=task.comment,
                processed_at=task.processed_at.isoformat() if task.processed_at else None,
                created_at=q.created_at.isoformat() if q.created_at else "",
            )
        )
    return result


@app.get("/questions/export")
async def export_questions_excel(
    ids: str,
    db: Session = Depends(get_db),
):
    """
    导出指定 ID 的试题为 Excel 文件。
    参数 ids: 逗号分隔的试题 ID，如 1,2,3
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="请提供至少一个试题 ID（ids=1,2,3）")

    # 查出试题；审核通过的题在 AuditTask 中有审批意见
    tasks_by_qid = {}
    for t in db.query(AuditTask).filter(
        AuditTask.status == AuditStatus.APPROVED,
        AuditTask.question_id.in_(id_list),
    ).all():
        tasks_by_qid[t.question_id] = t

    questions = db.query(ExamQuestion).filter(ExamQuestion.id.in_(id_list)).all()
    if not questions:
        raise HTTPException(status_code=404, detail="未找到对应试题")

    wb = Workbook()
    ws = wb.active
    ws.title = "审核通过试题"
    headers = ["试题ID", "试题类型", "题目", "选项A", "选项B", "选项C", "选项D", "正确答案/参考答案", "解析", "审批意见", "审核时间"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h).font = Font(bold=True)
    row = 2
    for q in questions:
        opts = {}
        try:
            opts = json.loads(q.options) if isinstance(q.options, str) else (q.options or {})
        except Exception:
            pass
        task = tasks_by_qid.get(q.id)
        audit_comment = (task.comment or "") if task else ""
        processed_at = (task.processed_at.strftime("%Y-%m-%d %H:%M") if task and task.processed_at else "")
        q_type = getattr(q, "question_type", None) or "single_choice"
        type_label = QUESTION_TYPE_LABELS.get(q_type, q_type)
        ws.cell(row=row, column=1, value=q.id)
        ws.cell(row=row, column=2, value=type_label)
        ws.cell(row=row, column=3, value=q.question or "")
        ws.cell(row=row, column=4, value=opts.get("A", ""))
        ws.cell(row=row, column=5, value=opts.get("B", ""))
        ws.cell(row=row, column=6, value=opts.get("C", ""))
        ws.cell(row=row, column=7, value=opts.get("D", ""))
        ws.cell(row=row, column=8, value=q.answer or "")
        ws.cell(row=row, column=9, value=(q.explanation or ""))
        ws.cell(row=row, column=10, value=audit_comment)
        ws.cell(row=row, column=11, value=processed_at)
        row += 1
    for col in range(1, 12):
        ws.column_dimensions[get_column_letter(col)].width = 18
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=approved_questions.xlsx"},
    )