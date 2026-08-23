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
    read -p "Qwen API Key (sk-..., 主模型): " QWEN_KEY
    read -p "DeepSeek API Key (sk-..., 可留空作 fallback): " DEEPSEEK_KEY
    read -p "API Key for admin access (leave empty for dev mode): " API_KEY

    cat > .env <<ENVEOF
DEEPSEEK_API_KEY=${DEEPSEEK_KEY}
DEEPSEEK_FLASH_MODEL=deepseek-v4-flash
DEEPSEEK_PRO_MODEL=deepseek-v4-pro
LLM_API_KEY=${QWEN_KEY}
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
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
