#!/usr/bin/env bash
# ==============================================================================
# One-Line Installer (macOS / Linux / WSL):
# Antigravity Real Quota -> 24/7 LIVE RPG Office Full-Auto Bridge
# ==============================================================================
set -euo pipefail

DEST_DIR="${HOME}/.gemini/antigravity-cli"
DEST_SCRIPT="${DEST_DIR}/agy_quota_sync.py"
RAW_URL="https://raw.githubusercontent.com/howardKung/project-lifeblood-rpg-office/main/scripts/agy_quota_sync.py"

mkdir -p "${DEST_DIR}"
echo "⬇️  Downloading agy_quota_sync.py -> ${DEST_SCRIPT} ..."
curl -fsSL "${RAW_URL}" -o "${DEST_SCRIPT}"
chmod +x "${DEST_SCRIPT}"

TOKEN_ARG=""
if [[ -n "${1:-}" ]]; then
  TOKEN_ARG="--github-token ${1}"
fi

python3 "${DEST_SCRIPT}" --configure-agy ${TOKEN_ARG}

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  echo "✅ 偵測到本機 gh CLI 已登入 GitHub，免額外設定 Token，開箱即用！"
elif [[ -f "${DEST_DIR}/rpg_office_env.json" ]] || [[ -n "${GITHUB_TOKEN:-}" ]]; then
  echo "✅ 已設定 GitHub Token，開箱即用！"
else
  echo "⚠️  提醒：本機尚未登入 gh CLI。請執行 'gh auth login'，或傳入 GitHub PAT 重新執行："
  echo "   python3 \"${DEST_SCRIPT}\" --configure-agy --github-token <YOUR_GITHUB_PAT>"
fi
