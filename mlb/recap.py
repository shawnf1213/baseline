"""
MLB recaps — per book, to their own channels.

COMPLETELY SEPARATE FROM THE TENNIS RECAP. Different store (mlb_picks, not
picks), different channels, different embed builder, different resolver. Nothing
in this module touches discord-bot/bot.py, `_post_recap_for`, `daily_recap_embed`
or the tennis record. A tennis recap cannot be affected by anything here, and an
MLB failure is caught at every entry point (Rule 2).

One deliberate similarity: the cashed convention. W+PUSH over W+L+PUSH with VOID
excluded, exactly as tennis scores it — so a 60% here and a 60% there mean the
same thing even though the records live apart and must never be summed.

SHADOW: these post to hidden channels. Nothing is announced with @everyone.
"""

import os
import logging
import datetime as _dt

from . import store, post as _post, client
from .post import BOOK_LABEL, COLOR

log = logging.getLogger("baseline.mlb.recap")

_MARK = {"W": "✅", "L": "❌", "PUSH": "➖", "VOID": "🚫", "PENDING": "⏳"}


def et_today() -> str:
    """MLB's own calendar day, US Eastern."""
    return _dt.datetime.now(
        _dt.timezone(_dt.timedelta(hours=-4))).strftime("%Y-%m-%d")


def resolve_pending(book: str = None) -> dict:
    """Grade every ungraded MLB pick whose start has completed.

    Uses the sport module's own resolve()/grade(), so the physical-completion
    logic lives with the sport. A start that cannot be found stays PENDING rather
    than being graded as a zero — the tennis resolver produced four misgrades in
    a week by treating absent data as a real result, and that lesson transfers.

    EXCEPT ONCE THE GAME IS FINAL. A pitcher who was scratched will NEVER produce
    a game log, so "stays pending" means pending forever, and one such pick holds
    its whole slate's recap hostage — which is exactly what happened to 8/8. When
    the game has finished and the player still has no line in it, he did not
    play: that is a VOID, the same thing the book does, not an unknown.
    """
    from . import board as _board
    out = {"graded": 0, "still_pending": 0, "voided": 0, "errors": 0}
    _final_cache = {}

    def _game_is_final(slate_date, game_pk) -> bool:
        """True when this pick's game has finished. Unknown -> False, so a
        missing schedule can never manufacture a void."""
        if not game_pk or not slate_date:
            return False
        if slate_date not in _final_cache:
            try:
                _final_cache[slate_date] = {
                    g.get("game_pk"): g.get("abstract_state")
                    for g in client.get_schedule(slate_date)}
            except Exception:  # noqa: BLE001
                _final_cache[slate_date] = {}
        return _final_cache[slate_date].get(game_pk) == "Final"

    for row in store.pending(book):
        try:
            # THE PROP MUST BE PASSED. Defaulting to strikeouts would settle an
            # earned-runs pick against the pitcher's K count — a silent misgrade
            # that looks like a real result.
            r = _board.resolve(row.get("pitcher_id"),
                               game_date=row.get("slate_date"),
                               prop=row.get("prop_type") or "strikeouts")
            if r.get("result") != "OK":
                if _game_is_final(row.get("slate_date"), row.get("game_pk")):
                    if store.update_result(row["id"], "VOID", None):
                        out["voided"] += 1
                        log.info("mlb resolve: VOID %s %s (%s) — game final, "
                                 "no appearance: %s", row.get("pitcher"),
                                 row.get("prop_type"), row.get("slate_date"),
                                 r.get("reason"))
                    else:
                        out["errors"] += 1
                    continue
                out["still_pending"] += 1
                continue
            value = r.get("value")
            grade = _board.grade(row.get("lean"), row.get("line"), value)
            if grade == "NEEDS REVIEW":
                out["still_pending"] += 1
                continue
            if store.update_result(row["id"], grade, value):
                out["graded"] += 1
            else:
                out["errors"] += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("mlb resolve_pending row %s failed: %s",
                          row.get("id"), exc)
            out["errors"] += 1
    log.info("mlb resolve (%s): %s", book or "all books", out)
    return out


