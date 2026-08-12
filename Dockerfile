FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: pip install (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Layer 2: source code (changes most often)
COPY src/ ./src/
COPY docs/anchor-v4.html docs/anchor-v5.html ./docs/
COPY cultural_rules.json ./

RUN mkdir -p /app/data /app/output /app/novels

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
