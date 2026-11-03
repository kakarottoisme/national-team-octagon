#!/usr/bin/env bash
# 盘中约每 60s 直连腾讯 → 写 data/live.json → 有变化则推 main（供 GitHub Pages 同源读）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PIDFILE="$ROOT/data/.live_daemon.pid"
mkdir -p data
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

in_session() {
  python3 - <<'PY'
import datetime
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
if now.weekday() >= 5:
    raise SystemExit(1)
m = now.hour * 60 + now.minute
# 稍早开、稍晚收，覆盖集合竞价尾巴与尾盘
ok = (9 * 60 + 15 <= m <= 11 * 60 + 35) or (12 * 60 + 55 <= m <= 15 * 60 + 5)
raise SystemExit(0 if ok else 1)
PY
}

last_sig=""
while true; do
  if ! in_session; then
    sleep 60
    continue
  fi
  if ! python3 fetch_live.py > /tmp/fetch_live.out 2>/tmp/fetch_live.err; then
    sleep 30
    continue
  fi
  sig=$(python3 - <<'PY'
import json
d=json.load(open("data/live.json"))
e=next(iter(d["etfs"].values()))
print(d.get("date"), e["t"][-1] if e.get("t") else "", round(e["p"][-1],4) if e.get("p") else "")
PY
)
  if [[ "$sig" != "$last_sig" ]]; then
    git add data/live.json
    if ! git diff --cached --quiet; then
      msg="live $(python3 -c 'import json;print(json.load(open("data/live.json"))["updated"][11:16])')"
      git commit -m "$msg" >/dev/null
      git push origin main >/tmp/live_push.out 2>&1 || true
    fi
    last_sig="$sig"
  fi
  sleep 55
done
