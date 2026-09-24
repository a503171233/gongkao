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

# 数据目录（挂载卷，见 docker-compose）
RUN mkdir -p /data/vector /data/models /data/raw

EXPOSE 9000
CMD ["python", "-m", "uvicorn", "poc.api:app", "--host", "0.0.0.0", "--port", "9000"]
