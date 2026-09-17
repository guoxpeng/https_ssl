#!/bin/sh
# 为 fnOS 系统 nginx 的证书补齐 CA 链，并统一 fallback 证书。
#
# 背景：fnOS 的 /usr/trim/nginx 用 trim_hook_cert_switch 按 SNI 选证书。
# 用 IP 直连时客户端不发 SNI，会回退到 fallback 证书（原为自签名 CN=fnOS），
# 导致证书不受信任。本脚本把 https_ssl 签发的证书（含 CA 链、SAN 含 IP）用于
# 系统证书与 fallback，并修正权限（trim nginx worker 非 root，需可读）。
#
# 用法：sudo sh scripts/fix-fnos-cert.sh [证书名] [容器名]
#   证书名默认 fnos；容器名默认 qilin_ssl。
set -e

CERT_NAME="${1:-fnos}"
CONTAINER="${2:-qilin_ssl}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

exec python3 "$SCRIPT_DIR/fix_fnos_cert.py" "$CERT_NAME" "$CONTAINER"
