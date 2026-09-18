# https_ssl 部署说明

## 这个项目能做什么

**一句话**：给局域网里的 HTTP 服务套上 HTTPS，并且让内网设备真正信任它。

自签证书不难，难的是"自己签的证书浏览器不认"。这个项目把整套流程做成一个网页面板：
生成自己的根 CA → 用它签发服务器证书 → 一键配好 HTTPS 反向代理 → 把根 CA 装到设备上，
之后内网访问就是带锁的绿标。

### 主要功能

**1. 自建根证书（CA）**
- 一键创建自己的证书颁发机构，可选私钥密码保护
- 下载根证书安装到手机/电脑，让设备信任你签发的所有证书

**2. 签发服务器证书**
- 基于自己的 CA 签发证书，支持多域名和多 IP（SAN 扩展）
- 支持通配域名（`*.example.com`）
- 证书列表可查看到期时间、批量删除

**3. HTTPS 反向代理**
- 把已有的 HTTP 服务（比如 `http://192.168.5.3:4000`）转成 HTTPS
- 面板里填两个地址、选一张证书就能启用，不用手写 nginx 配置
- 启停是热加载，不影响其他服务

**4. 证书验证**
- 检查一张证书是否有效、链是否完整、是否覆盖某个域名/IP
- 支持上传外部证书一起验证

### 典型场景

- 群晖 / 飞牛 NAS、iKuai、iStoreOS 等内网设备的管理页改成 HTTPS
- 内网自建服务（NAS、Docker 应用、Home Assistant）不再弹"不安全"警告
- 内网服务必须 HTTPS 才能用的场景（PWA、摄像头、剪贴板 API 等）

---

## 安装步骤（Docker）

宿主机只需要 **Docker**，不用装 Python、OpenSSL 或 nginx。

### 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh | bash
```

脚本会依次：检查 Docker / Compose → 拉取 Docker Hub 上的镜像 → 生成带随机会话密钥的
`.env` → 启动容器 → 等待面板就绪 → 打印访问地址。默认装到当前目录下的 `https_ssl`。

镜像地址是 `nameguoguo/https_ssl`，同时支持 amd64 与 arm64，**不需要 git、不编译，几十秒装完**。
国内直连 Docker Hub 经常超时，用 `IMAGE` 换镜像加速站，或 `IMAGE=build` 改回从源码构建。

想换安装目录或端口：

```bash
curl -fsSL https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh \
  | INSTALL_DIR=/vol1/docker/https_ssl PANEL_PORT=2002 bash
