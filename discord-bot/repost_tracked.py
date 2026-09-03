"""One-off: fresh board scan -> post -> LOG, then exit.

Imports bot.py (safe: it has a __main__ guard) and REPLACES its on_ready with
this one, so none of the normal startup work runs -- no second-wave scheduling,
no startup test post, no task loops. Exactly one action happens: the same
_post_daily_picks the nightly trigger calls, with track=True so every play is
written to the record.
"""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout, force=True)

import bot  # noqa: E402  (module-level import defines the client; __main__ guard stops it running)

log = logging.getLogger("repost")


@bot.client.event
async def on_ready():   # REPLACES bot.on_ready — discord.py keeps one per event
    try:
        log.info("connected as %s", bot.client.user)
        ch = bot.client.get_channel(bot.POD_CHANNEL_ID)
        if ch is None:
            ch = await bot.client.fetch_channel(bot.POD_CHANNEL_ID)
        log.info("posting tracked board to #%s (%s)", getattr(ch, "name", "?"), ch.id)
        status = await bot._post_daily_picks(ch, track=True)
        log.info("RESULT: %s", status)
    except Exception:
        log.exception("tracked repost FAILED")
    finally:
        await bot.client.close()


if __name__ == "__main__":
    if not bot.DISCORD_BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN not set")
    bot.client.run(bot.DISCORD_BOT_TOKEN, reconnect=False, log_handler=None)
