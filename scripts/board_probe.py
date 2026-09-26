"""What would the tennis board look like if it generated RIGHT NOW?

READ-ONLY. pick_of_day.generate_ranked_and_slip() evaluates and ranks; it does
not log picks, does not start the line monitor and does not touch Discord — all
of that lives in bot._post_daily_picks, which is not called here. Nothing is
posted and nothing enters the record.

Written to answer one question: the Asia swing opens its card around 21:00 ET,
so an 8pm board has under an hour of lead on it. Moving the board earlier only
helps if the book has actually put the lines up by then, and the only way to
know that is to look at the live board at the hour in question.

Run it through `railway run` so it sees production's environment:

    cd E:\\baseline
    railway run python scripts/board_probe.py
"""
import asyncio
import datetime
import os
import sys
import zoneinfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "discord-bot"))
sys.path.insert(0, r"E:\baseline\discord-bot")

try:                      # player names and embeds carry non-cp1252 characters
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:         # noqa: BLE001
    pass

ET = zoneinfo.ZoneInfo("America/New_York")
import pick_of_day as pod  # noqa: E402


async def main():
    now = datetime.datetime.now(ET)
    print(f"=== TENNIS BOARD AS IT WOULD GENERATE AT {now:%H:%M} ET "
          f"({now:%a %Y-%m-%d}) ===")

    raw = await asyncio.to_thread(pod.current_board_lines)
    print(f"live book board: {len(raw or {})} tennis line(s) up right now")

    b = await pod.generate_ranked_and_slip()
    ranked = b.get("ranked") or []
    print(f"thin_slate={b.get('thin_slate')}  has_star={b.get('has_star')}  "
          f"MAX_RANKED_PLAYS={pod.MAX_RANKED_PLAYS}  "
          f"BOARD_MIN_CONF={pod.BOARD_MIN_CONF}\n")
    if not ranked:
        print("  (no qualifying plays)")
    for i, p in enumerate(ranked, 1):
        st = p.get("start_timestamp") or (p.get("data") or {}).get("start_timestamp")
        when = ""
        if st:
            e = datetime.datetime.fromtimestamp(st, datetime.timezone.utc).astimezone(ET)
            lead = (e - now).total_seconds() / 3600.0
            when = f"{e:%a %m-%d %H:%M} ET  (+{lead:.1f}h)"
        star = "*" if i == 1 and b.get("has_star") else " "
        _d = p.get("data") or {}
        _es = p.get("edge_sigma")
        _do = p.get("confidence_data_only")
        _h, _ho = _d.get("health_score"), _d.get("opponent_health_score")
        print("  %s%d. %-22s %-24s %-5s %-6s proj=%-6s conf=%-5s edge=%-6s data=%-5s %s" % (
            star, i, str(p.get("player"))[:22], str(p.get("prop_type"))[:24],
            p.get("lean"), p.get("line"), p.get("projection"),
            p.get("confidence"),
            ("%.2f" % _es) if isinstance(_es, (int, float)) else "--",
            _do if _do is not None else "--", when))
        t = p.get("tournament") or _d.get("tournament")
        if t:
            print(f"       {t}")
        # Freshness — court load for BOTH players, plus what it cost confidence.
        if _h is not None or _ho is not None:
            _pen = _d.get("health_conf_penalty") or 0
            print("       fresh %s (opp %s)%s  %s" % (
                _h if _h is not None else "--",
                _ho if _ho is not None else "--",
                (" conf %+d" % _pen) if _pen else "",
                _d.get("health_basis") or ""))
        # The coherence check that caught the Volynets board.
        _ge = _d.get("games_accounting_error")
        if isinstance(_ge, (int, float)) and abs(_ge) > 1.0:
            print("       ⚠ games accounting error %+.2f" % _ge)

    # ── THE 3x, and WHY each play is or is not a leg ─────────────────────────
    # The board and the slip are selected by different rules, and the slip's are
    # the ones that changed most recently — so a probe that shows only the board
    # cannot answer "why is there no 3x tonight".
    print(f"\n=== 3x  (conf floors {pod.SLIP_MIN_CONF}/{pod.SLIP_PROP_MIN_CONF}, "
          f"excluded {sorted(pod.SLIP_PROP_EXCLUDED)}, "
          f"edge floor {pod.SLIP_MIN_EDGE_SIGMA}σ) ===")
    _star = ranked[:1] if (ranked and b.get("has_star")) else []
    slip = pod._select_slip(ranked, _star)
    for p in ranked:
        if _star and p is _star[0]:
            print(f"  {str(p.get('player'))[:22]:22s} {str(p.get('prop_type'))[:22]:22s}  "
                  f"-- is the star, excluded from the slip")
            continue
        why = []
        flo = pod._slip_floor(p.get("prop_type"))
        if p.get("prop_type") in pod.SLIP_PROP_EXCLUDED:
            why.append("prop excluded")
        elif (p.get("confidence") or 0) < flo:
            why.append("conf %s < %s" % (p.get("confidence"), flo))
        if not pod._edge_sigma_ok(p, pod.SLIP_MIN_EDGE_SIGMA):
            why.append("edge %.2f < %sσ" % (p.get("edge_sigma") or 0,
                                                pod.SLIP_MIN_EDGE_SIGMA))
        print(f"  {str(p.get('player'))[:22]:22s} {str(p.get('prop_type'))[:22]:22s}  "
              + ("ELIGIBLE" if not why else "cut: " + ", ".join(why)))
    print()
    if slip:
        for leg in slip:
            print(f"  LEG  {leg.get('player')} {leg.get('prop_type')} "
                  f"{leg.get('lean')} {leg.get('line')}")
    else:
        print("  no 3x — fewer than two independent qualifying legs")

asyncio.run(main())
