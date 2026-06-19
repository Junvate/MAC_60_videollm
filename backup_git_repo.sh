#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/data/yjc}"
REMOTE_URL="git@github.com:Junvate/MAC_60_videollm.git"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_junvate}"
BRANCH="${BRANCH:-main}"
COMMIT_MESSAGE="${1:-backup: update repository $(date +%Y%m%d_%H%M%S)}"

cd "$REPO_DIR"

# Git 备份 pipeline：固定远端和 SSH key，然后提交当前改动并推送到 main。
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

git branch -M "$BRANCH"
git add -A

if git diff --cached --quiet; then
  echo "nothing to commit"
else
  git commit -m "$COMMIT_MESSAGE"
fi

GIT_SSH_COMMAND="ssh -o IdentitiesOnly=yes -i $SSH_KEY" git push origin "$BRANCH"

echo "git backup pushed to: $REMOTE_URL ($BRANCH)"
