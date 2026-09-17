# 更新日志

本文件记录本项目的所有重要变更。

## [1.3.2] - 2026-09-17

在真机上实测一键安装脚本时发现的问题，一并修掉。

### 修复

- **`install.sh` 在 14000 端口被占用时启动失败，且提示无指导**：仓库的
  `docker-compose.yml` 里 `14000:14000` 只是个示例反代端口，同一台机器上装第二个
  实例、或该端口已被别的服务占用时，`docker compose up` 会以
  `Bind for 0.0.0.0:14000 failed: port is already allocated` 退出。
  现在新增 `PROXY_PORT` 环境变量（默认 `14000`）可改写该映射，并且启动失败时会
  明确提示是端口冲突、打印换端口重跑的命令，同时说明代码与 `.env` 不会丢。

### 文档

- INSTALL.md / README.en.md 补上 `install.sh` 全部环境变量表（`INSTALL_DIR`、
  `PANEL_PORT`、`PROXY_PORT`、`ADMIN_PASSWORD`、`REPO`、`SUDO`）。
- 补充说明 **`curl | bash` 时 sudo 无法弹密码提示**（stdin 被脚本占用）：
  账号不在 `docker` 组时需先 `sudo -v`，或改用 `curl ... | sudo bash`。

### 实测记录

在飞牛 NAS（`192.168.5.3`）上真机跑通：克隆 → 生成 `.env`（随机会话密钥、
`chmod 600`）→ 改写端口 → 构建镜像 → 启动容器 → 等待就绪 → 打印地址，
`admin/admin` 登录返回 302、鉴权后首页 200，`data/` 目录独立。

## [1.3.1] - 2026-09-17

### 变更

- **项目更名为 `https_ssl`**：仓库名、面板标题、README / INSTALL 文档、Dockerfile 注释、
  logo 文件名（`static/images/https_ssl-logo.*`、`https_ssl-zt.png`）以及默认 CA 机构名
  统一改为 `https_ssl`。

  以下标识属于兼容层，**刻意保持不变**，以免破坏已有部署、已签发证书与历史数据：

  - `QILIN_*` 环境变量（`QILIN_ADMIN_PASSWORD`、`QILIN_SECRET_KEY`、`QILIN_COOKIE_SECURE`、
    `QILIN_PROXY_DIR`、`QILIN_PROXY_LISTEN_HOST`、`QILIN_OPENSSL`）
  - CA 文件名 `qilin-ca.key` / `qilin-ca.crt` / `qilin-ca.cnf` / `qilin-ca.srl`
  - 容器名 `qilin_ssl` 与 compose 服务名 `qilin-ssl`
  - 反向代理的 `cert_type=qilin` 协议值（已写入 `proxy_data.json`）及前端 `QilinCertPicker`
  - `_nas_backup.sh` 中的 NAS 目录 `/vol1/docker/qilin_SSL`（已改为可用第一个参数覆盖）

- **文档注明来源**：README 与 INSTALL 中明确 `https://github.com/linzxcw/qilin_SSL`
  为作者源库（原始项目），本仓库是其 Linux / Docker 适配分支。
- **新增英文说明** `README.en.md`。
- **新增一键安装脚本** `install.sh`：检查 Docker / Compose / git → 克隆代码 →
  生成带随机会话密钥的 `.env`（`chmod 600`）→ 构建并启动容器 → 轮询等待面板就绪 →
  打印访问地址。支持 `INSTALL_DIR` / `PANEL_PORT` / `ADMIN_PASSWORD` / `REPO` 环境变量覆盖，
  自动判断是否需要 `sudo`。

  ```bash
  curl -fsSL https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh | bash
  ```

