'use client';

import { useState, useEffect } from 'react';

interface Question {
  question: string;
  options: {
    A: string;
    B: string;
    C: string;
    D: string;
  };
  answer: string;
  explanation: string;
}

interface AuditItem {
  id: number;
  question: Question;
  created_at: string;
}

export default function AuditPage() {
  const [auditList, setAuditList] = useState<AuditItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState<number | null>(null);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // 获取待审核列表
  const fetchAuditList = async () => {
    try {
      setLoading(true);
      const response = await fetch('/audit/list');
      if (!response.ok) {
        throw new Error('获取审核列表失败');
      }
      const data = await response.json();
      setAuditList(data);
      setMessage(null);
    } catch (error) {
      setMessage({
        type: 'error',
        text: error instanceof Error ? error.message : '获取审核列表失败',
      });
    } finally {
      setLoading(false);
    }
  };

  // 处理审核操作
  const handleAudit = async (taskId: number, status: 'approved' | 'rejected') => {
    try {
      setProcessing(taskId);
      setMessage(null);

      const response = await fetch(`/audit/action/${taskId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          status,
          comment: status === 'approved' ? '审核通过' : '审核拒绝',
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.message || '审核操作失败');
      }

      const data = await response.json();
      
      if (data.success) {
        setMessage({
          type: 'success',
          text: data.message,
        });
        // 刷新列表
        await fetchAuditList();
      } else {
        throw new Error(data.message || '审核操作失败');
      }
    } catch (error) {
      setMessage({
        type: 'error',
        text: error instanceof Error ? error.message : '审核操作失败',
      });
    } finally {
      setProcessing(null);
    }
  };

  // 触发智能体生成题目
  const handleStartAgent = async () => {
    try {
      setLoading(true);
      setMessage(null);

      const response = await fetch('/agent/start', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: '医药合规法规条款',
        }),
      });

      if (!response.ok) {
        throw new Error('启动智能体失败');
      }

      const data = await response.json();
      
      if (data.success) {
        setMessage({
          type: 'success',
          text: data.message,
        });
        // 刷新列表
        await fetchAuditList();
      } else {
        throw new Error(data.message || '启动智能体失败');
      }
    } catch (error) {
      setMessage({
        type: 'error',
        text: error instanceof Error ? error.message : '启动智能体失败',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAuditList();
    // 每5秒自动刷新一次
    const interval = setInterval(fetchAuditList, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 py-8">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* 头部 */}
        <div className="mb-8">
          <div className="flex justify-between items-center">
            <h1 className="text-3xl font-bold text-gray-900">审核管理</h1>
            <button
              onClick={handleStartAgent}
              disabled={loading}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white font-semibold py-2 px-4 rounded-lg transition duration-200"
            >
              {loading ? '生成中...' : '生成新题目'}
            </button>
          </div>
        </div>

        {/* 消息提示 */}
        {message && (
          <div
            className={`mb-4 p-4 rounded-lg ${
              message.type === 'success'
                ? 'bg-green-100 text-green-800 border border-green-300'
                : 'bg-red-100 text-red-800 border border-red-300'
            }`}
          >
            {message.text}
          </div>
        )}

        {/* 待审核列表 */}
        {loading && auditList.length === 0 ? (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            <p className="mt-4 text-gray-600">加载中...</p>
          </div>
        ) : auditList.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-lg shadow">
            <p className="text-gray-500 text-lg">暂无待审核题目</p>
            <p className="text-gray-400 text-sm mt-2">点击"生成新题目"按钮开始生成</p>
          </div>
        ) : (
          <div className="space-y-6">
            {auditList.map((item) => (
              <div
                key={item.id}
                className="bg-white rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow"
              >
                <div className="flex justify-between items-start mb-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="bg-blue-100 text-blue-800 text-xs font-semibold px-2.5 py-0.5 rounded">
                        任务 #{item.id}
                      </span>
                      <span className="text-sm text-gray-500">
                        {new Date(item.created_at).toLocaleString('zh-CN')}
                      </span>
                    </div>
                    <h3 className="text-lg font-semibold text-gray-900 mb-4">
                      {item.question.question}
                    </h3>
                  </div>
                </div>

                {/* 选项 */}
                <div className="space-y-2 mb-4">
                  {Object.entries(item.question.options).map(([key, value]) => (
                    <div
                      key={key}
                      className={`p-3 rounded border ${
                        item.question.answer === key
                          ? 'bg-green-50 border-green-300'
                          : 'bg-gray-50 border-gray-200'
                      }`}
                    >
                      <span className="font-semibold text-gray-700">{key}.</span>{' '}
                      <span className="text-gray-700">{value}</span>
                      {item.question.answer === key && (
                        <span className="ml-2 text-green-600 font-semibold">✓ 正确答案</span>
                      )}
                    </div>
                  ))}
                </div>

                {/* 解析 */}
                {item.question.explanation && (
                  <div className="mb-4 p-4 bg-gray-50 rounded border border-gray-200">
                    <h4 className="font-semibold text-gray-900 mb-2">解析：</h4>
                    <p className="text-gray-700 whitespace-pre-wrap">
                      {item.question.explanation}
                    </p>
                  </div>
                )}

                {/* 操作按钮 */}
                <div className="flex gap-3 pt-4 border-t border-gray-200">
                  <button
                    onClick={() => handleAudit(item.id, 'approved')}
                    disabled={processing === item.id}
                    className="flex-1 bg-green-600 hover:bg-green-700 disabled:bg-gray-400 text-white font-semibold py-2 px-4 rounded-lg transition duration-200"
                  >
                    {processing === item.id ? '处理中...' : '✓ 通过'}
                  </button>
                  <button
                    onClick={() => handleAudit(item.id, 'rejected')}
                    disabled={processing === item.id}
                    className="flex-1 bg-red-600 hover:bg-red-700 disabled:bg-gray-400 text-white font-semibold py-2 px-4 rounded-lg transition duration-200"
                  >
                    {processing === item.id ? '处理中...' : '✗ 拒绝'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
