#!/bin/sh
# 单容器启动脚本：先在后台拉起 nginx，再用前台进程运行 Flask。
set -e

mkdir -p /app/proxy/sites /app/proxy/certs

# 站点目录为空时放一个匹配 *.conf 的占位文件，保证 include 通配始终有命中。
# （原来的 .keep 不匹配 *.conf，起不到这个作用。）
if ! ls /app/proxy/sites/*.conf >/dev/null 2>&1; then
    printf '# 占位文件：无反向代理服务时保持 include 通配可用\n' \
        > /app/proxy/sites/_placeholder.conf
fi

nginx -t
nginx

exec python app.py
