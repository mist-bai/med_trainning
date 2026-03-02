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
from typing import Optional, List, Dict, Any, Tuple
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


# 试题类型枚举（与前端一致）
QUESTION_TYPES = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "true_false": "判断题",
    "subjective": "主观题",
    "fill_blank": "填空题",
}

# 定义状态结构（与参考 GraphState 对应：用户输入 -> 检索 -> 生成）
class WorkflowState(TypedDict):
    question_context: str  # 用户输入的关键词/知识点
    query: str  # 兼容旧字段，与 question_context 同义
    retrieved_clauses: List[Dict[str, Any]]  # 检索到的合规条款
    retrieved_docs: List[str]  # 检索到的文档正文列表
    question_count: int  # 生成试题数量（1-10）
    question_type: str  # 试题类型：single_choice / multiple_choice / true_false / subjective / fill_blank
    generated_question: Optional[Dict[str, Any]]  # 单题（兼容）
    generated_questions: Optional[List[Dict[str, Any]]]  # 多题列表
    audit_task_id: Optional[int]  # 第一个审核任务ID（兼容）
    audit_task_ids: Optional[List[int]]  # 所有审核任务ID
    approval_status: Optional[Literal["approved", "rejected"]]
    audit_comment: Optional[str]
    source_document: Optional[str]
    rejection_reason: Optional[str]


def retrieve_clauses(state: WorkflowState) -> WorkflowState:
    """
    节点1（retrieve_node）：根据用户输入的关键词/知识点去 Qdrant 检索
    使用 question_context（或兼容 query）作为检索词，sentence-transformers 向量检索
    """
    # 优先使用用户输入的关键词/知识点
    question_context = (state.get("question_context") or state.get("query") or "").strip()
    if not question_context:
        question_context = "医药合规法规条款"
    
    # 生成查询向量（384维）
    query_vector = embedding_model.encode(question_context).tolist()
    
    try:
        search_results = qdrant_client.query_points(
            collection_name="med_knowledge_base",
            query=query_vector,
            limit=5,
            with_payload=True,
            with_vectors=False
        )
        
        clauses = []
        doc_texts = []
        for result in search_results.points:
            text = result.payload.get("text", "") if result.payload else ""
            document = result.payload.get("document", "") if result.payload else ""
            clauses.append({
                "text": text,
                "document": document,
                "score": result.score if hasattr(result, 'score') else 0.0
            })
            doc_texts.append(text)
        
        source_doc = clauses[0]["document"] if clauses else None
        return {
            **state,
            "question_context": question_context,
            "query": question_context,
            "retrieved_clauses": clauses,
            "retrieved_docs": doc_texts,
            "source_document": source_doc
        }
    except Exception as e:
        print(f"❌ 检索失败: {str(e)}")
        return {
            **state,
            "question_context": question_context or state.get("question_context", ""),
            "retrieved_clauses": [],
            "retrieved_docs": [],
            "source_document": None
        }


def _type_spec(state: WorkflowState) -> Tuple[str, str]:
    """根据 question_type 返回题型说明和期望的 JSON 格式说明。"""
    qtype = (state.get("question_type") or "single_choice").strip() or "single_choice"
    count = max(1, min(10, int(state.get("question_count") or 1)))
    type_desc = QUESTION_TYPES.get(qtype, "单选题")
    if qtype == "single_choice":
        fmt = '''每道题格式：{"question": "题目", "options": {"A":"...","B":"...","C":"...","D":"..."}, "answer": "A", "explanation": "解析"}'''
    elif qtype == "multiple_choice":
        fmt = '''每道题格式：{"question": "题目", "options": {"A":"...","B":"...","C":"...","D":"..."}, "answer": "A,B,C", "explanation": "解析"}（answer 为多个正确选项字母用逗号连接）'''
    elif qtype == "true_false":
        fmt = '''每道题格式：{"question": "题目", "options": {"正确": "正确", "错误": "错误"}, "answer": "正确" 或 "错误", "explanation": "解析"}'''
    elif qtype == "subjective":
        fmt = '''每道题格式：{"question": "题目", "options": {}, "answer": "参考答案（要点）", "explanation": "解析"}'''
    elif qtype == "fill_blank":
        fmt = '''每道题格式：题目中用 _____ 表示填空；{"question": "题干_____处_____", "options": {}, "answer": "空1:答案1; 空2:答案2", "explanation": "解析"}'''
    else:
        fmt = '''每道题格式：{"question": "题目", "options": {"A":"...","B":"...","C":"...","D":"..."}, "answer": "A", "explanation": "解析"}'''
    return type_desc, fmt


