#!/bin/bash
set -e

# One repo, two Railway services share this root config. The SERVICE_ROLE env
# var (set per service in the Railway dashboard) decides what to launch:
#   SERVICE_ROLE=bot  -> the Discord bot (discord-bot/bot.py)
#   anything else     -> the FastAPI backend (default — unchanged behaviour)
if [ "$SERVICE_ROLE" = "bot" ]; then
  exec python /app/discord-bot/bot.py
fi

cd /app/backend
exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
