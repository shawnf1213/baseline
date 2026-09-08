"""
One-off NFL test post — proves the wiring end to end, in the real channels.

Run it through `railway run` so the bot token is INJECTED FROM THE ENVIRONMENT.
Credentials never come from arguments:

    cd E:/baseline && railway run python scripts/nfl_test_post.py

WHAT IT POSTS, AND THE ONE HONEST COMPROMISE
--------------------------------------------
With the production gate on, the week-1 board is EMPTY — every player is priced
on prior-season usage only, which measured +25.3% relative error against the
live market, so nfl.board holds all of it back. A test that posted nothing would
prove the channels are wired and nothing else.

So this lifts the gate FOR THE TEST ONLY (NFL_ALLOW_PRIOR_SEASON=1, set in this
process, never in Railway) and posts the board that results. Every row therefore
carries its own "prior-season usage only" warning, which is the point: the test
demonstrates the caveat machinery as much as the plumbing. Both the embed title
and a line above it say TEST in plain words, and nothing here ever pings.

The Underdog channel gets a STATUS post rather than a board, because Underdog's
v6 endpoint is returning 426 for every sport right now. Posting an empty board
there would read as "no plays today", which is a different and untrue statement.
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("nfl_test_post")

MAX_TEST_PLAYS = 6


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true",
                    help="actually send to Discord (otherwise dry-run to stdout)")
    ap.add_argument("--skip-intro", action="store_true",
                    help="do not post the projections-channel intro")
    a = ap.parse_args()

    token = os.getenv("DISCORD_BOT_TOKEN")
    if a.post and not token:
        print("\n  REFUSING TO POST: DISCORD_BOT_TOKEN is not set.\n"
              "  Run this through `railway run` so the real environment is\n"
              "  injected, rather than putting a token on the command line.\n")
        return 2

    # Gate lifted for THIS PROCESS ONLY — see module docstring.
    os.environ["NFL_ALLOW_PRIOR_SEASON"] = "1"
    import importlib
    from nfl import board as _b
    importlib.reload(_b)
    from nfl import post as _p, lines as _l

    print("scanning prizepicks (gate lifted for the test) ...")
    rows = _b.scan_board("prizepicks")[:MAX_TEST_PLAYS]
    print(f"  {len(rows)} row(s)")
    for r in rows:
        print(f"    {r['player']:24s} {r['prop']:16s} {r['lean']:5s} "
              f"{r['line']:6.1f}  proj {r['projection']:6.1f}  "
              f"prior_only={r.get('prior_season_only')}")

    ud = _l.fetch_underdog_lines()
    print(f"underdog lines: {len(ud)}")

    if not a.post:
        print("\ndry run — nothing sent. add --post to send.")
        return 0

    import discord
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    done = asyncio.Event()
    rc = {"code": 0}

    @client.event
    async def on_ready():
        try:
            no_ping = discord.AllowedMentions.none()
            banner = ("🧪 **TEST POST** — verifying the NFL wiring. Not a play "
                      "list, not a recommendation.")

            # 1. PrizePicks board channel
            cid = _p.channel_for("board", "prizepicks")
            ch = client.get_channel(cid)
            if ch is None:
                log.error("prizepicks channel %s not visible", cid)
            else:
                e = _p.build_board_embed(rows, "prizepicks", shadow=True,
                                         date_label="TEST")
                if e is None:
                    await ch.send(content=banner + "\n_Board came back empty._",
                                  allowed_mentions=no_ping)
                else:
                    await ch.send(content=banner, embed=e,
                                  allowed_mentions=no_ping)
                print(f"  posted board -> {cid}")

            # 2. Underdog board channel — status, not a board. See docstring.
            cid = _p.channel_for("board", "underdog")
            ch = client.get_channel(cid)
            if ch is None:
                log.error("underdog channel %s not visible", cid)
            else:
                msg = (banner + "\n\n**Underdog board: no lines available.**\n"
                       "Underdog's public board endpoint is returning "
                       "`426 upgrade_required` for every sport right now, so "
                       "there is nothing to price. The scanner is wired and "
                       "will fill this channel as soon as that feed answers "
                       "again.\n_Note: this affects the tennis Underdog board "
                       "too — same endpoint._")
                await ch.send(content=msg, allowed_mentions=no_ping)
                print(f"  posted underdog status -> {cid}")

            # 3. Projections channel — the real intro, but WITHOUT the @everyone
            #    the scheduled version uses. A test must not ping the server.
            if not a.skip_intro:
                cid = _p.channel_for("projections")
                ch = client.get_channel(cid)
                if ch is None:
                    log.error("projections channel %s not visible", cid)
                else:
                    await ch.send(content=banner, embed=_p.build_intro_embed(),
                                  allowed_mentions=no_ping)
                    print(f"  posted intro -> {cid}")

            # 4. Line-changes channel — a worked example off a real row, so the
            #    alert format can be judged on a real player rather than a stub.
            cid = _p.channel_for("lines")
            ch = client.get_channel(cid)
            if ch is None:
                log.error("line channel %s not visible", cid)
            elif rows:
                r = rows[0]
                alert = {
                    "player": r["player"], "prop": r["prop"],
                    "old_line": r["line"], "new_line": r["line"] + 1.5,
                    "projection": r["projection"],
                    "old_lean": r.get("lean"),
                    "new_lean": ("OVER" if r["projection"] > r["line"] + 1.5
                                 else "UNDER"),
                    "flipped": ((r["projection"] > r["line"])
                                != (r["projection"] > r["line"] + 1.5)),
                    "book": "prizepicks",
                }
                await ch.send(
                    content=banner + "\n_Illustrative move on a real row — the "
                                     "line did not actually change._",
                    embed=_p.build_line_alert_embed(alert),
                    allowed_mentions=no_ping)
                print(f"  posted line alert -> {cid}")
        except Exception:  # noqa: BLE001
            log.exception("test post failed")
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