def force_resolve_all(book: str, value: float, slate_date: str = None) -> dict:
    """TEST ONLY — grade every pending pick with a FIXED value.

    Exists so the store -> resolve -> recap chain can be exercised end to end
    before real games settle. It writes fabricated results, so it must never run
    on a slate whose numbers anyone will read as real: callers gate it behind an
    explicit env flag and the rows it writes stay in the shadow MLB database,
    never the tennis record.

    Returns {graded, errors}.
    """
    out = {"graded": 0, "errors": 0, "value": value}
    from . import board as _board
    for row in store.pending(book):
        if slate_date and row.get("slate_date") != slate_date:
            continue
        try:
            grade = _board.grade(row.get("lean"), row.get("line"), value)
            if store.update_result(row["id"], grade, value):
                out["graded"] += 1
            else:
                out["errors"] += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("mlb force_resolve row %s failed: %s", row.get("id"), exc)
            out["errors"] += 1
    log.warning("mlb FORCE-RESOLVE (%s) with test value %s: %s", book, value, out)
    return out


def build_recap_embed(book: str, slate_date: str, shadow: bool = True) -> dict:
    """One book's recap for one slate. Returns {} when there is nothing to show."""
    rows = store.board_for(book, slate_date)
    if not rows:
        return {}
    label = BOOK_LABEL.get(book, book)
    lines = []
    for r in rows:
        res = (r.get("result") or "PENDING").upper()
        mark = _MARK.get(res, "⏳")
        val = r.get("result_value")
        # Unit comes from the row's own prop — a recap that labels every result
        # "K" is wrong the moment the board carries more than strikeouts.
        from .post import PROP_LABEL
        unit = PROP_LABEL.get(r.get("prop_type") or "strikeouts", ("", ""))[0]
        tail = ""
        if res in ("W", "L", "PUSH") and isinstance(val, (int, float)):
            tail = f"  →  **{val:g} {unit}**"
        elif res == "VOID":
            tail = "  —  **DNP**"
        lines.append(f"{mark} **{r.get('pitcher')}** "
                     f"{r.get('lean')} {r.get('line')} {unit}{tail}")
    w = sum(1 for r in rows if (r.get("result") or "") in ("W", "PUSH"))
    l = sum(1 for r in rows if (r.get("result") or "") == "L")
    v = sum(1 for r in rows if (r.get("result") or "") == "VOID")
    pend = sum(1 for r in rows if (r.get("result") or "PENDING") == "PENDING")
    dec = w + l
    day = f"**Today:** {w}/{dec} cashed ({w/dec*100:.0f}%)" if dec else \
          "**Today:** nothing settled yet"
    roll = store.record(book, since_days=30)
    r30 = ""
    if roll and roll.get("win_rate") is not None:
        r30 = (f"\n**Last 30 days:** {roll['wins']}/"
               f"{roll['wins'] + roll['losses']} cashed ({roll['win_rate']:.0f}%)")
    extra = []
    if v:
        extra.append(f"{v} void")
    if pend:
        extra.append(f"{pend} pending")
    suffix = ("\n_" + " · ".join(extra) + "_") if extra else ""
    desc = "\n".join(lines) + "\n\n" + day + r30 + suffix
    if shadow:
        desc = ("⚠️ **SHADOW** — MLB is in testing. This record is separate from "
                "tennis and is not part of the public track record.\n\n" + desc)
    try:
        _y, _m, _d = slate_date.split("-")
        _label_date = f"{int(_m)}/{int(_d)}"      # 8/7, matching the tennis recaps
    except Exception:  # noqa: BLE001
        _label_date = slate_date
    return {
        "title": f"📊 {_label_date} {label} MLB Recap",
        "description": desc[:4000],
        "color": COLOR,
        "footer": {"text": f"Baseline MLB · {label}"
                           + (" · shadow" if shadow else "")},
    }


def already_posted(book: str, slate_date: str, token: str = None) -> bool:
    """True if this book's recap for this slate is ALREADY in its channel.

    Reads the channel rather than trusting in-memory state, exactly as the tennis
    guard does: a restart, a redeploy or a manual post must never produce a second
    recap for the same day. tasks.loop fires immediately on start, so without this
    every bot restart reposted — which is precisely what happened.

    On any read failure returns TRUE (refuse to post). Duplicate spam is worse
    than a missed recap, and a missed one is recovered on the next pass.
    """
    import requests
    cid = _post.channel_for(book, "recap")
    tok = token or os.getenv("DISCORD_BOT_TOKEN", "")
    if not cid or not tok:
        return True
    try:
        _y, _m, _d = slate_date.split("-")
        want = f"{int(_m)}/{int(_d)} {BOOK_LABEL.get(book, book)} MLB Recap"
    except Exception:  # noqa: BLE001
        return True
    try:
        r = requests.get(
            f"https://discord.com/api/v10/channels/{cid}/messages",
            headers={"Authorization": f"Bot {tok}"},
            params={"limit": 50}, timeout=20)
        if r.status_code >= 300:
            log.warning("mlb recap dup-check HTTP %s — not posting", r.status_code)
            return True
        for m in (r.json() or []):
            for e in (m.get("embeds") or []):
                if want in (e.get("title") or ""):
                    return True
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb recap dup-check failed (%s) — not posting", str(exc)[:120])
        return True


