"""
Post the Underdog TENNIS board out of schedule, to the real channel.

Run it through `railway run` so the bot token is INJECTED FROM THE ENVIRONMENT.
Credentials never come from arguments:

    cd E:/baseline && railway run python scripts/underdog_board_now.py
    cd E:/baseline && railway run python scripts/underdog_board_now.py --post

IT CALLS THE BOT'S OWN POSTER. `_post_underdog_board` is what the 22:30 and
07:30 jobs run — same ranking, same gating, same repeat-suppression, same card.
A script that re-implemented any of that would start drifting from the
scheduled board the first time either was touched, and the two would quietly
disagree about what the board is.

bot.py is import-safe: client.run() only fires under __main__, so importing it
builds the client and the task definitions without connecting.

`--additional` posts a TOP-UP rather than a second full board — capped, no star,
titled "Underdog Additional Plays". Use it when a board already went out today;
without it a second run posts a competing board with its own star.
"""

import argparse
import asyncio
import logging
import os
import sys

# WINDOWS CONSOLES ARE cp1252 AND DISCORD NAMES ARE NOT. The first run of this
# posted the board successfully and then died printing the confirmation, because
# the channel name carries an emoji. A crash AFTER the side effect is the worst
# shape of failure — it reads as "the post failed" when the post went out.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — older interpreters, and it is only output
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
# The sport packages live under backend/; the bot lives in discord-bot/.
for p in (os.path.join(_REPO, "backend"), os.path.join(_REPO, "discord-bot"), _REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("underdog_board_now")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true",
                    help="send to Discord (otherwise dry-run to stdout)")
    ap.add_argument("--additional", action="store_true",
                    help="post as a top-up rather than a full board")
    ap.add_argument("--no-track", action="store_true",
                    help="do not write the plays to the record")
    a = ap.parse_args()

    token = os.getenv("DISCORD_BOT_TOKEN")
    if a.post and not token:
        print("\n  REFUSING TO POST: DISCORD_BOT_TOKEN is not set.\n"
              "  Run this through `railway run` so the real environment is\n"
              "  injected, rather than putting a token on the command line.\n")
        return 2

    import underdog

    # ── DRY RUN FIRST, ALWAYS ────────────────────────────────────────────────
    # The scan is the same fetch the poster makes, so seeing it here costs one
    # extra call and shows exactly what would go out.
    props = await asyncio.to_thread(underdog.to_board_props)
    print(f"underdog board: {len(props or [])} straight two-way tennis prop(s)")
    if not props:
        print("  nothing on the board — not posting")
        return 0

    import pick_of_day
    # _rank_board returns (candidates, thin_slate_flag) — the second value is a
    # BOOL, not a list of thin plays.
    ordered, thin_slate = await pick_of_day._rank_board(props=props)
    print(f"  {len(ordered or [])} cleared the gating"
          f"{' (thin slate)' if thin_slate else ''}")
    for i, r in enumerate((ordered or [])[:12], 1):
        print(f"   {i:2d}. {str(r.get('player'))[:22]:22s} "
              f"{str(r.get('lean')):5s} {r.get('line')!s:>6s} "
              f"{str(r.get('prop_type'))[:22]:22s} "
              f"proj {r.get('projection')!s:>6s}  conf {r.get('confidence')}")

    if not a.post:
        print("\ndry run — nothing sent. add --post to send.")
        return 0

    import discord
    import bot as B

    client = discord.Client(intents=discord.Intents.default())
    done = asyncio.Event()
    rc = {"code": 0}

    @client.event
    async def on_ready():
        try:
            ch = client.get_channel(B.UNDERDOG_CHANNEL_ID) \
                or await client.fetch_channel(B.UNDERDOG_CHANNEL_ID)
            # The bot module holds its own client; the poster only needs a
            # channel it can send on, and this one is connected.
            status = await B._post_underdog_board(
                ch, track=not a.no_track, additional=a.additional)
            # The id, not the name: an id cannot fail to encode, and it is what
            # identifies the channel unambiguously anyway.
            print(f"posted -> channel {B.UNDERDOG_CHANNEL_ID}: {status}")
        except Exception as exc:  # noqa: BLE001
            log.exception("post failed")
            print(f"FAILED: {type(exc).__name__}: {str(exc)[:200]}")
            rc["code"] = 1
        finally:
            done.set()
            await client.close()

    await client.start(token)
    await done.wait()
    return rc["code"]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
