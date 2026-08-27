#!/usr/bin/env bash
#
# Undo ./install.sh. Removes build output and the `ccgod` command.
# Your data under ~/.ccgod is kept unless you pass --purge.
#
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
HOME_DIR="${CCGOD_HOME:-$HOME/.ccgod}"

if [ -t 1 ]; then
  B=$(printf '\033[1m'); G=$(printf '\033[32m'); Y=$(printf '\033[33m')
  R=$(printf '\033[31m'); D=$(printf '\033[2m'); N=$(printf '\033[0m')
else
  B=""; G=""; Y=""; R=""; D=""; N=""
fi

step() { printf '%s==>%s %s%s%s\n' "$G" "$N" "$B" "$1" "$N"; }
warn() { printf '%s warn%s %s\n' "$Y" "$N" "$1"; }
gone() { printf '  %sremoved%s %s\n' "$D" "$N" "$1"; }
skip() { printf '  %sabsent%s  %s\n' "$D" "$N" "$1"; }

PURGE=0
ASSUME_YES=0

usage() {
  cat <<EOF
${B}uninstall.sh${N} — undo ./install.sh

  ${B}./uninstall.sh${N}          remove the \`ccgod\` command, .venv, node_modules
                          and the built dashboard. Keeps your data.
  ${B}./uninstall.sh --purge${N}  the above, and also delete $HOME_DIR
                          (repo registry, demo repos, worktrees)
  ${B}./uninstall.sh --yes${N}    do not ask for confirmation

Task folders inside your own repos (\`.agents/tasks/\`) are never touched.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --purge) PURGE=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf '%s error%s unknown option: %s (try --help)\n' "$R" "$N" "$1" >&2; exit 1 ;;
  esac
  shift
done

# ---- say what will happen before doing any of it ---------------------
printf '%sThis will remove:%s\n' "$B" "$N"
printf '  the %sccgod%s command (uv tool)\n' "$B" "$N"
printf '  %s\n' "$ROOT/.venv" "$ROOT/ui/node_modules" "$ROOT/ui/dist" \
                 "$ROOT/server/ccgod/static/ (built assets)"
if [ "$PURGE" -eq 1 ]; then
  printf '  %s%s%s  %s<- registry, demo repos and worktrees%s\n' "$R" "$HOME_DIR" "$N" "$R" "$N"
else
  printf '\n%sKeeping%s %s — pass --purge to delete it too.\n' "$D" "$N" "$HOME_DIR"
fi
printf '\n'

if [ "$ASSUME_YES" -eq 0 ]; then
  printf 'Continue? [y/N] '
  read -r reply || reply=""
  case "$reply" in
    y|Y|yes|YES) ;;
    *) printf 'Cancelled. Nothing was removed.\n'; exit 0 ;;
  esac
  printf '\n'
fi

rm_path() {
  if [ -e "$1" ]; then rm -rf -- "$1"; gone "$1"; else skip "$1"; fi
}

# ---- the command -----------------------------------------------------
step "Removing the ccgod command"
if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^ccgod'; then
  uv tool uninstall ccgod >/dev/null 2>&1 && gone "uv tool: ccgod"
else
  skip "uv tool: ccgod"
fi

# ---- worktrees, before the directory holding them disappears ---------
if [ "$PURGE" -eq 1 ] && [ -d "$HOME_DIR/worktrees" ]; then
  step "Detaching lane worktrees from their repos"
  # Registered repos keep refs to worktrees under ~/.ccgod; deleting the
  # directory without pruning would leave every one of them with dangling refs.
  if command -v python3 >/dev/null 2>&1 && [ -f "$HOME_DIR/config.json" ]; then
    python3 - "$HOME_DIR/config.json" <<'PY' | while IFS= read -r repo; do
import json, sys
try:
    for r in json.load(open(sys.argv[1])).get("repos", []):
        print(r["path"])
except Exception:
    pass
PY
      if [ -d "$repo/.git" ] || [ -f "$repo/.git" ]; then
        git -C "$repo" worktree prune 2>/dev/null && gone "worktree refs in $repo"
      fi
    done
  else
    warn "could not read the repo registry — run 'git worktree prune' in your repos"
  fi
fi

# ---- build output ----------------------------------------------------
step "Removing build output"
rm_path "$ROOT/.venv"
rm_path "$ROOT/ui/node_modules"
rm_path "$ROOT/ui/dist"
if [ -d "$ROOT/server/ccgod/static" ]; then
  find "$ROOT/server/ccgod/static" -mindepth 1 ! -name '.gitkeep' -delete 2>/dev/null || true
  gone "$ROOT/server/ccgod/static/ (kept .gitkeep)"
fi
rm_path "$ROOT/.pytest_cache"
find "$ROOT/server" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ---- data ------------------------------------------------------------
if [ "$PURGE" -eq 1 ]; then
  step "Removing your data"
  rm_path "$HOME_DIR"
fi

printf '\n%s Done.%s The checkout itself is untouched — delete %s to finish.\n' "$G" "$N" "$D$ROOT$N"
if [ "$PURGE" -eq 0 ] && [ -d "$HOME_DIR" ]; then
  printf ' Your registry and demo repos are still in %s.\n' "$HOME_DIR"
fi
printf ' Task folders inside your own repos (.agents/tasks/) were left alone.\n'