```

可用的环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `IMAGE` | `nameguoguo/https_ssl:latest` | 镜像地址。换成加速站地址即可走国内镜像；置为 `build` 则改为克隆仓库、从源码构建（需要 git，耗时几分钟） |
| `INSTALL_DIR` | `<当前目录>/https_ssl` | 安装目录 |
| `PANEL_PORT` | `2002` | 面板端口 |
| `ADMIN_PASSWORD` | `admin` | 管理员初始密码，登录后请到「设置」页修改 |
| `REPO` | 本仓库 | 代码仓库地址 |
| `SUDO` | 自动判断 | 置 `1` 强制用 sudo，置 `0` 强制不用 |

> 端口被占用时脚本会明确提示，并给出换端口重跑的命令——代码和 `.env` 都不会丢。

> 不想把脚本直接喂给 shell 的话，先下载看一眼再执行：
>
> ```bash
> curl -fsSLO https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh
> less install.sh && bash install.sh
> ```

> **`curl | bash` 时 sudo 无法弹密码提示**（stdin 被脚本占用）。如果你的账号不在
> `docker` 组（飞牛 NAS 的 `admin` 就是这种），先 `sudo -v` 缓存一次凭据，
> 或者用 `curl -fsSL <地址> | sudo bash` 直接以 root 执行。

### 网络模式（固定 host）

**只支持 `host` 模式**，所以：**面板里填端口 → 点启用 → 结束，能访问了。**
不用改 compose，也不用重建容器。

早期版本还有个 `bridge` 备选，**已经移除**。原因就是它太容易踩：Docker 桥接网络的
端口映射在**容器创建时就固定了**，运行时加不了。于是 `bridge` 下每加一个端口，都得
先改 compose 的 `ports` 再 `docker compose up -d` 重建容器 —— 否则容器里 nginx 明明
监听着（`docker exec` 进去能 `curl` 通），从局域网访问却是「连接被拒」，
很容易被当成证书或面板的 bug。

选 host 就要接受这几个代价：

| | 说明 |
|---|---|
| 网络隔离 | 无，容器与宿主机共用网络栈 |
| 平台 | **仅 Linux**。macOS / Windows 的 Docker Desktop 没有 host 模式，请用 Linux 虚拟机 |
| 端口被宿主机别的服务占用 | 只有那一个站点加载失败，已运行的站点不受影响 |

> 面板端口由 `.env` 里的 `QILIN_PORT` 决定（默认 2002），没有端口映射一说。

**反代端口从外面访问不到？** 按这两条查，基本一眼就能定位：

```bash
ss -ltn | grep -E ':(5245|14000)'                          # 宿主机上到底有没有在监听
sudo docker inspect -f '{{.HostConfig.NetworkMode}}' qilin_ssl   # 期望输出 host
```

第二条如果输出 `bridge`（用图形化面板建容器时容易选错），说明容器不在受支持的
网络模式下：容器里 nginx 在监听、端口却没映射到宿主机。重建容器时把网络模式改成
`host` 即可，**数据卷保留就不会丢证书和反代配置**：

```bash
sudo docker rm -f qilin_ssl
sudo docker run -d --name qilin_ssl --restart always --network host \
  -e TZ=Asia/Shanghai -e QILIN_PORT=2002 \
  -v ./data/ca:/app/ca -v ./data/certs:/app/certs -v ./data/uploads:/app/uploads \
  -v ./data/proxy:/app/proxy -v ./data:/app/data \
  nameguoguo/https_ssl:latest
```

### 手动安装

**1. 拿到部署文件**

方式 A —— 直接用 Docker Hub 镜像（推荐，不需要 git）：

```bash
mkdir https_ssl && cd https_ssl
curl -fsSL -o docker-compose.yml \
  https://raw.githubusercontent.com/guoxpeng/https_ssl/main/docker-compose.yml
