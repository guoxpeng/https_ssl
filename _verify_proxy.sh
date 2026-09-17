#!/bin/bash
# 反向代理真机验证：签发证书 -> 建反代 -> 启用 -> 通过 nginx 访问 -> 清理。
#   docker exec -e QILIN_PASS='你的密码' qilin_ssl bash /app/_verify_proxy.sh
# 注意：proxy_url 里的端口需要已在 docker-compose.yml 中映射出来。
set -u

B=${QILIN_BASE:-http://127.0.0.1:2002}
USER_NAME=${QILIN_USER:-admin}
PASS=${QILIN_PASS:-}
NAME=${QILIN_PROXY_NAME:-https-ssl-proxy-test}
HOST=${QILIN_PROXY_HOST:-https-ssl-proxy-test.local}
PORT=${QILIN_PROXY_PORT:-14000}
SITE_DIR=${QILIN_PROXY_DIR:-/app/proxy}/sites
CJ=$(mktemp)
trap 'rm -f "$CJ"' EXIT

if [ -z "$PASS" ]; then
    echo "请通过 QILIN_PASS 提供面板密码，例如："
    echo "  QILIN_PASS='你的密码' bash _verify_proxy.sh"
    exit 1
fi

echo '--- 登录 ---'
code=$(curl -s -c "$CJ" -o /dev/null -w '%{http_code}' -X POST "$B/login" \
    --data-urlencode "username=$USER_NAME" --data-urlencode "password=$PASS")
echo "login=$code（302 为成功）"

echo '--- 准备证书 ---'
curl -s -b "$CJ" -X POST "$B/create_cert" \
    --data-urlencode "cert_name=$NAME" \
    --data-urlencode 'ip_addresses=127.0.0.1' \
    --data-urlencode "domains=$HOST" \
    --data-urlencode 'cert_password=' \
    -o /dev/null -w 'create_cert=%{http_code}\n'

echo '--- 创建反向代理 ---'
curl -s -b "$CJ" -X POST "$B/create_proxy" \
    --data-urlencode "service_name=$NAME" \
    --data-urlencode "original_url=$B" \
    --data-urlencode "proxy_url=https://$HOST:$PORT" \
    --data-urlencode 'cert_type=qilin' \
    --data-urlencode "cert_id=$NAME"
echo

echo '--- 反代列表 ---'
curl -s -b "$CJ" "$B/get_proxy_list" | head -c 600; echo

echo '--- 启用服务 ---'
curl -s -b "$CJ" -X POST "$B/run_proxy" \
    -H 'Content-Type: application/json' -d "{\"proxy_id\":\"$NAME\"}"
echo

sleep 2
echo "--- 监听 $PORT ---"
ss -ltnp 2>/dev/null | grep ":$PORT" || echo "NO_LISTEN_$PORT"

echo '--- 站点配置 ---'
ls -l "$SITE_DIR" 2>/dev/null

echo '--- 经 nginx 访问（完整链路）---'
curl -s --resolve "$HOST:$PORT:127.0.0.1" --cacert /app/ca/qilin-ca.crt \
    -o /dev/null -w 'with_ca=%{http_code}\n' "https://$HOST:$PORT/login"

curl -s --resolve "$HOST:$PORT:127.0.0.1" \
    -o /dev/null -w 'no_ca=%{http_code}（000 为预期：未信任 CA 时被拒）\n' \
    "https://$HOST:$PORT/login"

echo '--- 停用并清理 ---'
curl -s -b "$CJ" -X POST "$B/stop_proxy" \
    -H 'Content-Type: application/json' -d "{\"proxy_id\":\"$NAME\"}"
echo
curl -s -b "$CJ" -X POST "$B/delete_proxy" \
    -H 'Content-Type: application/json' -d "{\"proxy_ids\":[\"$NAME\"]}"
echo
curl -s -b "$CJ" -X POST "$B/delete_certs" \
    -H 'Content-Type: application/json' -d "{\"cert_names\":[\"$NAME\"]}"
echo
