'use client';

import { useState, useEffect, useMemo } from 'react';
import Link from 'next/link';

interface QuestionData {
  question: string;
  options: { A: string; B: string; C: string; D: string };
  answer: string;
  explanation: string;
}

const QUESTION_TYPE_LABELS: Record<string, string> = {
  single_choice: '单选题',
  multiple_choice: '多选题',
  true_false: '判断题',
  subjective: '主观题',
  fill_blank: '填空题',
};

const QUESTION_TYPE_COLORS: Record<string, string> = {
  single_choice: 'bg-blue-100 text-blue-800',
  multiple_choice: 'bg-violet-100 text-violet-800',
  true_false: 'bg-amber-100 text-amber-800',
  subjective: 'bg-teal-100 text-teal-800',
  fill_blank: 'bg-rose-100 text-rose-800',
};

interface ApprovedItem {
  id: number;
  question: QuestionData;
  question_type?: string | null;
  audit_comment: string | null;
  processed_at: string | null;
  created_at: string;
}

type TimeRange = 'all' | 'today' | 'week' | 'month';

export default function ApprovedQuestionsPage() {
  const [list, setList] = useState<ApprovedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [timeRange, setTimeRange] = useState<TimeRange>('all');
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [keywordFilter, setKeywordFilter] = useState('');

  const fetchList = async () => {
    try {
      setLoading(true);
      const res = await fetch('/questions/approved');
      if (!res.ok) throw new Error('获取列表失败');
      const data = await res.json();
      setList(data);
      setMessage(null);
    } catch (e) {
      setMessage({
        type: 'error',
        text: e instanceof Error ? e.message : '获取列表失败',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchList();
  }, []);

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filteredList = useMemo(() => {
    let result = list;
    const now = Date.now();
    const day = 24 * 60 * 60 * 1000;
    if (timeRange === 'today') {
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      result = result.filter((x) => (x.processed_at ? new Date(x.processed_at).getTime() : 0) >= start.getTime());
    } else if (timeRange === 'week') {
      result = result.filter((x) => (x.processed_at ? new Date(x.processed_at).getTime() : 0) >= now - 7 * day);
    } else if (timeRange === 'month') {
      result = result.filter((x) => (x.processed_at ? new Date(x.processed_at).getTime() : 0) >= now - 30 * day);
    }
    if (typeFilter) {
      result = result.filter((x) => (x.question_type || '') === typeFilter);
    }
    const kw = keywordFilter.trim().toLowerCase();
    if (kw) {
      result = result.filter((x) => (x.question?.question || '').toLowerCase().includes(kw));
    }
    return result;
  }, [list, timeRange, typeFilter, keywordFilter]);

  const toggleSelectAll = () => {
    if (selectedIds.size === filteredList.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(filteredList.map((x) => x.id)));
  };

  const exportExcel = async () => {
    if (selectedIds.size === 0) {
      setMessage({ type: 'error', text: '请先勾选要导出的试题' });
      return;
    }
    try {
      setExporting(true);
      setMessage(null);
      const ids = Array.from(selectedIds).join(',');
      const res = await fetch(`/questions/export?ids=${encodeURIComponent(ids)}`);
      if (!res.ok) throw new Error('导出失败');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'approved_questions.xlsx';
      a.click();
      URL.revokeObjectURL(url);
      setMessage({ type: 'success', text: `已导出 ${selectedIds.size} 道试题` });
    } catch (e) {
      setMessage({
        type: 'error',
        text: e instanceof Error ? e.message : '导出失败',
      });
    } finally {
      setExporting(false);
    }
  };

  const detail = detailId ? list.find((x) => x.id === detailId) : null;

  return (
    <div className="min-h-screen bg-slate-50 py-8">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* 头部：渐变感 + 返回链接 */}
        <div className="mb-6 flex flex-wrap justify-between items-center gap-4">
          <div className="flex items-center gap-4">
            <Link
              href="/admin/audit"
              className="text-sm font-medium text-violet-600 hover:text-violet-800 transition-colors"
            >
              ← 返回审核管理
            </Link>
            <h1 className="text-2xl font-bold text-slate-800">
              <span className="text-teal-600">审核通过</span>试题库
            </h1>
          </div>
          <button
            onClick={exportExcel}
            disabled={exporting || selectedIds.size === 0}
            className="px-4 py-2.5 rounded-lg bg-teal-600 hover:bg-teal-700 disabled:bg-slate-300 text-white font-medium text-sm shadow-sm transition-colors"
          >
            {exporting ? '导出中…' : `导出 Excel（已选 ${selectedIds.size}）`}
          </button>
        </div>

        {/* 筛选：时间 / 题型 / 主题 */}
        <div className="mb-6 p-4 rounded-xl bg-white border border-slate-200/80 shadow-sm">
          <div className="flex flex-wrap items-center gap-4">
            <span className="text-sm font-medium text-slate-600">筛选：</span>
            <div className="flex flex-wrap items-center gap-2">
              {(['all', 'today', 'week', 'month'] as const).map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setTimeRange(r)}
                  className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    timeRange === r
                      ? 'bg-slate-700 text-white'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                >
                  {r === 'all' ? '全部' : r === 'today' ? '今天' : r === 'week' ? '近7天' : '近30天'}
                </button>
              ))}
            </div>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-700 bg-white focus:ring-2 focus:ring-slate-400/30 focus:border-slate-400 outline-none"
            >
              <option value="">全部题型</option>
              {Object.entries(QUESTION_TYPE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            <input
              type="text"
              value={keywordFilter}
              onChange={(e) => setKeywordFilter(e.target.value)}
              placeholder="按题目/主题关键词搜索"
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-700 placeholder-slate-400 focus:ring-2 focus:ring-slate-400/30 focus:border-slate-400 outline-none min-w-[160px]"
            />
            <span className="text-xs text-slate-500">
              共 {filteredList.length} 条（全量 {list.length}）
            </span>
          </div>
        </div>

        {message && (
          <div
            className={`mb-4 p-4 rounded-lg border ${
              message.type === 'success'
                ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
                : 'bg-rose-50 text-rose-800 border-rose-200'
            }`}
          >
            {message.text}
          </div>
        )}

        {loading ? (
          <div className="text-center py-16 bg-white rounded-xl border border-slate-200/80 shadow-sm">
            <div className="inline-block w-8 h-8 border-2 border-slate-300 border-t-teal-500 rounded-full animate-spin" />
            <p className="mt-4 text-slate-600">加载中…</p>
          </div>
        ) : list.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-xl border border-slate-200/80 shadow-sm">
            <p className="text-slate-600">暂无审核通过的试题</p>
            <p className="text-slate-400 text-sm mt-2">在审核管理中通过题目后将出现在此处</p>
          </div>
        ) : filteredList.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl border border-slate-200/80 shadow-sm">
            <p className="text-slate-600">当前筛选条件下无结果</p>
            <p className="text-slate-400 text-sm mt-2">可调整时间、题型或关键词</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-slate-200/80 shadow-sm overflow-hidden">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left">
                    <input
                      type="checkbox"
                      checked={filteredList.length > 0 && selectedIds.size === filteredList.length}
                      onChange={toggleSelectAll}
                      className="rounded border-slate-300 text-teal-600 focus:ring-teal-500/40"
                    />
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-slate-700">试题ID</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-slate-700">试题类型</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-slate-700">题目</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-slate-700">正确答案</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-slate-700">审批意见</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-slate-700">审核时间</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-slate-700">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {filteredList.map((item) => (
                  <tr key={item.id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(item.id)}
                        onChange={() => toggleSelect(item.id)}
                        className="rounded border-slate-300 text-teal-600 focus:ring-teal-500/40"
                      />
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-600">{item.id}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${QUESTION_TYPE_COLORS[item.question_type || ''] || 'bg-slate-100 text-slate-600'}`}>
                        {QUESTION_TYPE_LABELS[item.question_type || ''] || item.question_type || '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-800 max-w-md truncate">
                      {item.question.question}
                    </td>
                    <td className="px-4 py-3 text-sm font-medium text-emerald-700">{item.question.answer}</td>
                    <td className="px-4 py-3 text-sm text-slate-600 max-w-xs">
                      {item.audit_comment ? (
                        <span className="line-clamp-2" title={item.audit_comment}>
                          {item.audit_comment}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-500">
                      {item.processed_at
                        ? new Date(item.processed_at).toLocaleString('zh-CN')
                        : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => setDetailId(item.id)}
                        className="text-violet-600 hover:text-violet-800 text-sm font-medium"
                      >
                        查看详情
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 详情/审批意见弹层 */}
      {detail && (
        <div
          className="fixed inset-0 bg-slate-900/60 flex items-center justify-center z-50 p-4"
          onClick={() => setDetailId(null)}
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto p-6 border border-slate-200"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-start mb-4 gap-4">
              <h2 className="text-lg font-bold text-slate-800">试题详情与审批意见</h2>
              {detail?.question_type && (
                <span className={`inline-block px-2.5 py-1 rounded text-sm font-medium ${QUESTION_TYPE_COLORS[detail.question_type] || 'bg-slate-100 text-slate-600'}`}>
                  {QUESTION_TYPE_LABELS[detail.question_type] || detail.question_type}
                </span>
              )}
              <button
                type="button"
                onClick={() => setDetailId(null)}
                className="text-slate-500 hover:text-slate-800 transition-colors"
              >
                关闭
              </button>
            </div>
            <p className="text-slate-800 font-medium mb-3">{detail.question.question}</p>
            {Object.keys(detail.question.options || {}).length > 0 ? (
              <div className="space-y-2 mb-4">
                {Object.entries(detail.question.options || {}).map(([k, v]) => (
                  <div key={k} className="text-sm text-slate-700 p-2 rounded bg-slate-50">
                    <span className="font-semibold text-slate-600">{k}.</span> {v}
                    {String(detail.question.answer).split(',').includes(k) && (
                      <span className="ml-2 text-emerald-600 font-semibold">✓ 正确答案</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="mb-4 p-3 rounded-lg bg-teal-50 border border-teal-100">
                <span className="text-sm font-medium text-slate-600">参考答案：</span>
                <span className="text-sm text-slate-700 whitespace-pre-wrap">{detail.question.answer}</span>
              </div>
            )}
            {detail.question.explanation && (
              <div className="mb-4 p-3 bg-amber-50/60 rounded-lg border border-amber-100">
                <h4 className="font-semibold text-slate-700 mb-1">解析</h4>
                <p className="text-sm text-slate-600 whitespace-pre-wrap">{detail.question.explanation}</p>
              </div>
            )}
            <div className="p-3 bg-violet-50 rounded-lg border border-violet-100">
              <h4 className="font-semibold text-slate-700 mb-1">审批意见</h4>
              <p className="text-sm text-slate-700">{detail.audit_comment || '（无）'}</p>
              <p className="text-xs text-slate-500 mt-2">
                审核时间：{detail.processed_at ? new Date(detail.processed_at).toLocaleString('zh-CN') : '—'}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
