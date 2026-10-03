"""
Durable storage for the Pick of the Day results tracker (Feature 1).

Uses the Railway-provisioned PostgreSQL database via the DATABASE_URL env var.
Completely self-contained: if DATABASE_URL is missing or the DB is unreachable,
every helper degrades to a no-op / empty result and logs a warning, so a DB
problem can never crash the API or affect any prop calculation.

Records survive Railway redeploys, restarts and new deployments because they
live in Postgres, not in memory or the (ephemeral) container filesystem.
"""

import os
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("baseline.database")

# Railway/Heroku historically hand out "postgres://"; SQLAlchemy needs
# "postgresql://". Normalise so either form works.
_RAW_URL = os.getenv("DATABASE_URL", "") or ""
if _RAW_URL.startswith("postgres://"):
    _RAW_URL = _RAW_URL.replace("postgres://", "postgresql://", 1)
DATABASE_URL = _RAW_URL

# Model-generation marker stamped on every new pick (Fix D, 2026-07-23). Bump this
# whenever a material projection-model change ships so calibration can segment
# results by generation instead of pooling incompatible models. Current value marks
# the BP four-scenario outcome-conditioning rebuild (A2): BP picks BEFORE this
# (old C1–C8 chain) carry the backfilled "pre-a2" and must not be pooled with
# post-fix BP results. Same pattern as board_policy_version.
MODEL_VERSION = "2026.07.23-bp-a2"

_engine = None
_Session = None
_READY = False

