#!/usr/bin/env bash
# Stops any running Sunbird, then starts it again in the background.
#   ./restart.sh          → restart against the SigenStor in config.json
#   ./restart.sh --demo   → restart with simulated data
#   ./restart.sh --stop   → just stop it
# Log: sunbird.log   PID: sunbird.pid
set -uo pipefail
cd "$(dirname "$0")"

PIDFILE=sunbird.pid
LOG=sunbird.log
PORT=$(python3 -c 'import json;print(json.load(open("config.json")).get("http_port",8742))' 2>/dev/null || echo 8742)

HERE=$(pwd -P)

# True only for a Python interpreter running "-m sunbird" whose working directory is this folder.
is_sunbird() {
  local pid=$1 cmd cwd
  [ "$pid" = "$$" ] && return 1
  kill -0 "$pid" 2>/dev/null || return 1
  cmd=$(ps -o command= -p "$pid" 2>/dev/null) || return 1
  # first word must be a Python interpreter (macOS venvs report ".../MacOS/Python", capital P)
  [[ "$cmd" =~ ^[^\ ]*[Pp]ython[0-9.]*\ -m\ sunbird([[:space:]]|$) ]] || return 1
  if command -v lsof >/dev/null; then
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)
    [ -n "$cwd" ] && [ "$(cd "$cwd" 2>/dev/null && pwd -P)" != "$HERE" ] && return 1
  fi
  return 0
}

collect_pids() {
  {
    [ -f "$PIDFILE" ] && cat "$PIDFILE"
    pgrep -f "python" 2>/dev/null
  } | grep -E '^[0-9]+$' | sort -u | while read -r p; do
    is_sunbird "$p" && echo "$p"
  done
}

PIDS=$(collect_pids | tr '\n' ' ')
if [ -n "${PIDS// /}" ]; then
  echo "Stopping Sunbird (pid $PIDS)…"
  kill $PIDS 2>/dev/null
  for _ in $(seq 1 20); do                      # up to 5 s for a clean shutdown
    sleep 0.25
    LEFT=$(for p in $PIDS; do kill -0 "$p" 2>/dev/null && echo "$p"; done | tr '\n' ' ')
    [ -z "${LEFT// /}" ] && break
  done
  if [ -n "${LEFT// /}" ]; then
    echo "Force-killing $LEFT"
    kill -9 $LEFT 2>/dev/null
  fi
else
  echo "Sunbird wasn't running."
fi
rm -f "$PIDFILE"

[ "${1:-}" = "--stop" ] && exit 0

if command -v lsof >/dev/null && lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is in use by another program:"
  lsof -i "tcp:$PORT" -sTCP:LISTEN
  echo "Not touching it. Free the port or change http_port in config.json."
  exit 1
fi

# first-run setup (same as run.sh)
if [ ! -x .venv/bin/python ]; then
  echo "First run: creating virtualenv and installing dependencies…"
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt || exit 1
fi

echo "Starting Sunbird…"
nohup .venv/bin/python -m sunbird "$@" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"

for _ in $(seq 1 90); do                        # wait up to 45 s (first demo run seeds history)
  sleep 0.5
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Sunbird exited during startup. Last lines of $LOG:"; tail -n 20 "$LOG"; rm -f "$PIDFILE"; exit 1
  fi
  if curl -s -m 1 -o /dev/null "http://localhost:$PORT/api/live"; then
    echo "Running (pid $(cat "$PIDFILE")) → http://localhost:$PORT"
    exit 0
  fi
done
echo "Started (pid $(cat "$PIDFILE")) but not answering yet - check $LOG"
