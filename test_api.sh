#!/bin/bash
# 医药合规试题生成智能体 API 测试脚本

BASE_URL="http://localhost:8000"

echo "=========================================="
echo "医药合规试题生成智能体 API 测试"
echo "=========================================="
echo ""

# 1. 测试连接
echo "1. 测试服务连接..."
curl -s "${BASE_URL}/test_connections" | python3 -m json.tool
echo ""
echo ""

# 2. 查看 API 文档
echo "2. API 文档地址:"
echo "   ${BASE_URL}/docs (Swagger UI)"
echo "   ${BASE_URL}/redoc (ReDoc)"
echo ""
echo ""

# 3. 生成试题
echo "3. 生成试题（触发工作流）..."
RESPONSE=$(curl -s -X POST "${BASE_URL}/api/generate-question" \
  -H "Content-Type: application/json" \
  -d '{"query": "医药合规法规条款"}')

echo "$RESPONSE" | python3 -m json.tool
echo ""

# 提取 audit_task_id
AUDIT_TASK_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('audit_task_id', ''))" 2>/dev/null)

if [ -n "$AUDIT_TASK_ID" ]; then
  echo "✅ 题目已生成，审核任务 ID: $AUDIT_TASK_ID"
  echo ""
  
  # 4. 查看待审核任务
  echo "4. 查看待审核任务列表..."
  curl -s "${BASE_URL}/api/audit-tasks?status=pending" | python3 -m json.tool
  echo ""
  echo ""
  
  # 5. 查看单个审核任务详情
  echo "5. 查看审核任务详情 (ID: $AUDIT_TASK_ID)..."
  curl -s "${BASE_URL}/api/audit-tasks/${AUDIT_TASK_ID}" | python3 -m json.tool
  echo ""
  echo ""
  
  echo "6. 审核题目（批准）..."
  echo "   执行以下命令批准题目："
  echo "   curl -X POST \"${BASE_URL}/api/audit-tasks/${AUDIT_TASK_ID}/review\" \\"
  echo "     -H \"Content-Type: application/json\" \\"
  echo "     -d '{\"status\": \"approved\", \"comment\": \"题目质量良好\"}'"
  echo ""
  echo ""
  
  echo "7. 查看已审核通过的题目..."
  curl -s "${BASE_URL}/api/questions" | python3 -m json.tool
  echo ""
else
  echo "❌ 生成题目失败，请检查日志"
fi

echo ""
echo "=========================================="
echo "测试完成"
echo "=========================================="