def generate_question(state: WorkflowState) -> WorkflowState:
    """
    节点2（generate_node）：根据检索资料 + 用户要求 + 试题数量与类型，生成题目
    支持：单选、多选、判断、主观题、填空题；可一次生成 1-10 道
    """
    clauses = state.get("retrieved_clauses", [])
    retrieved_docs = state.get("retrieved_docs") or []
    user_req = (state.get("question_context") or state.get("query") or "").strip() or "医药合规法规"
    question_count = max(1, min(10, int(state.get("question_count") or 1)))
    question_type = (state.get("question_type") or "single_choice").strip() or "single_choice"
    
    if not clauses and not retrieved_docs:
        return {
            **state,
            "generated_question": None,
            "generated_questions": [],
        }
    
    if retrieved_docs:
        docs_text = "\n\n".join([f"【资料 {i+1}】\n{t}" for i, t in enumerate(retrieved_docs)])
    else:
        docs_text = "\n\n".join([
            f"【资料 {i+1}】（来源：{c.get('document', '未知')}）\n{c.get('text', '')}"
            for i, c in enumerate(clauses)
        ])
    
    rejection_hint = ""
    if state.get("rejection_reason"):
        rejection_hint = f"\n【重要】上一题被拒绝，请根据以下原因改进：{state.get('rejection_reason')}\n\n"
    
    type_desc, format_desc = _type_spec(state)
    num_hint = f"共生成 {question_count} 道{type_desc}。" if question_count > 1 else f"生成 1 道{type_desc}。"
    
    if question_count > 1:
        output_instruction = f'请以 JSON 格式返回，且只返回一个 JSON 对象，包含键 "questions"，值为数组，数组中有 {question_count} 个对象。' + format_desc
    else:
        output_instruction = '请以 JSON 格式返回，且只返回一个 JSON 对象。' + format_desc
    
    prompt = f"""你是一位医药合规培训专家。请根据以下「参考资料」和「用户特定要求」出题。
{rejection_hint}
## 参考资料
{docs_text}

## 用户特定要求/关键词
{user_req}

## 出题要求
{num_hint}
题型：{type_desc}。题目必须基于上述合规条款，紧扣用户关键词，表述清晰、无歧义，并给出解析。

{output_instruction}

若生成多道题，返回格式示例：{{"questions": [{{"question":"...","options":{{}},"answer":"...","explanation":"..."}}, ...]}}
若只生成一道题，可返回单题对象：{{"question":"...","options":{{}},"answer":"...","explanation":"..."}}
"""
    
    content = ""
    try:
        llm = ChatDeepSeek(
            model="deepseek-chat",
            api_key=settings.DEEPSEEK_API_KEY,
            temperature=0.7
        )
        response = llm.invoke(prompt)
        content = response.content or ""
        
        if "```json" in content:
            json_start = content.find("```json") + 7
            json_end = content.find("```", json_start)
            json_str = content[json_start:json_end].strip()
        elif "```" in content:
            json_start = content.find("```") + 3
            json_end = content.find("```", json_start)
            json_str = content[json_start:json_end].strip()
        else:
            json_str = content.strip()
        
        data = json.loads(json_str)
        questions_list = data.get("questions")
        if questions_list is None and isinstance(data, dict) and "question" in data:
            questions_list = [data]
        if not isinstance(questions_list, list):
            questions_list = [data] if isinstance(data, dict) else []
        
        # 归一化：每题都有 question, options, answer, explanation
        normalized = []
        for q in questions_list[:question_count]:
            if not isinstance(q, dict):
                continue
            normalized.append({
                "question": q.get("question", ""),
                "options": q.get("options") if isinstance(q.get("options"), dict) else {},
                "answer": str(q.get("answer", "")),
                "explanation": q.get("explanation", ""),
            })
        
        return {
            **state,
            "generated_question": normalized[0] if normalized else None,
            "generated_questions": normalized,
        }
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失败: {str(e)}")
        print(f"响应内容: {content[:500]}")
        return {**state, "generated_question": None, "generated_questions": []}
    except Exception as e:
        print(f"❌ 生成题目失败: {str(e)}")
        return {**state, "generated_question": None, "generated_questions": []}


