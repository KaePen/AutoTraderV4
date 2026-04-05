#!/bin/bash
# WSL→Windows同期ヘルパー
# Usage:
#   ./scripts/wsl_sync.sh push     - WSLのコミットをpush → Windows側でpull
#   ./scripts/wsl_sync.sh pull     - Windows側の変更をWSLにpull
#   ./scripts/wsl_sync.sh status   - 両方の状態を表示
#   ./scripts/wsl_sync.sh queue    - Windows側のキューにジョブ投入(stdin or 引数)
#   ./scripts/wsl_sync.sh cmd <command> - ランナーにコマンド送信

set -euo pipefail

WSL_PROJECT="$HOME/projects/AutoTraderV4"
WIN_PROJECT="/mnt/d/Projects/AutoTraderV4"
WIN_DATA="/mnt/d/Projects/AutoTraderV4_data"

case "${1:-status}" in
  push)
    echo "[WSL] Pushing to remote..."
    git -C "$WSL_PROJECT" push origin main
    echo "[WIN] Pulling from remote..."
    git -C "$WIN_PROJECT" pull origin main
    echo "[DONE] Windows project synced."
    ;;

  pull)
    echo "[WSL] Pulling from remote..."
    git -C "$WSL_PROJECT" pull origin main
    echo "[DONE] WSL project synced."
    ;;

  status)
    echo "=== WSL ==="
    git -C "$WSL_PROJECT" log --oneline -3
    echo ""
    echo "=== Windows ==="
    git -C "$WIN_PROJECT" log --oneline -3
    echo ""
    echo "=== Supervisor ==="
    if [ -f "$WIN_DATA/state/supervisor_state.json" ]; then
      python3 -c "
import json
s=json.load(open('$WIN_DATA/state/supervisor_state.json'))
for name, p in s.get('processes',{}).items():
    print(f\"  {p['label']:<15} {p['status']:<10} PID={p.get('pid','?')} uptime={p.get('uptime','?')}\")
"
    else
      echo "  Supervisor not running"
    fi
    echo ""
    echo "=== Queue ==="
    python3 -c "
import json
d=json.load(open('$WIN_DATA/state/backtest_queue.json'))
print(f'  Pending jobs: {len(d[\"jobs\"])}')
for j in d['jobs'][:3]:
    print(f'    {j[\"description\"]}')
if len(d['jobs'])>3: print(f'    ... +{len(d[\"jobs\"])-3} more')
"
    ;;

  queue)
    QUEUE_FILE="$WIN_DATA/state/backtest_queue.json"
    if [ -n "${2:-}" ]; then
      # 引数がJSONファイルパスの場合
      cp "$2" "$QUEUE_FILE"
      echo "Queue updated from $2"
    else
      # stdinから読み取り
      cat > "$QUEUE_FILE"
      echo "Queue updated from stdin"
    fi
    python3 -c "import json; d=json.load(open('$QUEUE_FILE')); print(f'Jobs: {len(d[\"jobs\"])}')"
    ;;

  cmd)
    CMD="${2:-status}"
    CMD_FILE="$WIN_DATA/state/runner_commands.json"
    python3 -c "
import json, time
cmd = {'command': '$CMD', 'timestamp': time.time()}
json.dump(cmd, open('$CMD_FILE', 'w'))
print(f'Sent command: $CMD')
"
    ;;

  *)
    echo "Usage: $0 {push|pull|status|queue|cmd <command>}"
    echo ""
    echo "Commands:"
    echo "  push          Push WSL commits and pull on Windows"
    echo "  pull          Pull remote changes to WSL"
    echo "  status        Show both repos + supervisor + queue status"
    echo "  queue [file]  Write queue JSON to Windows (file or stdin)"
    echo "  cmd <cmd>     Send runner command (stop/pause/resume/status)"
    exit 1
    ;;
esac
