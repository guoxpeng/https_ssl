# https_ssl 自签证书管理系统

<div align="center">
  <img src="./static/images/https_ssl-logo.png" alt="https_ssl Logo" width="150">
  <p>一款易用的自签证书管理系统</p>
  <img width="1559" height="1615" alt="image" src="https://github.com/user-attachments/assets/620f2eea-6351-4636-985e-2fd8d715b45c" />
  
<img width="1358" height="1369" alt="image" src="https://github.com/user-attachments/assets/d62b0fcc-bd11-4ffa-9285-86285eb35d1f" />

 <img width="938" height="621" alt="PixPin_2026-09-17_22-34-00" src="https://github.com/user-attachments/assets/b8f2eace-1592-4676-9ca9-90f9d3612e6c" />

</div>

[English](./README.en.md) · [部署说明（Docker）](./INSTALL.md)

## 项目介绍

https_ssl 是一个基于 Flask 和 OpenSSL 开发的自签证书管理系统，旨在简化 SSL 证书的创建、管理和验证过程。系统提供了直观的 Web 界面，使用户能够轻松创建自签名 CA 证书、签发服务器证书，并配置反向代理服务，无需深入了解复杂的 OpenSSL 命令。

本仓库在上游项目基础上完成 Linux / Docker 适配：反向代理不再依赖 Windows 专用的 `proxy.exe`，改为同一容器内的 nginx 进程实现，可在飞牛 NAS 等 Linux 环境下完整使用全部功能。

## 主要功能

### 1. 虚拟机构（CA）管理
- 创建自定义虚拟证书颁发机构（CA）
- 支持设置 CA 私钥密码保护
- 查看和下载 CA 证书
- 管理 CA 证书生命周期

### 2. 证书申请与管理
- 基于创建的 CA 签发服务器证书
- 支持多域名和多 IP 地址的 SAN 扩展
- 证书批量管理和删除
- 证书和私钥的安全下载

> 证书名称只能包含中英文、数字、下划线、连字符和点，长度 1–64，且不能重名
> （重名会返回 409，请先删除旧证书）。`ca` 为保留名称。

### 3. 证书验证
- 验证已签发证书的有效性：解析证书、校验私钥配对、检查有效期，
  再与证书的 SAN 比对所填地址，最后在回环地址完成一次真实 TLS 握手
- 支持上传自定义证书进行验证（此时按系统信任库校验证书链）

### 4. 反向代理服务
- 基于已签发证书配置 HTTPS 反向代理
- 将 HTTP 服务转换为 HTTPS 服务
- 支持多服务、多站点配置，配置变更热加载
- 管理反向代理服务的启动和停止

## 架构说明

整个系统只运行一个容器，Flask 与 nginx 在同一容器内由 `entrypoint.sh` 一并拉起：

```
┌───────────────────────────────────────────┐
│   qilin_ssl                               │
│                                           │
│  ┌──────────────┐    ┌──────────────────┐ │
│  │ nginx 进程   │    │ Flask 进程       │ │
│  │ · TLS 终止   │◄───│ · 证书签发       │ │
│  │ · 多站点路由 │    │ · CA 管理        │ │
│  │ · 热加载配置 │    │ · 反代配置管理   │ │
│  └──────────────┘    └──────────────────┘ │
│    :14000 等            :2002             │
└───────────────────────────────────────────┘
```

- **nginx**：在容器内承担 TLS 终止与请求转发，站点配置位于 `/app/proxy/sites`。
- **Flask**：证书全生命周期管理面板，同时负责生成 nginx 站点配置并执行 `nginx -s reload` 热加载。

反代不再依赖任何 Windows 专用二进制，也无需挂载 Docker socket，容器权限更小。

## 安装指南

> 完整的 Docker 部署步骤、配置说明、升级与排错见 **[部署说明（Docker）](./INSTALL.md)**。

### 系统要求

- Linux（已在飞牛 NAS / fnOS、Debian 12 验证）
- Docker Engine 20.10+ 与 Docker Compose v2
- 宿主机**无需**安装 Python / OpenSSL / nginx，它们都在镜像内

### 部署步骤

1. 将项目上传到服务器，例如 `/vol1/docker/https_ssl`。

2. 进入目录并启动：

   ```bash
   cd /vol1/docker/https_ssl
   docker compose up -d --build
   ```

3. 在浏览器中访问 `http://<服务器IP>:2002`。

4. 使用默认账号登录：
   - 用户名：`admin`
   - 密码：`admin`

   密码在首次启动时用于初始化账号，之后以哈希形式存于容器内 `/app/users.json`，
   请及时在「设置」页修改。想换成别的初始密码，见下方环境变量表。

### 反向代理端口

反向代理不需要额外容器。新增一个反代服务后，请在 `docker-compose.yml` 的 `ports` 中把该服务要监听的端口映射出来，例如：

```yaml
    ports:
      - "2002:2002"      # 管理面板
      - "14000:14000"    # 某个反向代理服务
```

修改后执行 `docker compose up -d` 重建容器即可。之后在面板「反向代理」页面新增服务并点击启用，站点配置由面板自动生成并热加载。

## 使用指南

### 创建虚拟机构（CA）

1. 登录系统后，在主页点击「创建」按钮
2. 填写机构名称（可选）
3. 设置私钥密码（可选，增强安全性）
4. 点击「创建」完成 CA 证书生成
5. 下载 CA 证书并安装到本地受信任的根证书颁发机构

### 申请服务器证书

1. 在主页的「证书列表」区域点击「新增」按钮
2. 填写证书名称
3. 输入需要支持的 IP 地址（多个地址用分号分隔）
4. 输入需要支持的域名（多个域名用分号分隔）
5. 点击「创建」完成证书申请
6. 下载证书和私钥文件

