# App Review reply — Guideline 2.1 "Information Needed" (2026-10-06, updated 2026-10-07 for build 8)

Apple asked for a screen recording plus written answers on the first submission (new developer account). Part B is the text in App Review Information > Notes (and the reply, if a thread is open). The password is the reviewer code in the backend settings (APP_REVIEWER_CODE), never written here; `mobile/.secrets/app-review-reply.txt` holds the filled copy.

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
PART B — PASTE THIS as the reply, AND into App Review Information > Notes
=====================================================================

Thank you for the review. Answers to each item follow.

1. SCREEN RECORDING
Attached: a screen recording from a physical iPhone on the current iOS,
starting from app launch. It shows sign-in, every main screen, the
members-only content, the Subscribe path (which opens checkout on our
website in Safari), account deletion, and signing in again afterwards.
There is no in-app account creation: a Baseline account comes from
subscribing on our website, baselineev.com, and the app only signs in,
so the recording shows sign-in rather than registration.

2. PURPOSE AND AUDIENCE
Baseline is a sports statistics and projection tool for adults who follow
tennis, NFL and NBA player props. It prices a player prop (a tennis
player's total games or aces, an NFL receiver's receiving yards, an NBA
player's points) with a statistical model, shows the model's number next
to the line the books post, and grades every pick it publishes in public,
win or loss, so its record can be checked by anyone. The problem it
solves: judging whether a posted line is high or low normally takes hours
of manual stats work; Baseline does that work and shows the inputs
(recent form, serve and return rates, game logs, matchup data). The
audience is adult (18+) sports fans and prop-market researchers, mainly in
the United States. Baseline is informational: it does not accept or place
bets, has no sportsbook integration, no affiliate or referral links, and
every projection screen states that projections are for informational
purposes only. The app includes the National Problem Gambling Helpline
(1-800-GAMBLER). The app sells nothing and has no in-app purchases; a
membership is bought on our website.

3. SETUP AND ACCESS
Demo account (full member access; please use this one):
  Sign-in method: Sign in with email
  User name: chxpvro@gmail.com
  Password: (the reviewer code — see App Review Information)
Steps: launch the app > tap "Already a member? Sign in" > tap "Sign in
with email" > enter the user name > tap "Email me a code" > on the next
screen type the password above into the code box > tap "Sign in".
Members receive a 6-digit code by email, and this address receives one
too; the review account additionally accepts the fixed password above in
the code box, so no inbox access is needed for review.
There is one account type. Discord sign-in is a second way for members of
our Discord server to reach the same membership; it is not needed for
review.

Main features after sign-in:
- Board: the live player-prop lines that PrizePicks and Underdog list for
  tennis, NFL and NBA, each with Baseline's projection and confidence.
  Switch sport and book at the top; search; tap a player card to see all
  of that player's props; tap a prop for the full sheet (line vs
  projection, edge, confidence, recent form, game log).
- Project: price any matchup. Pick the players (search), the prop, the
  line and the surface, then tap Run projection.
- Picks: the ranked picks Baseline released each day, the Pick of the Day
  when one qualified, results once games finish, and the month's record.
- Research: look up any player: recent form, prop history, game logs.
- Account (round button at the top right of every tab): membership
  status, notification preferences, privacy policy, support, sign out,
  delete account.
Account deletion: Account > Delete account > confirm "Delete". It removes
the account's personal data and signs the device out; the app returns to
Welcome. The review account can be signed in again afterwards with the
same credentials.
Push notifications are optional and the app works fully without them. No
sample files are needed. The app needs an internet connection; all data
comes from our own backend.

4. EXTERNAL SERVICES
- Baseline backend API (our own service, hosted on Railway): the only
  server the app talks to. Sign-in, membership status, all sports data and
  projections.
- Baseline website, baselineev.com (hosted on Vercel): opened in the
  device's browser for checkout, the privacy policy and support. Never
  embedded in the app.
- Authentication: Discord OAuth 2.0 (optional sign-in method); email
  one-time codes sent through Resend (transactional email).
- Payments: Stripe. Checkout and the billing portal are Stripe-hosted
  pages on our website, opened in Safari; the app never collects payment
  details. Some members pay through Whop (a membership platform) and
  manage it there through a link that opens in the browser.
- Push notifications: Expo's push service, delivering through the Apple
  Push Notification service.
- Sports data (fetched by our backend, never by the device): Sofascore
  (tennis match data and statistics), Tennis Abstract / Match Charting
  Project (public tennis datasets), nflverse (public NFL statistics),
  ESPN's public API (NFL and NBA schedules and game odds for context),
  NBA.com statistics, and the public player-prop line feeds of PrizePicks
  and Underdog.
- Images: where a free-use photo of a player exists on Wikipedia, the app
  shows it; otherwise the player's initials. The app shows no team logos
  or league marks.
- No AI services, analytics SDKs, advertising SDKs or crash reporters are
  used in the app.

5. REGIONAL DIFFERENCES
The app behaves the same in every region with one difference: the
Subscribe section, which opens checkout on our website in the browser, is
shown only when the device's region is set to the United States. Outside
the US, the Welcome and "No active membership" screens offer sign-in only
and contain no purchase wording. Content, features and data are identical
everywhere; the interface is in English only.

6. REGULATED INDUSTRY AND THIRD-PARTY MATERIAL
Baseline does not operate in a regulated industry. It is not a gambling
operator or a sportsbook: it does not accept, place, broker or settle
bets, it takes no money other than its own subscription (sold on the
website), it has no sportsbook integrations and no affiliate or referral
links, and it holds no gambling licence because none is required for a
statistics and projection service. The lines it displays are the publicly
posted player-prop lines of PrizePicks and Underdog, shown as the
reference the projection is compared against. The app is rated 18+ and
carries the 1-800-GAMBLER helpline.
Third-party material: the only third-party images are player photographs
taken from Wikipedia, used only where Wikipedia offers a free-use image for
that player (Creative Commons or public domain); players without one are
shown as initials. The app contains no team logos or league marks and no
third-party music or video. Sports statistics are factual data.
```
