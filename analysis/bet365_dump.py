"""
bet365 tennis odds collector — RUN THIS FROM A REGION BET365 SERVES YOU.

WHY IT LIVES HERE AND NOT IN THE BACKEND: bet365 gates its odds feed on
JURISDICTION, not on bots. From an unlicensed region the app shell renders fine
and the content pane returns "Unable to display this content", which is the same
refusal the SportsBook.API endpoint gives as a 403. Nothing in the code fixes
that -- it has to run where bet365 already serves you.

WHY A HEADED BROWSER: headless Chromium gets a hard Cloudflare block ("Sorry,
you have been blocked", HTTP 403) within one request. Headed gets a clean 200 and
routes correctly. That is the whole difference -- no stealth plugins, no
fingerprint patching, no CAPTCHA solving. If this ever hits a CAPTCHA, stop:
solving it is not part of this tool.

WHAT IT DOES: opens a match, waits for the SPA to hydrate, and writes both the
rendered text and the full DOM next to this file. That output is what a parser
gets built from -- selectors cannot be guessed for markup nobody has seen.

USAGE
    pip install playwright && playwright install chromium
    python analysis/bet365_dump.py                      # uses the default match URL
    python analysis/bet365_dump.py "<bet365 match url>"  # a specific match

Then hand over bet365_rendered.txt (and bet365_dom.html if the text is thin).
"""
from __future__ import annotations

import sys
import pathlib

# The URL Shawn supplied, as a default. Any #/AC/B13/... match route works; an
# event that has already finished renders empty, so use one that is upcoming.
DEFAULT_URL = "https://www.co.bet365.com/#/AC/B13/C21164712/D8/E200142406/F8/"

OUT_DIR = pathlib.Path(__file__).parent
HYDRATE_MS = 15000     # the SPA fills the content pane well after domcontentloaded


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright missing.  pip install playwright && playwright install chromium")
        return 1

    with sync_playwright() as p:
        # headless=False is REQUIRED, see module docstring.
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            locale="en-US",
            viewport={"width": 1600, "height": 1000},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        print(f"opening {url}")
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print(f"  HTTP {resp.status if resp else '?'}")
        page.wait_for_timeout(HYDRATE_MS)

        text = page.inner_text("body")
        dom = page.content()
        low = text.lower()

        # The two diagnostics that tell you whether this run is usable at all.
        if "unable to display" in low:
            print("\n  CONTENT REFUSED — bet365 will not serve odds to this location.")
            print("  The shell rendered but the odds pane did not. Run from a region")
            print("  where bet365 serves you; nothing in this script can change it.")
        if "have been blocked" in low:
            print("\n  CLOUDFLARE BLOCK — this should not happen headed. Do not add")
            print("  stealth plugins to get around it.")

        (OUT_DIR / "bet365_rendered.txt").write_text(text, encoding="utf-8")
        (OUT_DIR / "bet365_dom.html").write_text(dom, encoding="utf-8")
        page.screenshot(path=str(OUT_DIR / "bet365_screen.png"), full_page=True)

        print(f"\n  rendered text : {len(text):>8} chars -> analysis/bet365_rendered.txt")
        print(f"  dom           : {len(dom):>8} chars -> analysis/bet365_dom.html")
        print(f"  screenshot                     -> analysis/bet365_screen.png")
        print("\n  market keywords present:")
        for kw in ("ace", "break point", "total games", "set betting",
                   "game handicap", "to win", "over", "under"):
            print(f"    {kw:14} {kw in low}")

        print("\n  first 1200 chars of what rendered:")
        print("  " + text[:1200].replace("\n", " | "))
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
