#!/usr/bin/env bash
# https_ssl 一键安装脚本
#
#   curl -fsSL https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh | bash
#
# 也可以先下载、看一眼再执行：
#   curl -fsSLO https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh
#   less install.sh && bash install.sh
#
# 默认直接拉 Docker Hub 上的镜像，几十秒装完，不需要 git、不编译。
# 拉不到镜像（没发版、或国内网络连不上 Docker Hub）时，只要机器上有 git，
# 会自动退回「克隆仓库 + 本地构建」，不会让你卡在半路。
#
# 可用环境变量覆盖默认值：
#   IMAGE           镜像地址，默认 nameguoguo/https_ssl:latest。
#                     改成别的镜像站地址即可走国内加速；
#                     置为 build 则直接克隆仓库、从源码构建（需要 git，耗时几分钟）。
#   INSTALL_DIR     安装目录，默认 <当前目录>/https_ssl
#   PANEL_PORT      面板端口，默认 2002
#   NETWORK         网络模式，默认 host
#                     host   = 反向代理端口填了即生效，不用改 compose 也不用重建容器；
#                              仅 Linux 可用。
#                     bridge = 端口要先在 ports 里声明再重建容器；
#                              macOS / Windows 的 Docker Desktop 用这个。
#   PROXY_PORT      示例反向代理端口，默认 14000（仅 bridge 模式有效）
#   ADMIN_PASSWORD  管理员初始密码，默认 admin
#   REPO            代码仓库地址（下载 compose 文件、或源码构建时用）
#   BRANCH          分支，默认 main
#   SUDO            留空自动判断；置 1 强制用 sudo，置 0 强制不用
set -euo pipefail

# 这三个函数必须最先定义 —— 下面的 case 分支会调用 die
info() { printf '\033[36m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

REPO=${REPO:-https://github.com/guoxpeng/https_ssl.git}
BRANCH=${BRANCH:-main}
INSTALL_DIR=${INSTALL_DIR:-$PWD/https_ssl}
PANEL_PORT=${PANEL_PORT:-2002}
PROXY_PORT=${PROXY_PORT:-14000}
NETWORK=${NETWORK:-host}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}
IMAGE=${IMAGE:-nameguoguo/https_ssl:latest}

# IMAGE=build 表示不走镜像，克隆仓库现场构建
case "$IMAGE" in
    build|BUILD|source|src|none|'') BUILD_MODE=1; IMAGE='' ;;
    *)                              BUILD_MODE=0 ;;
esac

case "$NETWORK" in
    host)   BRIDGE=0 ;;
    bridge) BRIDGE=1 ;;
    *)      die "NETWORK 只能是 host 或 bridge，收到：$NETWORK" ;;
esac

# 选 compose 文件：
#   host   + 镜像 -> 用仓库默认的 docker-compose.yml（不带 -f）
#   host   + 源码 -> docker-compose.build.yml
#   bridge + 任意 -> docker-compose.bridge.yml
pick_compose() {
    if [ "$BRIDGE" = '1' ]; then
        COMPOSE_FILE='docker-compose.bridge.yml'
    elif [ "$BUILD_MODE" = '1' ]; then
        COMPOSE_FILE='docker-compose.build.yml'
    else
        COMPOSE_FILE=''
    fi
    COMPOSE_LABEL=${COMPOSE_FILE:-docker-compose.yml}
}
pick_compose

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

# ---------------------------------------------------------------- 2. 预检镜像
# 先在这里把镜像拉下来，拉不到就当场决定退回源码构建。
# 放在建目录、写文件之前，失败时不会留下半个安装目录。
if [ "$BUILD_MODE" = '0' ]; then
    info "正在拉取镜像 $IMAGE ..."
    if ! $SUDO docker pull "$IMAGE"; then
        warn ''
        warn "拉取镜像 $IMAGE 失败。常见原因：镜像还没发布，或国内直连 Docker Hub 超时。"
        if command -v git >/dev/null 2>&1; then
            warn '本机有 git —— 自动改用源码构建（要编译，几分钟）。'
            warn '想中止请按 Ctrl+C。'
            warn ''
            BUILD_MODE=1
            IMAGE=''
            pick_compose
        else
            warn '本机没有 git，无法退回源码构建。请换镜像加速地址重跑：'
            warn "  IMAGE=<加速站>/nameguoguo/https_ssl:latest INSTALL_DIR=$INSTALL_DIR bash install.sh"
            warn '或装好 git 后再跑一次（会自动退回源码构建）。'
            exit 1
        fi
    fi
fi

if [ "$BUILD_MODE" = '1' ]; then
    command -v git >/dev/null 2>&1 || die '未找到 git。若不想装 git，去掉 IMAGE=build 用默认的镜像安装即可'
else
    command -v curl >/dev/null 2>&1 || die '未找到 curl，请先安装 curl（下载 compose 文件需要）'
fi

info "安装目录：$INSTALL_DIR"