try:
    from sqlalchemy import (
        create_engine, Column, Integer, String, Float, DateTime, func,
    )
    from sqlalchemy.orm import declarative_base, sessionmaker
    Base = declarative_base()

    class NflPick(Base):
        """NFL picks — SEPARATE TABLE, SAME DATABASE.

        Deliberately not the tennis `picks` table and deliberately not a `sport`
        column on it. Two sports in one table means every tennis query has to
        remember to filter, and the first one that forgets silently pools an NFL
        record into the public tennis one. MLB made the same call with
        mlb_picks; this mirrors it.

        Same DATABASE_URL, so there is no second Postgres to provision and the
        bot reaches it exactly the way it reaches the tennis record — over the
        backend's HTTP API, which works from a container and a laptop alike.
        """
        __tablename__      = "nfl_picks"
        id                 = Column(Integer, primary_key=True, autoincrement=True)
        book               = Column(String, default="prizepicks")
        slate_date         = Column(String, nullable=False)   # ET YYYY-MM-DD
        player             = Column(String, nullable=False)
        team               = Column(String, default="")
        opponent           = Column(String, default="")
        prop_type          = Column(String, nullable=False)
        line               = Column(Float)
        model_projection   = Column(Float)
        lean               = Column(String)                   # OVER / UNDER
        confidence         = Column(Float)                    # model prob, its own side
        edge               = Column(Float)
        result             = Column(String, default="PENDING")  # W/L/PUSH/VOID/PENDING
        result_value       = Column(Float)
        is_potd            = Column(Integer, default=0)
        # WHAT THE MODEL KNEW WHEN IT PRICED THIS. A record that cannot say
        # whether a pick ran on prior-season usage cannot answer the only
        # question worth asking of an early-season NFL board.
        usage_window       = Column(String, default="")
        prior_season_only  = Column(Integer, default=0)
        shadow             = Column(Integer, default=1)
        season             = Column(Integer)
        week               = Column(Integer)
        generated_at       = Column(DateTime(timezone=True), server_default=func.now())
        resolved_at        = Column(DateTime(timezone=True), nullable=True)
        excluded_from_record = Column(Integer, default=0)
        # JSON snapshot of the projector's own drivers — carries / carry_share /
        # yards_per_carry, targets / catch_rate, pass attempts. See the migration
        # note: without these the record cannot say WHY a projection missed.
        drivers            = Column(String)

    class NflBoardRow(Base):
        """The CURRENT scanned NFL board — every priced line, not just the posted ones.

        WHY THIS EXISTS SEPARATELY FROM nfl_picks. nfl_picks is the RECORD: the
        handful of plays that were actually posted, kept forever, graded. This
        is the BOARD: everything the scan priced, replaced wholesale each run,
        and never graded. The website needs the second to show what the tennis
        board shows — the full market with our number beside each line.

        WHY THE BOT WRITES IT INSTEAD OF THE BACKEND COMPUTING IT. The NFL model
        lives in nfl/, which deploys with the BOT. Running it here would mean
        pyarrow plus a play-by-play parquet of a few hundred megabytes on the
        service that has to cold-start fast for the app. The bot already does
        this scan on a schedule and already has the data cached, so it posts the
        result and this table is the handoff.
        """
        __tablename__     = "nfl_board"
        id                = Column(Integer, primary_key=True, autoincrement=True)
        book              = Column(String, default="prizepicks", index=True)
        slate_date        = Column(String, nullable=False, index=True)
        player            = Column(String, nullable=False)
        team              = Column(String, default="")
        opponent          = Column(String, default="")
        matchup           = Column(String, default="")
        kickoff           = Column(String, default="")
        prop_type         = Column(String, nullable=False)
        line              = Column(Float)
        model_projection  = Column(Float)
        lean              = Column(String)
        confidence        = Column(Float)          # model prob on its own side
        edge              = Column(Float)
        usage_window      = Column(String, default="")
        prior_season_only = Column(Integer, default=0)
        scanned_at        = Column(DateTime(timezone=True), server_default=func.now())

    class NflPlayer(Base):
        """Published player profiles — stats and recent form for the NFL sheet.

        SAME HANDOFF AS nfl_board, for the same reason: the model lives in nfl/,
        which deploys with the BOT. The backend cannot compute a target share or
        a depth-chart rank, so the bot computes them on its scan and posts them
        here. This table is a cache of the bot's answer, not a source of truth —
        it is REPLACED per slate, never merged.

        `profile` and `form` are JSON blobs rather than columns on purpose. The
        shape is nfl/queries.py's output and it will change as that module
        grows; freezing it into thirty columns would mean a migration every time
        a stat is added, and the website only ever reads it whole.
        """
        __tablename__ = "nfl_players"
        id          = Column(Integer, primary_key=True, autoincrement=True)
        slate_date  = Column(String, nullable=False, index=True)
        player      = Column(String, nullable=False, index=True)
        team        = Column(String, default="")
        position    = Column(String, default="")
        profile     = Column(String)       # JSON: role, usage, efficiency, splits
        form        = Column(String)       # JSON: recent game log
        updated_at  = Column(DateTime(timezone=True), server_default=func.now())

    class NbaPick(Base):
        """NBA picks — SEPARATE TABLE, SAME DATABASE.

        The third sport to make the same call, for the third time for the same
        reason: not the tennis `picks` table and not a `sport` column on it. Two
        sports in one table means every tennis query has to remember to filter,
        and the first one that forgets silently pools another sport's record
        into the public tennis one. mlb_picks, nfl_picks, and now this.
        """
        __tablename__      = "nba_picks"
        id                 = Column(Integer, primary_key=True, autoincrement=True)
        book               = Column(String, default="prizepicks")
        slate_date         = Column(String, nullable=False)   # ET YYYY-MM-DD
        player             = Column(String, nullable=False)
        team               = Column(String, default="")
        opponent           = Column(String, default="")
        prop_type          = Column(String, nullable=False)
        line               = Column(Float)
        model_projection   = Column(Float)
        lean               = Column(String)                   # OVER / UNDER
        confidence         = Column(Float)                    # EVR-graded score
        edge               = Column(Float)
        result             = Column(String, default="PENDING")  # W/L/PUSH/VOID
        result_value       = Column(Float)
        is_star            = Column(Integer, default=0)
        # WHAT THE MODEL KNEW WHEN IT PRICED THIS. Before opening night every
        # NBA projection runs on last season's role, and a record that cannot
        # say which rows those were cannot answer the only question worth asking
        # of an early-season board.
        usage_window       = Column(String, default="")
        prior_season_only  = Column(Integer, default=0)
        shadow             = Column(Integer, default=1)
        season             = Column(Integer)
        generated_at       = Column(DateTime(timezone=True), server_default=func.now())
        resolved_at        = Column(DateTime(timezone=True), nullable=True)
        excluded_from_record = Column(Integer, default=0)
        # JSON snapshot of the projector's drivers — minutes, rotation cv, pace
        # factor, usage vacuum, per-stat components. The NBA version of the
        # question nfl_picks.drivers exists to answer: when a projection misses,
        # was it the minutes term or the per-minute rate?
        drivers            = Column(String)

    class NbaBoardRow(Base):
        """The CURRENT scanned NBA board — every priced line, not just posted ones.

        Same split as nfl_board vs nfl_picks, for the same reason: nba_picks is
        the RECORD (posted plays, kept forever, graded); this is the MARKET
        (everything the scan priced, replaced wholesale each run, never graded).
        The website needs the second to show what the tennis board shows.
        """
        __tablename__     = "nba_board"
        id                = Column(Integer, primary_key=True, autoincrement=True)
        book              = Column(String, default="prizepicks", index=True)
        slate_date        = Column(String, nullable=False, index=True)
        player            = Column(String, nullable=False)
        team              = Column(String, default="")
        opponent          = Column(String, default="")
        matchup           = Column(String, default="")
        tipoff            = Column(String, default="")
        prop_type         = Column(String, nullable=False)
        line              = Column(Float)
        model_projection  = Column(Float)
        fair_line         = Column(Float)
        lean              = Column(String)
        confidence        = Column(Float)
        edge              = Column(Float)
        minutes           = Column(Float)
        rotation          = Column(String, default="")
        usage_window      = Column(String, default="")
        prior_season_only = Column(Integer, default=0)
        scanned_at        = Column(DateTime(timezone=True), server_default=func.now())

    class Pick(Base):
        __tablename__ = "picks"
        id               = Column(Integer, primary_key=True, autoincrement=True)
        player           = Column(String, nullable=False)
        opponent         = Column(String, default="")
        prop_type        = Column(String, nullable=False)
        line             = Column(Float)
        model_projection = Column(Float)
        lean             = Column(String)          # OVER / UNDER
        confidence       = Column(Float)
        result           = Column(String, default="PENDING")  # W/L/PUSH/PENDING/NEEDS REVIEW
        generated_at     = Column(DateTime(timezone=True), server_default=func.now())
        resolved_at      = Column(DateTime(timezone=True), nullable=True)
        original_line    = Column(Float)
        # ── CLOSING LINE (2026-09-29) ────────────────────────────────────────
        # The last line observed before the match started. With original_line
        # this gives CLOSING LINE VALUE: did the market move toward our side
        # after we picked it, or away from it?
        #
        # WHY THIS MATTERS MORE THAN ANOTHER WIN-RATE COLUMN. Win/loss is one
        # bit per pick, so separating a 57% edge from a 54% coin flip takes
        # hundreds of graded picks — the POTD sits at 15-7 (68.2%) with a
        # confidence interval running from 49% to 88%, which answers nothing,
        # and proving it would need roughly 180 more. CLV is a continuous
        # measurement on every pick, moves the moment the market does, and does
        # not wait for a result. It is how the question "do we actually have
        # edge" gets answered in weeks instead of half a year.
        #
        # It also measures the one thing our own record cannot: whether we are
        # right BEFORE the outcome adds its noise.
        closing_line     = Column(Float)
        closing_line_at  = Column(DateTime(timezone=True), nullable=True)
        tournament       = Column(String, default="")
        surface          = Column(String, default="")
        # "potd" (Pick of the Day) or "3x" (two-leg slip). Legacy rows are NULL
        # and treated as "potd" everywhere they're read.
        pick_group       = Column(String, default="potd")
        # THE STARRED PLAY, not the group. pick_group says "this came off the
        # board rather than the 3x slip" and every board pick carries it, so it
        # cannot answer "how has the Pick of the Day done" — the question the
        # record is actually asked. 1 = this row is the (single) starred pick
        # the bot led that board with. 0 = a board pick. NULL = posted before
        # this column existed, and therefore unknown rather than "no".
        is_potd          = Column(Integer, default=0)
        # JSON snapshot of the confidence component breakdown at pick time, so a
        # faithful calibration recompute is possible later. NULL on legacy rows.
        confidence_breakdown = Column(String)
        # 1 = this pick's confidence was computed BEFORE the degraded-fetch cache
        # guard shipped (2026-07-14), so it may have been scored against a poisoned
        # Sofascore snapshot (events present, per-match statistics missing — a
        # player's usable match count collapsing to ~0). Those scores are not
        # trustworthy calibration inputs. The pick RECORD stands as posted and is
        # never altered; this flag only excludes it from calibration maths.
        pre_guard = Column(Integer, default=0)
        # Board qualification policy in force when this pick was selected:
        #   v1 = per-prop bars (standard 70/75, Total Games 85, PTGW 80, blowout
        #        exception; DF board-excluded; TG-90 / PTGW star gates)
        #   v2 = uniform 65 board floor, uniform 80 POTD bar, DF star-blocked only
        # Existing rows are backfilled to v1; new picks default to v2. Calibration
        # can report per-prop hit rates split by policy version without a reset.
        board_policy_version = Column(String, default="v2")
        # PrizePicks odds_type: "standard" or "demon" (goblins are never posted).
        # Lets the tracker / recaps / hit-rate reports segment standard vs demon.
        # Existing rows are backfilled to "standard".
        odds_type = Column(String, default="standard")
        # 1 = this record is a superseded / earlier-generation / duplicate pick that
        # must NOT count toward the public record or appear in recaps. Kept in the
        # DB for the reproducibility audit, never deleted. Default 0 = counts.
        excluded_from_record = Column(Integer, default=0)
        # Projection-model generation in force when this pick was made (Fix D).
        # Existing rows are backfilled to "pre-a2" (old BP C1–C8 chain); new picks
        # default to MODEL_VERSION. Lets calibration split BP hit-rates by the A2
        # outcome-conditioning boundary instead of pooling incompatible models.
        model_version = Column(String, default=MODEL_VERSION)
        # Actual stat the player recorded when the pick resolved (e.g. 18 aces,
        # 21 total games). Captured by the resolver at grade time so the recap can
        # show the final number next to each prop. NULL = not recorded (older rows
        # or manual grades from matches the resolver couldn't fetch).
        result_value = Column(Float, nullable=True)
        # ── THE CONFIDENCE/EDGE SPLIT, IN SHADOW (2026-09-24) ────────────────
        # The shipped `confidence` above is min(evidence, f(distance from the
        # book line)) — three separate line-derived ceilings (main._edge_cap, the
        # EVR grade, the ace big-edge cap) push it down, so it is not the "how
        # well does the data support this projection" number it is presented as.
        # These two columns record the split at pick time so it can be GRADED
        # rather than argued about:
        #   edge_sigma           |projection − line| / σ. The value signal.
        #   confidence_data_only evidence with NO line-derived ceiling.
        # Both NULL for every row written before this column existed — do not
        # backfill them, a reconstructed value is not what the model said.
        # Nothing selects on either yet; see the pick_of_day slip floors.
        edge_sigma = Column(Float, nullable=True)
        confidence_data_only = Column(Float, nullable=True)
        # ── TOUR LEVEL (2026-09-24) ──────────────────────────────────────────
        # The tournament column cannot answer "was this a challenger match":
        # Sofascore supplies a bare city, so 'Tolentino, Italy' (an ITF event)
        # and 'Cincinnati, USA' (a Masters) are indistinguishable by string. A
        # search for 'challenger' or 'itf' across the whole record matches ZERO
        # rows, which reads as "we never post lower-tier matches" and is false.
        # World rank is the only basis that works, and it is already computed on
        # every pick — it was simply never persisted, so the question could not
        # be asked of the record at all. NULL on rows predating this column.
        player_rank = Column(Integer, nullable=True)
        opponent_rank = Column(Integer, nullable=True)
        both_challenger_level = Column(Integer, nullable=True)
        # WHAT THE MODEL ACTUALLY USED, captured at pick time.
        #
        # Every retrospective analysis before this had to re-run today's code
        # against today's statistics to reconstruct a pick made days ago — which
        # is not the pick. The stats have moved, the model has changed, and an
        # anchor present then may be absent now. That is how a whole evening of
        # diagnostics went wrong: `model_projection` silently changed meaning
        # (a mean before the A2 rebuild, the fair line after), so regressing
        # outcomes against it compared two different quantities; and a games
        # margin was measured against `bp_base_proj` when the code consumed
        # `project_break_points()["projection"]`.
        #
        # projection_kind is the field that fixes the first of those: it records
        # WHAT KIND OF NUMBER model_projection is, so nobody has to infer it from
        # a date. The rest record the inputs a later question will want and which
        # cannot be recovered afterwards.
        model_inputs = Column(String)

        def to_dict(self) -> dict:
            return {
                "id": self.id,
                "player": self.player,
                "opponent": self.opponent,
                "prop_type": self.prop_type,
                "line": self.line,
                "model_projection": self.model_projection,
                "lean": self.lean,
                "confidence": self.confidence,
                "result": self.result,
                "generated_at": self.generated_at.isoformat() if self.generated_at else None,
                "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
                "original_line": self.original_line,
                "tournament": self.tournament,
                "surface": self.surface,
                "pick_group": (self.pick_group or "potd"),
                # NOT coerced to 0. NULL means "posted before the star was
                # recorded", which is different from "was not the star", and
                # potd_month_record has to be able to tell them apart.
                "is_potd": (None if self.is_potd is None else int(self.is_potd)),
                "confidence_breakdown": self.confidence_breakdown,
                "pre_guard": int(self.pre_guard or 0),
                "board_policy_version": (self.board_policy_version or "v1"),
                "odds_type": (self.odds_type or "standard"),
                "excluded_from_record": int(self.excluded_from_record or 0),
                "model_version": (self.model_version or "pre-a2"),
                "result_value": self.result_value,
                "model_inputs": self.model_inputs,
                # NULL on every row predating the split — never coerced to 0,
                # because "not recorded" and "zero edge" are different facts.
                "edge_sigma": self.edge_sigma,
                "confidence_data_only": self.confidence_data_only,
                "player_rank": self.player_rank,
                "opponent_rank": self.opponent_rank,
                # NULL, not 0 — "not recorded" and "was a tour match" are
                # different facts and the calibration has to tell them apart.
                "both_challenger_level": self.both_challenger_level,
            }

    class CacheEntry(Base):
        """Durable key-value cache — the DURABILITY layer behind the in-process
        caches, not a per-read dependency.

        Why: every cache in this app lived only in the process. A Railway deploy
        wipes them, so the opponent-hold cache measured 5/7 opponents resolved,
        then 0/7 immediately after a push — and the BP quality adjustment (a pure
        function of cache state) moved with it. Cache warmth was being destroyed
        by the act of shipping, which also silently reset the stat-rich counts and
        made cross-deploy reproducibility impossible to observe.

        Design: memory stays the hot path. Postgres is read ONCE per key on the
        first miss (lazy hydrate — no bulk load at boot) and written through on
        every set. Warm reads never touch Postgres, so there is no latency change.
        """
        __tablename__ = "cache_entries"
        cache_key   = Column(String, primary_key=True)
        value       = Column(String, nullable=False)      # JSON
        written_at  = Column(DateTime(timezone=True), server_default=func.now())
        ttl_seconds = Column(Integer)                     # NULL = never expires

    class Subscription(Base):
        """One row per PAYING CUSTOMER, keyed by their Stripe customer id.

        WHY A LOCAL TABLE AND NOT "ASK STRIPE": entitlement is checked on every
        gated request, and a network call to Stripe per request would put their
        uptime in front of ours. Stripe remains the source of truth; webhooks
        write here, and this is the fast read.

        NO CARD DATA IS EVER STORED, and none is available to store — checkout is
        Stripe-hosted, so the card never touches this server. The only payment
        identifiers here are Stripe's own opaque ids.

        discord_id links a subscription to a Discord account so the bot can grant
        and revoke the role; app_email links it to an app login. Either may be
        NULL: someone can pay before connecting Discord, and the link is made
        later without disturbing the subscription itself.
        """
        __tablename__ = "subscriptions"
        id                  = Column(Integer, primary_key=True, autoincrement=True)
        stripe_customer_id  = Column(String, nullable=False, index=True)
        stripe_sub_id       = Column(String, nullable=False, unique=True, index=True)
        # active | trialing | past_due | canceled | unpaid | incomplete
        status              = Column(String, nullable=False, default="incomplete")
        plan                = Column(String, default="")        # weekly | monthly
        discord_id          = Column(String, default="", index=True)
        app_email           = Column(String, default="", index=True)
        # When the paid period ends. Access is granted up to this instant even
        # after a cancellation — a cancel is "do not renew", not "cut them off
        # mid-period", and treating it as the latter would be taking money for
        # time not served.
        current_period_end  = Column(DateTime(timezone=True), nullable=True)
        cancel_at_period_end = Column(Integer, default=0)        # 0/1
        created_at          = Column(DateTime(timezone=True), server_default=func.now())
        updated_at          = Column(DateTime(timezone=True), server_default=func.now())
        # The IP the trial was started from, kept RAW and deliberately so: the
        # question it answers is "has this address already taken a free trial
        # under a different email", and a hash cannot be eyeballed during an
        # abuse review. Personal data under GDPR — it needs a line in the privacy
        # policy and should not outlive its purpose.
        signup_ip           = Column(String, default="", index=True)
        # A SECOND SIGNAL THAT IS NOT AN EMAIL. A new address is free to make and
        # a household IP is shared by everyone in it, so neither alone can
        # separate "a repeat trialist" from "a different person on the same
        # wifi". This is a salted hash of the address AND the browser's
        # user-agent — weak as a fingerprint, but it distinguishes two devices
        # behind one router, which is the case email and IP both get wrong.
        # Hashed for the same reason signup_ip is, and personal data likewise.
        signup_device       = Column(String, default="", index=True)

    class PreviewSession(Base):
        """One row per anonymous visitor's free look at the app.

        WHY SERVER-SIDE: a timer in the browser is a suggestion. Anything held in
        localStorage or a cookie dies with a refresh at best and an incognito
        window at worst, which is exactly the bypass this is meant to close. The
        clock therefore lives here, and the browser only ever asks how much is
        left.

        KEYED ON A HASHED IP, NOT A COOKIE. A private window carries no cookies
        and no storage, so the only thing that survives it is the address the
        request came from. Hashed because this table only needs to recognise a
        repeat visitor, never to identify one — unlike Subscription.signup_ip
        above, which exists to be read by a human.

        WHAT THIS CANNOT DO, stated plainly so nobody assumes otherwise: an IP is
        not a person. Everyone behind one office NAT or one mobile carrier CGNAT
        shares a window, and anyone who flips on a VPN or switches to cell data
        gets a fresh one. This raises the cost of a bypass; it does not make it
        impossible, and no client-side scheme does better.
        """
        __tablename__ = "preview_sessions"
        id          = Column(Integer, primary_key=True, autoincrement=True)
        visitor_key = Column(String, nullable=False, unique=True, index=True)
        first_seen  = Column(DateTime(timezone=True), server_default=func.now())
        last_seen   = Column(DateTime(timezone=True), server_default=func.now())
        hits        = Column(Integer, default=0)

    _SQLALCHEMY_OK = True
