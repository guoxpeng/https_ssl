#!/usr/bin/env bash
# https_ssl 一键安装脚本
#
#   curl -fsSL https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh | bash
#
# 也可以先下载、看一眼再执行：
#   curl -fsSLO https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh
#   less install.sh && bash install.sh
#
# 可用环境变量覆盖默认值：
#   INSTALL_DIR     安装目录，默认 <当前目录>/https_ssl
#   PANEL_PORT      面板端口，默认 2002
#   NETWORK         网络模式，默认 host
#                     host   = 用默认的 docker-compose.yml。反向代理端口填了即生效，
#                              不用改 compose 也不用重建容器；仅 Linux 可用。
#                     bridge = 用 docker-compose.bridge.yml。端口要先在 ports 里声明
#                              再重建容器；macOS / Windows 的 Docker Desktop 用这个。
#   PROXY_PORT      示例反向代理端口，默认 14000（仅 bridge 模式有效）
#   ADMIN_PASSWORD  管理员初始密码，默认 admin
#   REPO            代码仓库地址
#   SUDO            留空自动判断；置 1 强制用 sudo，置 0 强制不用
#
# 脚本只做四件事：检查依赖 -> 拉代码 -> 生成 .env -> docker compose up -d --build
set -euo pipefail

info() { printf '\033[36m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

REPO=${REPO:-https://github.com/guoxpeng/https_ssl.git}
INSTALL_DIR=${INSTALL_DIR:-$PWD/https_ssl}
PANEL_PORT=${PANEL_PORT:-2002}
PROXY_PORT=${PROXY_PORT:-14000}
NETWORK=${NETWORK:-host}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}

# host 模式用默认的 docker-compose.yml（命令不带 -f）；bridge 模式用单独的文件。
case "$NETWORK" in
    host)   COMPOSE_FILE='' ;;
    bridge) COMPOSE_FILE='docker-compose.bridge.yml' ;;
    *)      die "NETWORK 只能是 host 或 bridge，收到：$NETWORK" ;;
esac
COMPOSE_LABEL=${COMPOSE_FILE:-docker-compose.yml}

# ---------------------------------------------------------------- 1. 依赖检查
command -v docker >/dev/null 2>&1 || die '未找到 docker，请先安装 Docker Engine 20.10+'

if docker compose version >/dev/null 2>&1; then
    COMPOSE='docker compose'
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE='docker-compose'
else
    die '未找到 docker compose，请安装 Docker Compose v2'
fi

# 非 root 且不在 docker 组时，docker 命令需要 sudo（飞牛 NAS 的 admin 就是这种）
if [ -z "${SUDO:-}" ]; then
    if docker info >/dev/null 2>&1; then
        SUDO=''
    elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
        SUDO='sudo'
    else
        SUDO='sudo'
        warn '当前用户不能直接访问 docker，将使用 sudo（可能提示输入密码）'
    fi
fi

command -v git >/dev/null 2>&1 || die '未找到 git，请先安装 git'

info "安装目录：$INSTALL_DIR"

# ---------------------------------------------------------------- 2. 拉取代码
if [ -d "$INSTALL_DIR/.git" ]; then
    info '检测到已有仓库，执行更新...'
    git -C "$INSTALL_DIR" pull --ff-only
elif [ -e "$INSTALL_DIR" ]; then
    die "$INSTALL_DIR 已存在且不是 git 仓库，请换一个 INSTALL_DIR 或先移走它"
else
    info '正在克隆仓库...'
    git clone --depth 1 "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

[ -f "$COMPOSE_LABEL" ] || die "仓库里没有 $COMPOSE_LABEL，请确认代码完整"
info "网络模式：$NETWORK（$COMPOSE_LABEL）"

# 后面所有 compose 命令都带上 -f，打印给用户的命令也保持一致
DC="$SUDO $COMPOSE"
if [ -n "$COMPOSE_FILE" ]; then
    DC="$DC -f $COMPOSE_FILE"
fi

# ---------------------------------------------------------------- 3. 生成 .env
if [ -f .env ]; then
    info '.env 已存在，保持不变'
else
    info '正在生成 .env（含随机会话密钥）...'
    SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    cat > .env <<EOF
