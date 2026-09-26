"""Build today's YouTube Short: narration first, then a timeline that fits it.

AUDIO LEADS. The promo renderer picked scene lengths and the music was cut to
them; a voiceover cannot work that way — the picture has to wait for the
sentence. So every line is synthesised FIRST, measured, and the scene timings
are derived from those real durations. Nothing is guessed and nothing drifts.

IT NARRATES WHAT WAS POSTED, not a fresh projection. The stored pick carries its
own model_inputs (opponent hold, expected sets, P(side), the mixture), so the
video repeats the number that went out on Discord. Re-projecting here would let
the Short disagree with the post the moment Sofascore refreshed — the same class
of split that put two different confidences on one prop.
"""

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import json
import os
import re
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, HERE)

from pick import choose, latest_batch, slate_date  # noqa: E402
import pick_of_day as _POD  # noqa: E402  (pick.py put it on the path)

# ── VOICE ────────────────────────────────────────────────────────────────────
# NEURAL, NOT SAPI. The first cut used Windows' built-in Speech API, which is a
# 2013-era concatenative voice — flat pitch, no sentence prosody, and it reads a
# number exactly like it reads a name. edge-tts reaches Microsoft's current
# neural voices, needs no API key and costs nothing, and the difference is not
# subtle.
#
# Andrew is a conversational voice ("warm, confident, authentic") rather than a
# newsreader — the register that suits explaining a number rather than announcing
# one. SHORT_VOICE swaps it; --list-voices shows the rest.
VOICE = os.getenv("SHORT_VOICE", "en-US-AndrewMultilingualNeural")
VOICE_RATE = os.getenv("SHORT_VOICE_RATE", "+4%")   # a touch quicker than default
GAP = 0.30          # seconds of breath between lines
TAIL = 1.1          # hold after the last word

_FFMPEG = os.path.join(HERE, "node_modules", "ffmpeg-static", "ffmpeg.exe")


# Windows process-creation flags. CREATE_NEW_PROCESS_GROUP is the important one:
# a console control event delivered to this process will NOT propagate into the
# child, and vice versa.
_NO_WINDOW = 0x08000000
_NEW_GROUP = 0x00000200
_FLAGS = (_NO_WINDOW | _NEW_GROUP) if os.name == "nt" else 0


def _edge(text: str, mp3: str) -> None:
    """Synthesise via the edge-tts CLI, in its own process group.

    NOT the in-process async API. Under Task Scheduler there is no attached
    console, and asyncio's Windows event loop installs a console CTRL handler on
    startup — the first scheduled run died eight seconds in with 0xC000013A
    (STATUS_CONTROL_C_EXIT) and a stray ^C in the log, having worked perfectly
    every time it was run by hand from a terminal.
    Running the synthesis as a detached child removes the whole question: it has
    its own process group, so nothing about consoles can reach back into the job.
    """
    subprocess.run([sys.executable, "-m", "edge_tts",
                    "--text", text, "--voice", VOICE,
                    "--rate", VOICE_RATE, "--write-media", mp3],
                   check=True, capture_output=True, creationflags=_FLAGS)


def _sapi(text: str, path: str) -> None:
    """Last-resort local voice, so a network blip cannot stop the day's post."""
    ps = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile({json.dumps(path)})
