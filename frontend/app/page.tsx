import Link from 'next/link';

export default function Home() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-gray-900 mb-4">
          医药合规试题生成系统
        </h1>
        <p className="text-gray-600 mb-8">智能体管理平台</p>
        <Link
          href="/admin/audit"
          className="inline-block bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 px-6 rounded-lg transition duration-200"
        >
          进入审核管理
        </Link>
      </div>
    </div>
  );
}
