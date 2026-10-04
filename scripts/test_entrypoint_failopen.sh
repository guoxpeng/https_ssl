#!/bin/sh
# 验证 entrypoint.sh 的 fail-open 行为 —— 配置坏了也要把面板拉起来。
#
# 做法：把 entrypoint.sh 里的 /app 换成临时目录、把 `exec python app.py` 换成
# 一句回显，再用一个假的 nginx 顶替真 nginx，然后跑四种场景。
# 这样不依赖 Docker，本机和 CI 都能跑。
#
# 用法： sh scripts/test_entrypoint_failopen.sh
set -u

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
ENTRYPOINT="$PROJECT_DIR/entrypoint.sh"

if [ ! -f "$ENTRYPOINT" ]; then
    echo "找不到 $ENTRYPOINT" >&2
    exit 2
fi

PASS=0
FAIL=0

ok() {
    PASS=$((PASS + 1))
    echo "  PASS  $1"
}

no() {
    FAIL=$((FAIL + 1))
    echo "  FAIL  $1"
}

# 造一个假的 nginx。NGINX_T_MODE 控制 `nginx -t` 的行为：
#   ok    —— 校验通过
#   once  —— 第一次失败、之后通过（模拟「隔离掉坏配置就好了」）
#   never —— 一直失败（模拟 nginx 主配置本身有问题）
# NGINX_START_MODE 控制 `nginx`（启动）的行为：ok / fail
make_fake_nginx() {
    bindir="$1"
    mkdir -p "$bindir"
    cat > "$bindir/nginx" <<'FAKE'
#!/bin/sh
is_test=0
for a in "$@"; do
    if [ "$a" = "-t" ]; then is_test=1; fi
done
if [ "$is_test" = "1" ]; then
    case "${NGINX_T_MODE:-ok}" in
        ok)    exit 0 ;;
        never) echo "nginx: [emerg] simulated always-fail" >&2; exit 1 ;;
        once)
            if [ -f "${NGINX_STATE}/tried" ]; then exit 0; fi
            : > "${NGINX_STATE}/tried"
            echo "nginx: [emerg] simulated first-fail" >&2
            exit 1 ;;
    esac
fi
case "${NGINX_START_MODE:-ok}" in
    ok)   echo "nginx: simulated start ok"; exit 0 ;;
    fail) echo "nginx: [emerg] bind() to 0.0.0.0:14000 failed" >&2; exit 1 ;;
esac
FAKE
    chmod +x "$bindir/nginx"
}

# 每个场景一个干净的沙箱：改写入口脚本 + 准备站点目录
# 注意：这里刻意不做 rm -rf —— 每次用全新的 mktemp 子目录即可。
# 在受限环境里批量删临时文件会被安全策略拦下，反而让测试报假失败。
prepare_case() {
    root="$1"
    mkdir -p "$root/app/proxy/sites" "$root/app/proxy/certs" "$root/state"
    # 替换进去的是路径，必须先转义反斜杠：sed 的替换段里 \U \L 之类是转义指令，
    # 直接塞 Windows 路径会被当成「大写/小写后续字符」，替换结果面目全非。
    root_sed=$(printf '%s' "$root" | sed 's|\\|\\\\|g')
    sed -e "s|/app|$root_sed/app|g" \
        -e 's|exec python app\.py|echo PANEL_STARTED; exit 0|' \
        "$ENTRYPOINT" > "$root/entrypoint.sh"
    chmod +x "$root/entrypoint.sh"
    make_fake_nginx "$root/bin"
}

# 跑一个场景，输出写到 $root/out.txt，返回退出码
run_case() {
    root="$1"
    (
        cd "$root" || exit 9
        PATH="$root/bin:$PATH" \
        NGINX_T_MODE="$NGINX_T_MODE" \
        NGINX_START_MODE="$NGINX_START_MODE" \
        NGINX_STATE="$root/state" \
        sh "$root/entrypoint.sh"
    ) > "$root/out.txt" 2>&1
    return $?
}

count_quarantine() {
    ls -d "$1"/app/proxy/sites.quarantine-* 2>/dev/null | wc -l | tr -d ' '
}

TMPROOT="${TMPDIR:-/tmp}/entrypoint-failopen-$$"
# Windows 上 TMPDIR 常是 `C:\Users\...\Temp` 这种混合路径，反斜杠会被 sh 当成
# 转义字符 —— 拼出来的 glob（.../sites/*.conf）会匹配不上，测试就会假失败。
# 有 cygpath 就先转成 /c/Users/... 这种纯 POSIX 路径。
if command -v cygpath >/dev/null 2>&1; then
    TMPROOT=$(cygpath -u "$TMPROOT")