```

方式 B —— 从源码构建：

```bash
git clone https://github.com/guoxpeng/https_ssl.git
cd https_ssl
```

也可以直接把项目目录上传到服务器，例如 `/vol1/docker/https_ssl`。

**2. 启动**

方式 A：

```bash
docker compose up -d
```

方式 B：

```bash
docker compose -f docker-compose.build.yml up -d --build
```

> 飞牛 NAS 等系统上 `admin` 用户不在 docker 组，命令前要加 `sudo`：
> `sudo docker compose up -d`

> 升级到新版本：方式 A 执行 `docker compose pull && docker compose up -d`；
> 方式 B 执行 `git pull && docker compose -f docker-compose.build.yml up -d --build`。
> 用户表默认落在挂载出来的 `./data/users.json`，升级不会丢密码。

> **关于「颁发给」里的中文：** 1.6.1 之前机构名填中文会写成双重编码的乱码，
> 1.6.1 已修复。证书主题是签发时写死的，所以升级后要让「颁发给」变成中文，
> 必须到面板里把 CA 和名字带中文的证书**重新创建一遍**（同名重新签发即可，
> 反代记录仍指向同名证书）。旧证书链校验不受影响，不重建也能继续用。
> 老版本写的 `qilin-ca.key/crt/cnf/srl` 会在容器启动时自动改名成 `https-ssl-ca.*`，
> 数据卷里的 CA 不会丢。

**3. 打开面板**

浏览器访问：

```
http://<服务器IP>:2002
```

默认账号：

| 用户名  | 密码    |
| ---- | ----- |
| `admin` | `admin` |

**登录后请立即到「设置」页改掉密码。**

### 想换掉默认密码

在项目目录建一个 `.env` 文件（不改也能跑，密码就是 `admin`）：

```bash
echo 'QILIN_ADMIN_PASSWORD=你的密码' > .env
docker compose up -d
```

注意这个密码只在**首次启动**时用来创建账号；账号建好后改 `.env` 不再生效，
改密码请用面板的「设置」页。

---

## 上手三步

### 1. 创建根证书（CA）

主页 → 「创建」→ 填机构名称（可留空）→ 点创建。
然后下载 `https-ssl-ca.crt`，装到需要访问的设备上（见下方「让设备信任 CA」）。

### 2. 签发一张服务器证书

主页「证书列表」→ 「新增」→ 填证书名称 → 填要支持的 IP 和域名（多个用分号隔开）
→ 点创建。

例如给 NAS 签一张：

```
证书名称：nas
IP 地址：192.168.5.3
域名：nas.lan
```

### 3. 配置反向代理

**它在做什么**：把**别的设备**上的 HTTP 服务，用**本机（跑面板的这台机器）的端口**
代理成 HTTPS 服务。TLS 在本机终止，后端服务在哪台机器上都不影响。

**host 模式（唯一模式）不用做任何额外操作**——端口填了即生效，直接到面板「反向代理」页：

- 服务名称：`nas`
- 原本地址：`http://192.168.5.3:4000`
- 反代后地址：`https://192.168.5.3:14000`
- 证书：选刚才签发的 `nas`

**两个地址怎么填**：

| 字段 | 填什么 |
|---|---|
| 原本地址 | 后端服务的真实地址，**可以是别的设备**（如 `http://192.168.5.5:5244`） |
| 反代后地址 | **IP 只能填本机**（跑面板的这台机器），如 `https://192.168.5.3:5245` |

原因：HTTPS 是在本机终止的，浏览器敲的也是本机地址，所以证书的 SAN 里必须有本机 IP，
访问时也只能用本机 IP。

**后端自己就是 HTTPS？** 不用让面板再签一张——「证书类型」选**上传自定义证书**，
把后端服务自己的证书和私钥传上来即可，原本地址照旧填后端的原 IP 和端口。

**保存 ≠ 启用**，两步都要做：

1. 点「创建」保存 —— 服务记录建好了，但状态是未启用，端口还没开始监听；
2. 在列表里点开关启用 —— 这一步才生成 nginx 站点并热加载，**不需要重启容器**。

启用后立刻访问 `https://192.168.5.3:14000` 验证。端口挑宿主机没被占用的
（`ss -lntp | grep 14000`）；万一撞车，只有这一个站点加载失败，面板会显示 nginx
的报错原文，其它已启用服务不受影响。

> 不需要提前声明端口，也不需要重建容器 —— 这是 host 模式的直接好处。
>
> 下面这段是旧版本 bridge 模式的写法，**现已不支持**，留在这里只为对照旧文档：
>
> ```yaml
>     ports:
>       - "2002:2002"      # 管理面板
>       - "14000:14000"    # 反向代理服务
> ```

---

## 签发证书后，在电脑上怎么用

面板里点完「创建」只是把证书文件生成出来了，电脑真正认它还需要两步：
**把根证书装进电脑**，**把证书和私钥装到你要保护的服务上**。

### 1. 把根证书装进电脑

主页「证书下载」里点 `下载CA证书`，得到 `https-ssl-ca.crt`，然后：

