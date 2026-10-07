# Apple questionnaires — what to answer, and why

Written 2026-10-06 from what the app actually does and stores. Re-check if the
app gains a feature that collects something new.

## Age rating (App Store Connect → App Information → Age Ratings)

**SET 2026-10-06 through the API, on Apple's 2025 questionnaire:** every
content item None/No, including **Gambling: No** and **Simulated Gambling:
None**, with the **age rating override at 18+**. In the 2025 definitions
"Gambling" means the app itself takes real-money bets or wagers, which also
brings licensing requirements (guideline 5.3.4); Baseline takes no bets, so the
honest answer is No. The truthful answers compute to 4+, and the 18+ override
is what keeps a product built on real-money sportsbook lines adults-only. The
table below is the older questionnaire and is superseded by this paragraph.

Apple's questionnaire asks about content, not purpose. Older questionnaire,
for reference only:

| Question | Answer | Why |
|---|---|---|
| Cartoon or Fantasy Violence | None | — |
| Realistic Violence | None | — |
| Prolonged Graphic or Sadistic Realistic Violence | None | — |
| Profanity or Crude Humor | None | — |
| Mature/Suggestive Themes | None | — |
| Horror/Fear Themes | None | — |
| Medical/Treatment Information | None | — |
| Alcohol, Tobacco, or Drug Use or References | None | — |
| Sexual Content or Nudity | None | — |
| Graphic Sexual Content and Nudity | None | — |
| **Simulated Gambling** | None | No games of chance are played in the app. |
| **Gambling and Contests** | **Yes — Frequent/Intense** | The app shows sportsbook (PrizePicks / Underdog) lines and projections against them. It does not take bets, has no sportsbook links, sign-ups or affiliate links. |
| Contests | None | — |
| Unrestricted Web Access | No | Only our own privacy/support pages and Stripe/Whop open, in the system browser. |
| Loot boxes / random items | No | — |
| Made for Kids | No | — |

Keep the in-app helpline, the "informational purposes only" line on every pick
and projection screen, and the absence of any sportsbook link — those are what
the reviewer checks against guideline 5.3.

## App Privacy (App Store Connect → App Privacy)

"Do you or your third-party partners collect data from this app?" — **Yes.**

Declare exactly these data types. Every one is **linked to the user's
identity** (it's tied to the account) and **not used for tracking** (no
advertising, no data brokers, no cross-app tracking SDKs).

| Data type | Collected? | Purpose(s) | Linked to user | Tracking |
|---|---|---|---|---|
| Contact Info → **Email Address** | Yes (email sign-in / Stripe subscribers) | App Functionality (sign-in, membership) | Yes | No |
| Contact Info → Name | No | — | — | — |
| Identifiers → **User ID** | Yes (Discord user id / username when Discord sign-in is used) | App Functionality | Yes | No |
| Identifiers → **Device ID** | Yes — the push notification token, only if the user enables notifications | App Functionality (notifications) | Yes | No |
| Purchases → **Purchase History** | Yes (subscription status and plan, from Stripe; no card data) | App Functionality | Yes | No |
| Financial Info → Payment Info | **No** — card details never touch Baseline (Stripe-hosted checkout in the browser) | — | — | — |
| Location | No | — | — | — |
| Usage Data → Product Interaction | **No** — no analytics SDK, no event logging | — | — | — |
| Diagnostics → Crash Data | **No** — no crash reporter is bundled | — | — | — |
| Browsing / Search History | No (player searches are not stored) | — | — | — |
| User Content | No | — | — | — |
| Sensitive Info / Health / Contacts / Other | No | — | — | — |

Notes that keep the declaration honest:
- The **IP address at trial sign-up** is recorded by the *website* checkout
  flow (see the privacy policy), not by the app. Checkout happens in the
  browser, so it is not an app data collection.
- Saved players and notification preferences live only on the device
  (AsyncStorage) and are not collected.
- The app makes requests to our own backend only; sports data is fetched by
  the backend, never by the device from a third party.
- Third-party SDKs in the binary: Expo modules (notifications, secure store,
  haptics, fonts, localization, linear gradient, web browser). None collects
  data for its own purposes; Expo's push service relays notifications and sees
  only the device token.

**Privacy policy URL:** https://baselineev.com/privacy
**Support URL:** https://baselineev.com/support

## Images and third-party material (guideline 5.2)

**Since build 8 (2026-10-07):** player photos for every sport come only from
Wikipedia / Wikimedia Commons through `/api/player/image`, which accepts a
photo only when the article names that player, describes a player of that
sport, is the only such article, and hosts the photo on Commons (free
licence). **No source label is shown in the app** (operator ruling: other
apps carry none); the review notes state that free-use Wikipedia images are
used where available. `/api/player/image/credit` still answers a photo's file
page if provenance is ever asked for. The app shows **no team logos or league
marks** (ESPN's logo CDN and the ESPN/NBA.com headshots were removed —
unlicensed). The website still draws team marks on its NFL/NBA boards; that is
outside Apple's review but carries the same exposure.

## Account deletion (guideline 5.1.1(v))

In-app: Account sheet → Delete account → typed-style confirmation dialog →
`POST /api/account/delete` (server-verified `{"confirm":"DELETE"}`) → session
and push token cleared → Welcome. Also available on the website.

## Review notes (App Store Connect → App Review Information)

**2026-10-06:** Apple's first-submission "Information Needed" request (screen
recording + six written items) was answered with the text in
`app-review-reply.md`; the same text now lives in the Notes field. The
recording shot list is Part A of that file.

- Sign-in for review: *Sign in with email* → `chxpvro@gmail.com` → enter the
  fixed reviewer code (a backend secret; paste it from your records into the
  review notes — it is not stored in this repo). This account has premium
  access only; it is not an owner or admin.
- Discord sign-in also works but needs a Discord account with the premium role;
  the email route above is the one to use.
- Subscriptions are sold on the website through Stripe and open in the default
  browser; nothing is purchased inside the app (guideline 3.1.3(b)-style
  reader/multiplatform content — the membership is a service used across the
  website, Discord and the app). The app shows a Subscribe button only on the
  US storefront.
