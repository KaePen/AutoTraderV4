#!/bin/bash
# AutoTrader WebUI 起動スクリプト

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="$PROJECT_ROOT/src/autotrader/web/frontend"

# 色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Options:"
    echo "  dev       開発モード（バックエンド + フロントエンド開発サーバー）"
    echo "  prod      本番モード（ビルド済みフロントエンドをFastAPIから配信）"
    echo "  backend   バックエンドのみ起動"
    echo "  frontend  フロントエンド開発サーバーのみ起動"
    echo "  build     フロントエンドをビルド"
    echo "  stop      全プロセスを停止"
    echo "  status    起動状態を確認"
    echo ""
    echo "Examples:"
    echo "  $0 dev     # 開発モードで起動"
    echo "  $0 prod    # 本番モードで起動"
}

check_dependencies() {
    if ! command -v uv &> /dev/null; then
        echo -e "${RED}Error: uv がインストールされていません${NC}"
        exit 1
    fi

    if ! command -v npm &> /dev/null; then
        echo -e "${RED}Error: npm がインストールされていません${NC}"
        exit 1
    fi
}

start_backend() {
    local port="${1:-8000}"
    local reload="${2:-}"

    echo -e "${GREEN}バックエンド起動中... (port: $port)${NC}"
    cd "$PROJECT_ROOT"

    if [ "$reload" = "--reload" ]; then
        uv run uvicorn autotrader.web.main:app --host 0.0.0.0 --port "$port" --reload &
    else
        uv run uvicorn autotrader.web.main:app --host 0.0.0.0 --port "$port" &
    fi

    echo $! > /tmp/autotrader_backend.pid
    echo -e "${GREEN}バックエンドPID: $(cat /tmp/autotrader_backend.pid)${NC}"
}

start_frontend_dev() {
    echo -e "${GREEN}フロントエンド開発サーバー起動中...${NC}"
    cd "$FRONTEND_DIR"

    # 依存関係チェック
    if [ ! -d "node_modules" ]; then
        echo -e "${YELLOW}node_modules が見つかりません。npm install を実行します...${NC}"
        npm install
    fi

    npm run dev &
    echo $! > /tmp/autotrader_frontend.pid
    echo -e "${GREEN}フロントエンドPID: $(cat /tmp/autotrader_frontend.pid)${NC}"
}

build_frontend() {
    echo -e "${GREEN}フロントエンドをビルド中...${NC}"
    cd "$FRONTEND_DIR"

    if [ ! -d "node_modules" ]; then
        echo -e "${YELLOW}npm install を実行します...${NC}"
        npm install
    fi

    npm run build
    echo -e "${GREEN}ビルド完了: $FRONTEND_DIR/dist${NC}"
}

stop_all() {
    echo -e "${YELLOW}プロセスを停止中...${NC}"

    if [ -f /tmp/autotrader_backend.pid ]; then
        kill "$(cat /tmp/autotrader_backend.pid)" 2>/dev/null || true
        rm -f /tmp/autotrader_backend.pid
        echo "バックエンド停止"
    fi

    if [ -f /tmp/autotrader_frontend.pid ]; then
        kill "$(cat /tmp/autotrader_frontend.pid)" 2>/dev/null || true
        rm -f /tmp/autotrader_frontend.pid
        echo "フロントエンド停止"
    fi

    # 残存プロセスも停止
    pkill -f "uvicorn autotrader.web.main:app" 2>/dev/null || true
    pkill -f "vite.*autotrader" 2>/dev/null || true

    echo -e "${GREEN}停止完了${NC}"
}

show_status() {
    echo "=== AutoTrader WebUI Status ==="

    if [ -f /tmp/autotrader_backend.pid ] && kill -0 "$(cat /tmp/autotrader_backend.pid)" 2>/dev/null; then
        echo -e "バックエンド:   ${GREEN}Running${NC} (PID: $(cat /tmp/autotrader_backend.pid))"
    else
        echo -e "バックエンド:   ${RED}Stopped${NC}"
    fi

    if [ -f /tmp/autotrader_frontend.pid ] && kill -0 "$(cat /tmp/autotrader_frontend.pid)" 2>/dev/null; then
        echo -e "フロントエンド: ${GREEN}Running${NC} (PID: $(cat /tmp/autotrader_frontend.pid))"
    else
        echo -e "フロントエンド: ${RED}Stopped${NC}"
    fi

    echo ""
    echo "URLs:"
    echo "  API:      http://localhost:8000/api/v1/health"
    echo "  Swagger:  http://localhost:8000/docs"
    echo "  WebUI:    http://localhost:5173 (dev) / http://localhost:8000 (prod)"
}

# メイン処理
case "${1:-}" in
    dev)
        check_dependencies
        stop_all
        start_backend 8000 --reload
        sleep 2
        start_frontend_dev
        echo ""
        echo -e "${GREEN}=== 開発モードで起動しました ===${NC}"
        echo "  API:     http://localhost:8000"
        echo "  WebUI:   http://localhost:5173"
        echo "  Swagger: http://localhost:8000/docs"
        echo ""
        echo "停止: $0 stop"
        ;;
    prod)
        check_dependencies
        stop_all
        if [ ! -d "$FRONTEND_DIR/dist" ]; then
            build_frontend
        fi
        start_backend 8000
        echo ""
        echo -e "${GREEN}=== 本番モードで起動しました ===${NC}"
        echo "  WebUI:   http://localhost:8000"
        echo "  Swagger: http://localhost:8000/docs"
        echo ""
        echo "停止: $0 stop"
        ;;
    backend)
        check_dependencies
        start_backend 8000 --reload
        ;;
    frontend)
        check_dependencies
        start_frontend_dev
        ;;
    build)
        check_dependencies
        build_frontend
        ;;
    stop)
        stop_all
        ;;
    status)
        show_status
        ;;
    *)
        usage
        exit 1
        ;;
esac
