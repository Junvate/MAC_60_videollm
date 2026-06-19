#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${1:-/data/yjc}"
BACKUP_ROOT="${2:-/data/yjc/git_backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"

cd "$REPO_DIR"
REPO_TOP="$(git rev-parse --show-toplevel)"
REPO_NAME="$(basename "$REPO_TOP")"
BACKUP_DIR="${BACKUP_ROOT}/${REPO_NAME}_${STAMP}"

# Git 备份 pipeline：导出完整仓库 bundle，同时记录远端、分支和当前工作区状态。
mkdir -p "$BACKUP_DIR"
git bundle create "$BACKUP_DIR/${REPO_NAME}.bundle" --all --tags
git remote -v > "$BACKUP_DIR/remotes.txt"
git branch -avv > "$BACKUP_DIR/branches.txt"
git status --short --branch > "$BACKUP_DIR/status.txt"
git diff > "$BACKUP_DIR/working_tree.diff"

cat > "$BACKUP_DIR/README.txt" <<EOF
backup_time=$STAMP
repo=$REPO_TOP
bundle=$BACKUP_DIR/${REPO_NAME}.bundle

restore example:
git clone $BACKUP_DIR/${REPO_NAME}.bundle ${REPO_NAME}_restore
EOF

echo "git repo backup saved to: $BACKUP_DIR"
