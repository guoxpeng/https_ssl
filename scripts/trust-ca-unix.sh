#!/bin/sh
# 将 https_ssl 根 CA 安装到 Linux 或 macOS 的系统信任库。
#
# 用法：sudo sh scripts/trust-ca-unix.sh <https-ssl-ca.crt>
set -e

CA_PATH="${1:?用法: trust-ca-unix.sh <https-ssl-ca.crt>}"

if [ ! -f "$CA_PATH" ]; then
    echo "找不到 CA 文件: $CA_PATH" >&2
    exit 1
fi

CA_ABS=$(cd "$(dirname "$CA_PATH")" && pwd)/$(basename "$CA_PATH")

if [ "$(uname -s)" = "Darwin" ]; then
    echo '正在导入 macOS 系统钥匙串...'
    security add-trusted-cert -d -r trustRoot \
        -k /Library/Keychains/System.keychain "$CA_ABS"
    echo '完成'
    exit 0
fi

if [ ! -d /usr/local/share/ca-certificates ] && [ ! -d /etc/pki/ca-trust/source/anchors ]; then
    echo '未知的 Linux 发行版，未找到 ca-certificates 目录' >&2
    exit 1
fi

if [ -d /usr/local/share/ca-certificates ]; then
    # Debian/Ubuntu
    cp "$CA_ABS" /usr/local/share/ca-certificates/https-ssl-ca.crt
    update-ca-certificates
elif [ -d /etc/pki/ca-trust/source/anchors ]; then
    # RHEL/Fedora/openSUSE
    cp "$CA_ABS" /etc/pki/ca-trust/source/anchors/https-ssl-ca.crt
    update-ca-trust
fi

echo '完成'