except Exception as exc:  # pragma: no cover — missing dep shouldn't crash the app
    logger.warning("SQLAlchemy unavailable — results DB disabled: %s", exc)
    _SQLALCHEMY_OK = False
    Pick = None  # type: ignore
    CacheEntry = None  # type: ignore
    Subscription = None  # type: ignore
    PreviewSession = None  # type: ignore


def init_db() -> None:
    """Create the engine and ensure the picks table exists. Never drops data.
    Safe to call once on startup; failures are logged and leave the DB disabled."""
    global _engine, _Session, _READY
    if not _SQLALCHEMY_OK:
        return
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set — results tracker DB disabled.")
        return
    try:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
        Base.metadata.create_all(_engine)   # CREATE TABLE IF NOT EXISTS — never drops
        # Lightweight migration: create_all won't ALTER an existing table, so
        # add columns introduced after the table was first created. IF NOT
        # EXISTS makes this idempotent and safe on every boot.
        try:
            from sqlalchemy import text
            with _engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS pick_group "
                    "VARCHAR DEFAULT 'potd'"))
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS confidence_breakdown VARCHAR"))
                # signup_ip: the subscriptions table already exists in production,
                # so create_all above will not add this. Existing rows stay blank —
                # the IP was never captured for them and inventing one would be
                # worse than an honest gap in the abuse history.
                conn.execute(text(
                    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS signup_ip VARCHAR"))
                # signup_device: added with the trial gate (2026-09-21). Existing
                # rows stay blank for the same reason signup_ip's do — it was
                # never captured for them, so they can only be matched on email
                # and IP, which is an honest gap rather than a guess.
                conn.execute(text(
                    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS signup_device VARCHAR"))
                # Existing rows stay NULL: the inputs were never captured for them
                # and inventing values would be worse than an honest gap.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS model_inputs VARCHAR"))
                # nfl_picks.drivers — the model's INPUTS, not just its output.
                # Measured 2026-09-27 on 142 graded NFL picks: rush_yards runs
                # 38.5% and over-projects by 31% of the line, while receptions
                # under-projects by 20% — opposite directions, which is what a
                # mis-allocated volume term looks like rather than a calibration
                # error. Deciding between the two needs the carries / carry_share
                # / yards_per_carry (and targets / catch_rate) the projector
                # already computes and hands to post.py for display, then throws
                # away. Without them the record says a projection was wrong but
                # never which half of "carries x yards per carry" was wrong.
                # Rows written before this column exists keep NULL; nothing is
                # invented for them.
                conn.execute(text(
                    "ALTER TABLE nfl_picks ADD COLUMN IF NOT EXISTS drivers VARCHAR"))
                # Closing line value — see the column note on Pick. Existing
                # rows stay NULL; nothing is back-filled, because the closing
                # line for a match already played cannot be recovered.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS closing_line FLOAT"))
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "closing_line_at TIMESTAMPTZ"))
                # pre_guard: every row that already exists when this column is
                # first created predates the degraded-fetch cache guard, so it is
                # backfilled to 1 exactly once. NULL is the "never seen" marker —
                # after this UPDATE no row is NULL, so a redeploy can't reflag
                # post-guard picks. New picks default to 0 via the column default.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS pre_guard INTEGER"))
                _bf = conn.execute(text(
                    "UPDATE picks SET pre_guard = 1 WHERE pre_guard IS NULL"))
                if getattr(_bf, "rowcount", 0):
                    logger.info("picks pre_guard backfill: %d existing rows marked "
                                "pre-cache-guard (excluded from calibration maths)",
                                _bf.rowcount)
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN pre_guard SET DEFAULT 0"))
                # board_policy_version: every row existing when this column is
                # first added predates the v2 policy, so backfill NULL -> 'v1'
                # exactly once, then set the column default to 'v2' so new picks
                # are v2 automatically. log_pick also passes 'v2' explicitly.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "board_policy_version VARCHAR"))
                _bfp = conn.execute(text(
                    "UPDATE picks SET board_policy_version = 'v1' "
                    "WHERE board_policy_version IS NULL"))
                if getattr(_bfp, "rowcount", 0):
                    logger.info("picks board_policy_version backfill: %d existing "
                                "rows marked v1", _bfp.rowcount)
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN board_policy_version "
                    "SET DEFAULT 'v2'"))
                # odds_type: existing rows predate demon evaluation -> "standard".
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS odds_type VARCHAR"))
                conn.execute(text(
                    "UPDATE picks SET odds_type = 'standard' WHERE odds_type IS NULL"))
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN odds_type SET DEFAULT 'standard'"))
                # is_potd: which board pick carried the star. Deliberately left
                # NULL on existing rows — they were posted before the bot
                # recorded it, and backfilling them to 0 would assert that none
                # of them was the Pick of the Day, which is false. NULL reads as
                # "unknown" and those rows are simply outside the POTD record.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS is_potd INTEGER"))
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN is_potd SET DEFAULT 0"))
                # One-off: recover the stars that were posted before the column
                # existed, from the bot's own channel history. See
                # potd_backfill.py for why this is read back rather than
                # recomputed. Idempotent, and never fatal.
                try:
                    from src.potd_backfill import apply as _apply_stars
                    _n = _apply_stars(conn, text, logger, _slate_date_of)
                    if _n:
                        logger.info("potd backfill: flagged %d posted star(s)", _n)
                except Exception as _exc:  # noqa: BLE001
                    logger.warning("potd backfill skipped: %s", _exc)
                # excluded_from_record: superseded / duplicate picks flagged out of
                # the record + recaps but retained for audit. Existing rows -> 0.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "excluded_from_record INTEGER"))
                conn.execute(text(
                    "UPDATE picks SET excluded_from_record = 0 "
                    "WHERE excluded_from_record IS NULL"))
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN excluded_from_record SET DEFAULT 0"))
                # model_version (Fix D): every row existing when this column is first
                # added predates the BP A2 outcome-conditioning rebuild, so backfill
                # NULL -> 'pre-a2' exactly once, then set the column default to the
                # current MODEL_VERSION so new picks are stamped automatically.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS model_version VARCHAR"))
                _bmv = conn.execute(text(
                    "UPDATE picks SET model_version = 'pre-a2' "
                    "WHERE model_version IS NULL"))
                if getattr(_bmv, "rowcount", 0):
                    logger.info("picks model_version backfill: %d existing rows marked "
                                "pre-a2 (old BP C1-C8 chain)", _bmv.rowcount)
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN model_version SET DEFAULT '%s'"
                    % MODEL_VERSION))
                # result_value: the actual stat recorded at resolution (aces, games,
                # etc.) so the recap can show the final number. Nullable, no backfill
                # — existing graded rows simply have no stored value.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "result_value DOUBLE PRECISION"))
                # edge_sigma / confidence_data_only: the confidence-vs-edge split,
                # recorded from the board so it can be graded rather than argued
                # about. DELIBERATELY NOT BACKFILLED — a value reconstructed from
                # today's code is not what the model said on the day, and the whole
                # point of these columns is to measure the new numbers on picks
                # that were actually posted. NULL means "posted before the split".
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "edge_sigma DOUBLE PRECISION"))
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "confidence_data_only DOUBLE PRECISION"))
                # Tour level. Not backfilled: ranks move, so a rank fetched
                # today is not the rank the pick was priced against, and a
                # reconstructed value would quietly become "evidence".
                for _c in ("player_rank", "opponent_rank", "both_challenger_level"):
                    conn.execute(text(
                        "ALTER TABLE picks ADD COLUMN IF NOT EXISTS %s INTEGER" % _c))
        except Exception as mexc:  # noqa: BLE001 — non-fatal; column may already exist
            logger.warning("picks pick_group migration skipped: %s", mexc)
        _READY = True
        logger.info("Results DB ready (picks table ensured).")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Results DB init failed — tracker disabled: %s", exc)
        _READY = False


def is_ready() -> bool:
    return _READY


@contextmanager
def _session():
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ── CRUD helpers (all degrade gracefully when the DB is disabled) ────────────
def _f_or_none(v):
    """float(v), or None for anything that is not a real number.

    Guards the shadow columns: the bot sends whatever the API returned, and a
    missing/garbage value must land as NULL rather than 0 or an insert error.
    """
    try:
        return float(v) if isinstance(v, (int, float)) and v == v else None
    except (TypeError, ValueError):
        return None


def _i_or_none(v):
    """int(v), or None for anything that is not a real number. Booleans count
    (both_challenger_level arrives as one), so they are coerced deliberately."""
    if isinstance(v, bool):
        return int(v)
    try:
        return int(v) if isinstance(v, (int, float)) and v == v else None
    except (TypeError, ValueError):
        return None


def log_pick(rec: dict) -> dict:
    """Insert one pick record. Returns the stored row as a dict, or {} on failure."""
    if not _READY:
        return {}
    try:
        with _session() as s:
            row = Pick(
                player=rec.get("player", ""),
                opponent=rec.get("opponent", ""),
                prop_type=rec.get("prop_type", ""),
                line=rec.get("line"),
                model_projection=rec.get("model_projection"),
                lean=(rec.get("lean") or "").upper(),
                confidence=rec.get("confidence"),
                result=(rec.get("result") or "PENDING").upper(),
                original_line=rec.get("original_line", rec.get("line")),
                tournament=rec.get("tournament", ""),
                surface=rec.get("surface", ""),
                pick_group=(rec.get("pick_group") or "potd"),
                confidence_breakdown=rec.get("confidence_breakdown"),
                board_policy_version=(rec.get("board_policy_version") or "v2"),
                odds_type=(rec.get("odds_type") or "standard"),
                model_version=(rec.get("model_version") or MODEL_VERSION),
                model_inputs=(rec.get("model_inputs") or None),
                is_potd=(1 if rec.get("is_potd") else 0),
                # Kept as None rather than 0 when absent: a pick with no recorded
                # edge is not a pick with zero edge, and the calibration that
                # reads these has to be able to tell the two apart.
                edge_sigma=_f_or_none(rec.get("edge_sigma")),
                confidence_data_only=_f_or_none(rec.get("confidence_data_only")),
                player_rank=_i_or_none(rec.get("player_rank")),
                opponent_rank=_i_or_none(rec.get("opponent_rank")),
                both_challenger_level=_i_or_none(rec.get("both_challenger_level")),
                # Settable AT INSERT so a shadow pick can never touch the
                # published record. Previously this was only reachable through
                # /api/results/exclude, i.e. a pick had to be counted first and
                # flagged out afterwards — a window in which a recap or a record
                # read would include it. Shadow props (Aces, Double Faults) are
                # logged with this set, graded by the normal resolver, and
                # excluded from record_summary throughout.
                excluded_from_record=(1 if rec.get("excluded_from_record") else 0),
            )
            s.add(row)
            s.flush()
            return row.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.exception("log_pick failed: %s", exc)
        return {}


