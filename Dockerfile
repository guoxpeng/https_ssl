# https_ssl 自签证书管理系统
# 3.9 已停止维护，这里使用仍在支持期内的 3.12（代码兼容 3.9+）。
FROM python:3.12-slim

WORKDIR /app

# 1. 换源并安装系统依赖（OpenSSL 与 nginx）
#    bookworm 之后 apt 源改为 deb822 格式，这里两种路径都兼容，
#    避免基础镜像换代后 sed 找不到文件导致构建中断。
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|mirrors.ustc.edu.cn|g" /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|mirrors.ustc.edu.cn|g" /etc/apt/sources.list; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends openssl ca-certificates nginx \
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
    && mkdir -p /app/proxy/sites /app/proxy/certs

# 5. 暴露端口：2002 面板，其余端口由反代站点监听
EXPOSE 2002

# 6. 启动命令：先起 nginx，再拉起 Flask
CMD ["/entrypoint.sh"]