- **INSTALL.md 新增「签发证书后，在电脑上怎么用」**：签发只是生成文件，真正让电脑认它
  还需要「把根证书装进电脑」和「把证书与私钥装到服务上」两步。新增内容含装根证书的
  6 系统对照表、证书与私钥部署到 nginx / Node.js / Python / Docker / NAS 的示例、
  证书链要拼全（`cat xxx.crt qilin-ca.crt > fullchain.crt`）以及 `openssl s_client` 验证方式。
- **文档补充两个实际会踩到的坑**：
  - INSTALL.md 新增「让面板自己也走 HTTPS」——用面板自带的反代把 `2002` 套成
    `https://<IP>:12002`，并说明 `https://<IP>:2002` 本身是打不开的（面板不做 TLS）。
  - 明确 Windows 下的信任方式：不加管理员装 `CurrentUser\Root` 即可让
    Chrome / Edge 生效，`LocalMachine\Root` 才需要提权；
    **Firefox 有自己的信任库，不读 Windows 存储**，需开启
    `security.enterprise_roots.enabled` 或手动导入。
    `scripts/trust-ca-windows.ps1` 同步改为非管理员时导入 `CurrentUser\Root`
    （此前非管理员会直接跳过，等于什么都没做）。
- `scripts/smoke_test.py` 临时目录前缀与 e2e 证书名同步改名（`https-ssl-*`）。

### 修复

- **证书列表的有效期直接显示 OpenSSL 原始格式**：面板上原本显示
  `Feb 16 08:19:50 2036 GMT`，既长又难读，和 CA 那行的 `2036-02-16` 也对不齐。
  新增 `_pretty_date()` 统一转成 `YYYY-MM-DD`，解析不了则原样返回，不吞信息。
- `scripts/smoke_test.py` 新增 2 条断言（有效期格式、HTML 列表不再出现原始日期），
  90 → 92 通过 0 失败。

## [1.3.0] - 2026-09-17

本轮为一次完整的问题修复，重点是把断掉的链路接通、把漏掉的校验补齐。
`app.py` 按「校验与工具 / 用户与会话 / 页面路由 / CA / 签发 / 验证 / 反向代理」
重新分区，并抽出共用函数消除重复代码（1239 行 → 约 1350 行，其中含大量注释与文档字符串，
重复逻辑减少约 200 行）。

### 修复

- **证书验证功能在界面上完全不可用**：前端以 `multipart/form-data` 提交，后端却用
  `request.json` 取值，实测恒返回 `415 Unsupported Media Type`。现统一由 `_body()`
  解析请求体，JSON 与表单两种提交方式都支持；自定义证书分支的字段名也对齐了。
- **证书验证名不符实**：收到 `address` 却从不校验，现会与证书的 SAN/CN 比对
  （支持 `*.example.com` 通配），不覆盖时明确报错。
- **证书链判定错误**：原先用 `cert_name.startswith('qilin-')` 判断是否用本机 CA 作信任锚，
  导致名字不带该前缀的自签证书必然报"链校验失败"。现改为用
  `openssl verify -CAfile` 真实校验一次链。
- **OpenSSL 3.x 下 SAN 解析失败**：3.x 把 SAN 中的 IP 打印为 `IP Address:`（旧版为 `IP:`），
  原解析逻辑整行匹配不上，域名和 IP 会一起丢掉（`list_certs` 的 JSON 与验证页因此一直是空的）。
  现做归一化处理，两种格式都兼容。
- **`create_cert` 缺少名称校验**：这是唯一漏掉白名单的入口，`cert_name` 直接进入路径拼接，
  可写出证书目录之外；同一个值还未经转义拼进返回的 HTML。
- **SAN 参数注入**：域名/IP 未校验就写入 OpenSSL 配置，换行可以注入额外扩展
  （例如把 `CA:TRUE` 塞进被签发的证书）。现用 `_valid_domain` / `_valid_ip` 逐项校验。
- **中文证书名可用性**：白名单改为允许 Unicode 字母数字，中文名证书现在可以
  正常下载、验证、删除（此前能创建但不可用，删除还会被静默跳过）。