def update_result(pick_id: int, result: str, value: float = None) -> bool:
    """Set the result (W/L/PENDING/NEEDS REVIEW) and resolved_at. Returns success.
    ``value`` is the actual stat the player recorded (aces, games, …); when given
    it's stored so the recap can show the final number. None leaves it untouched."""
    if not _READY:
        return False
    try:
        with _session() as s:
            row = s.get(Pick, int(pick_id))
            if row is None:
                return False
            row.result = (result or "").upper()
            row.resolved_at = datetime.now(timezone.utc)
            if value is not None:
                try:
                    row.result_value = float(value)
                except (TypeError, ValueError):
                    pass
            return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("update_result failed: %s", exc)
        return False


def set_excluded(ids: list, excluded: bool = True) -> int:
    """Flag (or unflag) pick rows as excluded_from_record. Retains the rows; only
    the flag changes. Returns the number of rows updated."""
    if not _READY or not ids:
        return 0
    try:
        n = 0
        with _session() as s:
            for pid in ids:
                row = s.get(Pick, int(pid))
                if row is None:
                    continue
                row.excluded_from_record = 1 if excluded else 0
                n += 1
        return n
    except Exception as exc:  # noqa: BLE001
        logger.exception("set_excluded failed: %s", exc)
        return 0


def observe_closing_line(player: str, prop_type: str, line: float) -> int:
    """Record the line currently showing for a PENDING pick. Returns rows touched.

    Called repeatedly by the line monitor while a match is still upcoming, so
    the value left behind when the match starts IS the closing line. Overwriting
    on every pass is deliberate — the monitor stops at match start, so the last
    write is the last observation before the market closed.

    MATCHED ON PLAYER + PROP, NOT ID. The monitor identifies a pick the same way
    the board does (player + prop type); the pick id is not threaded through its
    three call sites. Restricted to PENDING rows so a settled pick can never be
    rewritten, and to the newest match when a player somehow has two open, which
    is the one the monitor is watching.
    """
    if not _READY or not player or not prop_type or line is None:
        return 0
    try:
        with _session() as s:
            row = (s.query(Pick)
                   .filter(Pick.player == player,
                           Pick.prop_type == prop_type,
                           Pick.result == "PENDING")
                   .order_by(Pick.generated_at.desc()).first())
            if row is None:
                return 0
            row.closing_line = float(line)
            row.closing_line_at = datetime.now(timezone.utc)
            s.commit()
            return 1
    except Exception as exc:  # noqa: BLE001 — never break the monitor
        logger.warning("observe_closing_line(%s, %s) failed: %s",
                       player, prop_type, str(exc)[:120])
        return 0


def set_line(pick_id: int, line=None, original_line=None) -> bool:
    """Correct a pick's line / original_line (admin). Used when a PrizePicks line
    moved between posting and logging, so the stored line no longer matches what
    members played. Returns success."""
    if not _READY:
        return False
    try:
        with _session() as s:
            row = s.get(Pick, int(pick_id))
            if row is None:
                return False
            if isinstance(line, (int, float)):
                row.line = line
            if isinstance(original_line, (int, float)):
                row.original_line = original_line
            return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("set_line failed: %s", exc)
        return False


def delete_pick(pick_id: int) -> bool:
    """Delete one pick row (admin cleanup / removing a bad entry)."""
    if not _READY:
        return False
    try:
        with _session() as s:
            row = s.get(Pick, int(pick_id))
            if row is None:
                return False
            s.delete(row)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("delete_pick failed: %s", exc)
        return False


