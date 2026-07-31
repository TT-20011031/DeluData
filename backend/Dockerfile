FROM python:3.10-slim

ARG DEBIAN_MIRROR=https://mirrors.aliyun.com/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security
ARG DEBIAN_FRONTEND=noninteractive
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
      -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources \
 && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=30 update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
COPY constraints.txt /app/constraints.txt
RUN python -m pip install \
      --index-url "${PIP_INDEX_URL}" \
      --retries 5 \
      --timeout 120 \
      --upgrade pip \
 && python -m pip install \
      --index-url "${PIP_INDEX_URL}" \
      --retries 5 \
      --timeout 120 \
      -r /app/requirements.txt \
      -c /app/constraints.txt

COPY . /app

RUN mkdir -p /app/data /app/logs /app/static

EXPOSE 8000 8001
