#!/usr/bin/env bash
# Cloud cron entrypoint: isolated checkout, persistent dedupe state, scoped writes.
set -euo pipefail
TASK="${1:?task required}"
STATE="${2:?absolute state directory required}"
case "$TASK" in ic|archive|margin|live-health|live-recover|ic-signal|data-audit|watch-intraday|watch-daily) ;; *) exit 2;; esac
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
# Retry only read-only checkout transport failures, before any business write.
# Partial clones contain no user data; auth failures stop immediately.
for attempt in 1 2 3; do
  ERR="$RUN/clone-error.txt"
  if git clone --quiet --depth 20 https://github.com/kakarottoisme/national-team-octagon.git "$RUN/repo" 2>"$ERR"; then break; fi
  if python3 - "$ERR" <<'PY'
import sys
s=open(sys.argv[1]).read().lower()
sys.exit(0 if any(x in s for x in ('authentication failed','permission denied','403','401','repository not found')) else 1)
PY
  then
    printf 'Checkout authentication/permission failure; no retry\n' >&2
    exit 1
  fi
  if [[ "$attempt" == 3 ]]; then
    printf 'Checkout failed after 3 bounded transport attempts; no business writes\n' >&2
    exit 1
  fi
  rm -rf -- "$RUN/repo"
  sleep "$((attempt * 2))"
done
if [[ "$TASK" == archive ]]; then
  export ARCHIVE_CHECKOUT_READY_AT="$(TZ=Asia/Shanghai date -Iseconds)"
  export ARCHIVE_CLONE_SECONDS="$((SECONDS-CLONE_START))"
fi
PYTHON=python3
if [[ "$TASK" == margin || "$TASK" == data-audit || "$TASK" == watch-daily ]]; then
  python3 -m venv "$STATE/margin-env"
  PYTHON="$STATE/margin-env/bin/python"
  "$PYTHON" -c 'import akshare,pandas; assert akshare.__version__ == "1.18.97"' 2>/dev/null || "$PYTHON" -m pip install --quiet 'akshare==1.18.97' pandas
fi
if [[ "$TASK" == watch-daily ]]; then
  "$PYTHON" -c 'import xlrd; assert xlrd.__version__ == "2.0.2"' 2>/dev/null || "$PYTHON" -m pip install --quiet 'xlrd==2.0.2'
fi
if [[ "$TASK" == watch-intraday || "$TASK" == watch-daily ]]; then
  WATCH_EXTRA=()
  if [[ "${3:-}" == --followup ]]; then WATCH_EXTRA=(--followup); fi
  "$PYTHON" "$RUN/repo/dashboard_watchdog.py" "${TASK#watch-}" --state-dir "$STATE" "${WATCH_EXTRA[@]}"
  exit $?
fi
SCRIPT=ashare_tasks.py
case "$TASK" in live-recover|ic-signal) SCRIPT=ashare_observers.py;; esac
# Only the retry controller may explicitly mark the last attempt final.
EXTRA=()
if [[ "${3:-}" == --final ]]; then EXTRA=(--final); fi
"$PYTHON" "$RUN/repo/$SCRIPT" "$TASK" --state-dir "$STATE" "${EXTRA[@]}"
