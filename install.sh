#!/usr/bin/env bash
#
# One-shot install: dependencies, dashboard, and the `ccgod` command on your PATH.
# Re-run it any time to update. `./uninstall.sh` undoes it.
#
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

if [ -t 1 ]; then
  B=$(printf '\033[1m'); G=$(printf '\033[32m'); Y=$(printf '\033[33m')
  R=$(printf '\033[31m'); D=$(printf '\033[2m'); N=$(printf '\033[0m')
else
  B=""; G=""; Y=""; R=""; D=""; N=""
fi

step() { printf '%s==>%s %s%s%s\n' "$G" "$N" "$B" "$1" "$N"; }
warn() { printf '%s warn%s %s\n' "$Y" "$N" "$1"; }
die()  { printf '%s error%s %s\n' "$R" "$N" "$1" >&2; exit 1; }

WITH_UI=1
WITH_DEV=0
GLOBAL=1

usage() {
  cat <<EOF
${B}install.sh${N} — set up claude-god in one command

  ${B}./install.sh${N}              deps, dashboard, and \`ccgod\` on your PATH
  ${B}./install.sh --dev${N}        also install pytest and ruff
  ${B}./install.sh --no-ui${N}      skip the dashboard build (API and CLI only)
  ${B}./install.sh --no-global${N}  do not put \`ccgod\` on PATH; use \`uv run ccgod\`

Undo with ${B}./uninstall.sh${N}.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dev) WITH_DEV=1 ;;
    --no-ui) WITH_UI=0 ;;
    --no-global) GLOBAL=0 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# ---- prerequisites ---------------------------------------------------
step "Checking prerequisites"
MISSING=0
need() {
  if command -v "$1" >/dev/null 2>&1; then
    printf '  %s✔%s %-6s %s%s%s\n' "$G" "$N" "$1" "$D" "$(command -v "$1")" "$N"
  else
    printf '  %s✘%s %-6s %s\n' "$R" "$N" "$1" "$2"
    MISSING=1
  fi
}
need uv   "https://docs.astral.sh/uv/getting-started/installation/"
need git  "install git, or run: xcode-select --install"
[ "$WITH_UI" -eq 1 ] && need npm "https://nodejs.org/ — or pass --no-ui"
[ "$MISSING" -eq 1 ] && die "install the missing tools above, then re-run"

# claude is needed to run tasks, but not to install.
if ! command -v claude >/dev/null 2>&1; then
  warn "the \`claude\` CLI is not on your PATH — install it and log in before running tasks"
fi

# ---- python side -----------------------------------------------------
step "Installing Python dependencies"
if [ "$WITH_DEV" -eq 1 ]; then
  uv sync --extra dev
else
  uv sync
fi

# ---- dashboard -------------------------------------------------------
if [ "$WITH_UI" -eq 1 ]; then
  step "Building the dashboard"
  # npm ci needs the lockfile to match; fall back to install if it drifted.
  ( cd ui && { npm ci || npm install; } && npm run build )
else
  warn "skipping the dashboard — \`ccgod serve\` will serve the API only"
fi

# ---- the command -----------------------------------------------------
if [ "$GLOBAL" -eq 1 ]; then
  step "Putting \`ccgod\` on your PATH"
  # Editable on purpose: the agent prompts live in ./plugin, which the package
  # finds relative to its own location. A copied install would not see them.
  uv tool install --force --editable .
  if ! command -v ccgod >/dev/null 2>&1; then
    warn "\`ccgod\` is installed but not on your PATH yet — run: uv tool update-shell"
  fi
fi

# ---- verify ----------------------------------------------------------
step "Checking the install"
if [ "$GLOBAL" -eq 1 ] && command -v ccgod >/dev/null 2>&1; then
  RUN="ccgod"
  ccgod doctor
else
  RUN="uv run ccgod"
  uv run ccgod doctor
fi

cat <<EOF

$G Done.$N ccgod runs from this checkout: $D$ROOT$N
 Keep the directory where it is, or re-run this script after moving it.

 Next:
   ${B}$RUN demo${N}                                 a sandbox repo to try it on
   ${B}$RUN new "add order refunds" -r <repo>${N}    run a task
   ${B}$RUN serve${N}                                dashboard on http://127.0.0.1:8787

 Point it at your own repo with ${B}$RUN init /path/to/repo${N}.
 Uninstall with ${B}./uninstall.sh${N}.
EOF
