FROM python:3.11-slim

WORKDIR /app

# 换清华源：国内服务器直连 deb.debian.org 会卡死（实测 apt update 单步超 10 分钟）
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ trixie main" > /etc/apt/sources.list \
    && echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ trixie-updates main" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian-security/ trixie-security main" >> /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: pip install (cached unless requirements.txt changes)
# 阿里云 pip 源：服务器在国内，直连 PyPI 会卡死
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# Layer 2: source code (changes most often)
COPY src/ ./src/
COPY docs/anchor-v4.html docs/anchor-v5.html ./docs/
COPY cultural_rules.json ./

RUN mkdir -p /app/data /app/output /app/novels

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