LOOKBACK_DAYS = 3


def pending_slates(book: str, days: int = LOOKBACK_DAYS) -> list:
    """Stored slates for this book over the last `days`, OLDEST FIRST.

    A recap covers the slate that FINISHED, not the one in progress. Defaulting
    to today was the bug that swallowed the 8/8 recap: at 22:21 ET the 8/8 board
    was found but one pick was still pending, so it held — and by 00:21 ET
    "today" had rolled to 8/9, which has no stored board yet. The day became
    unreachable, and every later pass reported "no stored board for that slate"
    as though nothing had ever been boarded.

    Looking back several days, oldest first, also recovers a slate held up by a
    late finish: it posts as soon as it settles instead of being skipped.
    """
    out = []
    today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-4)))
    for back in range(days, -1, -1):
        d = (today - _dt.timedelta(days=back)).strftime("%Y-%m-%d")
        if store.board_for(book, d):
            out.append(d)
    return out


def post_ready_recap(book: str, token: str = None, shadow: bool = True) -> dict:
    """Post the oldest stored slate that is fully settled and not yet posted.

    This is what the scheduled loop should call. post_recap() targets ONE slate;
    this picks the right one.
    """
    slates = pending_slates(book)
    if not slates:
        return {"ok": False, "book": book, "reason": "no stored board in the "
                f"last {LOOKBACK_DAYS} days"}
    last = {"ok": False, "book": book, "reason": "nothing ready"}
    for d in slates:
        res = post_recap(book, d, token=token, shadow=shadow)
        if res.get("ok"):
            return res
        last = res
        last["slate"] = d
    return last


def post_recap(book: str, slate_date: str = None, token: str = None,
               shadow: bool = True, require_settled: bool = True,
               force: bool = False) -> dict:
    """POST one book's recap to that book's MLB recap channel.

    require_settled mirrors the tennis rule: a day posts only once every pick on
    it is settled. An incomplete recap is worse than a late one. Never raises.

    Callers wanting "whichever day is ready" should use post_ready_recap();
    this one recaps exactly the slate it is given.
    """
    import requests
    slate_date = slate_date or et_today()
    cid = _post.channel_for(book, "recap")
    tok = token or os.getenv("DISCORD_BOT_TOKEN", "")
    if not cid:
        return {"ok": False, "book": book,
                "reason": f"no recap channel configured for {book!r}"}
    if not force and already_posted(book, slate_date, token=tok):
        return {"ok": False, "book": book,
                "reason": "recap already posted for that slate — skipping"}
    rows = store.board_for(book, slate_date)
    if not rows:
        return {"ok": False, "book": book, "reason": "no stored board for that slate"}
    if require_settled:
        pend = [r for r in rows if (r.get("result") or "PENDING") == "PENDING"]
        if pend:
            return {"ok": False, "book": book, "reason":
                    f"{len(pend)} pick(s) still pending — holding the recap"}
    if not tok:
        return {"ok": False, "book": book, "reason": "no DISCORD_BOT_TOKEN"}
    embed = build_recap_embed(book, slate_date, shadow=shadow)
    if not embed:
        return {"ok": False, "book": book, "reason": "nothing to recap"}
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{cid}/messages",
            headers={"Authorization": f"Bot {tok}",
                     "Content-Type": "application/json"},
            json={"embeds": [embed], "allowed_mentions": {"parse": []}},
            timeout=30)
        if r.status_code >= 300:
            log.warning("mlb recap (%s) failed %s: %s", book, r.status_code,
                        r.text[:300])
            return {"ok": False, "book": book, "status": r.status_code,
                    "reason": r.text[:300]}
        return {"ok": True, "book": book, "channel_id": cid,
                "message_id": (r.json() or {}).get("id")}
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb post_recap (%s) failed: %s", book, exc)
        return {"ok": False, "book": book, "reason": str(exc)[:200]}
