"""One-off: post the recaps that never went out, each to its own slate date.

The track record went silent after 8/31 -- postponed and unscoreable picks held
their days open forever. With _recap_date_of in place those days are settled,
but the normal loop posts at most ONE recap per pass every 2 hours and only
looks back three days, so the backlog would trickle out over ~8 hours and 9/1
could age out first. This posts them directly, oldest first.

_post_recap_for keeps its own duplicate guard (it reads the channel), so a day
already in the channel is skipped -- re-running this is safe.
"""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout, force=True)

import bot  # noqa: E402

log = logging.getLogger("backfill")

# (slate date, book) oldest first, so the channel reads chronologically.
TARGETS = [
    ("2026-09-01", "prizepicks"),
    ("2026-09-01", "underdog"),
    ("2026-09-02", "prizepicks"),
    ("2026-09-02", "underdog"),
]


@bot.client.event
async def on_ready():       # REPLACES bot.on_ready — no other startup work runs
    try:
        log.info("connected as %s", bot.client.user)
        ch = bot.client.get_channel(bot.TRACK_RECORD_CHANNEL_ID)
        if ch is None:
            ch = await bot.client.fetch_channel(bot.TRACK_RECORD_CHANNEL_ID)
        log.info("track-record channel: #%s", getattr(ch, "name", "?"))

        # Grade anything still gradeable first, so a recap never ships stale.
        try:
            n = await bot._resolve_all_pending()
            log.info("pre-backfill resolve: %s newly graded", n)
        except Exception:
            log.exception("pre-backfill resolve failed (continuing)")

        for date_str, source in TARGETS:
            try:
                sent = await bot._post_recap_for(ch, date_str, "backfill", source)
                log.info("RESULT %s %s -> %s", source, date_str,
                         "POSTED" if sent else "skipped (already posted / no record)")
            except Exception:
                log.exception("backfill failed for %s %s", source, date_str)
            await asyncio.sleep(2)
    except Exception:
        log.exception("backfill FAILED")
    finally:
        await bot.client.close()


if __name__ == "__main__":
    if not bot.DISCORD_BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN not set")
    bot.client.run(bot.DISCORD_BOT_TOKEN, reconnect=False, log_handler=None)
