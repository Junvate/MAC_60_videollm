#!/usr/bin/env bash
set -euo pipefail

SESSION=${SESSION:-qwen3vl-gpu1}
PORT=${PORT:-18008}
GPU=${GPU:-1}
MODEL=${MODEL:-/data/yjc/model_weights/Qwen3-VL-8B-Instruct}
MEDIA_PATH=${MEDIA_PATH:-/data/yjc/video}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-12000}
VLLM_BIN=${VLLM_BIN:-/data/envs/vllm-ysx/bin/vllm}
STAMP=${STAMP:-$(date +%Y%m%d_%H%M%S)}
LOG_DIR=${LOG_DIR:-/data/yjc/log}
LOG=${LOG:-$LOG_DIR/qwen3vl_${STAMP}.log}

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
export NO_PROXY=127.0.0.1,localhost,::1
export no_proxy=127.0.0.1,localhost,::1

mkdir -p "$LOG_DIR"

tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" \
  "CUDA_VISIBLE_DEVICES=$GPU $VLLM_BIN serve $MODEL --host 0.0.0.0 --port $PORT --served-model-name qwen3-vl-8b-instruct --dtype bfloat16 --gpu-memory-utilization 0.90 --max-model-len $MAX_MODEL_LEN --allowed-local-media-path $MEDIA_PATH >> $LOG 2>&1"

echo "session=$SESSION"
echo "log=$LOG"
echo "media_path=$MEDIA_PATH"
echo "max_model_len=$MAX_MODEL_LEN"
echo "health=http://127.0.0.1:$PORT/health"
echo "models=http://127.0.0.1:$PORT/v1/models"
