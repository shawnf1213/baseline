# Apple questionnaires — what to answer, and why

Written 2026-10-06 from what the app actually does and stores. Re-check if the
app gains a feature that collects something new.

## Age rating (App Store Connect → App Information → Age Rating)

Apple's questionnaire asks about content, not purpose. Answer each item as
below; the result is **17+** in the current questionnaire (driven by the
"Gambling and Contests" item), which is also the honest outcome for a sports
projection product that references sportsbook lines.

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

If the newer (2025+) questionnaire is shown, the equivalent is: **Gambling
content: "Infrequent/Mild references"** is *not* accurate — choose the option
that describes real-money gambling references (the lines are real sportsbook
lines) and set the minimum age to **18+** (Apple's current minimum for gambling-
related content). Keep the in-app helpline, the "informational purposes only"
line on every pick and projection screen, and the absence of any sportsbook
link — those are what the reviewer checks against guideline 5.3.

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

## Account deletion (guideline 5.1.1(v))

In-app: Account sheet → Delete account → typed-style confirmation dialog →
`POST /api/account/delete` (server-verified `{"confirm":"DELETE"}`) → session
and push token cleared → Welcome. Also available on the website.

## Review notes (App Store Connect → App Review Information)

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
