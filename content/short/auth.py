"""One-time YouTube authorisation. Run once; the daily job reuses the result.

WHAT HAPPENS: your browser opens, you approve, and a refresh token is written to
secrets/token.json on this machine. The token is never printed, logged or sent
anywhere — it is standing write access to the channel, so it is treated like the
password it effectively is.

IT TELLS YOU WHICH CHANNEL YOU JUST AUTHORISED. A Google account often manages
several channels (a personal one plus any Brand Accounts), and the consent screen
shows a picker. Approving the wrong one does not fail — it silently uploads to
the wrong channel, and you would only find out by looking. So this reads the
channel back and prints its title for you to confirm before anything is posted.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "secrets")
CLIENT = os.path.join(SECRETS, "client_secret.json")
TOKEN = os.path.join(SECRETS, "token.json")

# upload is what the job needs; readonly is only so this script can name the
# channel back to you. If you did not add readonly on the consent screen the
# check is skipped and the upload scope still works.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]


def load():
    """Existing credentials, refreshed if needed. None when not authorised yet."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    if not os.path.exists(TOKEN):
        return None
    try:
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    except Exception:  # noqa: BLE001 — a corrupt token is re-authorised, not fatal
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds)
        except Exception as exc:  # noqa: BLE001
            print(f"  refresh failed ({type(exc).__name__}) — re-authorising")
            return None
    return creds if creds and creds.valid else None


def _save(creds) -> None:
    os.makedirs(SECRETS, exist_ok=True)
    with open(TOKEN, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    try:                       # not readable by other users on this machine
        os.chmod(TOKEN, 0o600)
    except OSError:
        pass


def authorise():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CLIENT):
        print(f"missing {CLIENT}\n"
              "Download the Desktop-app OAuth client JSON and save it there.")
        return None
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT, SCOPES)
    print("opening your browser — approve the app, and pick the channel you want\n"
          "to post to if you are shown a chooser...\n")
    # Print the URL rather than suppressing it: if the browser does not open on
    # its own there is otherwise nothing on screen to click, and the script just
    # appears to hang while it waits for a redirect that will never come.
    creds = flow.run_local_server(
        port=0, prompt="consent",
        authorization_prompt_message="If your browser did not open, visit:\n{url}\n",
        success_message="Baseline is authorised. You can close this tab.")
    _save(creds)
    return creds


def whoami(creds):
    """(title, id) of the authorised channel, or None if readonly wasn't granted."""
    from googleapiclient.discovery import build
    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        r = yt.channels().list(part="snippet,statistics", mine=True).execute()
        items = r.get("items") or []
        if not items:
            return None
        it = items[0]
        return (it["snippet"]["title"], it["id"],
                it.get("statistics", {}).get("subscriberCount"))
    except Exception as exc:  # noqa: BLE001
        print(f"  (couldn't read the channel back: {str(exc)[:120]})")
        return None


def main() -> int:
    creds = load()
    if creds:
        print("already authorised — token.json is valid")
    else:
        creds = authorise()
        if not creds:
            return 1
        print("authorised; refresh token saved to secrets/token.json")

    who = whoami(creds)
    print()
    if who:
        title, cid, subs = who
        print("=" * 58)
        print(f"  CHANNEL : {title}")
        print(f"  ID      : {cid}")
        if subs is not None:
            print(f"  SUBS    : {subs}")
        print("=" * 58)
        print("\nIf that is NOT the channel you want to post to, delete\n"
              "secrets/token.json and run this again, picking the other one.")
    else:
        print("Authorised, but could not read the channel name back.\n"
              "Add the youtube.readonly scope on the consent screen if you want\n"
              "this confirmation — uploading works either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
