#!/bin/bash
echo "=== 端口 3000 访问诊断 ==="
echo ""

echo "1. 检查端口监听状态："
netstat -tunlp | grep 3000
echo ""

echo "2. 检查 Docker 容器状态："
docker ps | grep frontend
echo ""

echo "3. 检查 iptables 规则："
iptables -L INPUT -n | grep 3000
echo ""

echo "4. 检查 Next.js 服务状态："
docker logs --tail=5 med_frontend 2>&1 | grep -E "Local|Network|Ready|ready"
echo ""

echo "5. 测试本地访问："
curl -s -I http://localhost:3000 2>&1 | head -3
echo ""

echo "6. 服务器 IP 信息："
echo "   内网 IP: $(hostname -I | awk '{print $1}')"
echo "   公网 IP: $(curl -s ifconfig.me 2>/dev/null || echo '无法获取')"
echo ""

echo "7. 防火墙规则位置："
iptables -L INPUT -n --line-numbers | grep -E "3000|REJECT" | head -3
echo ""

echo "=== 诊断完成 ==="
echo ""
echo "如果仍无法从公网访问，请检查："
echo "1. 阿里云/腾讯云等云服务器的安全组是否开放了 3000 端口"
echo "2. 安全组规则："
echo "   方向：入方向"
echo "   协议：TCP"
echo "   端口：3000"
echo "   授权对象：0.0.0.0/0（或你的 IP）"
echo ""
echo "3. 测试命令（在本地执行）："
echo "   curl -v http://8.140.248.43:3000"
