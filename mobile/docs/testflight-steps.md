# TestFlight and App Store Connect — the steps only you can do

State when this was written (2026-10-06): EAS project `@shawnf1213/baseline`
(id `63326d11-fa09-4641-b7c8-b3b132ee421d`); bundle id `com.baselineev.app`
registered in your Apple developer account (id `G8NX2ZT49G`) with the Push
Notifications capability; an Apple Distribution certificate and an App Store
provisioning profile created through the App Store Connect API and used as
EAS local credentials (`mobile/.secrets/`, git-ignored); production build
#2 submitted to EAS Build. Everything below assumes you are signed in to
https://appstoreconnect.apple.com and https://developer.apple.com with the
Team `WHD8WZRR58`.

## 1. The app record in App Store Connect (if `eas submit` could not create it)

App Store Connect → **My Apps** → **+** → **New App**:
- Platforms: **iOS**
- Name: **Baseline** (if taken: **Baseline EV**, then **Baseline Props** — see `store-listing.md`)
- Primary Language: **English (U.S.)**
- Bundle ID: **com.baselineev.app** (it is in the list because the identifier is registered)
- SKU: **baseline-ios**
- User Access: **Full Access**

Then tell me, or run `scripts/eas-ios.sh submit` yourself; EAS finds the app by bundle id.

## 2. Push notifications key (one time, 3 minutes)

Expo's push service needs an APNs key on the EAS project. Apple does not let
an API create APNs keys, so:

1. https://developer.apple.com/account/resources/authkeys/list → **+**
2. Key Name: **Baseline Push** → tick **Apple Push Notifications service (APNs)** → Continue → Register
3. **Download** the `.p8` (only offered once) and note the **Key ID**.
4. https://expo.dev/accounts/shawnf1213/projects/baseline/credentials → **iOS** → `com.baselineev.app` → **Push Key** → **Add a push key** → upload the `.p8`, enter the Key ID and Team ID `WHD8WZRR58`.

Until this is done the app registers devices and the backend fans out, but
Apple never receives anything. No app rebuild is needed afterwards.

## 3. TestFlight

After `eas submit` reports "Submitted", App Store Connect processes the build
(5–30 minutes; you get an email).

- App Store Connect → your app → **TestFlight** tab.
- Build 1.0.0 (2) appears under **iOS Builds**. If it shows **Missing Compliance**,
  click it → "No" to the encryption question (the binary already declares
  `ITSAppUsesNonExemptEncryption: false`, so this usually does not appear).
- **Internal Testing** → **+** group "Baseline team" → add yourself (and anyone else with an App Store Connect role) → the build is available in the TestFlight app on your phone within minutes.
- For external testers (App Review needed for the first build): **External Testing** → create a group → add emails → attach the build → fill **Test Information** (what to test, feedback email `support@baselineev.com`) → submit for review.

## 4. App Review notes (App Store submission)

App Store Connect → your app → **App Store** tab → version 1.0 → **App Review Information**:

- Sign-in required: **Yes**
- User name: `chxpvro@gmail.com`
- Password: **the fixed reviewer code** — the value of the Railway variable `APP_REVIEWER_CODE` on the backend (only you can read it: Railway → backend service → Variables). It is not in this repo.
- Notes (paste):

  > Baseline is a sports statistics and projection tool. Sign in with **Sign in with email**, enter the address above, and on the code screen enter the code above (a fixed review code; real members receive a 6-digit code by email). The account has full member access. Memberships are sold on our website (baselineev.com) through Stripe and used across the website, our Discord and this app; nothing is purchased inside the app, and the app contains no sportsbook links. Projections are informational only; a responsible-gambling helpline is in the account sheet.

- Contact: your name, phone, email.

## 5. App Information, Pricing, Privacy, Age Rating

- **App Information**: name, subtitle, category **Sports**, privacy policy URL `https://baselineev.com/privacy` — copy from `store-listing.md`.
- **Pricing and Availability**: Free (the membership is bought on the website). Availability: all territories is fine; the app itself only shows Subscribe on the US storefront.
- **App Privacy**: answer per `apple-review.md` (Email, User ID, Device ID (push token), Purchase History — all "linked to you", none used for tracking; no analytics, no crash data).
- **Age Rating**: per `apple-review.md` ("Gambling and Contests" → 17+/18+).
- **Screenshots**: upload `mobile/store/screenshots/6.9in-1320x2868/*.png` (and the 6.5" set if required).
- **Description / keywords / support URL / marketing URL**: from `store-listing.md`.

## 6. Afterwards

- Version bumps: `eas build` auto-increments the build number (remote); bump `version` in `app.json` for a new marketing version.
- Credentials renew in a year (certificate and profile expire 2027-10-06); re-run `python scripts/apple_credentials.py` then rebuild.
- Rotate the App Store Connect API key if it is ever exposed: App Store Connect → Users and Access → Integrations → revoke, create a new one, put the new `.p8` in `mobile/.secrets/` and update the IDs in `eas.json` and the scripts.
