"""
LangGraph 智能体工作流：医药合规试题生成
"""
from typing import TypedDict, Annotated, Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_deepseek import ChatDeepSeek
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
import json
from datetime import datetime
from sqlalchemy.orm import Session
from database import AuditTask, ExamQuestion, AuditStatus, SessionLocal
from pydantic_settings import BaseSettings
from typing import Optional, List, Dict, Any
import redis
import os


class Settings(BaseSettings):
    QDRANT_HOST: str = "qdrant-server"
    QDRANT_PORT: int = 6333
    DEEPSEEK_API_KEY: str
    MYSQL_ROOT_PASSWORD: str
    MYSQL_DATABASE: str
    MYSQL_HOST: str = "mysql-server"
    REDIS_HOST: str = "host.docker.internal"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    # Redis 数据库编号，禁止使用 db2（其他项目在用），本项目默认使用 db0
    REDIS_DB: int = 0

    class Config:
        env_file = ".env"


settings = Settings()

# 确保不使用 Redis db2（其他项目在用）
if settings.REDIS_DB == 2:
    raise ValueError("REDIS_DB 不能为 2，该库被其他项目使用，请使用 0、1、3 等")

# 初始化 Qdrant 客户端（禁用版本检查以兼容旧版本服务器）
qdrant_client = QdrantClient(
    host=settings.QDRANT_HOST,
    port=settings.QDRANT_PORT,
    check_compatibility=False
)

# 初始化 sentence-transformers 模型（384维），优先使用本地 models 目录
_backend_dir = os.path.dirname(os.path.abspath(__file__))
_local_model_path = os.path.join(_backend_dir, "models", "paraphrase-multilingual-MiniLM-L12-v2")
embedding_model = SentenceTransformer(_local_model_path)  # 使用本地模型，384维

# 初始化 Redis 客户端（用于临时存储题目数据），使用 REDIS_DB，禁止使用 db2
redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    db=settings.REDIS_DB,
    decode_responses=True
)


# 定义状态结构
class WorkflowState(TypedDict):
    query: str  # 用户查询（用于检索）
    retrieved_clauses: List[Dict[str, Any]]  # 检索到的合规条款
    generated_question: Optional[Dict[str, Any]]  # 生成的题目
    audit_task_id: Optional[int]  # 审核任务ID
    approval_status: Optional[Literal["approved", "rejected"]]  # 审核状态
    audit_comment: Optional[str]  # 审核意见
    source_document: Optional[str]  # 来源文档名


def retrieve_clauses(state: WorkflowState) -> WorkflowState:
    """
    节点1：从 Qdrant 检索合规条款
    使用 sentence-transformers (384维) 从 med_knowledge_base 集合中检索 5 条合规条款
    """
    query = state.get("query", "")
    
    if not query:
        # 如果没有查询，使用默认查询
        query = "医药合规法规条款"
    
    # 生成查询向量（384维）
    query_vector = embedding_model.encode(query).tolist()
    
    try:
        # 从 Qdrant 检索（使用 query_points API，直接传入向量列表）
        search_results = qdrant_client.query_points(
            collection_name="med_knowledge_base",
            query=query_vector,
            limit=5,
            with_payload=True,
            with_vectors=False
        )
        
        # 格式化检索结果
        clauses = []
        for result in search_results.points:
            clause_data = {
                "text": result.payload.get("text", "") if result.payload else "",
                "document": result.payload.get("document", "") if result.payload else "",
                "score": result.score if hasattr(result, 'score') else 0.0
            }
            clauses.append(clause_data)
        
        # 提取来源文档名（取第一个结果的文档名）
        source_doc = clauses[0]["document"] if clauses else None
        
        return {
            **state,
            "retrieved_clauses": clauses,
            "source_document": source_doc
        }
    except Exception as e:
        print(f"❌ 检索失败: {str(e)}")
        return {
            **state,
            "retrieved_clauses": [],
            "source_document": None
        }


