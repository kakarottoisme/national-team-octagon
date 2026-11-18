#!/usr/bin/env bash
# Cloud cron entrypoint: isolated checkout, persistent dedupe state, scoped writes.
set -euo pipefail
TASK="${1:?task required}"
STATE="${2:?absolute state directory required}"
case "$TASK" in ic|archive|margin|live-health) ;; *) exit 2;; esac
case "$STATE" in /*) ;; *) exit 2;; esac
export GIT_TERMINAL_PROMPT=0
mkdir -p "$STATE"
RUN=$(mktemp -d "$STATE/run.XXXXXX")
cleanup() {
  if [[ -d "$RUN/repo/.git" ]] && ! git -C "$RUN/repo" diff --quiet; then
    printf 'Unpublished work retained at %s\n' "$RUN" >&2
  elif [[ -d "$RUN/repo/.git" ]] && [[ -n "$(git -C "$RUN/repo" log origin/main..HEAD --oneline)" ]]; then
    printf 'Unpushed commit retained at %s\n' "$RUN" >&2
  else
    rm -rf -- "$RUN"
  fi
}
trap cleanup EXIT
# No credential/token copied into command arguments or output.
git clone --quiet --depth 20 https://github.com/kakarottoisme/national-team-octagon.git "$RUN/repo"
PYTHON=python3
if [[ "$TASK" == margin ]]; then
  python3 -m venv "$STATE/margin-env"
  PYTHON="$STATE/margin-env/bin/python"
  "$PYTHON" -c 'import akshare,pandas' 2>/dev/null || "$PYTHON" -m pip install --quiet 'akshare>=1.17,<2' pandas
fi
"$PYTHON" "$RUN/repo/ashare_tasks.py" "$TASK" --state-dir "$STATE"
