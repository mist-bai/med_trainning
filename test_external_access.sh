#!/bin/bash
echo "测试外部访问..."
echo "1. 检查端口监听："
ss -tlnp | grep 3000
echo ""
echo "2. 检查 iptables："
iptables -L INPUT -n | grep 3000
echo ""
echo "3. 检查 Docker："
docker ps | grep frontend
echo ""
echo "4. 如果以上都正常，问题很可能是："
echo "   - 云服务器安全组未开放 3000 端口"
echo "   - 需要在阿里云/腾讯云控制台配置安全组规则"