def all_picks() -> list:
    """All pick rows as dicts, most recent first."""
    if not _READY:
        return []
    try:
        with _session() as s:
            rows = s.query(Pick).order_by(Pick.generated_at.desc(), Pick.id.desc()).all()
            return [r.to_dict() for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.exception("all_picks failed: %s", exc)
        return []


def pending_picks() -> list:
    """Pick rows still awaiting a result (PENDING), oldest first."""
    if not _READY:
        return []
    try:
        with _session() as s:
            rows = (s.query(Pick)
                    .filter(Pick.result == "PENDING")
                    .order_by(Pick.generated_at.asc()).all())
            return [r.to_dict() for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.exception("pending_picks failed: %s", exc)
        return []


# ── Durable cache (see CacheEntry) ──────────────────────────────────────────
# Every helper degrades to a no-op / miss when the DB is unavailable, so a
# Postgres problem costs cache warmth and NOTHING else — the callers still have
# their in-memory layer and their network fallback.
# ── Subscriptions ────────────────────────────────────────────────────────────
def upsert_subscription(rec: dict) -> dict:
    """Insert or update one subscription, keyed on stripe_sub_id.

    UPSERT, NOT INSERT: Stripe sends created -> updated -> deleted for the same
    subscription and re-delivers any event whose response it did not see. An
    insert-only path would fan one subscription into duplicate rows and leave
    entitlement depending on which row a query happened to find first.
    """
    if not _READY or Subscription is None:
        return {}
    try:
        with _session() as s:
            row = (s.query(Subscription)
                    .filter(Subscription.stripe_sub_id == rec["stripe_sub_id"])
                    .one_or_none())
            if row is None:
                row = Subscription(stripe_sub_id=rec["stripe_sub_id"])
                s.add(row)
            for f in ("stripe_customer_id", "status", "plan",
                      "current_period_end", "cancel_at_period_end"):
                if rec.get(f) is not None:
                    setattr(row, f, rec[f])
            # Never blank an existing link: a later event may carry no metadata,
            # and overwriting a known discord_id with "" would silently strip a
            # paying subscriber of their role.
            if rec.get("discord_id"):
                row.discord_id = rec["discord_id"]
            if rec.get("app_email"):
                row.app_email = rec["app_email"]
            row.updated_at = datetime.now(timezone.utc)
            s.flush()
            return {"id": row.id, "stripe_sub_id": row.stripe_sub_id,
                    "status": row.status}
    except Exception:
        logger.exception("upsert_subscription failed")
    return {}


def find_subscription(discord_id: str = "", email: str = "") -> dict:
    """Best current subscription for a person, or {}. Prefers an ACTIVE row —
    someone who cancelled and resubscribed has two, and the live one is the
    answer."""
    if not _READY or Subscription is None:
        return {}
    if not discord_id and not email:
        return {}
    try:
        with _session() as s:
            q = s.query(Subscription)
            q = (q.filter(Subscription.discord_id == discord_id) if discord_id
                 else q.filter(Subscription.app_email == email))
            rows = q.all()
            if not rows:
                return {}
            rows.sort(key=lambda r: (
                0 if (r.status or "") in ("active", "trialing") else 1,
                -(r.current_period_end.timestamp() if r.current_period_end else 0),
            ))
            r = rows[0]
            return {"id": r.id, "stripe_customer_id": r.stripe_customer_id,
                    "stripe_sub_id": r.stripe_sub_id, "status": r.status,
                    "plan": r.plan, "discord_id": r.discord_id,
                    "app_email": r.app_email,
                    "current_period_end": r.current_period_end,
                    "cancel_at_period_end": r.cancel_at_period_end}
    except Exception:
        logger.exception("find_subscription failed")
    return {}


def subscriptions_debug() -> dict:
    """Row count and a redacted sample. Never exposed without the sync token."""
    if not _READY or Subscription is None:
        return {"ready": bool(_READY), "model": Subscription is not None,
                "note": "db disabled or model missing"}
    out = {"ready": True, "rows": 0, "sample": [], "by_status": {},
           "lapsed": []}
    with _session() as s:
        rows = s.query(Subscription).all()
        out["rows"] = len(rows)
        # STATUS HISTOGRAM + the lapsed rows, so "who still has access and why"
        # is answerable without opening Stripe. Aggregate and Discord-id only:
        # no email, no customer id, no full subscription id. This endpoint is
        # already behind BILLING_SYNC_TOKEN and must stay as boring as possible.
        from collections import Counter as _C
        out["by_status"] = dict(_C((r.status or "").lower() or "(blank)"
                                   for r in rows))
        _now = datetime.now(timezone.utc)
        for r in rows:
            st = (r.status or "").lower()
            end = r.current_period_end
            if end is not None and end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            expired = bool(end and end <= _now)
            # Must match billing.ACTIVE_STATUSES — active | trialing, unexpired.
            if st in ("active", "trialing") and not expired:
                continue
            out["lapsed"].append({
                "discord_id": r.discord_id or "",
                "status": st or "(blank)",
                "period_end": str(end)[:19] if end else None,
                "expired": expired,
                "linked": bool(r.discord_id),
            })
        for r in rows[:5]:
            out["sample"].append({
                "sub": (r.stripe_sub_id or "")[-8:],
                "status": r.status,
                "has_email": bool(r.app_email),
                "has_discord": bool(r.discord_id),
                "period_end": str(r.current_period_end)[:19] if r.current_period_end else None,
            })
        return out
    return out


# How long after a Stripe subscription ends the bot keeps trying to remove the
# role. After this the row is left alone forever, so a later grant — by hand, or
# by Whop's bot — sticks. 0 disables revocation entirely (grant-only sync).
_REVOKE_GRACE_DAYS = int(os.getenv("SUB_REVOKE_GRACE_DAYS", "14") or 14)


def subscription_role_sets() -> dict:
    """{"grant": [...], "revoke": [...]} of Discord ids for role syncing.

    THE REVOKE LIST IS DELIBERATELY NARROW: it contains only people who have a
    Stripe subscription record that has LAPSED. Someone with no record at all
    never appears in either list.

    That distinction is the whole safety of this feature. The premium role is
    also granted by Discord's own server subscriptions, by Whop, by comps and by
    hand, and a sync that revoked "everyone with the role who is not currently
    paying us through Stripe" would strip the role from every one of those
    people the first time it ran.

    IT IS ALSO TIME-BOUNDED (2026-09-26). Narrow was not narrow enough: a row
    that lapsed months ago stayed in the revoke list permanently, so a member
    who had once paid through Stripe and now pays through Whop had the role
    taken back every three minutes, and re-granting it by hand did nothing.
    Revocation now only applies while the lapse is recent — see
    _REVOKE_GRACE_DAYS. After that the row is inert and a human's grant is
    final.
    """
    if not _READY or Subscription is None:
        return {"grant": [], "revoke": []}
    try:
        now = datetime.now(timezone.utc)
        grant, lapsed = set(), set()
        with _session() as s:
            for r in s.query(Subscription).all():
                did = (r.discord_id or "").strip()
                if not did:
                    continue
                end = r.current_period_end
                if end is not None and end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                # Must match billing.ACTIVE_STATUSES. past_due is NOT
                # entitled: a failed payment removes the role on the next sync.
                live = ((r.status or "") in ("active", "trialing")
                        and (end is None or end > now))
                if live:
                    grant.add(did)
                    continue
                # ── A LAPSE IS AN EVENT, NOT A PERMANENT STATE ───────────────
                # Only revoke while the lapse is RECENT. A row whose period
                # ended months ago keeps reappearing in every cycle forever,
                # and the role it is fighting over is not ours alone: Stripe,
                # Whop and hand-granted comps all apply the same Discord role,
                # and the bot can only see Stripe. So an old dead row was
                # stripping the role from people who are currently paying
                # through Whop, and undoing hand-grants within three minutes —
                # which is what "I keep giving him the role and the bot keeps
                # taking it" was.
                #
                # Enforcing a lapse for a bounded window keeps the real job
                # (a Stripe subscriber who stops paying loses access promptly)
                # while making a human's decision final afterwards. It is also
                # the conservative direction: the failure mode is someone
                # keeping access slightly too long, not a paying customer being
                # locked out.
                #
                # end is None means an open-ended row with a dead status; there
                # is no lapse moment to measure, so it is not enforced here.
                if end is None:
                    continue
                # 0 (or less) means never revoke — a grant-only sync.
                if _REVOKE_GRACE_DAYS <= 0:
                    continue
                if (now - end).days > _REVOKE_GRACE_DAYS:
                    continue
                lapsed.add(did)
        # Someone who resubscribed has both an old dead row and a live one.
        # Active always wins, so they are never revoked on the strength of a
        # superseded record.
        return {"grant": sorted(grant), "revoke": sorted(lapsed - grant)}
    except Exception:
        logger.exception("subscription_role_sets failed")
    return {"grant": [], "revoke": []}


def link_subscription(stripe_sub_id: str, discord_id: str = "",
                      email: str = "") -> bool:
    """Attach a Discord id or app email to an existing subscription — someone
    who paid before connecting either one."""
    if not _READY or Subscription is None or not stripe_sub_id:
        return False
    try:
        with _session() as s:
            row = (s.query(Subscription)
                    .filter(Subscription.stripe_sub_id == stripe_sub_id)
                    .one_or_none())
            if row is None:
                return False
            if discord_id:
                row.discord_id = discord_id
            if email:
                row.app_email = email
            row.updated_at = datetime.now(timezone.utc)
            return True
    except Exception:
        logger.exception("link_subscription failed")
    return False


def active_subscriber_discord_ids() -> list:
    """Discord ids entitled right now — what the bot syncs roles against."""
    if not _READY or Subscription is None:
        return []
    try:
        now = datetime.now(timezone.utc)
        out = []
        with _session() as s:
            for r in s.query(Subscription).all():
                if not r.discord_id:
                    continue
                if (r.status or "") not in ("active", "trialing"):
                    continue
                end = r.current_period_end
                if end is not None:
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                    if end <= now:
                        continue
                out.append(r.discord_id)
        return sorted(set(out))
    except Exception:
        logger.exception("active_subscriber_discord_ids failed")
    return []


def cache_get(key: str):
    """Value for ``key``, or None on miss/expiry/DB-unavailable. TTL is enforced
    HERE on read: an expired row is a miss and the caller refetches, so a stale
    value can never be served just because it survived a restart."""
    if not _READY:
        return None
    try:
        with _session() as s:
            row = s.get(CacheEntry, key)
            if row is None:
                return None
            if row.ttl_seconds:
                age = (datetime.now(timezone.utc) - row.written_at).total_seconds()
                if age > row.ttl_seconds:
                    return None          # expired -> treat as a miss
            import json as _json
            return _json.loads(row.value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_get(%s) failed — treating as miss: %s", key, str(exc)[:120])
        return None


def cache_set(key: str, value, ttl_seconds: int = None) -> bool:
    """Write-through upsert. ttl_seconds=None means NEVER expires — correct for
    immutable data (a completed match's statistics cannot change).

    NOTE FOR CALLERS: this does not know whether ``value`` is trustworthy. The
    degraded-fetch guard must run BEFORE calling this — a degraded fetch must
    never overwrite a healthy row, exactly as it must never overwrite a healthy
    in-memory entry."""
    if not _READY:
        return False
    try:
        import json as _json
        payload = _json.dumps(value)
        with _session() as s:
            row = s.get(CacheEntry, key)
            if row is None:
                s.add(CacheEntry(cache_key=key, value=payload,
                                 ttl_seconds=ttl_seconds,
                                 written_at=datetime.now(timezone.utc)))
            else:
                row.value = payload
                row.ttl_seconds = ttl_seconds
                row.written_at = datetime.now(timezone.utc)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_set(%s) failed — memory-only this run: %s", key, str(exc)[:120])
        return False


def cache_stats() -> dict:
    """Row count + oldest/newest write — for verifying the layer is actually
    persisting rather than silently no-opping."""
    if not _READY:
        return {"ready": False, "rows": 0}
    try:
        with _session() as s:
            n = s.query(CacheEntry).count()
            return {"ready": True, "rows": n}
    except Exception:  # noqa: BLE001
        return {"ready": False, "rows": 0}


def _avg(vals: list):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def _summarize(picks: list) -> dict:
    """Aggregate a set of pick rows (most-recent-first) into a record block."""
    wins = [p for p in picks if p["result"] == "W"]
    losses = [p for p in picks if p["result"] == "L"]
    pushes = [p for p in picks if p["result"] == "PUSH"]
    voids = [p for p in picks if p["result"] == "VOID"]
    # PUSH counts as a WIN (policy) — folded into the win-rate numerator AND the
    # denominator. VOID (cancelled / DNP) never played, so it stays out of both.
    # Denominator = W + L + PUSH.
    decided = wins + losses + pushes
    win_rate = round((len(wins) + len(pushes)) / len(decided) * 100, 1) if decided else 0.0

    # Current streak: walk decided picks newest→oldest, count consecutive sames.
    streak_type, streak_len = None, 0
    for p in picks:
        if p["result"] not in ("W", "L"):
            continue
        if streak_type is None:
            streak_type, streak_len = p["result"], 1
        elif p["result"] == streak_type:
            streak_len += 1
        else:
            break

    return {
        "picks": picks,
        "total": len(picks),
        "wins": len(wins),
        "losses": len(losses),
        "pushes": len(pushes),
        "voids": len(voids),
        "pending": len([p for p in picks if p["result"] == "PENDING"]),
        "needs_review": len([p for p in picks if p["result"] == "NEEDS REVIEW"]),
        "win_rate": win_rate,
        "avg_confidence_wins": _avg([p["confidence"] for p in wins]),
        "avg_confidence_losses": _avg([p["confidence"] for p in losses]),
        "streak_type": streak_type,
        "streak_len": streak_len,
    }


def _slip_record(threex_picks: list) -> dict:
    """Grade the 3x SLIP record from its individual legs. Both legs of a day's
    slip are logged together, so we group by generated_at date (one slip per
    day) and grade the pair:

      • both legs W        -> slip W
      • any leg L          -> slip L
      • a leg PUSHes       -> it drops out; the slip reduces to the remaining
                              leg(s) and is graded on those alone
      • all legs PUSH      -> slip PUSH
      • any leg unresolved -> slip still pending (not counted)
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for p in threex_picks:
        day = (p.get("generated_at") or "")[:10]
        groups[day].append(p)

    w = l = push = pending = 0
    for _day, legs in groups.items():
        results = [p["result"] for p in legs]
        if any(r in ("PENDING", "NEEDS REVIEW") for r in results):
            pending += 1
            continue
        graded = [r for r in results if r in ("W", "L")]  # PUSH / VOID legs drop out
        if not graded:
            push += 1                     # every leg pushed
        elif any(r == "L" for r in graded):
            l += 1                        # both legs must hit — one miss = loss
        else:
            w += 1
    # PUSH counts as a WIN (policy): an all-push slip is a win, and pushes sit in
    # both the numerator and the denominator. Raw w / l / push counts are kept for
    # display; only the rate folds pushes in.
    decided = w + l + push
    return {
        "slips": len(groups),
        "wins": w,
        "losses": l,
        "pushes": push,
        "pending": pending,
        "win_rate": round((w + push) / decided * 100, 1) if decided else 0.0,
    }


# A pick's SOURCE is the book its board came from, derived from pick_group.
# Everything that is not explicitly another book is PrizePicks, so legacy rows
# (potd / second-wave / 3x / NULL) keep counting exactly where they always have.
# Underdog groups are namespaced with an "underdog" prefix ("underdog",
# "underdog-wave", "underdog-3x", ...) so a new Underdog product needs no change
# here. Sources are scored SEPARATELY: mixing two books' hit rates into one
# number would make both meaningless.
SOURCE_PREFIXES = {"underdog": "underdog"}


def pick_source(p: dict) -> str:
    g = (p.get("pick_group") or "potd").lower()
    for prefix, src in SOURCE_PREFIXES.items():
        if g.startswith(prefix):
            return src
    return "prizepicks"


# ── PICK OF THE DAY, BY MONTH ───────────────────────────────────────────────
# The headline record counted every play the board posted — five to eighteen a
# day. That is a board record, not a Pick of the Day record, and the two answer
# different questions: "how did everything you put out do" against "how did the
# one you led with do". The second is what the ⭐ promises, so it is what the
# track record now reports.
#
# SCOPED TO THE CALENDAR MONTH IN ET, because that is the window the app's
# picks tab and the bot's recap already speak in, and an all-time POTD figure
# would be a different number again.
#
# Rows posted before the is_potd column existed carry NULL, not 0: we do not
# know which of them was starred, so they are outside this record rather than
# counted as board picks. That makes the figure start at the column, which is
# honest — and see the note in the Pick model.
_ET_ZONE = "America/New_York"


def _slate_date_of(p: dict) -> str:
    """The ET date a pick's match is PLAYED, from when the board was built.

    THE SAME NOON RULE THE BOT USES (bot._slate_date_of): a board generated from
    noon ET onward is building TOMORROW's card, anything earlier is today's.

    This is not a detail. Boards post around 22:00 ET, so generated_at is always
    the EVENING BEFORE the slate — measured against the ⭐ posts recovered from
    the channel, the raw ET date of generated_at matched the slate 0 times out
    of 11, always landing a day early. Grouping months on it therefore pushed
    the pick that PLAYED on September 1st into August.

    resolved_at is closer but not reliable either: a late grade (Sloane
    Stephens, 9/15 slate) resolves on the 16th, so it matched 9 of 11.
    """
    import datetime as _dt
    raw = p.get("generated_at")
    if not raw:
        return ""
    try:
        if isinstance(raw, str):
            raw = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=_dt.timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            raw = raw.astimezone(ZoneInfo(_ET_ZONE))
        except Exception:  # noqa: BLE001 — UTC is close enough to keep this alive
            pass
        day = raw.date()
        if raw.hour >= 12:
            day += _dt.timedelta(days=1)
        return day.isoformat()
    except Exception:  # noqa: BLE001
        return ""


def _et_month_of(p: dict) -> str:
    """The ET YYYY-MM a pick belongs to, by the date it PLAYS."""
    return _slate_date_of(p)[:7]


def et_month_now() -> str:
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        now = now.astimezone(ZoneInfo(_ET_ZONE))
    except Exception:  # noqa: BLE001
        pass
    return now.strftime("%Y-%m")


def potd_month_record(month: str = None, picks: list = None) -> dict:
    """W/L for the STARRED pick only, within one ET calendar month.

    ``tracked`` is how many starred picks the month has graded. It is published
    alongside the rate on purpose: the star only goes on a play that clears the
    80 bar, some days have none, and a rate over three picks should not be read
    like a rate over sixty.
    """
    month = month or et_month_now()
    rows = picks if picks is not None else all_picks()
    try:
        starred = [p for p in rows
                   if p.get("is_potd")
                   and not p.get("excluded_from_record")
                   and pick_source(p) == "prizepicks"
                   and _et_month_of(p) == month]
        decided = [p for p in starred if p.get("result") in ("W", "L", "PUSH")]
        # PUSH counts as a win — the same policy the rest of the record uses.
        wins = sum(1 for p in decided if p.get("result") in ("W", "PUSH"))
        losses = len(decided) - wins
        return {"month": month, "wins": wins, "losses": losses,
                "tracked": len(decided),
                "pending": sum(1 for p in starred
                               if p.get("result") not in ("W", "L", "PUSH", "VOID")),
                "win_rate": round(wins / len(decided) * 100, 1) if decided else None}
    except Exception as exc:  # noqa: BLE001 — the record must still render
        logger.warning("potd_month_record failed: %s", exc)
        return {"month": month, "wins": 0, "losses": 0, "tracked": 0,
                "pending": 0, "win_rate": None}


# ── PER-PROP PROJECTION BIAS ─────────────────────────────────────
# Measured against WHAT ACTUALLY HAPPENED, not against the book's line. Fitting a
# projection to the line would just be copying the market; fitting it to the
# realised stat corrects an error. Over the graded history the props ran:
#
#     Aces -1.70 (projects HIGH)   Player Total Games Won +1.92 (projects LOW)
#     Total Games +1.90            Double Faults +1.31
#     Break Points Won -0.27       Fantasy Score -0.28
#
# Aces over-projecting by 1.7 on a mean line of 7.3 is why it was the worst prop
# on the board before it was retired. Rolling rather than frozen so it tracks the
# model instead of a snapshot, and clamped because a small window on a thin prop
# can produce a correction larger than the thing being corrected.
# CLAMP RAISED 1.5 -> 2.5 (2026-09-24). The clamp, not the window, was the
# binding constraint: THREE of six props sat pinned against 1.5 (Aces -1.500,
# PTGW +1.500, Double Faults +1.434), so the correction was being throttled
# below what the realised errors were asking for.
#
# Walk-forward backtest, corrections computed only from picks already RESOLVED
# at that moment (no lookahead), on the props whose LEAN comes from
# sign(projection − line) — Aces / Total Games / Double Faults, n=237:
#
#   clamp 1.5 (old)   119-111   51.7%
#   clamp 2.5         127-108   54.0%     <- and per prop:
#   clamp 4.0         123-112   52.3%        Aces          42.9% -> 57.1%
#                                            Double Faults 54.5% -> 57.4%
#                                            Total Games   47.4% -> 49.5%
#
# Two reasons to trust it beyond the headline number. The WINDOW is nearly
# irrelevant — 45d, 60d, 90d and 180d all return exactly 54.0% at clamp 2.5 —
# so this is not a window fit. And clamp 4.0 is WORSE than 2.5, so it is not
# "bigger is better" either; 2.5 is an interior optimum.
#
# SCOPE. The backtest can only cover the three props whose side comes from the
# projection. Break Points Won, Fantasy Score and PTGW take their side from a
# scenario-mixture P(over), so a larger correction moves their projection, edge
# and board RANK but not their lean — untested there, which is the main risk in
# this change. PROP_BIAS_CLAMP reverts it without a deploy.
_BIAS_CLAMP_DEFAULT = float(os.getenv("PROP_BIAS_CLAMP", "2.5") or 2.5)


# Props whose stored model_projection is a FAIR LINE — the 50/50 point of a
# scenario mixture — rather than an expected value. Mirrors _FAIR_LINE_PROPS in
# discord-bot/bot.py, which records the same boundary per pick.
#
# THESE MUST NOT GET A BIAS CORRECTION, and it is a definitional problem rather
# than a tuning one. prop_bias measures mean(actual − projection). When the
# projection is a MEDIAN, that quantity is not "how far the model is off" — it
# is the model's error PLUS the mean-to-median gap of a skewed count
# distribution, and the two cannot be separated after the fact. For a
# right-skewed count the gap alone is positive even from a perfectly calibrated
# model, so the number is uninterpretable and correcting by it shifts a median
# by a mean-derived constant, which is not a defined operation.
#
# This is the exact error the _FAIR_LINE_PROPS comment in bot.py warns about:
# "comparing a mean against a median and will report noise as a broken model."
# It was being made here. BP's correction was small (-0.300, well inside both
# clamps and untouched by the clamp change), so nothing in the record was made
# worse by it — but it was never a meaningful number.
#
# Omitting the prop yields zero correction, which prop_bias already treats as
# the honest default for "we cannot estimate this". Break Points Won's lean and
# confidence come from the mixture P(over) and are computed BEFORE the
# correction is applied, so dropping it moves the displayed projection and the
# edge, never the side.
#
# To correct BP properly you would need the mixture MEAN (stored per pick as
# bp_mixture_mean in model_inputs), not the fair line — and then a correction
# applied to the mean, not to the fair line. That is a real piece of work, not
# a constant, and it is deliberately not attempted here.
FAIR_LINE_PROPS = {"Break Points Won", "Fantasy Score"}

# Props whose projection is CONSTRAINED BY AN IDENTITY, so a per-player additive
# constant is not a correction — it is a violation.
#
# Player Total Games Won renormalises onto the match total by construction:
#     projection = own_mean * (games_combined / (own_mean + opp_mean))
# which guarantees player + opponent == combined games. Adding the same bias to
# BOTH players breaks that by TWICE the constant, and the two sides of one match
# stop summing to the match.
#
# Measured live on Volynets/Birrell (2026-09-24), the case the operator caught:
#     renormalised   10.55 + 8.55 = 19.10  == games_combined 19.1   identity OK
#     +2.447 each    13.00 + 11.00 = 24.00 vs 19.1                  broken by 4.9
# and it is the same constant that pushed Birrell to a projection ABOVE her line
# while her lean (from the mixture P(over)) stayed UNDER. Removing it fixes the
# summing violation and the projection/lean contradiction together.
#
# This was made WORSE by raising PROP_BIAS_CLAMP 1.5 -> 2.5 earlier the same day:
# PTGW's correction went +1.500 -> +2.447, so the violation grew from 3.0 to 4.9.
#
# If PTGW genuinely runs low, the error is in the match LENGTH, not in the split —
# and Total Games carries its own correction, which PTGW inherits coherently
# through games_combined. Correcting the share instead of the total cannot be
# right, because the share is not free to move.
IDENTITY_CONSTRAINED_PROPS = {"Player Total Games Won"}

# What remains correctable: Aces, Total Games, Double Faults — exactly the props
# whose projection is an unconstrained MEAN and whose lean is sign(projection −
# line). That is also exactly the set the clamp walk-forward backtest covered
# (n=237), so the 2.5 clamp evidence now applies to precisely the props it is
# applied to, rather than spilling onto three it could not test.
NO_BIAS_PROPS = FAIR_LINE_PROPS | IDENTITY_CONSTRAINED_PROPS


def prop_bias(days: int = 90, min_n: int = 25, clamp: float = None) -> dict:
    """{prop_type: mean(actual - projection)} over a trailing window.

    A prop with fewer than ``min_n`` graded rows in the window is OMITTED rather
    than returned with a noisy estimate — callers treat a missing prop as zero
    correction, which is the honest default. Fair-line props are omitted for a
    stronger reason: the quantity is undefined for them. See FAIR_LINE_PROPS.
    """
    if clamp is None:
        clamp = _BIAS_CLAMP_DEFAULT
    import datetime as _dt
    out = {}
    try:
        cut = (_dt.datetime.now(_dt.timezone.utc)
               - _dt.timedelta(days=days)).isoformat()
        buckets = {}
        for p in all_picks():
            if p.get("excluded_from_record"):
                continue
            if p.get("result") not in ("W", "L", "PUSH"):
                continue
            # A fair line is a median, so actual − median is not a bias; and an
            # identity-constrained projection cannot take a per-player constant
            # at all. Skipped at collection so the prop never reaches min_n and
            # is therefore omitted entirely = zero correction.
            if (p.get("prop_type") or "") in NO_BIAS_PROPS:
                continue
            proj, act = p.get("model_projection"), p.get("result_value")
            if not isinstance(proj, (int, float)) or not isinstance(act, (int, float)):
                continue
            if str(p.get("resolved_at") or "") < cut:
                continue
            buckets.setdefault(p.get("prop_type") or "?", []).append(act - proj)
        for prop, errs in buckets.items():
            if len(errs) < min_n:
                continue
            b = sum(errs) / len(errs)
            out[prop] = round(max(-clamp, min(clamp, b)), 3)
    except Exception as exc:  # noqa: BLE001 — a projection must never fail on this
        logger.warning("prop_bias failed: %s", exc)
        return {}
    return out


def record_summary() -> dict:
    """Aggregate record, split by SOURCE then by pick group.

    Top-level fields describe the PrizePicks Pick of the Day (the headline
    product; legacy NULL-group rows count here); ``threex_legs`` is the
    individual-leg record and ``threex_slips`` the paired slip record for the 3x.

    ``underdog`` carries the Underdog board's own record in the same shape as the
    top level (picks / wins / losses / win_rate / ...). It is deliberately NOT
    folded into the headline numbers — a second book has its own lines, its own
    market, and must earn its own track record before it can be quoted alongside
    the first."""
    # EXCLUDE superseded / duplicate records (excluded_from_record=1) from every
    # record computation and from the recap's pick list. They remain in the DB
    # (all_picks) for the audit, just invisible to the public record.
    picks = [p for p in all_picks() if not p.get("excluded_from_record")]

    def _grp(p):
        return (p.get("pick_group") or "potd").lower()

    pp = [p for p in picks if pick_source(p) == "prizepicks"]
    ud = [p for p in picks if pick_source(p) == "underdog"]

    potd = [p for p in pp if _grp(p) != "3x"]
    threex = [p for p in pp if _grp(p) == "3x"]

    summary = _summarize(potd)
    summary["threex_legs"] = _summarize(threex)
    summary["threex_slips"] = _slip_record(threex)
    # Underdog, scored on its own. Same shape as the top level so any caller that
    # can render the PrizePicks record can render this one unchanged.
    summary["underdog"] = _summarize([p for p in ud if _grp(p) != "underdog-3x"])
    summary["underdog"]["threex_legs"] = _summarize(
        [p for p in ud if _grp(p) == "underdog-3x"])

    # Standard vs demon segmentation (weekly report line). Compact — counts and
    # win rate only, not the full pick lists, so the payload stays small.
    def _seg(rows):
        s = _summarize(rows)
        return {"total": s["total"], "wins": s["wins"], "losses": s["losses"],
                "win_rate": s["win_rate"], "avg_confidence_wins": s["avg_confidence_wins"]}
    # Scoped to PrizePicks: "demon" is a PrizePicks concept and Underdog carries
    # no equivalent, so mixing its rows in would quietly dilute the standard bucket.
    summary["by_odds_type"] = {
        "standard": _seg([p for p in pp if (p.get("odds_type") or "standard") == "standard"]),
        "demon":    _seg([p for p in pp if (p.get("odds_type") or "standard") == "demon"]),
    }
    # The headline the bot and the site now lead with — see potd_month_record.
    summary["potd_month"] = potd_month_record(picks=picks)
    return summary


# ── PUBLIC HEADLINE RECORD ──────────────────────────────────────────────────
# Props PULLED FROM THE BOARD after measuring badly. Mirrors the exclusions in
# discord-bot/pick_of_day.py::_POD_EXCLUDE_PROPS — the bot decides what gets
# posted, this only reports it, and if the two ever drift the headline would be
# describing a board that no longer exists.
RETIRED_PROPS = {"Aces", "Total Games"}


def public_summary(days: int = 30) -> dict:
    """The few figures the public landing page needs, and nothing else.

    The page was computing these from /api/results/record, which ships the FULL
    pick log — 620KB to arrive at four numbers, larger than the app's whole JS
    bundle, on the one screen a first-time visitor judges the product by.

    BOTH THE FILTERED AND THE UNFILTERED RECORD are returned, always. The
    headline covers the props still being posted, which is the honest answer to
    "what am I buying"; quoting only that without the all-time figure beside it
    would be selecting a number rather than reporting one.
    """
    import datetime as _dt

    def tally(rows):
        w = sum(1 for p in rows if p.get("result") == "W")
        n = sum(1 for p in rows if p.get("result") in ("W", "L"))
        return {"wins": w, "losses": n - w, "total": n,
                "win_rate": round(w / n * 100, 1) if n else None}

    try:
        # SAME POPULATION THE IN-APP RECORD HEADLINES — PrizePicks, excluding
        # the 3x legs. record_summary() keeps Underdog on its own track for a
        # stated reason ("a second book has its own lines, its own market, and
        # must earn its own track record"), and folding it in here would put a
        # different number on the landing page than the app shows. A visitor who
        # compares the two and finds they disagree has learned something worse
        # than either figure.
        graded = [p for p in all_picks()
                  if not p.get("excluded_from_record")
                  and p.get("result") in ("W", "L")
                  and pick_source(p) == "prizepicks"
                  and (p.get("pick_group") or "potd").lower() != "3x"]
        live = [p for p in graded if p.get("prop_type") not in RETIRED_PROPS]
        cut = (_dt.datetime.now(_dt.timezone.utc)
               - _dt.timedelta(days=days)).date().isoformat()
        recent = [p for p in live
                  if str(p.get("resolved_at") or "")[:10] >= cut]
        days_active = len({str(p.get("resolved_at"))[:10] for p in graded
                           if p.get("resolved_at")})
        nfl = [p for p in nfl_picks() if p.get("result") in ("W", "L")]
        return {"all_time": tally(graded), "live_props": tally(live),
                "recent": tally(recent), "recent_days": days,
                "days_active": days_active, "nfl": tally(nfl),
                # The headline. See potd_month_record: the starred play only,
                # for the current ET month.
                "potd_month": potd_month_record(),
                "retired_props": sorted(RETIRED_PROPS), "ready": True}
    except Exception as exc:  # noqa: BLE001 — a marketing page must still render
        logger.warning("public_summary failed: %s", exc)
        return {"ready": False}


# ── Anonymous preview window ────────────────────────────────────────────────
def preview_reset(visitor_key: str) -> bool:
    """Clear one visitor's preview clock. Support use: someone who genuinely lost
    their window to a shared office IP has no other way back."""
    if not _READY or PreviewSession is None:
        return False
    try:
        with _session() as s:
            row = (s.query(PreviewSession)
                     .filter(PreviewSession.visitor_key == visitor_key).one_or_none())
            if row is None:
                return False
            s.delete(row)
            return True
    except Exception:  # noqa: BLE001
        logger.exception("preview_reset failed")
        return False


def preview_touch(visitor_key: str, window_seconds: int,
                  reset_hours: float = 0.0) -> dict:
    """Start or read an anonymous visitor's free-look clock.

    THE CLOCK STARTS ON FIRST CONTACT AND NEVER RESTARTS. first_seen is written
    once and only read afterwards, so a refresh, a new tab, a private window and
    a cleared browser all land on the same row and the same deadline. That is the
    entire point: a browser-held timer resets in all four cases.

    Returns remaining seconds and whether the window is still open. Fails OPEN
    (grants the window) when the DB is unavailable — a database outage should
    not lock every visitor out of the marketing preview.
    """
    from datetime import datetime, timezone
    if not _READY or PreviewSession is None:
        return {"ok": False, "allowed": True, "remaining": window_seconds,
                "reason": "preview store unavailable — failing open"}
    try:
        with _session() as s:
            row = (s.query(PreviewSession)
                     .filter(PreviewSession.visitor_key == visitor_key).one_or_none())
            now = datetime.now(timezone.utc)
            if row is None:
                row = PreviewSession(visitor_key=visitor_key, hits=1)
                s.add(row)
                s.flush()
                started = row.first_seen or now
            else:
                started = row.first_seen or now
                row.hits = (row.hits or 0) + 1
                row.last_seen = now
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (now - started).total_seconds()
            # A LAPSED WINDOW MAY REOPEN. reset_hours=0 means the free look is
            # once ever, which is the strictest reading and also punishes the
            # person who browsed for 40 seconds, got interrupted, and came back
            # next week to a wall. Above 0, the clock restarts once that long has
            # passed since it began.
            if reset_hours > 0 and elapsed > reset_hours * 3600.0:
                row.first_seen = now
                row.hits = 1
                started, elapsed = now, 0.0
            remaining = max(0.0, window_seconds - elapsed)
            return {"ok": True, "allowed": remaining > 0,
                    "remaining": int(remaining), "elapsed": int(elapsed),
                    "hits": row.hits or 1}
    except Exception:  # noqa: BLE001
        logger.exception("preview_touch failed")
        return {"ok": False, "allowed": True, "remaining": window_seconds,
                "reason": "preview lookup failed — failing open"}


def trials_from_ip(ip: str) -> list:
    """Every subscription already started from this address.

    The anti-abuse read: a second free trial from an address that has one is the
    signal, and email alone cannot see it because a new address is free to make.
    """
    if not _READY or Subscription is None or not ip:
        return []
    try:
        with _session() as s:
            rows = (s.query(Subscription)
                      .filter(Subscription.signup_ip == ip)
                      .order_by(Subscription.created_at.desc()).all())
            return [{"stripe_sub_id": r.stripe_sub_id, "status": r.status,
                     "app_email": r.app_email, "discord_id": r.discord_id,
                     "created_at": r.created_at.isoformat() if r.created_at else None}
                    for r in rows]
    except Exception:  # noqa: BLE001
        logger.exception("trials_from_ip failed")
        return []


def prior_trial(email: str = "", ip: str = "", device: str = "") -> dict:
    """Has this person already started a subscription here before?

    {"used": bool, "matched": [...], "count": n} — never raises, and returns
    used=False on any failure, because a database blip must not turn a genuine
    new customer away from the trial.

    ANY ONE SIGNAL IS ENOUGH, deliberately. Requiring email AND ip AND device to
    all match would be trivial to walk around: change the email, which costs
    nothing, and the other two never get consulted. Each of the three is weak on
    its own for a different reason — a new address is free to make, a household
    IP is shared by everyone in it, and a device hash changes with a browser
    update — so the gate reads them as alternatives rather than as a conjunction.

    BLANK VALUES NEVER MATCH. Rows that predate signup_ip/signup_device carry
    empty strings, and an empty-matches-empty rule would treat every one of them
    as the same person and refuse the trial to everybody.
    """
    if not _READY or Subscription is None:
        return {"used": False, "matched": [], "count": 0}
    em = (email or "").strip().lower()
    ip = (ip or "").strip()
    dv = (device or "").strip()
    if not (em or ip or dv):
        return {"used": False, "matched": [], "count": 0}
    try:
        from sqlalchemy import or_, func
        terms = []
        if em:
            terms.append(func.lower(Subscription.app_email) == em)
        if ip:
            terms.append(Subscription.signup_ip == ip)
        if dv:
            terms.append(Subscription.signup_device == dv)
        with _session() as s:
            rows = (s.query(Subscription).filter(or_(*terms))
                      .order_by(Subscription.created_at.desc()).limit(25).all())
        matched = []
        for r in rows:
            why = []
            if em and (r.app_email or "").strip().lower() == em:
                why.append("email")
            if ip and (r.signup_ip or "").strip() == ip:
                why.append("ip")
            if dv and (r.signup_device or "").strip() == dv:
                why.append("device")
            if not why:
                continue
            matched.append({"on": why, "status": r.status,
                            "created_at": (r.created_at.isoformat()
                                           if r.created_at else None)})
        return {"used": bool(matched), "matched": matched, "count": len(matched)}
    except Exception:  # noqa: BLE001 — never block a sale on a failed read
        logger.exception("prior_trial lookup failed")
        return {"used": False, "matched": [], "count": 0}


def set_signup_device(stripe_sub_id: str, device: str) -> bool:
    """Stamp the signup device key onto a subscription once it exists.

    The device twin of set_signup_ip, and written once for the same reason: the
    FIRST device is the one that answers "where was this trial started from".
    Overwriting it on a later event would let someone launder a repeat trial by
    finishing it from a different browser.
    """
    if not _READY or Subscription is None or not (stripe_sub_id and device):
        return False
    try:
        with _session() as s:
            row = (s.query(Subscription)
                     .filter(Subscription.stripe_sub_id == stripe_sub_id).one_or_none())
            if row is None:
                return False
            if not (row.signup_device or ""):
                row.signup_device = device[:64]
            return True
    except Exception:  # noqa: BLE001
        logger.exception("set_signup_device failed")
        return False


def set_signup_ip(stripe_sub_id: str, ip: str) -> bool:
    """Stamp the signup IP onto a subscription once it exists.

    Separate from upsert_subscription because the IP is known at CHECKOUT time
    (the browser is talking to us) while the subscription id only exists after
    Stripe's webhook — the two facts arrive from different directions.
    """
    if not _READY or Subscription is None or not (stripe_sub_id and ip):
        return False
    try:
        with _session() as s:
            row = (s.query(Subscription)
                     .filter(Subscription.stripe_sub_id == stripe_sub_id).one_or_none())
            if row is None:
                return False
            if not (row.signup_ip or ""):
                row.signup_ip = ip[:64]
            return True
    except Exception:  # noqa: BLE001
        logger.exception("set_signup_ip failed")
        return False


# ── NFL ──────────────────────────────────────────────────────────────────────
# Same shape as the tennis helpers above, against nfl_picks. Kept as separate
# functions rather than a `sport=` argument on the tennis ones: a shared code
# path is exactly how a filter gets forgotten and an NFL row lands in the public
# tennis record.
def nfl_log_picks(rows: list) -> int:
    """Insert NFL picks as PENDING. Dedupes on (book, slate_date, player, prop).

    Returns rows written. A retry or a re-post must never double-count a play —
    the easiest way there is to make a win rate look better than it was.
    """
    if not is_ready() or not rows:
        return 0
    try:
        with _session() as s:
            n = 0
            for rec in rows:
                key = (rec.get("book") or "prizepicks", rec.get("slate_date"),
                       rec.get("player"), rec.get("prop_type"))
                if not key[2] or not key[1]:
                    continue
                dupe = s.query(NflPick).filter(
                    NflPick.book == key[0], NflPick.slate_date == key[1],
                    NflPick.player == key[2], NflPick.prop_type == key[3]).first()
                if dupe:
                    continue
                s.add(NflPick(**{k: v for k, v in rec.items()
                                 if k in NflPick.__table__.columns.keys()}))
                n += 1
            s.commit()
            return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("nfl_log_picks failed: %s", exc)
        return 0


def nfl_pending() -> list:
    if not is_ready():
        return []
    try:
        with _session() as s:
            rows = s.query(NflPick).filter(NflPick.result == "PENDING").order_by(
                NflPick.id).all()
            return [_nfl_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("nfl_pending failed: %s", exc)
        return []


def nfl_update_result(pick_id: int, result: str, value=None) -> bool:
    if not is_ready():
        return False
    try:
        with _session() as s:
            row = s.get(NflPick, int(pick_id))
            if row is None:
                return False
            row.result = result
            row.result_value = value
            row.resolved_at = func.now()
            s.commit()
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("nfl_update_result failed: %s", exc)
        return False


def nfl_picks(slate_date: str = None, book: str = None,
              since_days: int = None) -> list:
    if not is_ready():
        return []
    try:
        import datetime
        with _session() as s:
            q = s.query(NflPick).filter(NflPick.excluded_from_record == 0)
            if slate_date:
                q = q.filter(NflPick.slate_date == slate_date)
            if book:
                q = q.filter(NflPick.book == book)
            if since_days:
                cut = (datetime.date.today()
                       - datetime.timedelta(days=int(since_days))).isoformat()
                q = q.filter(NflPick.slate_date >= cut)
            return [_nfl_dict(r) for r in
                    q.order_by(NflPick.slate_date, NflPick.id).all()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("nfl_picks failed: %s", exc)
        return []


def _nfl_dict(r) -> dict:
    out = {}
    for c in r.__table__.columns:
        v = getattr(r, c.name)
        out[c.name] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


# ── NBA ──────────────────────────────────────────────────────────────────────
# Deliberately a parallel set rather than a generic sport-parameterised one. A
# shared helper taking a model argument reads cleaner and is exactly how a
# caller ends up passing the wrong table: the whole point of separate tables is
# that no single query can touch two sports, and that guarantee is worth more
# than the duplication it costs.

def nba_log_picks(rows: list) -> int:
    """Insert NBA picks as PENDING. Dedupes on (book, slate_date, player, prop).

    Returns rows written. A retry or a re-post must never double-count a play —
    the easiest way there is to make a win rate look better than it was.
    """
    if not is_ready() or not rows:
        return 0
    try:
        with _session() as s:
            n = 0
            for rec in rows:
                key = (rec.get("book") or "prizepicks", rec.get("slate_date"),
                       rec.get("player"), rec.get("prop_type"))
                if not key[2] or not key[1]:
                    continue
                dupe = s.query(NbaPick).filter(
                    NbaPick.book == key[0], NbaPick.slate_date == key[1],
                    NbaPick.player == key[2], NbaPick.prop_type == key[3]).first()
                if dupe:
                    continue
                s.add(NbaPick(**{k: v for k, v in rec.items()
                                 if k in NbaPick.__table__.columns.keys()}))
                n += 1
            s.commit()
            return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("nba_log_picks failed: %s", exc)
        return 0


def nba_pending() -> list:
    if not is_ready():
        return []
    try:
        with _session() as s:
            rows = s.query(NbaPick).filter(NbaPick.result == "PENDING").order_by(
                NbaPick.id).all()
            return [_nfl_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("nba_pending failed: %s", exc)
        return []


def nba_update_result(pick_id: int, result: str, value=None) -> bool:
    if not is_ready():
        return False
    try:
        with _session() as s:
            row = s.get(NbaPick, int(pick_id))
            if row is None:
                return False
            row.result = result
            row.result_value = value
            row.resolved_at = func.now()
            s.commit()
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("nba_update_result failed: %s", exc)
        return False


def nba_picks(slate_date: str = None, book: str = None,
              since_days: int = None) -> list:
    if not is_ready():
        return []
    try:
        import datetime
        with _session() as s:
            q = s.query(NbaPick).filter(NbaPick.excluded_from_record == 0)
            if slate_date:
                q = q.filter(NbaPick.slate_date == slate_date)
            if book:
                q = q.filter(NbaPick.book == book)
            if since_days:
                cutoff = (datetime.date.today()
                          - datetime.timedelta(days=int(since_days))).isoformat()
                q = q.filter(NbaPick.slate_date >= cutoff)
            return [_nfl_dict(r) for r in q.order_by(NbaPick.id).all()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("nba_picks failed: %s", exc)
        return []


def nba_board_replace(rows: list, book: str, slate_date: str) -> int:
    """REPLACE the stored board for one (book, slate). Returns rows written.

    Replace, not append: this table is a snapshot of the current market, and
    appending would leave yesterday's lines sitting next to today's with no way
    to tell them apart.
    """
    if not is_ready():
        return 0
    try:
        with _session() as s:
            s.query(NbaBoardRow).filter(
                NbaBoardRow.book == book,
                NbaBoardRow.slate_date == slate_date).delete()
            cols = NbaBoardRow.__table__.columns.keys()
            n = 0
            for rec in rows or []:
                if not rec.get("player"):
                    continue
                s.add(NbaBoardRow(**{k: v for k, v in rec.items() if k in cols}))
                n += 1
            s.commit()
            return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("nba_board_replace failed: %s", exc)
        return 0


def nba_board(book: str = None, slate_date: str = None,
              not_before: str = None) -> list:
    """The stored NBA board.

    `not_before` exists for the reason nfl_board documents: replace only
    replaces the slate it is given, so every slate ever scanned stays in the
    table and the default ascending order puts dead games first. On a sport that
    plays nightly that accumulates far faster than it does in the NFL, so a
    caller asking for "the board" without a bound would get months of finished
    games ahead of tonight's.
    """
    if not is_ready():
        return []
    try:
        with _session() as s:
            q = s.query(NbaBoardRow)
            if book:
                q = q.filter(NbaBoardRow.book == book)
            if slate_date:
                q = q.filter(NbaBoardRow.slate_date == slate_date)
            elif not_before:
                q = q.filter(NbaBoardRow.slate_date >= not_before)
            rows = q.order_by(NbaBoardRow.slate_date, NbaBoardRow.id).all()
            return [_nfl_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("nba_board failed: %s", exc)
        return []


# ── ACCOUNT DELETION (App Store guideline 5.1.1(v)) ──────────────────────────
# Apple requires an app that creates accounts to let the user delete one FROM
# INSIDE THE APP. "Email support" is an explicit rejection.
#
# WHAT DELETE MEANS HERE, and the honest limits:
#
#   subscriptions      the identity columns are CLEARED and the row is marked
#                      deleted. The row itself stays, because it is the record
#                      of a real payment: Stripe has it either way, tax and
#                      chargeback handling need it, and destroying it would
#                      break reconciliation without making the person any more
#                      forgotten. discord_id, app_email and signup_ip — the
#                      personal data — are what actually go.
#   preview_sessions   NOT touched, and that is correct rather than an
#                      oversight. It is keyed on a HASHED IP with no account
#                      identity on it at all — no discord id, no email — so
#                      there is nothing there belonging to this person to
#                      delete, and hunting for their row by address would mean
#                      re-identifying someone in the one table built not to.
#
# DELETION DOES NOT CANCEL BILLING, and the caller is told so rather than left
# to discover it on the next invoice. Cancelling silently would be worse: it
# throws away time the person has already paid for.
def delete_account(discord_id: str = "", email: str = "") -> dict:
    """Erase the personal data tied to one identity. Returns what was touched.

    The caller MUST have proven this identity — see the endpoint. Nothing here
    authenticates; it does what it is told.
    """
    out = {"ok": False, "subscriptions": 0,
           "active_subscription": False, "reason": ""}
    if not is_ready():
        out["reason"] = "database unavailable"
        return out
    did, mail = (discord_id or "").strip(), (email or "").strip().lower()
    if not did and not mail:
        out["reason"] = "no identity supplied"
        return out
    try:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        with _session() as s:
            q = s.query(Subscription)
            rows = [r for r in q.all()
                    if (did and (r.discord_id or "") == did)
                    or (mail and (r.app_email or "").lower() == mail)]
            for r in rows:
                # Still inside a paid period? Say so; do not cancel it here.
                if r.current_period_end is not None:
                    end = r.current_period_end
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=datetime.timezone.utc)
                    if end > now and (r.status or "") in ("active", "trialing",
                                                          "past_due"):
                        out["active_subscription"] = True
                r.discord_id = ""
                r.app_email = ""
                r.signup_ip = ""
                r.status = "deleted" if (r.status or "") != "deleted" else r.status
                out["subscriptions"] += 1
            s.commit()
        out["ok"] = True
        logger.warning("account deleted: subs=%d active_sub=%s",
                       out["subscriptions"], out["active_subscription"])
        return out
    except Exception as exc:  # noqa: BLE001
        logger.exception("delete_account failed: %s", exc)
        out["reason"] = str(exc)[:200]
        return out


def nfl_board_replace(rows: list, book: str, slate_date: str) -> int:
    """REPLACE the stored board for one (book, slate). Returns rows written.

    Replace, not append: this table is a snapshot of the current market, and
    appending would leave yesterday's lines sitting next to today's with no way
    to tell them apart.
    """
    if not is_ready():
        return 0
    try:
        with _session() as s:
            s.query(NflBoardRow).filter(NflBoardRow.book == book,
                                        NflBoardRow.slate_date == slate_date).delete()
            cols = NflBoardRow.__table__.columns.keys()
            n = 0
            for rec in rows or []:
                if not rec.get("player"):
                    continue
                s.add(NflBoardRow(**{k: v for k, v in rec.items() if k in cols}))
                n += 1
            s.commit()
            return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("nfl_board_replace failed: %s", exc)
        return 0


def nfl_board(book: str = None, slate_date: str = None,
              not_before: str = None) -> list:
    """The stored NFL board. `slate_date` pins one slate exactly; `not_before`
    drops slates that have already been played.

    WHY not_before EXISTS. nfl_board_replace only replaces the slate it is
    given, so every slate ever scanned stays in this table forever, and the
    default ordering is slate_date ASCENDING — so a caller that asked for "the
    board" got every dead game first and tonight's actual slate last. On 9/14
    that was 175 finished rows in front of 10 live ones, which is what the NFL
    tab looked like: a board full of games that had already been played.

    Slate dates are stored as ISO strings, where lexicographic order IS
    chronological order, so the comparison needs no date parsing.
    """
    if not is_ready():
        return []
    try:
        with _session() as s:
            q = s.query(NflBoardRow)
            if book:
                q = q.filter(NflBoardRow.book == book)
            if slate_date:
                q = q.filter(NflBoardRow.slate_date == slate_date)
            elif not_before:
                q = q.filter(NflBoardRow.slate_date >= not_before)
            rows = q.order_by(NflBoardRow.slate_date, NflBoardRow.id).all()
            return [_nfl_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("nfl_board failed: %s", exc)
        return []


def nfl_players_replace(rows: list, slate_date: str) -> int:
    """REPLACE the published profiles for one slate. Returns rows written."""
    if not is_ready():
        return 0
    try:
        import json as _json
        with _session() as s:
            s.query(NflPlayer).filter(NflPlayer.slate_date == slate_date).delete()
            n = 0
            for rec in rows or []:
                if not rec.get("player"):
                    continue
                s.add(NflPlayer(
                    slate_date=slate_date, player=rec.get("player"),
                    team=rec.get("team") or "", position=rec.get("position") or "",
                    profile=_json.dumps(rec.get("profile") or {}),
                    form=_json.dumps(rec.get("form") or {})))
                n += 1
            s.commit()
            return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("nfl_players_replace failed: %s", exc)
        return 0


def nfl_players(slate_date: str = None, player: str = None) -> list:
    if not is_ready():
        return []
    try:
        import json as _json
        with _session() as s:
            q = s.query(NflPlayer)
            if slate_date:
                q = q.filter(NflPlayer.slate_date == slate_date)
            if player:
                q = q.filter(NflPlayer.player == player)
            out = []
            # Newest slate first. Profiles are snapshots and every slate keeps
            # its own row, so a name query with no slate must answer with the
            # most recent publish rather than whichever row the table hands
            # back first.
            for r in q.order_by(NflPlayer.slate_date.desc(),
                                NflPlayer.player).all():
                d = {"slate_date": r.slate_date, "player": r.player,
                     "team": r.team, "position": r.position}
                for k, raw in (("profile", r.profile), ("form", r.form)):
                    try:
                        d[k] = _json.loads(raw) if raw else {}
                    except Exception:  # noqa: BLE001
                        d[k] = {}
                out.append(d)
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("nfl_players failed: %s", exc)
        return []
