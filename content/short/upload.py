"""Upload the rendered Short to YouTube.

PRIVATE BY DEFAULT, AND NOT ONLY BY CHOICE. Until a Google API compliance audit
is granted, videos uploaded through the API by this project are forced to
private regardless of what we ask for — asking for public would not fail, it
would just be ignored, which is worse than being explicit. So the default is
private and SHORT_PRIVACY is the switch for after the audit lands.

IDEMPOTENT PER DAY. The scheduler can fire twice, a run can be retried by hand,
and a laptop can wake up and re-trigger a missed task. Each upload writes
out/uploaded-<date>.json, and a second run for a date that already has one stops
rather than posting the same pick twice.
"""

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, HERE)

import auth   # noqa: E402
import meta   # noqa: E402

PRIVACY = os.getenv("SHORT_PRIVACY", "public")      # private | unlisted | public


def upload(video_path: str, plan: dict, privacy: str = None) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = auth.load()
    if not creds:
        raise SystemExit("not authorised — run:  python auth.py")

    m = meta.build(plan)
    body = {
        "snippet": {
            "title": m["title"],
            "description": m["description"],
            "tags": [t for t in m["tags"] if t],
            "categoryId": m["categoryId"],
        },
        "status": {
            "privacyStatus": privacy or PRIVACY,
            "selfDeclaredMadeForKids": m["selfDeclaredMadeForKids"],
        },
    }

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True,
                            mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"uploading {os.path.basename(video_path)} "
          f"({os.path.getsize(video_path) / 1024 / 1024:.1f} MB) "
          f"as {body['status']['privacyStatus']}...")
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"   {int(status.progress() * 100)}%")
    return resp


def main() -> int:
    plan_path = os.path.join(OUT, "plan.json")
    if not os.path.exists(plan_path):
        print("no plan.json — run build.py and render.js first")
        return 1
    plan = json.load(open(plan_path, encoding="utf-8"))
    video = os.path.join(OUT, f"baseline-short-{plan['date']}.mp4")
    if not os.path.exists(video):
        print(f"no video for {plan['date']} — run: node render.js")
        return 1

    marker = os.path.join(OUT, f"uploaded-{plan['date']}.json")
    if os.path.exists(marker) and "--force" not in sys.argv:
        prev = json.load(open(marker, encoding="utf-8"))
        print(f"already uploaded for {plan['date']}: {prev.get('url')}\n"
              f"pass --force to upload again anyway.")
        return 0

    resp = upload(video, plan)
    vid = resp.get("id")
    url = f"https://youtu.be/{vid}"
    rec = {"date": plan["date"], "id": vid, "url": url,
           "privacy": resp.get("status", {}).get("privacyStatus"),
           "title": resp.get("snippet", {}).get("title"),
           "pick": f"{plan['pick']['player']} {plan['pick']['prop']} "
                   f"{plan['pick']['lean']} {plan['pick']['line']}"}
    with open(marker, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)

    print()
    print("=" * 60)
    print(f"  {rec['title']}")
    print(f"  {url}")
    print(f"  visibility: {rec['privacy']}")
    print("=" * 60)
    if rec["privacy"] == "private":
        print("\nPrivate because this project has not passed Google's API\n"
              "compliance audit yet — publish it from YouTube Studio, or request\n"
              "the audit to make uploads public automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