def generate_question(state: WorkflowState) -> WorkflowState:
    """
    节点2：调用 DeepSeek-V3 API 生成题目
    根据检索到的条款生成 1 道高质量的医药合规选择题
    """
    clauses = state.get("retrieved_clauses", [])
    
    if not clauses:
        return {
            **state,
            "generated_question": None
        }
    
    # 构建提示词
    clauses_text = "\n\n".join([
        f"条款 {i+1}（来源：{clause.get('document', '未知')}）：\n{clause.get('text', '')}"
        for i, clause in enumerate(clauses)
    ])
    
    prompt = f"""你是一位医药合规培训专家。请根据以下合规条款，生成一道高质量的医药合规选择题。

要求：
1. 题目必须基于提供的合规条款内容
2. 题目应该具有实际应用价值，能够测试对合规要求的理解
3. 提供4个选项（A、B、C、D），其中只有一个正确答案
4. 提供详细的解析，说明为什么选择该答案
5. 题目和选项应该清晰、准确、无歧义

合规条款内容：
{clauses_text}

请以JSON格式返回，格式如下：
{{
    "question": "题目内容",
    "options": {{
        "A": "选项A内容",
        "B": "选项B内容",
        "C": "选项C内容",
        "D": "选项D内容"
    }},
    "answer": "A",
    "explanation": "详细解析内容"
}}
"""
    
    try:
        # 初始化 DeepSeek 客户端
        llm = ChatDeepSeek(
            model="deepseek-chat",
            api_key=settings.DEEPSEEK_API_KEY,
            temperature=0.7
        )
        
        # 调用 API 生成题目
        response = llm.invoke(prompt)
        content = response.content
        
        # 尝试从响应中提取 JSON
        # 如果响应包含代码块，提取其中的 JSON
        if "```json" in content:
            json_start = content.find("```json") + 7
            json_end = content.find("```", json_start)
            json_str = content[json_start:json_end].strip()
        elif "```" in content:
            json_start = content.find("```") + 3
            json_end = content.find("```", json_start)
            json_str = content[json_start:json_end].strip()
        else:
            # 尝试直接解析整个响应
            json_str = content.strip()
        
        # 解析 JSON
        question_data = json.loads(json_str)
        
        return {
            **state,
            "generated_question": question_data
        }
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失败: {str(e)}")
        print(f"响应内容: {content[:500]}")
        return {
            **state,
            "generated_question": None
        }
    except Exception as e:
        print(f"❌ 生成题目失败: {str(e)}")
        return {
            **state,
            "generated_question": None
        }


def human_review_checkpoint(state: WorkflowState) -> WorkflowState:
    """
    节点3：人工审核中断点（Checkpoint）
    将生成的题目状态设为 pending 并存入 AuditTask，暂停等待人工审核
    同时将题目数据保存到 Redis 中，以便审核通过后使用
    """
    generated_question = state.get("generated_question")
    source_document = state.get("source_document")
    
    if not generated_question:
        return {
            **state,
            "audit_task_id": None
        }
    
    # 创建审核任务
    db = SessionLocal()
    try:
        audit_task = AuditTask(
            status=AuditStatus.PENDING,
            comment=None,
            processed_at=None
        )
        db.add(audit_task)
        db.commit()
        db.refresh(audit_task)
        
        audit_task_id = audit_task.id
        
        # 将题目数据保存到 Redis（临时存储，审核通过后使用）
        question_data = {
            "question": generated_question.get("question", ""),
            "options": generated_question.get("options", {}),
            "answer": generated_question.get("answer", ""),
            "explanation": generated_question.get("explanation", ""),
            "source_document": source_document
        }
        redis_key = f"audit_task:{audit_task_id}:question_data"
        redis_client.setex(
            redis_key,
            86400,  # 24小时过期
            json.dumps(question_data, ensure_ascii=False)
        )
        
        return {
            **state,
            "audit_task_id": audit_task_id
        }
    except Exception as e:
        print(f"❌ 创建审核任务失败: {str(e)}")
        db.rollback()
        return {
            **state,
            "audit_task_id": None
        }
    finally:
        db.close()


