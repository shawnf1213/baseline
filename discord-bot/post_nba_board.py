"""One-off: post the NBA board to its own channels, now, outside the schedule.

    python post_nba_board.py                    both books, tonight's slate
    python post_nba_board.py prizepicks         one book
    python post_nba_board.py prizepicks 3       one book, 3-day slate window

Mirrors backfill_recaps.py: replaces bot.on_ready so no other startup work runs,
does the thing, closes the client. Goes to the NBA board channels only — it
cannot reach the tennis or NFL channels, because nba.post.channel_for is the
only place it looks one up.

The slate window argument exists because the NBA preseason is sparse: on a night
with one fixture, a 0-day window prices only the players in that game, and
widening it is the difference between a board and an empty post. It defaults to
0, which is what the scheduled scan uses.
"""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout, force=True)

import bot  # noqa: E402

log = logging.getLogger("nba-oneoff")

BOOKS = [b for b in sys.argv[1:2] if b] or ["prizepicks", "underdog"]
WINDOW = int(sys.argv[2]) if len(sys.argv) > 2 else 0


@bot.client.event
async def on_ready():       # REPLACES bot.on_ready — no other startup work runs
    try:
        log.info("connected as %s", bot.client.user)
        import nba as _pkg
        log.info("NBA shadow=%s | books=%s | slate window=%dd",
                 _pkg.SHADOW, BOOKS, WINDOW)
        for book in BOOKS:
            try:
                await bot._nba_post_board(book, window_days=WINDOW)
            except Exception:
                log.exception("NBA board failed for %s", book)
            await asyncio.sleep(2)
    except Exception:
        log.exception("NBA one-off FAILED")
    finally:
        await bot.client.close()


if __name__ == "__main__":
    bot.client.run(bot.DISCORD_BOT_TOKEN, reconnect=False, log_handler=None)