- **`create_proxy` 先落盘后校验**：校验失败时脏记录已经写入 `proxy_data.json` 且无法清理。
  现所有校验前置，并支持编辑时改名（清理旧服务记录与配置）。
- **`create_proxy` 响应缺少字段**：只返回 `{'success': True}`，前端要的 `proxy_id`、
  `cert_expiry` 都拿不到，导致新建后列表行渲染失败、自动启用必然报错。
- **证书选择下拉框不生效**：前端提交 `cert_id` 但后端从不读取，实际用的是"与服务名同名的证书"。
  现按 `cert_id` 取证书，站点证书按服务名落盘。
- **编辑已启用服务后状态错乱**：编辑会把状态重置为停止且不重载 nginx，
  面板显示"已停止"但服务仍在运行。现保留原状态并在启用状态下重载。
- **一个坏站点拖垮全部反代操作**：`nginx -t` 是整份配置一起校验的，任一站点引用了
  不存在的证书就会让所有服务的启停失败。现增加 `_prune_broken_sites()` 自愈，
  并在 `stop_proxy` 失败时回滚配置。
- **删除操作不联动**：删除证书时会先停用引用它的反向代理；删除反向代理会清理
  站点配置与证书文件；重新创建/删除 CA 后会同步各站点（能链上的刷新证书链，链不上的停用）。
- **`/check_ca_password` 接口缺失**：前端一直在调用但后端没有实现，
  CA 密码提示恒为降级文案。
- **签发失败留下半成品目录**：改为在暂存目录内完成全部签发后再改名到位。
- **同名证书静默覆盖**：改为返回 409，要求先删除或更换名称。
- **叶子证书有效期可能超出 CA**：现取 CA 剩余有效期与 3650 天的较小值。
- **上传接口**：文件名改由服务端生成（uuid + 白名单后缀），修复中文名经
  `secure_filename` 归零后写入目录导致的 500，并限制扩展名与大小（8MB）。
- **设置页**：补上用户名合法性、重名校验与新密码长度校验。
- **`init_proxy_data()` 使用相对路径**：与 `QILIN_PROXY_DIR` 脱钩，非 `/app` 工作目录
  启动时会另建一份数据文件。现统一走 `PROXY_DIR`，并改为原子写入。
- **旧反代记录编辑时证书下拉框空选**：1.2.0 之前写入的记录只有 `cert_type`，
  没有 `cert_id` / `cert_filename`。现前端按「`cert_id` → 证书文件名 → 服务名」
  依次兜底，与后端 `create_proxy` 的 `cert_id or service_name` 保持一致；
  同时修正 `loadCertOptions()` 直接 `val()` 一个不存在的值会把 `selectedIndex`
  置为 `-1`、下拉看起来是空的的问题。

### 变更

- **管理员默认密码改为 `admin`，支持零配置启动**：此前不设置 `QILIN_ADMIN_PASSWORD`
  会直接拒绝启动，`docker compose up -d --build` 前必须先建 `.env`。现在缺省为 `admin`，
  开箱即用；仍可用 `.env` 覆盖，且使用默认密码时启动日志会提示尽快修改。
- **CA 私钥不再通过面板下载**：`/download/ca/` 只放行根证书 `qilin-ca.crt`，
  私钥请从宿主目录 `./data/ca` 备份。
- **前端脚本整合**：`static/js/file_upload.js` 由 `static/js/cert_picker.js` 取代，
  验证页与反向代理页共用同一套证书选择逻辑；`proxy.html` 的内联脚本从 767 行精简到 307 行，
  并修掉重复的 `</body></html>`。
- **Dockerfile**：基础镜像从已停止维护的 `python:3.9-slim` 升级到 `python:3.12-slim`，
  apt 换源同时兼容 deb822 与 sources.list 两种格式。
