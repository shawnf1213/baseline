"""The plays that actually carried the ⭐, recovered from the bot's own posts.

WHY THIS EXISTS. is_potd was added after these boards had already gone out, so
their rows carry NULL — "unknown", not "was not the star". Which play held the
star cannot be recomputed after the fact: _promote_star decides it from the
whole board as it stood at post time, including plays that have since moved or
come off the book. Guessing would put inferred data into a public track record.

So they were read back out of the Discord channel instead. potd_embed() is only
posted when a board HAS a star (bot.py: `if has_star: post = [potd_embed(...)]`,
else the ranked board alone), so its presence in the channel is the flag, and
what is listed here is exactly what subscribers were shown. Scraped 2026-09-18,
covering 2026-08-31 onward — 13 boards. The dates missing from this list are
days on which nothing cleared the bar and no star was posted.

Applied once by the migration in database.py, idempotently. A row is touched
only when the match is UNIQUE; anything ambiguous is left alone and logged
rather than resolved by guesswork.
"""

import datetime as _dt

# (slate_date, player, prop_type, line, lean)
POSTED_STARS = [
    ("2026-08-31", "Linda Noskova", "Break Points Won", 5, "UNDER"),
    ("2026-09-01", "Leolia Jeanjean", "Break Points Won", 5, "UNDER"),
    ("2026-09-02", "Luca Van Assche", "Break Points Won", 4.5, "UNDER"),
    ("2026-09-03", "Alexei Popyrin", "Break Points Won", 2.5, "UNDER"),
    ("2026-09-04", "Alex Michelsen", "Break Points Won", 5, "UNDER"),
    ("2026-09-05", "Naomi Osaka", "Break Points Won", 4, "UNDER"),
    ("2026-09-06", "Frances Tiafoe", "Break Points Won", 3.5, "UNDER"),
    ("2026-09-08", "Frances Tiafoe", "Aces", 7.5, "OVER"),
    ("2026-09-11", "Noma Noha Akugue", "Break Points Won", 4.5, "OVER"),
    ("2026-09-15", "Sloane Stephens", "Break Points Won", 3.5, "UNDER"),
    ("2026-09-16", "Noemi Basiletti", "Break Points Won", 4.5, "OVER"),
    ("2026-09-17", "Laura Samson", "Break Points Won", 4.5, "OVER"),
    ("2026-09-18", "Kaitlin Quevedo", "Break Points Won", 4.5, "OVER"),
    # ── A SECOND WAVE OF LOSSES, AND A DIFFERENT CAUSE ───────────────────────
    # The three below were not "before the column existed". They were posted
    # with a ⭐ and written to the table as is_potd=0, because main.py's
    # ResultLogRequest never declared the field and pydantic dropped it silently
    # on every write. The flag was set correctly in the bot the whole time.
    #
    # That means the live path had NEVER worked: every star in this file above
    # was recovered by hand, and the record only ever looked right because the
    # backfill kept pace with it. Fixed 2026-09-21 by declaring the field; these
    # three cover the boards that posted in between.
    #
    # Read from the ⭐ embeds in the channel, same as the rest of this list.
    ("2026-09-19", "Zizou Bergs", "Break Points Won", 3.5, "UNDER"),
    ("2026-09-21", "Daria Kasatkina", "Break Points Won", 5, "OVER"),
    ("2026-09-22", "Donna Vekić", "Break Points Won", 4, "UNDER"),
]


def apply(conn, text, logger=None, slate_of=None):
    """Mark the recovered stars. Returns how many rows were newly flagged.

    MATCHED ON THE SLATE ITSELF, not on a date window around it. A window is not
    good enough here: Tiafoe was posted UNDER 3.5 Break Points Won on BOTH the
    9/04 and the 9/06 slate, so any window wide enough to contain one contained
    the other, and the pair was rightly skipped as ambiguous. `slate_of` applies
    the same noon-ET rule the bot uses, which pins each row to exactly one day.
    """
    marked = 0
    for slate, player, prop, line, lean in POSTED_STARS:
        try:
            cands = list(conn.execute(text(
                "SELECT id, generated_at FROM picks"
                " WHERE lower(player) = lower(:player)"
                "   AND lower(prop_type) = lower(:prop)"
                "   AND line = :line"
                "   AND upper(lean) = upper(:lean)"
                "   AND COALESCE(excluded_from_record, 0) = 0"
                # THE STAR IS ALWAYS A BOARD PICK. Without this, a play that
                # also rode the 3x slip that night appears twice — identical
                # player, prop, line, lean and slate — and the pair is skipped
                # as ambiguous. Kaitlin Quevedo, 9/18, was exactly that.
                "   AND COALESCE(pick_group, 'potd') = 'potd'"),
                {"player": player, "prop": prop, "line": line, "lean": lean}))
            if slate_of is not None:
                cands = [c for c in cands
                         if slate_of({"generated_at": c[1]}) == slate]
            if len(cands) != 1:
                if logger:
                    logger.warning(
                        "potd backfill: %s %s %s matched %d rows — skipped",
                        slate, player, prop, len(cands))
                continue
            res = conn.execute(text(
                "UPDATE picks SET is_potd = 1"
                " WHERE id = :i AND COALESCE(is_potd, 0) <> 1"), {"i": cands[0][0]})
            marked += res.rowcount or 0
        except Exception as exc:  # noqa: BLE001 — a backfill must never block boot
            if logger:
                logger.warning("potd backfill row failed (%s %s): %s",
                               slate, player, exc)
    return marked
