import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '医药合规试题生成系统',
  description: '医药合规试题生成智能体管理平台',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