- **新增 `.dockerignore`**：此前 `COPY . .` 会把 `users.json`、CA 私钥、
  签发的证书与反向代理私钥一并打进镜像层，运行时 volume 虽然遮蔽了路径，
  但镜像一旦分发就等于泄露私钥。
- **`entrypoint.sh`**：原来的 `.keep` 占位不匹配 `*.conf`，起不到注释所说的作用，
  改为写入真正匹配通配的 `_placeholder.conf`。
- **`.env.example` 与 `docker-compose.yml`** 补充 `QILIN_SECRET_KEY` 说明。

### 新增

- **`scripts/smoke_test.py`**：在临时目录里跑通全流程的冒烟测试（90 项断言），
  覆盖签发校验、SAN 注入、路径穿越、验证契约、反代增删改查、联动清理、
  旧数据兼容与默认密码回退。
- **`scripts/verify_deploy.py`**：部署体检脚本。与冒烟测试相反，它直接对**当前真实数据**
  跑一遍关键接口（证书 SAN 解析是否正确、反代状态与站点配置是否一致、CA 私钥是否被拒绝
  下载等），用于升级后确认服务正常。
- **`INSTALL.md`**：Docker 部署说明，含功能简介、安装步骤、上手三步、常用命令与注意事项。
- **`LICENSE`**：MIT。上游 `guoxpeng/fnos_qilin_SSL` 采用 MIT，按其要求在衍生作品中
  保留原始版权声明。
- **`.gitignore`** 补充 `uploads/`、`proxy/`、`.workbuddy-ai/`、`*.pem`、`.wsl_distro/`、`dist/`。

### 已知限制

- 容器内 nginx 与 Flask 以同一用户运行。
- `app.py` 仍使用 Flask 开发服务器，不建议直接暴露到公网。
- 反向代理端口需在 `docker-compose.yml` 中手动映射（容器无法感知宿主端口映射）。
- 表单类接口未加 CSRF token，依赖 `SameSite=Lax` 会话 Cookie 阻断跨站 POST；
  面板本身为单管理员使用，风险较低，但若要暴露到更大范围建议补上。
- `USERS` 保存在进程内存中，当前为单进程运行；若将来改用多 worker 需改为每次读盘。

## [1.2.0] - 2026-09-17

### 变更

- **单容器架构**：nginx 与 Flask 合并到同一个 `qilin_ssl` 容器，由 `entrypoint.sh` 一并拉起。不再需要独立的 `qilin_proxy` 容器。
- **移除 Docker socket 依赖**：面板不再通过 Docker CLI 控制代理容器，改为在容器内直接执行 `nginx -s reload`，容器权限显著收窄。
- **`Dockerfile`**：移除 `docker-ce-cli` 及 Docker 软件源配置，加装 `nginx`，并复制 `nginx.conf` 与 `entrypoint.sh`。
- **`docker-compose.yml`**：仅保留单个服务，删除 `/var/run/docker.sock` 与 `/vol1/docker/qilin_proxy` 挂载，新增 `QILIN_PROXY_DIR=/app/proxy`。
- **环境变量**：`QILIN_PROXY_CONTAINER`、`QILIN_PROXY_HOST_DIR` 已被 `QILIN_PROXY_DIR` 取代。

### 新增

- **`nginx.conf`**：容器内 nginx 主配置，站点配置由 `/app/proxy/sites/*.conf` 通配加载。
- **`entrypoint.sh`**：启动脚本，先校验并启动 nginx，再以前台进程运行 Flask。

### 修复

- **证书路径**：站点配置中的 `ssl_certificate` 改为指向容器内实际路径 `/app/proxy/certs/<service>.crt`，修复重载时 `BIO_new_file() failed` 报错。
- **nginx 进程检测**：`_nginx_running()` 改为扫描 `/proc` 中的 nginx 进程，不再依赖 pid 文件。此前 pid 文件在 `nginx -s stop` 后仍残留，导致重复启动多个 nginx master，端口冲突且 `nginx -s reload` 报 `invalid PID number`。
- **启动方式**：新增 `_start_nginx()`，以 `nginx -g 'daemon on;'` 并重定向 stdout 启动，修复 `subprocess.run` 等待继承管道导致的"命令执行超时"。
- **停止语义**：`stop_proxy` 不再停止 nginx 进程，只删除该服务的站点配置并重载，使 nginx 常驻、其他已启用服务不受影响。

