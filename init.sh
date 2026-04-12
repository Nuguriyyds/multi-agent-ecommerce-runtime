#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

echo "=== 智能电商推荐系统 — 开发环境初始化 ==="

# 1. Python venv
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/4] 创建 Python 虚拟环境..."
    python3 -m venv "$VENV_DIR"
else
    echo "[1/4] 虚拟环境已存在，跳过创建"
fi

# 激活 venv
source "$VENV_DIR/bin/activate" 2>/dev/null || source "$VENV_DIR/Scripts/activate" 2>/dev/null

# 2. 安装依赖
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    echo "[2/4] 安装 Python 依赖..."
    pip install -q -r "$PROJECT_ROOT/requirements.txt"
else
    echo "[2/4] requirements.txt 不存在，跳过（Feature F01 尚未实现）"
fi

# 3. 环境配置
if [ -f "$PROJECT_ROOT/.env.example" ] && [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "[3/4] 从 .env.example 复制 .env..."
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
else
    echo "[3/4] .env 已存在或 .env.example 不存在，跳过"
fi

# 4. 启动 & 健康检查
if [ -f "$PROJECT_ROOT/main.py" ]; then
    echo "[4/4] 启动 FastAPI 开发服务器..."
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
    SERVER_PID=$!
    sleep 2

    # 健康检查
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "✓ 服务启动成功，健康检查通过"
    else
        echo "✗ 健康检查失败，请检查日志"
        kill $SERVER_PID 2>/dev/null
        exit 1
    fi
else
    echo "[4/4] main.py 不存在，跳过启动（Feature F01 尚未实现）"
fi

echo ""
echo "=== 初始化完成 ==="
echo "项目目录: $PROJECT_ROOT"
echo "虚拟环境: $VENV_DIR"
echo "下一步: 参考 feature_list.json 开始实现 Feature F01"
