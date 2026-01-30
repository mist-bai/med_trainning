# 医药合规试题生成智能体 - API 验证指南

## 服务地址
- **后端 API**: http://localhost:8000
- **API 文档 (Swagger)**: http://localhost:8000/docs
- **API 文档 (ReDoc)**: http://localhost:8000/redoc

---

## 快速验证步骤

### 1. 测试服务连接

```bash
curl http://localhost:8000/test_connections
```

**预期响应：**
```json
{
  "redis": "✅ 已连通",
  "mysql": "✅ 已连通",
  "qdrant": "✅ 已连通"
}
```

---

### 2. 查看 API 文档

在浏览器中打开：
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

### 3. 完整功能测试流程

#### 步骤 1: 生成试题

```bash
curl -X POST "http://localhost:8000/api/generate-question" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "医药合规法规条款"
  }'
```

**预期响应：**
```json
{
  "task_id": "uuid-string",
  "audit_task_id": 1,
  "message": "题目已生成，等待人工审核",
  "generated_question": {
    "question": "题目内容...",
    "options": {
      "A": "选项A",
      "B": "选项B",
      "C": "选项C",
      "D": "选项D"
    },
    "answer": "A",
    "explanation": "解析内容..."
  }
}
```

**保存 `audit_task_id`，后续步骤需要使用！**

---

#### 步骤 2: 查看待审核任务列表

```bash
curl "http://localhost:8000/api/audit-tasks?status=pending"
```

**查看所有审核任务：**
```bash
curl "http://localhost:8000/api/audit-tasks"
```

---

#### 步骤 3: 查看单个审核任务详情

```bash
# 替换 {task_id} 为步骤1中获得的 audit_task_id
curl "http://localhost:8000/api/audit-tasks/{task_id}"
```

**示例：**
```bash
curl "http://localhost:8000/api/audit-tasks/1"
```

---

#### 步骤 4: 审核题目（批准）

```bash
# 替换 {task_id} 为实际的审核任务ID
curl -X POST "http://localhost:8000/api/audit-tasks/{task_id}/review" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "approved",
    "comment": "题目质量良好，符合要求"
  }'
```

**拒绝题目：**
```bash
curl -X POST "http://localhost:8000/api/audit-tasks/{task_id}/review" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "rejected",
    "comment": "题目不符合要求"
  }'
```

---

#### 步骤 5: 查看已审核通过的题目列表

```bash
curl "http://localhost:8000/api/questions"
```

**分页查询：**
```bash
curl "http://localhost:8000/api/questions?skip=0&limit=20"
```

---

#### 步骤 6: 查看单个题目详情

```bash
# 替换 {question_id} 为实际的题目ID
curl "http://localhost:8000/api/questions/{question_id}"
```

---

## 使用测试脚本

项目根目录提供了自动化测试脚本：

```bash
cd /var/www/med-training
./test_api.sh
```

该脚本会自动执行完整的测试流程。

---

## 使用 Postman 或类似工具

### 导入 API 定义

1. 访问 http://localhost:8000/openapi.json 获取 OpenAPI 规范
2. 在 Postman 中导入该 JSON 文件
3. 开始测试各个端点

---

## 常见问题

### 1. 连接失败
- 检查服务是否运行：`docker-compose ps`
- 查看日志：`docker logs med_backend`

### 2. 生成题目失败
- 检查 Qdrant 连接：`curl http://localhost:8000/test_connections`
- 检查 DeepSeek API Key 是否正确配置
- 查看日志：`docker logs -f med_backend`

### 3. 审核任务不存在
- 确保先执行了生成题目的步骤
- 检查审核任务ID是否正确

---

## API 端点总结

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/test_connections` | 测试服务连接 |
| POST | `/api/generate-question` | 生成试题 |
| GET | `/api/audit-tasks` | 获取审核任务列表 |
| GET | `/api/audit-tasks/{id}` | 获取单个审核任务 |
| POST | `/api/audit-tasks/{id}/review` | 审核题目 |
| GET | `/api/questions` | 获取已审核通过的题目列表 |
| GET | `/api/questions/{id}` | 获取单个题目详情 |
| GET | `/docs` | Swagger UI 文档 |
| GET | `/redoc` | ReDoc 文档 |

---

## 示例：完整测试流程

```bash
# 1. 测试连接
curl http://localhost:8000/test_connections

# 2. 生成题目
RESPONSE=$(curl -s -X POST "http://localhost:8000/api/generate-question" \
  -H "Content-Type: application/json" \
  -d '{"query": "医药合规法规条款"}')

# 3. 提取审核任务ID（需要安装 jq）
TASK_ID=$(echo $RESPONSE | jq -r '.audit_task_id')

# 4. 查看审核任务
curl "http://localhost:8000/api/audit-tasks/$TASK_ID"

# 5. 批准题目
curl -X POST "http://localhost:8000/api/audit-tasks/$TASK_ID/review" \
  -H "Content-Type: application/json" \
  -d '{"status": "approved", "comment": "测试通过"}'

# 6. 查看已保存的题目
curl "http://localhost:8000/api/questions"
```

---

## 注意事项

1. **审核任务ID**：生成题目后会返回 `audit_task_id`，后续审核步骤需要使用
2. **题目状态**：
   - `pending`: 待审核
   - `approved`: 已批准（题目已保存到数据库）
   - `rejected`: 已拒绝
3. **题目数据存储**：
   - 待审核题目：存储在 Redis 中
   - 已审核通过：存储在 MySQL 的 `exam_questions` 表中