### 配置反向代理

1. 点击侧边栏的「反向代理」菜单
2. 点击「新增」按钮
3. 填写服务名称（该名称同时作为证书名与站点配置名）
4. 输入原始服务地址（如 `http://192.168.5.3:4000`）
5. 输入反向代理后的地址（如 `https://192.168.5.3:14000`）
6. 选择证书类型（https_ssl 申请的证书或上传自定义证书）
7. 点击「创建」完成配置
8. 点击开关启用服务

配置变更会通过 `nginx -s reload` 热加载，不影响其他已启用服务。

### 验证证书

1. 点击侧边栏的「证书验证」菜单
2. 输入需要验证的 IP 地址或域名
3. 选择证书类型（https_ssl 申请的证书或上传自定义证书）
4. 点击「验证」按钮查看验证结果

## 配置项

支持以下环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `QILIN_ADMIN_PASSWORD` | `admin` | 首次启动创建 admin 账号用的初始密码。**只在还没有 `users.json` 时生效**，之后改这个变量不会影响已有账号 |
| `QILIN_SECRET_KEY` | 随机 | 会话签名密钥。留空时每次启动随机生成，**容器重启会导致所有登录失效**，建议固定 |
| `QILIN_COOKIE_SECURE` | `0` | 置 1 时会话 Cookie 仅在 HTTPS 下发送（面板经 HTTPS 反代暴露时使用） |
| `QILIN_PROXY_DIR` | `/app/proxy` | 反向代理站点配置与证书目录 |
| `QILIN_PROXY_LISTEN_HOST` | 空 | 代理监听地址，留空表示监听全部地址 |
| `QILIN_OPENSSL` | `/usr/bin/openssl` | OpenSSL 可执行文件路径 |

## 自检

`scripts/smoke_test.py` 会在临时目录里完整跑一遍流程（建 CA → 签发 → 验证 →
反代创建/启停/删除 → 删除联动），不会读写项目里的真实数据：

```bash
python scripts/smoke_test.py
# Windows 下若 openssl 不在 PATH：
#   set QILIN_OPENSSL=C:\Program Files\Git\usr\bin\openssl.exe
```

容器内的真机验证脚本（针对已运行的面板）：

```bash
docker exec -e QILIN_PASS='你的密码' qilin_ssl bash /app/_verify_e2e.sh
docker exec -e QILIN_PASS='你的密码' qilin_ssl bash /app/_verify_proxy.sh
```

## 安全说明

- 系统生成的 CA 证书和私钥仅用于测试和开发环境
- 在生产环境中，建议使用正规 CA 机构签发的证书
- 请妥善保管 CA 私钥，避免泄露
- CA 私钥不会通过面板分发（`/download/ca/` 只放行根证书 `qilin-ca.crt`），
  需要备份请直接从宿主目录 `./data/ca` 取
- 定期更换用户密码，提高系统安全性
- 容器内 nginx 与 Flask 以同一用户运行，请勿将该容器暴露到不可信网络
- 管理员默认账号为 `admin` / `admin`，首次启动时由 `QILIN_ADMIN_PASSWORD` 初始化
  （缺省 `admin`，启动日志会提示改密码）；请尽快在「设置」页更换

## 常见问题

1. **证书不被浏览器信任怎么办？**
   - 需要将生成的 CA 证书安装到操作系统的受信任根证书存储区

2. **如何在移动设备上信任证书？**
   - 将 CA 证书发送到移动设备并在设备设置中安装证书

3. **反向代理服务无法启动？**
   - 确认所填服务名对应的证书已存在（证书名需与服务名一致）
   - 检查容器内 nginx 配置是否正确：`docker exec qilin_ssl nginx -t`
   - 检查端口是否已在 `docker-compose.yml` 中映射、以及宿主端口是否被占用：`ss -lntp | grep <端口>`

4. **修改反向代理端口后不生效？**
   - 面板保存配置后会自动重载 nginx。若未生效，手动执行：
     `docker exec qilin_ssl nginx -s reload`

5. **创建反向代理后列表里没有 / 提示启动失败？**
   - 先确认该端口已在 `docker-compose.yml` 的 `ports` 中映射，否则容器内监听成功但宿主访问不到
   - 服务启动失败时面板会直接显示 nginx 的报错原文，按提示处理即可

6. **证书验证提示"证书未覆盖地址"？**
   - 说明所填 IP/域名不在该证书的 SAN 列表中。请核对申请证书时填写的
     IP 与域名（支持 `*.example.com` 形式的通配）

7. **证书验证提示"证书链校验失败"？**
   - 自定义证书是按系统信任库校验的：自签证书或缺少中间证书的链都会失败，
     浏览器同样会报警告。把 CA 证书装进系统信任库，或换用完整链后再试

8. **重新创建 CA 后原来签发的证书还能用吗？**
   - 不能。新 CA 无法验证旧证书，面板会自动停用受影响的反向代理服务，
     需要重新签发证书后再启用

## 版本信息

当前版本：v1.3.2

变更记录见 [CHANGELOG.md](./CHANGELOG.md)。

## 许可协议

本项目采用 [MIT 许可证](./LICENSE)。

血缘关系：作者源库 [linzxcw/qilin_SSL](https://github.com/linzxcw/qilin_SSL)（原始项目）
→ [guoxpeng/fnos_qilin_SSL](https://github.com/guoxpeng/fnos_qilin_SSL)（其 fnOS 适配分支）
→ 本仓库 `https_ssl`（在后者基础上完成 Linux / Docker 适配，反代由 Windows 二进制改为同容器内的 nginx）。

> 注意：原始仓库未附许可证文件。若计划公开发布，建议先联系原作者取得授权。