| 系统 | 做法 |
|---|---|
| Windows | `.\scripts\trust-ca-windows.ps1 -CaPath .\https-ssl-ca.crt`（普通权限即可，装进当前用户） |
| Windows（全机器） | 管理员 PowerShell：`certutil -addstore -f Root .\https-ssl-ca.crt` |
| Firefox | 不读 Windows 信任库，需单独处理（见下） |
| macOS | 双击导入「钥匙串访问」→ 系统 → 该证书 → 显示简介 → 信任 → 始终信任 |
| Linux | `sudo sh scripts/trust-ca-unix.sh ./https-ssl-ca.crt` |
| iOS / Android | 传到设备安装描述文件，再到设置里手动开启「完全信任」 |

Windows 下不加管理员也能装进**当前用户**的受信任根，Chrome / Edge 立刻生效。
想让全机器生效（系统服务、其他账号），用管理员再跑一次上面的 `certutil`。

**Firefox 有自己的信任库，不读 Windows 存储**，需要单独处理，二选一：

- `about:config` → 把 `security.enterprise_roots.enabled` 设为 `true`，重启浏览器；
- 或 设置 → 隐私与安全 → 证书 → 查看证书 → 证书颁发机构 → 导入 `https-ssl-ca.crt`，
  勾选"信任由此 CA 标识的网站"。

装完之后，凡是这张 CA 签发的证书，浏览器都不再报警告。

> 装完仍提示不安全？先确认访问的地址确实在该证书的 SAN 里
> （面板「证书验证」页可以直接查），再确认中间没有别的代理换掉了证书。

### 2. 把证书和私钥装到服务上

「证书列表」每行都有两个下载按钮：

- `xxx.crt` —— 服务器证书，含公钥，可以随便给
- `xxx.key` —— 私钥，**不要外传**，放到服务器上后建议 `chmod 600`

以 nginx 为例：

```nginx
server {
    listen 443 ssl;
    server_name nas.lan;

    ssl_certificate     /etc/nginx/certs/fullchain.crt;
    ssl_certificate_key /etc/nginx/certs/xxx.key;

    location / {
        proxy_pass http://127.0.0.1:8080;
    }
}
```

> **链要拼全**，否则部分客户端会报「证书链不完整」：
> `cat xxx.crt https-ssl-ca.crt > fullchain.crt`，`ssl_certificate` 指向 `fullchain.crt`。
> 用面板的「反向代理」功能则不用管，它会自动拼好。

其它服务同理：

- **Node.js**：`https.createServer({ cert, key })`
- **Python**：`ssl.SSLContext(...).load_cert_chain(cert, key)`
- **Docker 服务**：把两个文件挂进容器，在应用配置里指过去
- **群晖 / 飞牛等 NAS**：控制面板里「证书」→ 导入，选这两个文件

私钥必须和证书配对，不放心可以在面板「证书验证」页上传自检。

### 3. 验证

- 浏览器打开 `https://<域名或IP>`，应该是绿锁、没有警告
- 面板「证书验证」页填地址，它会做一次真实 TLS 握手并给出结果
- 命令行自查：

  ```bash
  openssl s_client -connect nas.lan:443 -CAfile https-ssl-ca.crt </dev/null 2>&1 \
    | grep 'Verify return code'
  ```

  输出 `Verify return code: 0 (ok)` 就说明链路正确。

### 4. 懒得手动配？

面板的「反向代理」就是为这个场景准备的：填两个地址、选一张证书，证书链与 nginx
配置全部自动生成，改完热加载，不用重启容器。

---

## 让面板自己也走 HTTPS

面板默认是明文 HTTP（`http://<服务器IP>:2002`），浏览器会提示"此站点的连接不安全"——
这是正常的，它就是个 HTTP 页面。注意 `https://<服务器IP>:2002` 是打不开的，
面板本身不提供 TLS。想让它也变成 HTTPS，用面板自带的反向代理功能就行：

1. 挑一个宿主机没被占用的端口（`ss -lntp | grep 12002`），不用改 compose ——
   端口填了即生效。

