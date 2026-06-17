#!/bin/bash
set -e

# One repo, two Railway services share this root config. The SERVICE_ROLE env
# var (set per service in the Railway dashboard) decides what to launch:
#   SERVICE_ROLE=bot  -> the Discord bot (discord-bot/bot.py)
#   anything else     -> the FastAPI backend (default — unchanged behaviour)
echo "[start.sh] dispatcher running | SERVICE_ROLE='${SERVICE_ROLE}' | RAILWAY_SERVICE_NAME='${RAILWAY_SERVICE_NAME}'"

# Decide whether this is the bot. Prefer an explicit SERVICE_ROLE=bot, but also
# auto-detect from the Railway-injected service name (this service is named
# "baseline-discord-bot"), so the bot runs even if the manual variable is missing.
IS_BOT=0
[ "$SERVICE_ROLE" = "bot" ] && IS_BOT=1
case "$RAILWAY_SERVICE_NAME" in
  *discord*|*Discord*) IS_BOT=1 ;;
esac

if [ "$IS_BOT" = "1" ]; then
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