fi
echo "== entrypoint.sh fail-open 测试 =="
echo "沙箱：$TMPROOT"
echo

# ---------------------------------------------------------------- 场景 1 --
echo "[1] 站点配置有坏文件 → 隔离后应正常启动面板"
NGINX_T_MODE=once
NGINX_START_MODE=ok
R1="$TMPROOT/case1"
prepare_case "$R1"
printf 'server { listen 14000 ssl; this is broken\n' > "$R1/app/proxy/sites/broken.conf"
printf 'server { listen 5245 ssl; }\n' > "$R1/app/proxy/sites/ok.conf"
run_case "$R1"
code=$?
if [ "$code" = "0" ]; then ok "退出码 0（没有崩溃循环）"; else no "退出码 $code（应为 0）"; fi
if grep -q PANEL_STARTED "$R1/out.txt"; then ok "面板已启动"; else no "面板未启动"; fi
if [ "$(count_quarantine "$R1")" = "1" ]; then ok "生成了隔离目录"; else no "未生成隔离目录"; fi
qdir=$(ls -d "$R1"/app/proxy/sites.quarantine-* 2>/dev/null | head -1)
if [ -n "$qdir" ] && [ -f "$qdir/broken.conf" ] && [ -f "$qdir/ok.conf" ]; then
    ok "坏配置被「移走」而不是删除（2 个 .conf 都在隔离目录里）"
else
    no "隔离目录内容不对"
fi
if [ -f "$R1/app/proxy/sites/_placeholder.conf" ] && \
   [ "$(ls "$R1/app/proxy/sites" | grep -c '\.conf$')" = "1" ]; then
    ok "站点目录只剩占位文件"
else
    no "站点目录未清理干净"
fi
echo

# ---------------------------------------------------------------- 场景 2 --
echo "[2] 配置全部正常 → 不应隔离任何东西"
NGINX_T_MODE=ok
NGINX_START_MODE=ok
R2="$TMPROOT/case2"
prepare_case "$R2"
printf 'server { listen 14000 ssl; }\n' > "$R2/app/proxy/sites/good.conf"
run_case "$R2"
code=$?
if [ "$code" = "0" ]; then ok "退出码 0"; else no "退出码 $code（应为 0）"; fi
if grep -q PANEL_STARTED "$R2/out.txt"; then ok "面板已启动"; else no "面板未启动"; fi
if [ "$(count_quarantine "$R2")" = "0" ]; then ok "没有误隔离（未生成隔离目录）"; else no "误隔离了正常配置"; fi
if [ -f "$R2/app/proxy/sites/good.conf" ]; then ok "正常配置原地保留"; else no "正常配置被挪走"; fi
echo

# ---------------------------------------------------------------- 场景 3 --
echo "[3] nginx 主配置本身坏（隔离后仍失败）→ 仍应启动面板"
NGINX_T_MODE=never
NGINX_START_MODE=ok
R3="$TMPROOT/case3"
prepare_case "$R3"
printf 'server { listen 14000 ssl; }\n' > "$R3/app/proxy/sites/good.conf"
run_case "$R3"
code=$?
if [ "$code" = "0" ]; then ok "退出码 0（没退出）"; else no "退出码 $code（应为 0）"; fi
if grep -q PANEL_STARTED "$R3/out.txt"; then ok "面板已启动"; else no "面板未启动"; fi
if grep -q "跳过 nginx 启动" "$R3/out.txt"; then ok "给出了跳过 nginx 的告警"; else no "缺少告警"; fi
echo

# ---------------------------------------------------------------- 场景 4 --
echo "[4] nginx 启动失败（端口被占用）→ 面板应继续启动"
NGINX_T_MODE=ok
NGINX_START_MODE=fail
R4="$TMPROOT/case4"
prepare_case "$R4"
printf 'server { listen 14000 ssl; }\n' > "$R4/app/proxy/sites/good.conf"
run_case "$R4"
code=$?
if [ "$code" = "0" ]; then ok "退出码 0"; else no "退出码 $code（应为 0）"; fi
if grep -q PANEL_STARTED "$R4/out.txt"; then ok "面板已启动"; else no "面板未启动"; fi
if grep -q "nginx 启动失败" "$R4/out.txt"; then ok "给出了 nginx 启动失败告警"; else no "缺少告警"; fi
echo

# ------------------------------------------------------------------ 汇总 --
echo "================================"
echo "通过 $PASS 项，失败 $FAIL 项"
echo "沙箱保留在：$TMPROOT（可自行删除）"
if [ "$FAIL" != "0" ]; then
    exit 1
fi
echo "entrypoint.sh fail-open 行为符合预期"