def should_continue(state: WorkflowState) -> Literal["save", "end"]:
    """
    条件判断：检查审核状态
    如果审核通过，继续到保存节点；否则结束
    """
    approval_status = state.get("approval_status")
    
    if approval_status == "approved":
        return "save"
    elif approval_status == "rejected":
        return "end"
    else:
        # 如果还没有审核结果，返回 end（等待人工审核）
        return "end"


def save_question_from_audit(audit_task_id: int, audit_comment: Optional[str] = None) -> Optional[int]:
    """
    从审核任务保存题目（独立函数，供 API 调用）
    从 Redis 中读取题目数据并保存到 ExamQuestion 表
    返回保存的题目ID，如果失败返回 None
    """
    db = SessionLocal()
    try:
        # 从 Redis 读取题目数据
        redis_key = f"audit_task:{audit_task_id}:question_data"
        question_data_str = redis_client.get(redis_key)
        
        if not question_data_str:
            print(f"❌ 未找到审核任务 {audit_task_id} 的题目数据")
            return None
        
        question_data = json.loads(question_data_str)
        
        # 创建试题记录
        exam_question = ExamQuestion(
            question=question_data.get("question", ""),
            options=json.dumps(question_data.get("options", {}), ensure_ascii=False),
            answer=question_data.get("answer", ""),
            explanation=question_data.get("explanation", ""),
            source_document=question_data.get("source_document"),
            created_at=datetime.utcnow()
        )
        db.add(exam_question)
        db.commit()
        db.refresh(exam_question)
        
        # 更新审核任务，关联试题ID
        audit_task = db.query(AuditTask).filter(AuditTask.id == audit_task_id).first()
        if audit_task:
            audit_task.question_id = exam_question.id
            audit_task.status = AuditStatus.APPROVED
            audit_task.comment = audit_comment
            audit_task.processed_at = datetime.utcnow()
            db.commit()
        
        # 删除 Redis 中的临时数据
        redis_client.delete(redis_key)
        
        print(f"✅ 试题已保存，ID: {exam_question.id}")
        return exam_question.id
    except Exception as e:
        print(f"❌ 保存试题失败: {str(e)}")
        db.rollback()
        return None
    finally:
        db.close()


def save_question(state: WorkflowState) -> WorkflowState:
    """
    节点4：保存通过的题目
    若审核通过，将试题正式写入 ExamQuestion 表
    （此函数保留用于工作流内部调用，但实际保存通过 save_question_from_audit 函数完成）
    """
    # 这个函数在工作流中可能不会被直接调用
    # 实际的保存操作通过 API 调用 save_question_from_audit 完成
    return state


# 构建工作流图
def create_workflow():
    """
    创建 LangGraph 工作流
    """
    workflow = StateGraph(WorkflowState)
    
    # 添加节点
    workflow.add_node("retrieve", retrieve_clauses)
    workflow.add_node("generate", generate_question)
    workflow.add_node("human_review", human_review_checkpoint)
    workflow.add_node("save", save_question)
    
    # 定义边和条件
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", "human_review")
    workflow.add_conditional_edges(
        "human_review",
        should_continue,
        {
            "save": "save",
            "end": END
        }
    )
    workflow.add_edge("save", END)
    
    # 添加检查点（用于人工审核中断）
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    
    return app


# 创建全局工作流实例
workflow_app = create_workflow()

def delete_audit_question_data(audit_task_id: int):
    """
    删除审核任务的临时题目数据（审核拒绝时调用）
    """
    try:
        redis_key = f"audit_task:{audit_task_id}:question_data"
        redis_client.delete(redis_key)
        return True
    except Exception as e:
        print(f"❌ 删除临时数据失败: {str(e)}")
        return False


# 导出保存函数供 API 使用
__all__ = ['workflow_app', 'save_question_from_audit', 'delete_audit_question_data', 'WorkflowState']
