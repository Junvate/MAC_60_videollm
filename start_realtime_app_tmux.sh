#!/usr/bin/env bash
set -euo pipefail

SESSION=${SESSION:-commentary-app}
HOST=${HOST:-0.0.0.0}
BACKEND_PORT=${BACKEND_PORT:-18080}
FRONTEND_PORT=${FRONTEND_PORT:-5173}
PROJECT_ROOT=${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
LOG_DIR=${LOG_DIR:-$PROJECT_ROOT/log}
BACKEND_TARGET=${BACKEND_TARGET:-http://127.0.0.1:$BACKEND_PORT}

mkdir -p "$LOG_DIR"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not found. Please install tmux first." >&2
  exit 1
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "frontend dependencies not found; running npm install..."
  (cd "$FRONTEND_DIR" && npm install)
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
fi

tmux new-session -d -s "$SESSION" -n backend \
  "cd '$PROJECT_ROOT' && python3 '$BACKEND_DIR/api_server.py' --host '$HOST' --port '$BACKEND_PORT' 2>&1 | tee '$LOG_DIR/backend_${BACKEND_PORT}.log'"

tmux new-window -t "$SESSION" -n frontend \
  "cd '$FRONTEND_DIR' && VITE_BACKEND_TARGET='$BACKEND_TARGET' npm run dev -- --host '$HOST' --port '$FRONTEND_PORT' 2>&1 | tee '$LOG_DIR/frontend_${FRONTEND_PORT}.log'"

LOCAL_BACKEND="http://127.0.0.1:$BACKEND_PORT"
LOCAL_FRONTEND="http://127.0.0.1:$FRONTEND_PORT/frontend/"
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)

cat <<MSG
Started tmux session: $SESSION

Backend API:
  $LOCAL_BACKEND
  $LOCAL_BACKEND/player

Frontend dev:
  $LOCAL_FRONTEND

Logs:
  $LOG_DIR/backend_${BACKEND_PORT}.log
  $LOG_DIR/frontend_${FRONTEND_PORT}.log

Useful commands:
  tmux attach -t $SESSION
  tmux ls
  tmux kill-session -t $SESSION
MSG

if [ -n "$SERVER_IP" ]; then
  cat <<MSG

LAN addresses:
  Backend:  http://$SERVER_IP:$BACKEND_PORT
  Player:   http://$SERVER_IP:$BACKEND_PORT/player
  Frontend: http://$SERVER_IP:$FRONTEND_PORT/frontend/
MSG
fi