# ---------------------------------------------------------------- 3. 准备文件
if [ "$BUILD_MODE" = '1' ]; then
    # ---- 源码模式：克隆 / 更新仓库
    if [ -d "$INSTALL_DIR/.git" ]; then
        info '检测到已有仓库，执行更新...'
        git -C "$INSTALL_DIR" pull --ff-only
    elif [ -e "$INSTALL_DIR" ]; then
        die "$INSTALL_DIR 已存在且不是 git 仓库，请换一个 INSTALL_DIR 或先移走它"
    else
        info '正在克隆仓库...'
        git clone --depth 1 --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
    fi
else
    # ---- 镜像模式：只取 compose 文件，不克隆仓库
    mkdir -p "$INSTALL_DIR"
    RAW_BASE=$(printf '%s' "$REPO" \
        | sed -e 's|^git@github\.com:|https://github.com/|' \
              -e 's|\.git$||' -e 's|/*$||')
    RAW_BASE="$RAW_BASE/raw/$BRANCH"

    fetch() {   # fetch <文件名>；失败重试一次
        local name=$1
        local _try
        for _try in 1 2; do
            if curl -fsSL --connect-timeout 15 --retry 2 \
                    -o "$INSTALL_DIR/$name" "$RAW_BASE/$name"; then
                return 0
            fi
            sleep 2
        done
        return 1
    }

    info '正在获取 compose 文件...'
    fetch "$COMPOSE_LABEL" || die "下载 $COMPOSE_LABEL 失败，请检查网络能否访问 GitHub。
  也可以改用源码构建：IMAGE=build bash install.sh"
fi

cd "$INSTALL_DIR"

[ -f "$COMPOSE_LABEL" ] || die "目录里没有 $COMPOSE_LABEL，请确认文件完整"

# bridge 文件默认用镜像跑；要求源码构建时把注释里的 build 打开、去掉 image 行
if [ "$BUILD_MODE" = '1' ] && [ "$BRIDGE" = '1' ]; then
    sed -i 's|^    # build: \.$|    build: .|' "$COMPOSE_FILE"
    sed -i '/^    image: \${QILIN_IMAGE/d' "$COMPOSE_FILE"
fi

if [ "$BUILD_MODE" = '1' ]; then
    info "网络模式：$NETWORK（$COMPOSE_LABEL，从源码构建）"
else
    info "网络模式：$NETWORK（$COMPOSE_LABEL，使用镜像 $IMAGE）"
fi

# 后面所有 compose 命令都带上 -f，打印给用户的命令也保持一致
DC="$SUDO $COMPOSE"
if [ -n "$COMPOSE_FILE" ]; then
    DC="$DC -f $COMPOSE_FILE"
fi

# ---------------------------------------------------------------- 4. 生成 .env
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
    if [ "$BUILD_MODE" = '0' ]; then
        printf 'QILIN_IMAGE=%s\n' "$IMAGE" >> .env
    fi
    chmod 600 .env
fi

# ---------------------------------------------------------------- 5. 端口改写
# 只有 bridge 模式需要改 compose 的端口映射：host 模式下没有 ports 段，
# 端口由 nginx 直接绑在宿主机上，面板端口走 .env 的 QILIN_PORT。
if [ "$BRIDGE" = '1' ]; then
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

# ---------------------------------------------------------------- 6. 启动
if [ "$BUILD_MODE" = '1' ]; then
    info '正在构建并启动容器（首次构建需要几分钟）...'
    START_CMD="$DC up -d --build"
else
    info '正在启动容器...'
    START_CMD="$DC up -d"
fi

if ! $START_CMD; then
    warn ''
    if [ "$BRIDGE" = '0' ]; then
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
READY=''
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PANEL_PORT/login" 2>/dev/null; then
        READY=1
        break
    fi
    sleep 2
done

IP=$(hostname -I 2>/dev/null | awk '{print $1}') || IP=''
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
    2. 主页「创建」建一个根 CA，下载 https-ssl-ca.crt 装到设备上
    3. 「新增」签发服务器证书，「反向代理」把 HTTP 服务套成 HTTPS

  常用命令：
    cd $INSTALL_DIR
    $DC ps          # 状态
    $DC logs -f     # 日志
    $DC down        # 停止

  提示：
EOF

if [ "$BRIDGE" = '0' ]; then
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
      $DC down && $SUDO $COMPOSE up -d
    - 想给面板也配上 HTTPS，见 INSTALL.md 的「让面板自己也走 HTTPS」。

EOF
fi

if [ "$BUILD_MODE" = '1' ]; then
    cat <<EOF
    - 当前是从源码构建的。以后升级：cd $INSTALL_DIR && git pull && $DC up -d --build
      用户表存在 ./data/users.json，升级不会丢密码。

EOF
else
    cat <<EOF
    - 当前使用的是 Docker Hub 镜像 $IMAGE。
      升级到新版本：cd $INSTALL_DIR && $DC pull && $DC up -d
      （用户表存在 ./data/users.json，升级不会丢密码）

EOF
fi
