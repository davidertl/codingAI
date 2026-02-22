#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DIR="${1:-$DEFAULT_SRC_DIR}"
REMOTE_URL="${2:-https://github.com/davidertl/codingAI.git}"
BRANCH="${3:-localstate}"
AUTHOR_MODE="${AUTHOR_MODE:-davidertl}"   # davidertl | bot
COMMIT_MSG="${COMMIT_MSG:-chore: initial localstate snapshot}"
INCLUDE_ALL="${INCLUDE_ALL:-0}"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Source directory not found: $SRC_DIR" >&2
  exit 1
fi

WORKROOT="$(mktemp -d /tmp/codingai-push-XXXXXX)"
SNAPSHOT_DIR="$WORKROOT/snapshot"
mkdir -p "$SNAPSHOT_DIR"

cleanup() {
  rm -rf "$WORKROOT"
}
trap cleanup EXIT

EXCLUDES=(".git")
if [[ "$INCLUDE_ALL" != "1" ]]; then
  EXCLUDES+=(
    ".cache"
    ".npm"
    ".vscode-server"
    ".local"
    ".codex/sessions"
    ".codex/tmp"
    "ai-agent/venv"
    "workspaces/KRT-leadtool/.git"
    "workspaces/KRT-leadtool-test/.git"
    "workspaces/KRT-Com_Discord/.git"
    "workspaces/KRT-leadtool/frontend/node_modules"
    "workspaces/KRT-leadtool-test/frontend/node_modules"
    "workspaces/KRT-leadtool/frontend/dist"
  )
fi

if command -v rsync >/dev/null 2>&1; then
  RSYNC_ARGS=(-a --delete --human-readable)
  for x in "${EXCLUDES[@]}"; do
    RSYNC_ARGS+=(--exclude "$x/")
  done
  rsync "${RSYNC_ARGS[@]}" "$SRC_DIR/" "$SNAPSHOT_DIR/"
else
  TAR_EXCLUDES=()
  for x in "${EXCLUDES[@]}"; do
    TAR_EXCLUDES+=(--exclude="$x")
  done
  tar -C "$SRC_DIR" -cf "$WORKROOT/snapshot.tar" "${TAR_EXCLUDES[@]}" .
  tar -C "$SNAPSHOT_DIR" -xf "$WORKROOT/snapshot.tar"
fi

cd "$SNAPSHOT_DIR"

git init -b "$BRANCH" >/dev/null

case "$AUTHOR_MODE" in
  davidertl)
    git config user.name "davidertl"
    git config user.email "github.aidev@david-ertl.de"
    ;;
  bot)
    # This creates a bot-like author string; GitHub "Verified bot" requires GitHub App/API signing path.
    git config user.name "local-aiagent[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    ;;
  *)
    echo "Invalid AUTHOR_MODE: $AUTHOR_MODE (expected: davidertl | bot)" >&2
    exit 1
    ;;
esac

git add -A
if git diff --cached --quiet; then
  echo "No files to commit after snapshot/exclusions."
  exit 0
fi

git commit -m "$COMMIT_MSG" >/dev/null

auth_remote="$REMOTE_URL"
if [[ -n "${GITHUB_TOKEN:-}" && "$REMOTE_URL" =~ ^https://github.com/ ]]; then
  auth_remote="https://x-access-token:${GITHUB_TOKEN}@${REMOTE_URL#https://}"
fi

git remote add origin "$auth_remote"

echo "Pushing branch '$BRANCH' to '$REMOTE_URL'..."
git push -u origin "$BRANCH"

if [[ "$auth_remote" != "$REMOTE_URL" ]]; then
  git remote set-url origin "$REMOTE_URL"
fi

echo "Done."
echo "Snapshot source: $SRC_DIR"
echo "Branch: $BRANCH"
