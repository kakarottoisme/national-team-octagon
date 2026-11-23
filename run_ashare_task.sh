#!/usr/bin/env bash
# Cloud cron entrypoint: isolated checkout, persistent dedupe state, scoped writes.
set -euo pipefail
TASK="${1:?task required}"
STATE="${2:?absolute state directory required}"
case "$TASK" in ic|archive|margin|live-health|live-recover|ic-signal) ;; *) exit 2;; esac
case "$STATE" in /*) ;; *) exit 2;; esac
export GIT_TERMINAL_PROMPT=0
if [[ "$TASK" == archive ]]; then
  export ARCHIVE_WRAPPER_STARTED_AT="$(TZ=Asia/Shanghai date -Iseconds)"
fi
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
CLONE_START=$SECONDS
git clone --quiet --depth 20 https://github.com/kakarottoisme/national-team-octagon.git "$RUN/repo"
if [[ "$TASK" == archive ]]; then
  export ARCHIVE_CHECKOUT_READY_AT="$(TZ=Asia/Shanghai date -Iseconds)"
  export ARCHIVE_CLONE_SECONDS="$((SECONDS-CLONE_START))"
fi
PYTHON=python3
if [[ "$TASK" == margin ]]; then
  python3 -m venv "$STATE/margin-env"
  PYTHON="$STATE/margin-env/bin/python"
  "$PYTHON" -c 'import akshare,pandas; assert akshare.__version__ == "1.18.97"' 2>/dev/null || "$PYTHON" -m pip install --quiet 'akshare==1.18.97' pandas
fi
SCRIPT=ashare_tasks.py
case "$TASK" in live-recover|ic-signal) SCRIPT=ashare_observers.py;; esac
"$PYTHON" "$RUN/repo/$SCRIPT" "$TASK" --state-dir "$STATE"