$s.Speak({json.dumps(text)})
$s.Dispose()
"""
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, capture_output=True, creationflags=_FLAGS)


def _say(text: str, path: str) -> float:
    """Speak `text` to a wav and return its duration in seconds."""
    mp3 = path[:-4] + ".mp3"
    try:
        _edge(text, mp3)
        # One canonical wav format for every segment, so the concat below can
        # splice raw frames without resampling anything.
        subprocess.run([_FFMPEG, "-y", "-i", mp3, "-ac", "1", "-ar", "24000",
                        "-sample_fmt", "s16", path],
                       check=True, capture_output=True, creationflags=_FLAGS)
    except Exception as exc:  # noqa: BLE001
        print(f"   (neural voice unavailable: {type(exc).__name__} — "
              f"falling back to the local voice)")
        _sapi(text, path)
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _fmt(v) -> str:
    """4.5 -> '4.5', 5.0 -> '5' — so the voice says 'five', not 'five point oh'."""
    f = float(v)
    return str(int(f)) if f == int(f) else str(round(f, 2))


def _lines(p: dict, kind: str) -> list:
    """(segment id, spoken text). One clip each, so the picture can follow."""
    mi = p.get("model_inputs")
    if isinstance(mi, str):
        try:
            mi = json.loads(mi)
        except Exception:  # noqa: BLE001
            mi = {}
    mi = mi or {}

    player = p.get("player") or ""
    opp = p.get("opponent") or ""
    prop = (p.get("prop_type") or "").lower()
    lean = (p.get("lean") or "").upper()
    line = _fmt(p.get("line"))
    proj = _fmt(p.get("model_projection"))
    where = (p.get("tournament") or "").split(",")[0].strip()

    # SHORT, BECAUSE IT IS THE HOOK. This used to explain the selection rule —
    # "no pick of the day cleared the bar today, so here's the number one play on
    # the board" — which is four seconds of housekeeping before anything happens.
    # A scroller decides in about one. The rule still governs WHICH pick is shown;
    # it just is not the first thing said about it.
    if kind == "potd":
        head = "Baseline's pick of the day."
    elif kind == "top":
        head = "Today's number one play."
    else:
        # A custom label like "#2 PLAY" — spoken as an ordinal so the voice does
        # not read a hash character.
        _m = re.search(r"#\s*(\d+)", str(kind))
        _n = {"2": "two", "3": "three", "4": "four", "5": "five"}.get(
            _m.group(1) if _m else "", None)
        head = (f"Today's number {_n} play." if _n
                else "Another play from today's board.")

    out = [("hook", head),
           ("who", f"{player}, {prop}, {('in ' + where) if where else ''}.".replace(" ,", ","))]
    out.append(("line", f"The book has the line at {line}. Baseline's model projects {proj}."))

    # THE CONCLUSION HAS TO FOLLOW THE LEAN. This always ended "that is a lot of
    # break chances", which argues for the OVER — and read as self-contradiction
    # the first time the pick was an UNDER.
    why = []
    hold = mi.get("opp_hold")
    if isinstance(hold, (int, float)):
        why.append(f"{opp} holds serve {round(hold)} percent of the time")
    sets_ = mi.get("expected_sets")
    if isinstance(sets_, (int, float)):
        # No leading "and" — Player Total Games Won carries no opp_hold, so this
        # was the ONLY clause and the line began "and the model expects 2.1 sets."
        why.append(f"the model expects {sets_} sets")
    if why:
        tail = ""
        if "break" in prop:
            tail = (", that is a lot of break chances." if lean == "OVER"
                    else ". A short match caps how many break chances there are.")
        else:
            tail = "."
        out.append(("why", " and ".join(why) + tail))

    # THE NUMBER DISCORD POSTED. Not the raw score (73 here), and not the model's
    # side-probability (56) — the CALIBRATED confidence, which is what the bot
    # posts and what the website shows. Computed with the bot's own function so a
    # fourth number cannot appear on a public video; on this pick all three
    # surfaces now say 53.
    conf = _POD.confidence_for(p.get("model_projection"), p.get("line"))
    if isinstance(conf, (int, float)):
        out.append(("verdict",
                    f"That's {lean.lower()}, at {round(conf)} percent confidence."))
    else:
        out.append(("verdict", f"That's {lean.lower()}."))

    out.append(("cta", "The full board is at baseline e v dot com."))
    return [(k, t) for k, t in out if t and t.strip()]


def _override(rows: list):
    """(pick, label) from the command line, or (None, None).

    WHY THIS EXISTS: a line gets pulled from the board between the Discord post
    and the video being built, and the ⭐ is then unpostable. The board moves on
    to the next play and so must this — but which play that is, is a call the
    operator makes against the live board, not something to re-derive here and
    hope it agrees.

        --player "Caroline Dolehide"      pick that player's play
        --label  "#2 PLAY"                what to call it (default: #N PLAY)
    """
    argv = sys.argv[1:]

    def val(flag):
        return argv[argv.index(flag) + 1] if flag in argv and len(argv) > argv.index(flag) + 1 else None

    name = val("--player")
    if not name:
        return None, None
    want = " ".join(name.lower().split())
    hit = next((p for p in rows
                if " ".join(str(p.get("player") or "").lower().split()) == want), None)
    if not hit:
        print(f"no pick on the current board for {name!r}")
        print("  board has: " + ", ".join(str(p.get("player")) for p in rows))
        return None, "missing"
    return hit, (val("--label") or "featured")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    rows = latest_batch()
    pick, kind = _override(rows)
    if kind == "missing":
        return 1
    if not pick:
        pick, kind = choose()
    if not pick:
        print("no picks today — nothing to build")
        return 1

    print(f"showcase ({kind}): {pick['player']} {pick['prop_type']} "
          f"{pick['lean']} {pick['line']} -> {pick['model_projection']}")

    segs, t = [], 0.0
    parts = []
    for sid, text in _lines(pick, kind):
        wav = os.path.join(OUT, f"vo_{sid}.wav")
        dur = _say(text, wav)
        segs.append({"id": sid, "text": text, "start": round(t, 3),
                     "dur": round(dur, 3)})
        parts.append(wav)
        print(f"   {sid:8} {dur:5.2f}s  {text}")
        t += dur + GAP
    total = round(t - GAP + TAIL, 3)

    # One narration track, gaps included, so the render only has to mux.
    with wave.open(parts[0], "rb") as w0:
        params = w0.getparams()
    silence = b"\x00" * int(params.framerate * GAP) * params.sampwidth * params.nchannels
    narration = os.path.join(OUT, "narration.wav")
    with wave.open(narration, "wb") as out:
        out.setparams(params)
        for i, f in enumerate(parts):
            if i:
                out.writeframes(silence)
            with wave.open(f, "rb") as w:
                out.writeframes(w.readframes(w.getnframes()))
        out.writeframes(b"\x00" * int(params.framerate * TAIL)
                        * params.sampwidth * params.nchannels)

    mi = pick.get("model_inputs")
    if isinstance(mi, str):
        try:
            mi = json.loads(mi)
        except Exception:  # noqa: BLE001
            mi = {}
    plan = {
        # The ET slate these picks are FOR. Using the run's own wall-clock
        # day labelled the 22:00 ET board with the previous date.
        "date": slate_date(latest_batch()), "kind": kind, "duration": total,
        "segments": segs,
        "pick": {
            "player": pick.get("player"), "opponent": pick.get("opponent"),
            "prop": pick.get("prop_type"), "lean": pick.get("lean"),
            "line": pick.get("line"), "projection": pick.get("model_projection"),
            "confidence": pick.get("confidence"),
            # what Discord and the website display for this pick
            "confidence_display": _POD.confidence_for(
                pick.get("model_projection"), pick.get("line")),
            "tournament": pick.get("tournament"), "surface": pick.get("surface"),
            "inputs": mi or {},
        },
    }
    with open(os.path.join(OUT, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1)
    print(f"\nnarration {total:.1f}s -> {narration}")
    print(f"plan -> {os.path.join(OUT, 'plan.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
