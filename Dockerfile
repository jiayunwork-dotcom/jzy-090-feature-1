FROM python:3.12-slim

# 不写 .pyc、日志不缓冲，便于容器内直接看到启动算例输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# 生产与测试依赖一并安装，使容器内可直接执行自动化测试
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .

EXPOSE 8000

# 容器化后监听固定端口对外服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# 轻量存活探针：使用标准库，无需在镜像内额外安装 curl
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"
