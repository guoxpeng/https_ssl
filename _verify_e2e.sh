#!/bin/bash
# 证书链路真机验证：对运行中的面板执行一遍签发与验证流程。
#   docker exec -e QILIN_PASS='你的密码' qilin_ssl bash /app/_verify_e2e.sh
set -u

B=${QILIN_BASE:-http://127.0.0.1:2002}
USER_NAME=${QILIN_USER:-admin}
PASS=${QILIN_PASS:-}
CERT=${QILIN_CERT:-https-ssl-e2e}
CJ=$(mktemp)
trap 'rm -f "$CJ"' EXIT

if [ -z "$PASS" ]; then
    echo "请通过 QILIN_PASS 提供面板密码，例如："
    echo "  QILIN_PASS='你的密码' bash _verify_e2e.sh"
    exit 1
fi

echo '--- 登录 ---'
code=$(curl -s -c "$CJ" -o /dev/null -w '%{http_code}' -X POST "$B/login" \
    --data-urlencode "username=$USER_NAME" --data-urlencode "password=$PASS")
echo "login=$code（302 为成功）"

echo '--- CA 密码状态 ---'
curl -s -b "$CJ" "$B/check_ca_password"; echo

echo '--- 签发证书 ---'
curl -s -b "$CJ" -X POST "$B/create_cert" \
    --data-urlencode "cert_name=$CERT" \
    --data-urlencode 'ip_addresses=127.0.0.1' \
    --data-urlencode "domains=$CERT.local" \
    --data-urlencode 'cert_password=' \
    -o /tmp/cc.out -w 'HTTP=%{http_code}\n'
head -c 200 /tmp/cc.out; echo

echo '--- 证书列表 ---'
curl -s -b "$CJ" -H 'Accept: application/json' "$B/list_certs" \
    | grep -o "\"name\":\"$CERT\"" | head -1

echo '--- 验证证书（multipart，与前端提交方式一致）---'
curl -s -b "$CJ" -X POST "$B/verify_cert" \
    -F 'address=127.0.0.1' -F 'cert_type=qilin' -F "cert_name=$CERT"
echo

echo '--- 验证证书（地址不匹配时应失败）---'
curl -s -b "$CJ" -X POST "$B/verify_cert" \
    -F 'address=not-covered.example.com' -F "cert_name=$CERT"
echo

echo '--- 下载 ---'
curl -s -b "$CJ" -o /dev/null -w 'crt=%{http_code}\n' "$B/download/$CERT/$CERT.crt"
curl -s -b "$CJ" -o /dev/null -w 'ca=%{http_code}\n' "$B/download/ca/qilin-ca.crt"
curl -s -b "$CJ" -o /dev/null -w 'ca_key=%{http_code}（403 为预期）\n' "$B/download/ca/qilin-ca.key"

echo '--- 清理 ---'
curl -s -b "$CJ" -X POST "$B/delete_certs" \
    -H 'Content-Type: application/json' -d "{\"cert_names\":[\"$CERT\"]}"
echo
