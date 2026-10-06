# App Store listing — draft copy

Category: **Sports** (primary). Secondary: none (leave blank; "Utilities" would
mislead). Age rating per `apple-review.md`.

## Name (30 characters max)

1. **Baseline** — first choice.
2. **Baseline EV** — matches the domain, if "Baseline" is taken.
3. **Baseline Props** — says what it is, if both are taken.

Check availability by trying to create the app record in App Store Connect; a
taken name is refused at that step. Bundle id stays `com.baselineev.app`
regardless of the display name.

## Subtitle (30 characters max)

**Player prop projections** (24)

Alternatives: "Sports stats & projections" (26) · "Props, priced by a model" (24)

## Promotional text (170, editable without a release)

Tennis, NFL and NBA player props, priced by a model and graded in public.
Every pick's result is on the record.

## Description (4000 max)

Baseline is a sports statistics and projection tool. It prices player props —
tennis aces, break points and games; NFL receptions and yards; NBA points,
rebounds and assists — with a model, shows its number against the line the
books post, and grades every released pick in public.

PICKS
Baseline's ranked board for each sport, Pick of the Day first. Every pick shows
the player, the side, the line, the prop and the model's confidence — and once
the match or game is done, how it landed.

BOARD
The live player board for tennis, NFL and NBA: every line the books list for
the props Baseline prices, with Baseline's projection beside it. Search, filter
by prop, sort by confidence or edge, tap any row for the full story.

PROJECT
Price any matchup yourself. Pick the players, the prop and the line; get the
projection, the lean, the confidence, and the numbers behind it — serve and
return rates, recent form, the game log with the line drawn through it.

RESEARCH
Look up any player: form, prop history, surface splits, this week's matchup,
game logs. Save the ones you follow.

NOTIFICATIONS
Optional alerts when a board posts and when a day's results are in, per sport.

THE RECORD
All-time, this month, live props — every number on the welcome screen comes
from the same public record the website shows, wins and losses alike.

Baseline is a statistics and projection tool. Projections are for
informational purposes only; nothing in the app is betting advice, and the app
does not take bets or link to any sportsbook. A Baseline membership is required
and is bought on baselineev.com.

If gambling stops being fun, help is free and confidential: 1-800-GAMBLER.

## Keywords (100 characters max, comma-separated, no spaces after commas)

tennis,nfl,nba,props,player props,projections,stats,prizepicks,underdog,sports stats,research

(93 characters.) Do not repeat the app name; Apple indexes it already.

## URLs

- Support: https://baselineev.com/support
- Marketing: https://baselineev.com
- Privacy policy: https://baselineev.com/privacy

## What's New (first version)

First release: Picks, Board, Project and Research for tennis, NFL and NBA;
notifications for boards and recaps; the public record on the welcome screen.

## Screenshots

Required sizes (iPhone only; `supportsTablet` is false):
- 6.9" display: 1320 × 2868 (or 1290 × 2796 for 6.7") — required
- 6.5" display: 1284 × 2778 or 1242 × 2688 — required unless 6.9" covers it (Apple now derives 6.5" from 6.9" uploads)

Marketing frames (build 6, on App Store Connect since 2026-10-06): a two-line
headline (green, white), the real app inside a drawn iPhone with status bar and
Dynamic Island, a two-line tagline and the B mark. The phone always holds an
unretouched capture of the running app with real data; only the frame is drawn.
No review counts, ratings, testimonials or results claims; copy describes what
the app does.

| # | Headline | Screen | Tagline |
|---|---|---|---|
| 1 | Player props / projected. | Board, tilted phone, brand bar and sport chips | — |
| 2 | Every line / priced live | Board (tennis) | The live board / top plays first |
| 3 | Our number / vs. the line | Pick sheet of the board's strongest play | Edge and confidence / on every prop |
| 4 | Every prop / one tap deep | NFL board, a player's props dropped down | NFL lines / all week long |
| 5 | Know / the form | Research player sheet | Last 5, last 10 / prop by prop |
| 6 | Price any / matchup | Project verdict | Pick the players / get the number |

To refresh after a UI change (from `mobile/`):

    npx expo export --platform web --output-dir dist-web
    RAW=1 ONLY=01-board,03-pick-sheet,04-project,05-research,06-nfl-board SERVE_DIR=dist-web PUPPETEER_DIR=<dir with puppeteer> node scripts/screenshots.mjs <reviewer-session-file> http://localhost:5173 store/raw
    PUPPETEER_DIR=<dir with puppeteer> node scripts/store_frames.mjs store/raw/raw store/screenshots
    python scripts/asc_screenshots.py --replace

Copy and frame order live in `scripts/store_frames.mjs` (`FRAMES`); the
Project matchup lives in `scripts/screenshots.mjs` and should be a current one.
