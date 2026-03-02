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
  /** 拒绝原因（按任务 ID），将发送给 DeepSeek 用于重新生成题目 */
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>({});
  /** 用户输入的关键词或知识点，用于检索知识库并生成题目 */
  const [keywordInput, setKeywordInput] = useState('');
  /** 生成试题数量（1-10） */
  const [questionCount, setQuestionCount] = useState(1);
  /** 试题类型 */
  const [questionType, setQuestionType] = useState<string>('single_choice');
  /** 是否正在生成试题（仅按钮 Loading，不阻塞列表） */
  const [generating, setGenerating] = useState(false);
  /** 待审批任务勾选（用于全选一键通过） */
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<number>>(new Set());
  /** 是否正在批量审批 */
  const [batchApproving, setBatchApproving] = useState(false);
  /** 已向量化文档列表（知识库） */
  const [docList, setDocList] = useState<{ document: string; summary: string; chunk_count: number }[]>([]);
  const [docListLoading, setDocListLoading] = useState(false);
  const [docListOpen, setDocListOpen] = useState(false);

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

      const comment =
        status === 'approved'
          ? '审核通过'
          : (rejectReasons[taskId]?.trim() || '未填写原因（将发送给 DeepSeek 重新生成题目）');

      const response = await fetch(`/audit/action/${taskId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          status,
          comment,
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
        setRejectReasons((prev) => {
          const next = { ...prev };
          delete next[taskId];
          return next;
        });
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

  const toggleSelectTask = (taskId: number) => {
    setSelectedTaskIds((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  };

  const toggleSelectAllTasks = () => {
    if (auditList.length === 0) return;
    if (selectedTaskIds.size === auditList.length) setSelectedTaskIds(new Set());
    else setSelectedTaskIds(new Set(auditList.map((x) => x.id)));
  };

  const handleBatchApprove = async () => {
    const ids = selectedTaskIds.size > 0 ? Array.from(selectedTaskIds) : auditList.map((x) => x.id);
    if (ids.length === 0) return;
    setBatchApproving(true);
    setMessage(null);
    let ok = 0;
    let err = '';
    for (const taskId of ids) {
      try {
        const res = await fetch(`/audit/action/${taskId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'approved', comment: '审核通过' }),
        });
        const data = await res.json();
        if (data.success) ok++;
        else err = data.message || '审核失败';
      } catch (e) {
        err = e instanceof Error ? e.message : '请求失败';
      }
    }
    setSelectedTaskIds(new Set());
    await fetchAuditList();
    setMessage({
      type: err ? 'error' : 'success',
      text: err ? err : `已一键通过 ${ok} 道题目`,
    });
    setBatchApproving(false);
  };

  // 触发智能体生成题目（仅按钮 Loading）
  const handleStartAgent = async () => {
    try {
      setGenerating(true);
      setMessage(null);

      const query = keywordInput.trim() || '医药合规法规条款';
      const count = Math.max(1, Math.min(10, questionCount));
      const response = await fetch('/agent/start', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query,
          question_count: count,
          question_type: questionType || 'single_choice',
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
      setGenerating(false);
    }
  };

  const fetchDocList = async () => {
    setDocListLoading(true);
    try {
      const res = await fetch('/documents/vectorized');
      if (res.ok) {
        const data = await res.json();
        setDocList(data);
      }
    } catch {
      setDocList([]);
    } finally {
      setDocListLoading(false);
    }
  };

  useEffect(() => {
    fetchAuditList();
    const interval = setInterval(fetchAuditList, 5000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (docListOpen) fetchDocList();
  }, [docListOpen]);

  return (
    <div className="min-h-screen bg-slate-50 py-8">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* 顶部：深蓝+琥珀标题 + 亮白区域 */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold text-slate-800 tracking-tight">
              <span className="text-amber-600">审核</span>管理
            </h1>
            <a
              href="/admin/questions"
              className="text-sm font-medium text-violet-600 hover:text-violet-800 transition-colors"
            >
              审核通过试题库 →
            </a>
          </div>

          {/* 大 Textarea 输入区 */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200/80 p-5 mb-4">
            <label className="block text-sm font-medium text-slate-700 mb-2">
              题目主题 / 合规条款关键词
            </label>
            <textarea
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              placeholder="请输入题目主题或相关合规条款关键词（如：招待标准、学术会议禁令）"
              rows={3}
              disabled={generating}
              className="w-full px-4 py-3 rounded-lg border border-slate-200 text-slate-800 placeholder-slate-400 focus:ring-2 focus:ring-slate-400/30 focus:border-slate-400 outline-none transition disabled:bg-slate-50 disabled:text-slate-500"
            />
            <div className="flex flex-wrap items-center gap-4 mt-4">
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <span>生成数量</span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={questionCount}
                  onChange={(e) => setQuestionCount(Math.max(1, Math.min(10, parseInt(e.target.value, 10) || 1)))}
                  disabled={generating}
                  className="w-14 px-2 py-1.5 rounded border border-slate-200 text-slate-800 text-center text-sm focus:ring-2 focus:ring-slate-400/30 focus:border-slate-400 outline-none disabled:bg-slate-50"
                />
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <span>试题类型</span>
                <select
                  value={questionType}
                  onChange={(e) => setQuestionType(e.target.value)}
                  disabled={generating}
                  className="px-3 py-1.5 rounded border border-slate-200 text-slate-800 text-sm bg-white focus:ring-2 focus:ring-slate-400/30 focus:border-slate-400 outline-none disabled:bg-slate-50"
                >
                  <option value="single_choice">单选题</option>
                  <option value="multiple_choice">多选题</option>
                  <option value="true_false">判断题</option>
                  <option value="subjective">主观题</option>
                  <option value="fill_blank">填空题</option>
                </select>
              </label>
              <button
                onClick={handleStartAgent}
                disabled={generating}
                className="inline-flex items-center justify-center gap-2 min-w-[140px] px-5 py-2.5 rounded-lg bg-teal-600 hover:bg-teal-700 disabled:bg-slate-400 disabled:cursor-not-allowed text-white font-medium text-sm shadow-sm transition-colors"
              >
                {generating ? (
                  <>
                    <span className="inline-block w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                    生成中…
                  </>
                ) : (
                  '开始生成试题'
                )}
              </button>
            </div>
          </div>

          {/* 已向量化文档列表（可展开） */}
          <div className="mb-6 rounded-xl border border-slate-200/80 bg-white shadow-sm overflow-hidden">
            <button
              type="button"
              onClick={() => setDocListOpen((v) => !v)}
              className="w-full flex items-center justify-between px-5 py-3 text-left text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
            >
              <span className="text-violet-600">已向量化文档（知识库）</span>
              <span className="text-slate-400">{docListOpen ? '收起' : '展开查看'}</span>
            </button>
            {docListOpen && (
              <div className="border-t border-slate-200/80 px-5 py-4 bg-slate-50/50">
                {docListLoading ? (
                  <div className="flex items-center gap-2 text-slate-500 text-sm">
                    <span className="inline-block w-4 h-4 border-2 border-slate-300 border-t-violet-500 rounded-full animate-spin" />
                    加载中…
                  </div>
                ) : docList.length === 0 ? (
                  <p className="text-slate-500 text-sm">暂无已向量化文档，或知识库未就绪。</p>
                ) : (
                  <ul className="space-y-3">
                    {docList.map((doc, i) => (
                      <li
                        key={i}
                        className="p-3 rounded-lg bg-white border border-slate-200/80 shadow-sm"
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-medium text-violet-600 bg-violet-50 px-2 py-0.5 rounded">
                            {doc.document}
                          </span>
                          <span className="text-xs text-slate-400">共 {doc.chunk_count} 个分块</span>
                        </div>
                        <p className="text-sm text-slate-600 line-clamp-2">{doc.summary}</p>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-3 text-xs text-slate-400">
                  后续将增加上传页面，配合 med_upload 工具扫描文件路径自动上传并向量化。
                </p>
              </div>
            )}
          </div>
        </div>

        {/* 消息提示 */}
        {message && (
          <div
            className={`mb-6 p-4 rounded-lg border ${
              message.type === 'success'
                ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
                : 'bg-red-50 text-red-800 border-red-200'
            }`}
          >
            {message.text}
          </div>
        )}

        {/* 待审核列表 */}
        {loading && auditList.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-xl shadow-sm border border-slate-200/80">
            <div className="inline-block w-8 h-8 border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin" />
            <p className="mt-4 text-slate-600">加载中…</p>
          </div>
        ) : auditList.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-xl shadow-sm border border-slate-200/80">
            <p className="text-slate-600 text-lg">暂无待审核题目</p>
            <p className="text-slate-400 text-sm mt-2">在上方输入主题或关键词后，点击「开始生成试题」</p>
          </div>
        ) : (
          <div className="space-y-6">
            {/* 全选 + 一键审批通过 工具栏 */}
            <div className="flex flex-wrap items-center gap-3 p-4 rounded-xl bg-white border border-slate-200/80 shadow-sm">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={auditList.length > 0 && selectedTaskIds.size === auditList.length}
                  onChange={toggleSelectAllTasks}
                  disabled={batchApproving}
                  className="rounded border-slate-300 text-amber-600 focus:ring-amber-500/40"
                />
                <span className="text-sm font-medium text-slate-700">全选</span>
              </label>
              <button
                type="button"
                onClick={handleBatchApprove}
                disabled={batchApproving}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-500 hover:bg-amber-600 disabled:bg-slate-300 text-white font-medium text-sm shadow-sm transition-colors"
              >
                {batchApproving ? (
                  <>
                    <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                    审批中…
                  </>
                ) : (
                  `一键审批通过${selectedTaskIds.size > 0 ? `（${selectedTaskIds.size}）` : ''}`
                )}
              </button>
              <span className="text-xs text-slate-500">
                共 {auditList.length} 道待审核 · 未勾选时点击将全部通过
              </span>
            </div>

            {auditList.map((item) => (
              <div
                key={item.id}
                className="bg-white rounded-xl shadow-sm border border-slate-200/80 p-6 hover:shadow-lg hover:border-amber-200/60 transition-all duration-200"
              >
                <div className="flex justify-between items-start gap-4 mb-4">
                  <label className="flex items-center gap-2 shrink-0 cursor-pointer select-none pt-0.5">
                    <input
                      type="checkbox"
                      checked={selectedTaskIds.has(item.id)}
                      onChange={() => toggleSelectTask(item.id)}
                      disabled={batchApproving || processing === item.id}
                      className="rounded border-slate-300 text-amber-600 focus:ring-amber-500/40"
                    />
                    <span className="text-xs font-medium text-slate-500">勾选通过</span>
                  </label>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-3 flex-wrap">
                      <span className="text-xs font-semibold text-amber-700 bg-amber-100 px-2.5 py-1 rounded-md">
                        任务 #{item.id}
                      </span>
                      <span className="text-xs text-slate-400">
                        {new Date(item.created_at).toLocaleString('zh-CN')}
                      </span>
                    </div>
                    <h3 className="text-base font-semibold text-slate-800 leading-snug">
                      {item.question.question}
                    </h3>
                  </div>
                </div>

                {/* 选项：较小字号 */}
                <div className="space-y-2 mb-4">
                  {Object.keys(item.question.options || {}).length > 0 ? (
                    Object.entries(item.question.options || {}).map(([key, value]) => (
                      <div
                        key={key}
                        className={`p-3 rounded-lg border text-sm ${
                          String(item.question.answer).split(',').includes(key)
                            ? 'bg-emerald-50/80 border-emerald-200 text-slate-800'
                            : 'bg-slate-50/80 border-slate-200 text-slate-700'
                        }`}
                      >
                        <span className="font-medium text-slate-600">{key}.</span>{' '}
                        <span>{value}</span>
                        {String(item.question.answer).split(',').includes(key) && (
                          <span className="ml-2 text-emerald-600 font-medium text-xs">✓ 正确答案</span>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="p-3 rounded-lg border border-slate-200 bg-slate-50/80 text-sm text-slate-700">
                      <span className="font-medium text-slate-600">参考答案：</span>
                      <span className="whitespace-pre-wrap">{item.question.answer}</span>
                    </div>
                  )}
                </div>

                {/* 解析 */}
                {item.question.explanation && (
                  <div className="mb-4 p-4 rounded-lg border border-slate-200 bg-slate-50/50">
                    <h4 className="text-sm font-semibold text-slate-700 mb-2">解析</h4>
                    <p className="text-sm text-slate-600 whitespace-pre-wrap leading-relaxed">
                      {item.question.explanation}
                    </p>
                  </div>
                )}

                {/* 拒绝原因 */}
                <div className="mb-4">
                  <label className="block text-sm font-medium text-slate-600 mb-1">
                    拒绝原因（选填，将发送给 DeepSeek 重新生成）
                  </label>
                  <textarea
                    value={rejectReasons[item.id] ?? ''}
                    onChange={(e) =>
                      setRejectReasons((prev) => ({ ...prev, [item.id]: e.target.value }))
                    }
                    placeholder="例如：选项表述不清、与条款不符……"
                    className="w-full px-3 py-2 rounded-lg border border-slate-200 text-sm text-slate-700 placeholder-slate-400 focus:ring-2 focus:ring-slate-400/30 focus:border-slate-400 outline-none"
                    rows={2}
                  />
                </div>

                {/* 操作按钮：绿/红 + 琥珀点缀 */}
                <div className="flex gap-3 pt-4 border-t border-slate-200">
                  <button
                    onClick={() => handleAudit(item.id, 'approved')}
                    disabled={processing === item.id || batchApproving}
                    className="flex-1 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-300 text-white font-medium text-sm transition-colors shadow-sm"
                  >
                    {processing === item.id ? '处理中…' : '✓ 通过'}
                  </button>
                  <button
                    onClick={() => handleAudit(item.id, 'rejected')}
                    disabled={processing === item.id || batchApproving}
                    className="flex-1 py-2.5 rounded-lg bg-rose-600 hover:bg-rose-700 disabled:bg-slate-300 text-white font-medium text-sm transition-colors shadow-sm"
                  >
                    {processing === item.id ? '处理中…' : '✗ 拒绝并重新生成'}
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