### 已知限制

- 容器内 nginx 与 Flask 以同一用户运行。
- `app.py` 仍使用 Flask 开发服务器，不建议直接暴露到公网。
- 默认账号密码仍为 `admin` / `admin123`，首次登录后请立即修改。

## [1.1.0] - 2026-09-17

### 新增

- **Linux / Docker 原生反向代理**：新增独立的 nginx 容器 `qilin_proxy`，承担 HTTPS 反向代理。此前反向代理依赖 Windows 专用的 `proxy.exe`，在 Linux 容器中无法运行。
- **`/get_proxy_pid/<id>` 接口**：前端一直在调用该接口，但后端从未实现，导致状态查询返回 404、前端拿到 `undefined`。现已补全，返回真实运行状态。
- **`/delete_proxy` 接口**：支持删除反向代理配置，同时移除对应的 nginx 站点配置。
- **多站点反向代理**：`qilin_proxy` 的 nginx 配置按服务拆分为 `conf/sites/<service>.conf`，新增或修改服务后通过 `nginx -s reload` 热加载，不中断其他服务。
- **环境变量配置**：反向代理相关路径与容器名可通过环境变量覆盖。
  - `QILIN_PROXY_CONTAINER`：代理容器名，默认 `qilin_proxy`
  - `QILIN_PROXY_HOST_DIR`：代理配置所在宿主目录，默认 `/vol1/docker/qilin_proxy`
  - `QILIN_PROXY_LISTEN_HOST`：代理监听地址，默认空（监听全部地址）

### 变更

- **`/run_proxy`**：改为通过 Docker CLI 启动 `qilin_proxy` 容器并热加载 nginx，不再尝试执行不存在的 `/app/proxy/proxy`。
- **`/stop_proxy`**：改为停止 `qilin_proxy` 容器；当仍有其他服务处于启用状态时，仅重载配置而不停止容器。
- **`/create_proxy`**：改为生成 nginx 站点配置，并把对应证书复制到代理容器的证书目录。若证书不存在，返回明确错误而非静默失败。
- **`Dockerfile`**：新增 `docker-ce-cli`、`ca-certificates`、`curl`、`gnupg` 依赖，使容器内可通过 Docker CLI 控制代理容器。
- **`docker-compose.yml`**：新增 `/var/run/docker.sock` 与代理配置目录的挂载，并声明上述环境变量。

### 修复

- **启动反向代理报「未找到 Linux 版 proxy 程序」**：这是上游项目在 Linux 下反向代理功能完全不可用的根因，现已通过 nginx 容器方案解决。
- **`/run_proxy` 返回 500**：此前无论配置是否正确都会因缺少 Windows 二进制而失败。
- **前端状态显示异常**：`get_proxy_pid` 缺失导致「启用/停用」开关状态无法正确回显。

### 已知限制

- `qilin_ssl` 容器需要挂载宿主 `/var/run/docker.sock`，这赋予其控制宿主 Docker 的能力。请仅在可信环境使用。
- 反向代理依赖 `qilin_proxy` 容器，首次部署需先创建该容器（见 README「部署反向代理容器」）。
- `app.py` 仍使用 Flask 开发服务器，不建议直接暴露到公网。
- 默认账号密码仍为 `admin` / `admin123`，首次登录后请立即修改。

## [1.0.1] - 上游版本

上游项目版本，反向代理功能仅支持 Windows。本仓库此前基于该版本适配飞牛 NAS 的 Docker 部署。