# https_ssl 自签证书管理系统 —— 单容器镜像（Flask + OpenSSL + nginx）
#
# 3.9 已停止维护，这里使用仍在支持期内的 3.12（代码兼容 3.9+）。
FROM python:3.12-slim

LABEL org.opencontainers.image.title="https_ssl" \
      org.opencontainers.image.description="自签证书管理面板：创建 CA、签发证书、把局域网 HTTP 服务反向代理成 HTTPS" \
      org.opencontainers.image.url="https://github.com/guoxpeng/https_ssl" \
      org.opencontainers.image.source="https://github.com/guoxpeng/https_ssl" \
      org.opencontainers.image.documentation="https://github.com/guoxpeng/https_ssl/blob/main/INSTALL.md" \
      org.opencontainers.image.licenses="MIT"

# 证书有效期等时间戳由 datetime.now() 生成，容器默认 UTC 会差 8 小时。
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1. 换源并安装系统依赖（OpenSSL、nginx、时区数据）
#    bookworm 之后 apt 源改为 deb822 格式，这里两种路径都兼容，
#    避免基础镜像换代后 sed 找不到文件导致构建中断。
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|mirrors.ustc.edu.cn|g" /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|mirrors.ustc.edu.cn|g" /etc/apt/sources.list; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
         openssl ca-certificates nginx tzdata \
    && rm -rf /var/lib/apt/lists/*

# 2. 安装 Python 依赖（使用清华源加速）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 复制项目文件（凭据、私钥与运行数据由 .dockerignore 排除）
COPY . .

# 4. 复制 nginx 主配置与启动脚本
COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/proxy/sites /app/proxy/certs /app/data

# 5. 声明持久化目录。不挂载时会落到匿名卷，至少不会随容器删除而丢。
VOLUME ["/app/ca", "/app/certs", "/app/uploads", "/app/proxy", "/app/data"]

# 6. 暴露端口：2002 面板，其余端口由反代站点监听
EXPOSE 2002

# 7. 健康检查：容器里没有 curl，用自带的 python 探测面板首页。
#    shell 形式才能展开 ${QILIN_PORT}。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request;p=os.environ.get('QILIN_PORT') or 2002;urllib.request.urlopen('http://127.0.0.1:%s/login'%p,timeout=4)" || exit 1

# 8. 启动命令：先起 nginx，再拉起 Flask
CMD ["/entrypoint.sh"]
