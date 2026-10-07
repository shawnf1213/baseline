# App Review reply — Guideline 2.1 "Information Needed" (2026-10-06, updated 2026-10-07 for build 8)

Apple asked for a screen recording plus written answers on the first submission (new developer account). Part B is the text for App Review Information > Notes (4,000-character limit) and for the reply if a thread is open. The password is the reviewer code in the backend settings (APP_REVIEWER_CODE), never written here; `mobile/.secrets/app-review-reply.txt` holds the filled copy.

```text
=====================================================================
PART A — FOR YOU: the screen recording (item 1). Not part of the reply.
=====================================================================

Before you record
- Install build 8 from TestFlight on your iPhone (latest iOS). Build 8 is
  the one with free-use photos, no team logos and no source labels; the
  recording must match what the reply says.
- Make sure the app is signed OUT so the recording starts on the Welcome
  screen: open the app, tap the round button top right, tap Sign out.
- Device region must be United States (it is). Turn on Do Not Disturb.
- Control Center > Screen Recording, then open Baseline.

Shot list (about 2-3 minutes)
 1. Welcome screen: pause on "What Baseline does" and "The record";
    scroll so the Subscribe card is visible. Do not tap it yet.
 2. Tap "Already a member? Sign in" > "Sign in with email" > type
    chxpvro@gmail.com > "Email me a code". A real 6-digit code now lands in
    that inbox within seconds; if the Mail app is on the same phone, let the
    notification show, then type the code and tap "Sign in". (The password
    below also works in the code box if the inbox is not at hand.)
 3. Board: scroll; tap NFL, then NBA, then Tennis at the top; tap a player
    card marked "2 props" so it drops down; tap one prop to open its
    sheet; scroll the sheet; tap Done.
 4. Project: tap "Run projection" (players and line are prefilled if you
    came from a pick sheet; otherwise pick two players) and wait for the
    verdict card.
 5. Picks: scroll; tap a pick > its sheet > Done.
 6. Research: search "Alcaraz" > open him > scroll > Done.
 7. Round button top right > scroll the Account sheet past Membership,
    Notifications, Play responsibly, About, Sign out, Delete account.
 8. Tap "Delete account" > "Delete" > "Account deleted" alert > OK. You
    are back on Welcome.
 9. Under Subscribe tap "Weekly - free trial": Safari opens our Stripe
    checkout page. Enter nothing. Return to the app.
10. Sign in again with the same email and password (it still works after
    deletion) until the Board shows. Stop recording.

Where it goes: App Store Connect > your app > version 1.0 > App Review
Information. Add the video under Attachments there and paste Part B into
Notes. Then tap "Add for Review" and "Submit for Review" (the earlier
submission closed when the screenshots were replaced; build 7 is already
attached to the version). If Apple's message thread is open on the new
submission, paste Part B there as well.

The password (the review account's fixed sign-in code): (the reviewer code — see App Review Information)

=====================================================================
PART B — PASTE THIS into App Review Information > Notes (4,000-character
limit; this fits). Also paste it as the reply if Apple's thread is open.
=====================================================================

1. SCREEN RECORDING: attached under Attachments. Physical iPhone, current iOS, from launch: sign-in, every screen, members-only content, the Subscribe path (opens checkout on our website in Safari), account deletion, and signing in again. There is no in-app registration: accounts come from subscribing on baselineev.com, so the recording shows sign-in.

2. PURPOSE: Baseline is a sports statistics and projection tool for adults (18+) who follow tennis, NFL and NBA player props. A statistical model prices a prop, shows its number beside the posted line, and grades every published pick in public, win or loss. Informational only: no bets are accepted or placed, no sportsbook integration, no affiliate or referral links; every projection screen says "for informational purposes only" and the app carries the 1-800-GAMBLER helpline. Nothing is sold in the app; a membership is bought on our website.

3. ACCESS. Demo account, full member access, sign-in method "Sign in with email". User name: chxpvro@gmail.com. Password: (the reviewer code — see App Review Information). Steps: launch > "Already a member? Sign in" > "Sign in with email" > enter the user name > "Email me a code" > type the password into the code box > "Sign in". This address receives the emailed 6-digit code like any member and also accepts the password, so no inbox is needed. One account type; Discord sign-in is an alternative route for members of our Discord server not needed for review.
Features: Board (live PrizePicks and Underdog lines for tennis, NFL and NBA with Baseline's projection and confidence; switch sport and book; tap a player, then a prop, for the full sheet). Project (price any matchup: players, prop, line, surface). Picks (each day's released picks, the Pick of the Day, results, the month's record). Research (any player's form, prop history, game logs). Account (round button, top right): membership, notifications, privacy policy, support, sign out, delete account. Delete account: Account > Delete account > Delete; it removes personal data and signs out, and the demo account can sign in again afterwards. Notifications are optional. Internet required; no sample files.

4. EXTERNAL SERVICES: our own backend API on Railway (the only server the app contacts); our website baselineev.com on Vercel, opened in the browser for checkout, privacy policy and support; Discord OAuth 2.0 (optional sign-in); Resend (email codes); Stripe (checkout and billing portal on the website, in Safari; the app never collects payment details); Whop (some members manage membership there, in the browser); Expo push service via APNs. Sports data, fetched by our backend only: Sofascore, Tennis Abstract datasets, nflverse, ESPN public API (schedules, odds), NBA.com statistics, and the public line feeds of PrizePicks and Underdog. Images: a free-use Wikipedia photo where one exists for a player, otherwise initials; no team logos or league marks. No AI services, analytics, advertising SDKs or crash reporters.

5. REGIONS: identical everywhere, with one difference: the Subscribe section (opens checkout on our website in the browser) appears only when the device region is United States; elsewhere sign-in only, with no purchase wording. English only.

6. REGULATED INDUSTRY / THIRD-PARTY MATERIAL: not a regulated business. Baseline does not accept, place, broker or settle bets, takes no money except its own subscription (sold on the website), has no sportsbook integrations or affiliate links, and needs no gambling licence as a statistics service. The lines shown are the publicly posted player-prop lines of PrizePicks and Underdog, the reference the projection is compared against. Rated 18+. Third-party material: only player photographs from Wikipedia, used where Wikipedia offers a free-use (Creative Commons or public domain) image for that player; no team logos, league marks, music or video. Statistics are facts.
```
