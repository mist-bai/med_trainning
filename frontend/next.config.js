/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Docker 内用服务名 backend，本地开发可用 NEXT_PUBLIC_API_HOST 覆盖
    const apiHost = process.env.NEXT_PUBLIC_API_HOST || 'backend:8000';
    return [
      {
        source: '/api/:path*',
        destination: `http://${apiHost}/api/:path*`,
      },
      {
        source: '/agent/:path*',
        destination: `http://${apiHost}/agent/:path*`,
      },
      {
        source: '/audit/:path*',
        destination: `http://${apiHost}/audit/:path*`,
      },
      {
        source: '/questions/:path*',
        destination: `http://${apiHost}/questions/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
