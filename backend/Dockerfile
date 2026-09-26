# 网站二（RAG 内核）镜像
# 轻量：python slim + fastembed(ONNX)，不装 torch
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_ENDPOINT=https://hf-mirror.com

WORKDIR /app

# 先装依赖（利用缓存层）
# 注：国内服务器默认 PyPI/DockerHub 不可达，恒走腾讯云 PyPI 镜像，否则构建静默卡死
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple -r requirements.txt

# 拷贝 poc 包
COPY poc ./poc

# 批次17：粉笔登录会话需要 chromium（Debian slim 无 --with-deps 所需包名，
# 手动装依赖；国内网络恒走腾讯 apt 源 + npmmirror 浏览器镜像）
RUN sed -i 's|deb.debian.org|mirrors.tencent.com|g' /etc/apt/sources.list.d/debian.sources || true \
    && apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        fonts-liberation fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/* \
    && PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ \
       playwright install chromium

# 数据目录（挂载卷，见 docker-compose）
RUN mkdir -p /data/vector /data/models /data/raw

EXPOSE 9000
CMD ["python", "-m", "uvicorn", "poc.api:app", "--host", "0.0.0.0", "--port", "9000"]
