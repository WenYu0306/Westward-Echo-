#!/bin/bash
# Westward Echo — server deployment script for Ubuntu 22.04
# Run as: bash deploy.sh

set -e

echo "=== Westward Echo Deployment ==="

# ── 1. Install Docker if missing ────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "Docker installed. You may need to re-login for group changes."
else
    echo "✓ Docker: $(docker --version)"
fi

# ── 2. Clone repo if missing ─────────────────────────────────────
REPO_DIR="$HOME/Westward-Echo"
if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning repository..."
    git clone https://github.com/WenYu0306/Westward-Echo-.git "$REPO_DIR"
fi
cd "$REPO_DIR"
git pull origin main

# ── 3. Create .env if missing ────────────────────────────────────
if [ ! -f .env ]; then
    echo ""
    echo "=== .env setup ==="
    read -p "Qwen API Key (sk-..., 判断层): " QWEN_KEY
    read -p "GLM API Key (sk-..., 文字层): " GLM_KEY
    read -p "API Key for admin access (leave empty for dev mode): " API_KEY

    cat > .env <<ENVEOF
LLM_API_KEY=${QWEN_KEY}
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.8-max
GLM_API_KEY=${GLM_KEY}
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_MODEL=glm-5.3-flash
GLM_FULL_MODEL=glm-5.3
GLM_WRITE_EFFORT=low
GLM_FIX_EFFORT=high
ANTHROPIC_API_KEY=
REDIS_URL=redis://redis:6379/0
API_KEY=${API_KEY}
LOG_LEVEL=INFO
ENVEOF
    echo "✓ .env created"
else
    echo "✓ .env exists"
fi

# ── 4. Start services ────────────────────────────────────────────
echo ""
echo "=== Starting services ==="
docker compose up -d --build

echo ""
echo "=== Deployment complete ==="
echo "Check status: cd $REPO_DIR && docker compose ps"
echo "View logs:   docker compose logs -f api"
echo "Health:      curl http://localhost:8000/api/health"