2. 面板「反向代理」→ 新增：

   - 服务名称：`panel`
   - 原本地址：`http://<服务器IP>:2002`
   - 反代后地址：`https://<服务器IP>:12002`
   - 证书：选一张 SAN 里包含该 IP 的证书

   保存后点开关启用，之后用 `https://<服务器IP>:12002` 访问面板。
   原来的 `http://<服务器IP>:2002` 仍然可用，出问题可以退回去。

自签证书浏览器仍会提示"不受信任"，把根证书装进设备（见上一节）才会显示绿锁。

---

## 常用命令

```bash
# 看状态和日志
docker compose ps
docker compose logs -f

# 重启 / 停止
docker compose restart
docker compose down

# 升级（用 Docker Hub 镜像）
docker compose pull && docker compose up -d

# 升级（从源码构建的，先 git pull）
docker compose -f docker-compose.build.yml up -d --build

# 体检：对真实数据跑一遍关键接口
docker cp scripts/verify_deploy.py qilin_ssl:/tmp/
docker exec qilin_ssl python /tmp/verify_deploy.py

# 全流程冒烟测试（在临时目录跑，不碰你的数据）
docker exec qilin_ssl python /app/scripts/smoke_test.py
```

## 备份

要备份的只有两样：`data/` 目录和 `.env` 文件。

```bash
tar czf https_ssl_backup.tar.gz data .env
```

`data/ca/https-ssl-ca.key` 是整个体系的根，**丢了所有已签发的证书都作废**，建议离线另存一份。

---

## 注意事项

**1. 不要暴露到公网。** 面板用的是 Flask 开发服务器，只适合内网使用。
确需对外，请在前面再套一层 HTTPS 反代。

**2. 用户表在哪，决定重建容器会不会丢密码。** 账号文件默认在容器内
`/app/users.json`，**重建容器会按 `.env` 的初始密码重新创建账号**。
仓库自带的 compose 文件已经把 `QILIN_USERS_FILE` 指向挂载出来的
`/app/data/users.json`，用它们部署时升级、重建都不会丢密码。

如果你用的是自己写的 compose 或 `docker run`，没设这个变量，升级前先备份：

```bash
docker cp qilin_ssl:/app/users.json ./users.json.bak     # 升级前
# ...重建容器...
docker cp ./users.json.bak qilin_ssl:/app/users.json
docker exec qilin_ssl chmod 600 /app/users.json
docker restart qilin_ssl     # USERS 在启动时读入，只 cp 不重启不生效
```

**3. 反代端口从外面访问不到。** 本项目只支持 `host` 网络模式，端口填了即生效，
没有「要先声明」这一步。真的连不上时先确认容器没被改成 bridge：

```bash
sudo docker inspect -f '{{.HostConfig.NetworkMode}}' qilin_ssl   # 期望 host
```

另外，面板里「创建」之后还要**点开关启用**，只保存不启用不会监听；
端口撞车则只有那一个站点失败，面板会显示 nginx 的报错原文。

**4. 登录后老掉线。** `.env` 里没设 `QILIN_SECRET_KEY` 时，每次重启都会换会话密钥。
固定下来：

```bash
echo "QILIN_SECRET_KEY=$(openssl rand -hex 32)" >> .env
```

**5. 反代开关报「证书 X 不存在」。** 该服务引用的证书被删了。到「反向代理」页编辑该服务，
重新选一张证书保存即可。

---

## 许可协议

本项目采用 [MIT 许可证](./LICENSE)。

**作者源库**：[linzxcw/qilin_SSL](https://github.com/linzxcw/qilin_SSL)
（原始项目）→ [guoxpeng/fnos_qilin_SSL](https://github.com/guoxpeng/fnos_qilin_SSL)
（其 fnOS 适配分支）→ 本仓库 `https_ssl`（Linux / Docker 适配）。

> 注意：原始仓库未附许可证文件。若计划公开发布，建议先联系原作者取得授权。
