#!/bin/bash
set -e

# One repo, two Railway services share this root config. The SERVICE_ROLE env
# var (set per service in the Railway dashboard) decides what to launch:
#   SERVICE_ROLE=bot  -> the Discord bot (discord-bot/bot.py)
#   anything else     -> the FastAPI backend (default — unchanged behaviour)
echo "[start.sh] dispatcher running | SERVICE_ROLE='${SERVICE_ROLE}'"

if [ "$SERVICE_ROLE" = "bot" ]; then
  echo "[start.sh] launching Discord bot"
  # Locate bot.py whether the build root is the repo root or discord-bot/
  if [ -f /app/discord-bot/bot.py ]; then
    exec python /app/discord-bot/bot.py
  elif [ -f /app/bot.py ]; then
    exec python /app/bot.py
  else
    echo "[start.sh] ERROR: bot.py not found under /app — listing /app:"
    ls -la /app
    exit 1
  fi
fi

echo "[start.sh] launching FastAPI backend"
cd /app/backend
exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
