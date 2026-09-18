# https_ssl

**自签证书管理面板** —— 在浏览器里建根 CA、签发证书、把局域网里的 HTTP 服务一键反向代理成 HTTPS。

> ### 📦 项目主页、完整文档与问题反馈都在 GitHub
> ## 👉 https://github.com/guoxpeng/https_ssl
>
> Docker Hub 这里只放镜像。**安装说明、常见问题、更新日志请以 GitHub 为准**，
> 遇到问题也请到 GitHub 提 Issue（Docker Hub 的评论区不常看）。
> 觉得好用的话，**点个 ⭐ Star** 就是最大的支持。

[![GitHub stars](https://img.shields.io/github/stars/guoxpeng/https_ssl?style=social)](https://github.com/guoxpeng/https_ssl)
[![GitHub release](https://img.shields.io/github/v/release/guoxpeng/https_ssl)](https://github.com/guoxpeng/https_ssl/releases)
[![License](https://img.shields.io/github/license/guoxpeng/https_ssl)](https://github.com/guoxpeng/https_ssl/blob/main/LICENSE)

---

## 这是什么

家里的 NAS、路由器、软路由上跑着一堆 Web 服务，默认都是 `http://192.168.x.x:端口`，
浏览器天天报「不安全」，有些功能（剪贴板、摄像头、PWA）还必须在 HTTPS 下才能用。

这个面板解决的就是这件事：

- 🏢 **建根 CA** —— 一次生成自己的根证书，装到电脑 / 手机 / 设备上，之后所有证书都被信任
- 📜 **签发证书** —— 填域名或 IP 就签，支持 SAN、支持下载完整证书链
- 🔀 **反向代理** —— 把别的设备的 HTTP 服务，用本机的端口代理成 HTTPS，TLS 在本机终止
- 🔒 **自带 HTTPS 也能接** —— 后端服务自己就有证书？直接上传它的证书和私钥，不用重签
- 🧩 **单容器** —— Flask + OpenSSL + nginx 全在一个镜像里，不用额外装东西

界面是中文的，全程点鼠标，不需要记 OpenSSL 命令。

---

## 快速开始

> 默认使用 **host 网络模式**（仅 Linux），反向代理端口**填了即生效**，
> 不用改配置也不用重建容器。macOS / Windows 的 Docker Desktop 不支持 host 模式，
> 请见 GitHub 上的 `docker-compose.bridge.yml`。

### 方式一：一行命令（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/guoxpeng/https_ssl/main/install.sh | bash
```

脚本会自动拉镜像、生成随机会话密钥、起容器，装完打印面板地址。

### 方式二：docker compose

```bash
mkdir https_ssl && cd https_ssl
curl -fsSL -o docker-compose.yml \
  https://raw.githubusercontent.com/guoxpeng/https_ssl/main/docker-compose.yml
docker compose up -d
```

### 方式三：docker run

```bash
docker run -d --name qilin_ssl \
  --network host \
  --restart always \
  -e TZ=Asia/Shanghai \
  -e QILIN_ADMIN_PASSWORD=admin \
  -e QILIN_SECRET_KEY=$(openssl rand -hex 32) \
  -e QILIN_HOST_NETWORK=1 \
  -e QILIN_USERS_FILE=/app/data/users.json \
  -v ./data/ca:/app/ca \
  -v ./data/certs:/app/certs \
  -v ./data/uploads:/app/uploads \
  -v ./data/proxy:/app/proxy \
  -v ./data:/app/data \
  nameguoguo/https_ssl:latest
```

装好后浏览器打开 `http://<设备IP>:2002`，默认账号 `admin` / `admin`
（**登录后请立刻到「设置」页改密码**）。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `QILIN_PORT` | `2002` | 面板监听端口。host 模式下直接绑宿主机端口 |
| `QILIN_ADMIN_PASSWORD` | `admin` | admin 初始密码。**只在还没有用户表时生效** |
| `QILIN_SECRET_KEY` | 随机 | 会话签名密钥。不设置则每次启动重新生成，重启后登录全部失效，建议固定 |
| `QILIN_COOKIE_SECURE` | `0` | 面板经 HTTPS 暴露时置 `1`，会话 Cookie 仅在 HTTPS 下发送 |
| `QILIN_USERS_FILE` | `/app/users.json` | 用户表位置。建议指向数据卷，重建容器不会丢密码 |
| `QILIN_PROXY_DIR` | `/app/proxy` | 反向代理配置与证书目录 |
| `QILIN_HOST_NETWORK` | 空 | 置 `1` 表示当前是 host 模式，界面提示会相应变化 |
| `TZ` | `UTC` | 时区。证书有效期等时间戳按它显示，国内建议 `Asia/Shanghai` |

---

## 数据持久化

**升级 / 重建容器前请确认这几个目录已挂载出来**，否则数据会丢：

| 容器内路径 | 内容 |
|---|---|
| `/app/ca` | 根证书与 CA 私钥（`https-ssl-ca.crt` / `.key`，1.6.1 起由 `qilin-ca.*` 自动改名） |
| `/app/certs` | 签发的所有证书 |
| `/app/uploads` | 上传的自定义证书 |
| `/app/proxy` | 反向代理站点配置与证书 |
| `/app/data` | 用户表（`users.json`）等运行时状态 |

---

## 支持的标签

| 标签 | 说明 |
|---|---|
| `latest` | 最新稳定版，跟随 `main` 分支 |
| `1.6` / `1.6.0` | 语义化版本标签，生产环境建议锁定到具体版本 |
| `main` | `main` 分支最新构建 |

镜像同时支持 `linux/amd64` 与 `linux/arm64`，群晖、树莓派等 ARM 设备可直接拉取。

---

## 相关链接

- **GitHub 仓库（文档 / 更新日志 / 问题反馈）**：https://github.com/guoxpeng/https_ssl
- **安装与配置详解**：https://github.com/guoxpeng/https_ssl/blob/main/INSTALL.md
- **更新日志**：https://github.com/guoxpeng/https_ssl/blob/main/CHANGELOG.md
- **提交 Issue**：https://github.com/guoxpeng/https_ssl/issues

## 许可证

MIT
