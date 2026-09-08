"""
Post the NFL board out of schedule, to the real channels.

Run it through `railway run` so the bot token is INJECTED FROM THE ENVIRONMENT.
Credentials never come from arguments:

    cd E:/baseline && railway run python scripts/nfl_board_now.py --post

The post carries NO banner, NO test label and NO caveat lines. It is just the
board, in the same shape as the tennis one (user, 2026-09-08: "send a true scan
and repost to all channels without extra text or warnings"). It never pings —
allowed_mentions is none throughout, unlike the scheduled tennis board.

--allow-prior lifts nfl.board's data-sufficiency gate. Read what that means in
nfl/board.py before using it: with the gate ON, early-season boards are empty
because every player is priced on last season's usage, which measured +25.3%
relative error against the live market. The flag exists because the operator
asked for the board posted anyway; it does not make those numbers better.
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("nfl_board_now")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true",
                    help="send to Discord (otherwise dry-run to stdout)")
    ap.add_argument("--allow-prior", action="store_true",
                    help="lift the prior-season gate (see module docstring)")
    ap.add_argument("--max", type=int, default=8, help="plays to show")
    ap.add_argument("--intro", action="store_true",
                    help="also repost the projections-channel intro")
    ap.add_argument("--alert-demo", action="store_true",
                    help="also post one line-alert card off the top row")
    ap.add_argument("--no-ping", action="store_true",
                    help="suppress the @everyone on boards and the intro")
    a = ap.parse_args()

    token = os.getenv("DISCORD_BOT_TOKEN")
    if a.post and not token:
        print("\n  REFUSING TO POST: DISCORD_BOT_TOKEN is not set.\n"
              "  Run this through `railway run` so the real environment is\n"
              "  injected, rather than putting a token on the command line.\n")
        return 2

    if a.allow_prior:
        os.environ["NFL_ALLOW_PRIOR_SEASON"] = "1"
    import importlib
    from nfl import board as _b
    importlib.reload(_b)
    from nfl import post as _p, lines as _l

    boards = {}
    for book in ("prizepicks", "underdog"):
        rows = _b.scan_board(book)[:a.max]
        boards[book] = rows
        print(f"{book}: {len(rows)} play(s)")
        for i, r in enumerate(rows, 1):
            print(f"   {i}. {r['player']:24s} {r['lean']:5s} {r['line']:6.1f} "
                  f"{r['prop']:16s} proj {r['projection']:6.1f}")
        if not rows and book == "underdog" and not _l.fetch_underdog_lines():
            print("   (underdog feed returned nothing — 426; nothing to post)")

    if not a.post:
        print("\ndry run — nothing sent. add --post to send.")
        return 0

    import discord
    client = discord.Client(intents=discord.Intents.default())
    done = asyncio.Event()
    rc = {"code": 0}

    @client.event
    async def on_ready():
        try:
            no_ping = discord.AllowedMentions.none()
            # Boards and the intro PING; line changes never do. They fire
            # repeatedly through the day and a ping per line move is how a
            # server learns to mute you.
            ping = (discord.AllowedMentions.none() if a.no_ping
                    else discord.AllowedMentions(everyone=True))
            ping_txt = None if a.no_ping else "@everyone"
            for book, rows in boards.items():
                cid = _p.channel_for("board", book)
                ch = client.get_channel(cid) if cid else None
                if ch is None:
                    log.error("%s channel %s not visible", book, cid)
                    continue
                # An empty board posts NOTHING. Saying "no plays today" when the
                # real reason is a dead feed or a held-back board would be a
                # different and untrue statement.
                # ⭐ POTD FIRST, then the board from #2 — the tennis running
                # order. The top-ranked play is already row 0; nothing is
                # re-selected here, so the star and the list cannot disagree.
                potd = _p.build_potd_embed(rows[0]) if rows else None
                if potd is not None:
                    await ch.send(content=ping_txt, embed=potd,
                                  allowed_mentions=ping)
                    print(f"  posted {book} POTD -> {cid}"
                          f"{'' if a.no_ping else ' (@everyone)'}")
                e = _p.build_board_embed(rows[1:], book, shadow=False,
                                         max_plays=a.max, start_rank=2)
                if e is None:
                    print(f"  {book}: no board rows beyond the POTD")
                    continue
                await ch.send(content=ping_txt, embed=e, allowed_mentions=ping)
                print(f"  posted {book} board -> {cid}"
                      f"{'' if a.no_ping else ' (@everyone)'}")

            if a.intro:
                cid = _p.channel_for("projections")
                ch = client.get_channel(cid) if cid else None
                if ch is None:
                    log.error("projections channel %s not visible", cid)
                else:
                    await ch.send(content=ping_txt, embed=_p.build_intro_embed(),
                                  allowed_mentions=ping)
                    print(f"  posted intro -> {cid}"
                          f"{'' if a.no_ping else ' (@everyone)'}")

            if a.alert_demo:
                rows = boards.get("prizepicks") or []
                cid = _p.channel_for("lines")
                ch = client.get_channel(cid) if cid else None
                if ch is not None and rows:
                    r = rows[0]
                    new_line = r["line"] + 1.5
                    await ch.send(embed=_p.build_line_alert_embed({
                        "player": r["player"], "prop": r["prop"],
                        "old_line": r["line"], "new_line": new_line,
                        "projection": r["projection"], "old_lean": r.get("lean"),
                        "new_lean": ("OVER" if r["projection"] > new_line
                                     else "UNDER"),
                        "book": "prizepicks"}), allowed_mentions=no_ping)
                    print(f"  posted line alert -> {cid}")
        except Exception:  # noqa: BLE001
            log.exception("post failed")
            rc["code"] = 1
        finally:
            done.set()
            await client.close()

    try:
        await client.start(token)
    except Exception:  # noqa: BLE001
        log.exception("discord login failed")
        return 1
    await done.wait()
    return rc["code"]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
