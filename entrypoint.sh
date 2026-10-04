#!/bin/sh
# 单容器启动脚本：先在后台拉起 nginx，再用前台进程运行 Flask。
#
# 这个脚本是容器的 1 号进程，它一退出容器就退出。所以这里的每一步都遵守一条
# 原则：**宁可少起一个反向代理站点，也不能让容器起不来**。
# 面板本身就是用户唯一的自救入口 —— 容器一旦陷入「启动→失败→重启」的崩溃
# 循环，用户连界面都进不去，也就没法在界面上把配置改回来。
set -e

mkdir -p /app/proxy/sites /app/proxy/certs

# 站点目录为空时放一个匹配 *.conf 的占位文件，保证 include 通配始终有命中。
# （原来的 .keep 不匹配 *.conf，起不到这个作用。）
if ! ls /app/proxy/sites/*.conf >/dev/null 2>&1; then
    printf '# 占位文件：无反向代理服务时保持 include 通配可用\n' \
        > /app/proxy/sites/_placeholder.conf
fi

# nginx -t 是拿「整份配置」一起校验的：任何一条站点配置出问题（引用的证书文件
# 被删、文件写到一半被打断、指令写法不合法），都会让整个校验失败。
#
# 旧写法是「校验不过就退出」，于是容器崩溃循环、面板跟着一起挂 —— 而面板恰恰
# 是修这些配置的地方，等于把唯一的出口锁死了。
#
# 现在改成 fail-open：校验不过就把全部站点配置「移走」（不是删除）到
# /app/proxy/sites.quarantine-<时间戳>/，让 nginx 先带着干净配置起来。
# 面板起来后，在界面上把对应服务重新「启用」一次即可恢复；被移走的文件都还在，
# 可以随时拿来对比排查。
if ! nginx -t; then
    echo '[warn] nginx 配置校验失败：正在隔离站点配置，先保证面板可用。' >&2
    quarantine="/app/proxy/sites.quarantine-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$quarantine"
    mv /app/proxy/sites/*.conf "$quarantine"/ 2>/dev/null || true
    printf '# 占位文件：站点配置被隔离期间保持 include 通配可用\n' \
        > /app/proxy/sites/_placeholder.conf
    echo "[warn] 站点配置已隔离到 $quarantine" >&2

    if ! nginx -t; then
        # 连「没有任何站点」的干净配置都过不了，说明问题出在 nginx 主配置本身，
        # 不是面板写的站点。这时仍然不能退出：面板跑在 Flask 自己的端口上
        # （默认 2002，不经过 nginx），起来之后还能登录、还能改配置。
        # 代价是所有反代入口暂时不可用 —— 但比整台面板失联好。
        echo '[warn] 隔离后配置仍校验失败，跳过 nginx 启动；面板将继续运行。' >&2
        exec python app.py
    fi
fi

# nginx 启动失败（最典型的是端口已被宿主机上别的服务占用）同样不该拖垮容器。
# 面板照常起来，用户可以在界面上停用冲突的服务；面板侧下次应用配置时会再尝试
# 启动 nginx（app.py 的 _apply_nginx 会发现进程不在并重新拉起）。
if ! nginx; then
    echo '[warn] nginx 启动失败（常见原因：端口已被占用），面板继续启动。' >&2
fi

exec python app.py
