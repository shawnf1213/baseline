"""Push notifications — fan-out to members' devices through Expo's push API.

WHEN: the bot calls POST /api/push/send after a Discord post SUCCEEDS (board
or recap, per sport). The bot's call is fire-and-forget on its side and the
fan-out here runs in a daemon thread, so a slow or failing push can never
block or delay the pipeline.

WHO: every registered device whose preferences allow this event for this
sport, whose member is still entitled (re-checked at send time — a lapsed
member's token is deleted here, which is how "remove tokens on lost
membership" is enforced without a cron), and that Expo still recognises
(DeviceNotRegistered deletes the token too).

Tokens are Expo push tokens (ExponentPushToken[...]); Expo relays to APNs.
Nothing about who a member is leaves this service: the receipt is a token.
"""
import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger("baseline.push")

EXPO_URL = "https://exp.host/--/api/v2/push/send"
CHUNK = 100
EVENTS = ("board", "recap")
SPORTS = ("tennis", "nfl", "nba", "mlb")
DEFAULT_PREFS = {e: {s: True for s in SPORTS} for e in EVENTS}


def normalise_prefs(p) -> dict:
    out = {e: dict(DEFAULT_PREFS[e]) for e in EVENTS}
    if isinstance(p, dict):
        for e in EVENTS:
            ev = p.get(e)
            if isinstance(ev, dict):
                for s in SPORTS:
                    if s in ev:
                        out[e][s] = bool(ev[s])
            elif isinstance(ev, bool):
                for s in SPORTS:
                    out[e][s] = ev
    return out


def _entitled(sub: str, kind: str) -> bool:
    try:
        from . import discord_auth
        if kind == "email":
            return bool(discord_auth.access_for_email(sub).get("active"))
        return bool(discord_auth.access_for(sub, "").get("active"))
    except Exception as exc:  # noqa: BLE001 — a lookup failure keeps the token (no false deletions)
        logger.warning("push: entitlement check failed for a token: %s", exc)
        return True


def _post_chunk(messages: list) -> list:
    req = urllib.request.Request(EXPO_URL, data=json.dumps(messages).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json",
                                          "Accept-Encoding": "gzip, deflate"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    return body.get("data") or []


def _fan_out(event: str, sport: str, title: str, body: str, data: dict) -> dict:
    from . import database
    targets = database.push_targets(event, sport)
    sent = dropped = failed = 0
    messages, owners = [], []
    for token, sub, kind in targets:
        if not _entitled(sub, kind):
            database.push_delete(token)
            dropped += 1
            continue
        messages.append({"to": token, "title": title, "body": body, "sound": "default",
                         "data": {"event": event, "sport": sport, **(data or {})}})
        owners.append(token)
    for i in range(0, len(messages), CHUNK):
        chunk = messages[i:i + CHUNK]
        try:
            tickets = _post_chunk(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.warning("push: Expo request failed for %d message(s): %s", len(chunk), str(exc)[:160])
            failed += len(chunk)
            continue
        for tok, t in zip(owners[i:i + CHUNK], tickets):
            if not isinstance(t, dict):
                continue
            if t.get("status") == "ok":
                sent += 1
                continue
            failed += 1
            err = ((t.get("details") or {}).get("error")) or t.get("message") or ""
            if "DeviceNotRegistered" in str(err):
                database.push_delete(tok)
                dropped += 1
    out = {"event": event, "sport": sport, "targets": len(targets), "sent": sent,
           "dropped": dropped, "failed": failed}
    logger.info("push: %s", out)
    return out


def send_async(event: str, sport: str, title: str, body: str, data: dict = None) -> None:
    """Fire the fan-out in a daemon thread. Returns at once."""
    def _run():
        try:
            _fan_out(event, sport, title, body, data or {})
        except Exception as exc:  # noqa: BLE001 — never surfaces to the caller
            logger.warning("push: fan-out crashed: %s", exc)
    threading.Thread(target=_run, daemon=True, name=f"push-{event}-{sport}-{int(time.time())}").start()