# 由 install.sh 生成。本文件含密钥，不要提交到版本库。
QILIN_ADMIN_PASSWORD=$ADMIN_PASSWORD
QILIN_SECRET_KEY=$SECRET
QILIN_COOKIE_SECURE=0
# 面板端口。host 模式（默认）下 Flask 直接绑这个端口；
# bridge 模式下容器内固定 2002，这个值不生效，改端口要动 compose 的 ports。
QILIN_PORT=$PANEL_PORT
EOF
    chmod 600 .env
fi

# 只有 bridge 模式需要改 compose 的端口映射：host 模式下没有 ports 段，
# 端口由 nginx 直接绑在宿主机上，面板端口走 .env 的 QILIN_PORT。
if [ "$NETWORK" = 'bridge' ]; then
    # 面板端口：改 compose 里的映射，不动其它已配好的端口
    if [ "$PANEL_PORT" != '2002' ]; then
        info "面板端口改为 $PANEL_PORT"
        sed -i "s|\"2002:2002\"|\"$PANEL_PORT:2002\"|" "$COMPOSE_FILE"
    fi

    # 示例反向代理端口同理。仓库自带的 14000 只是个占位示例，
    # 同一台机器上装第二个实例、或 14000 已被别的服务占用时会撞车。
    if [ "$PROXY_PORT" != '14000' ]; then
        info "示例反代端口改为 $PROXY_PORT"
        sed -i "s|\"14000:14000\"|\"$PROXY_PORT:14000\"|" "$COMPOSE_FILE"
    fi
fi

# ---------------------------------------------------------------- 4. 启动
info '正在构建并启动容器（首次构建需要几分钟）...'
if ! $DC up -d --build; then
    warn ''
    if [ "$NETWORK" = 'host' ]; then
        warn '启动失败。host 模式下容器与宿主机共用网络，最常见的原因是面板端口被占用：'
        warn "  面板端口 $PANEL_PORT"
        warn '换一个端口重跑即可，代码和 .env 都不会丢：'
        warn "  PANEL_PORT=2010 INSTALL_DIR=$INSTALL_DIR bash install.sh"
    else
        warn '启动失败。最常见的原因是端口已被占用：'
        warn "  面板端口 $PANEL_PORT、示例反代端口 $PROXY_PORT"
        warn '换一组端口重跑即可，代码和 .env 都不会丢：'
        warn "  PANEL_PORT=2010 PROXY_PORT=14010 INSTALL_DIR=$INSTALL_DIR bash install.sh"
    fi
    warn ''
    warn "也可以先看日志：cd $INSTALL_DIR && $DC logs"
    exit 1
fi

info '等待服务就绪...'
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PANEL_PORT/login" 2>/dev/null; then
        READY=1
        break
    fi
    sleep 2
done

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "${IP:-}" ] || IP='<服务器IP>'

echo
if [ "${READY:-}" = '1' ]; then
    info '安装完成 ✓'
else
    warn '容器已启动，但面板暂时没响应。用下面的命令看日志：'
    warn "  cd $INSTALL_DIR && $DC logs -f"
fi
cat <<EOF

  面板地址：http://$IP:$PANEL_PORT
  默认账号：admin / $ADMIN_PASSWORD

  下一步：
    1. 登录后到「设置」页改掉密码
    2. 主页「创建」建一个根 CA，下载 qilin-ca.crt 装到设备上
    3. 「新增」签发服务器证书，「反向代理」把 HTTP 服务套成 HTTPS

  常用命令：
    cd $INSTALL_DIR
    $DC ps          # 状态
    $DC logs -f     # 日志
    $DC down        # 停止

  提示：
EOF

if [ "$NETWORK" = 'host' ]; then
    cat <<EOF
    - 当前是 host 网络模式（默认）：反向代理端口填了即生效，不用改 compose、
      也不用重建容器。
    - 想给面板也配上 HTTPS，见 INSTALL.md 的「让面板自己也走 HTTPS」。

EOF
else
    cat <<EOF
    - 当前是 bridge 网络模式：反向代理端口要先在 $COMPOSE_LABEL 的 ports 里声明
      （已预留 $PROXY_PORT），加完执行 $DC up -d 生效。
    - Linux 上建议改用默认的 host 模式，反向代理端口填了即生效：
      $DC down && $SUDO $COMPOSE up -d --build
    - 想给面板也配上 HTTPS，见 INSTALL.md 的「让面板自己也走 HTTPS」。

EOF
fi