def human_review_checkpoint(state: WorkflowState) -> WorkflowState:
    """
    节点3：人工审核中断点
    将生成的题目（单题或多题）分别创建 AuditTask 并存入 Redis
    """
    generated_questions = state.get("generated_questions")
    if not generated_questions:
        generated_question = state.get("generated_question")
        if generated_question:
            generated_questions = [generated_question]
    source_document = state.get("source_document")
    
    if not generated_questions:
        return {**state, "audit_task_id": None, "audit_task_ids": []}
    
    db = SessionLocal()
    audit_task_ids = []
    try:
        for q in generated_questions:
            audit_task = AuditTask(
                status=AuditStatus.PENDING,
                comment=None,
                processed_at=None
            )
            db.add(audit_task)
            db.commit()
            db.refresh(audit_task)
            tid = audit_task.id
            audit_task_ids.append(tid)
            question_type = state.get("question_type") or "single_choice"
            question_data = {
                "question": q.get("question", ""),
                "options": q.get("options") if isinstance(q.get("options"), dict) else {},
                "answer": str(q.get("answer", "")),
                "explanation": q.get("explanation", ""),
                "source_document": source_document,
                "question_type": question_type,
            }
            redis_key = f"audit_task:{tid}:question_data"
            redis_client.setex(
                redis_key,
                86400,
                json.dumps(question_data, ensure_ascii=False)
            )
        return {
            **state,
            "audit_task_id": audit_task_ids[0] if audit_task_ids else None,
            "audit_task_ids": audit_task_ids,
        }
    except Exception as e:
        print(f"❌ 创建审核任务失败: {str(e)}")
        db.rollback()
        return {**state, "audit_task_id": None, "audit_task_ids": []}
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
        
        # 创建试题记录（含试题类型）
        exam_question = ExamQuestion(
            question=question_data.get("question", ""),
            options=json.dumps(question_data.get("options", {}), ensure_ascii=False),
            answer=question_data.get("answer", ""),
            explanation=question_data.get("explanation", ""),
            source_document=question_data.get("source_document"),
            question_type=question_data.get("question_type") or "single_choice",
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


def list_vectorized_documents(collection_name: str = "med_knowledge_base") -> List[Dict[str, Any]]:
    """
    从 Qdrant 集合中列出已向量化的文档列表，并返回每个文档的主题摘要（首段文本截取）。
    供审核管理界面「已向量化文档」展示使用。
    """
    result = []
    try:
        offset = None
        doc_chunks: Dict[str, List[Dict[str, Any]]] = {}
        limit = 100
        while True:
            points, next_offset = qdrant_client.scroll(
                collection_name=collection_name,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for pt in points or []:
                payload = pt.payload or {}
                doc_name = (payload.get("document") or payload.get("source") or "未知文档").strip()
                if not doc_name:
                    doc_name = "未知文档"
                text = (payload.get("text") or "").strip()
                if doc_name not in doc_chunks:
                    doc_chunks[doc_name] = []
                doc_chunks[doc_name].append({"text": text})
            if next_offset is None:
                break
            offset = next_offset
        for doc_name, chunks in sorted(doc_chunks.items()):
            summary = ""
            if chunks and chunks[0].get("text"):
                raw = chunks[0]["text"].replace("\n", " ").strip()
                summary = (raw[:200] + "…") if len(raw) > 200 else raw
            result.append({
                "document": doc_name,
                "summary": summary or "（暂无摘要）",
                "chunk_count": len(chunks),
            })
    except Exception as e:
        print(f"❌ 获取已向量化文档列表失败: {str(e)}")
    return result


# 导出保存函数供 API 使用
__all__ = [
    'workflow_app', 'save_question_from_audit', 'delete_audit_question_data',
    'WorkflowState', 'list_vectorized_documents',
]
