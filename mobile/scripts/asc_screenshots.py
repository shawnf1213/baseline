"""Upload the store screenshots to App Store Connect through the ASC API.

    python scripts/asc_screenshots.py                 # upload into empty sets
    python scripts/asc_screenshots.py --replace       # swap out what is there
    python scripts/asc_screenshots.py --dry-run       # say what would happen

Reads store/screenshots/<size>/*.png (rendered by scripts/screenshots.mjs) and
puts them, in file-name order, on the iOS version that is still editable
(Prepare for Submission and the like), en-US localization:

    6.9in-1320x2868  ->  APP_IPHONE_67   (App Store Connect's 6.9" slot)
    6.5in-1242x2688  ->  APP_IPHONE_65

--replace deletes the screenshots already in those two sets first; without it
a set that already has screenshots is left alone. Auth is the ASC API key in
.secrets/ (see apple_credentials.py); nothing secret is printed.
"""
import hashlib
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apple_credentials import call  # noqa: E402

APP_ID = "6819564801"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "store", "screenshots")
SETS = {"6.9in-1320x2868": "APP_IPHONE_67", "6.5in-1242x2688": "APP_IPHONE_65"}
EDITABLE = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED",
            "INVALID_BINARY"}


def must(st, d, what):
    if st not in (200, 201, 204):
        err = (d.get("errors") or [{}])[0]
        sys.exit(f"{what} failed: HTTP {st} {err.get('code')} {err.get('detail', '')[:200]}")
    return d


def editable_localization():
    d = must(*call("GET", f"/v1/apps/{APP_ID}/appStoreVersions?filter[platform]=IOS&limit=10"), "versions")
    vers = [v for v in d["data"]
            if (v["attributes"].get("appVersionState") or v["attributes"].get("appStoreState")) in EDITABLE]
    if not vers:
        sys.exit("no editable iOS version (all submitted or live)")
    v = vers[0]
    locs = must(*call("GET", f"/v1/appStoreVersions/{v['id']}/appStoreVersionLocalizations"), "localizations")["data"]
    loc = next((l for l in locs if l["attributes"].get("locale") == "en-US"), locs[0] if locs else None)
    if not loc:
        sys.exit("version has no localization")
    print(f"version {v['attributes'].get('versionString')} ({v['attributes'].get('appStoreState')}), "
          f"localization {loc['attributes'].get('locale')}")
    return loc["id"]


def screenshot_set(loc_id, display_type, dry):
    d = must(*call("GET", f"/v1/appStoreVersionLocalizations/{loc_id}/appScreenshotSets"), "sets")
    for s in d["data"]:
        if s["attributes"].get("screenshotDisplayType") == display_type:
            return s["id"]
    if dry:
        return None
    body = {"data": {"type": "appScreenshotSets", "attributes": {"screenshotDisplayType": display_type},
                     "relationships": {"appStoreVersionLocalization": {
                         "data": {"type": "appStoreVersionLocalizations", "id": loc_id}}}}}
    return must(*call("POST", "/v1/appScreenshotSets", body), f"create {display_type} set")["data"]["id"]


def upload(set_id, path):
    raw = open(path, "rb").read()
    name = os.path.basename(path)
    body = {"data": {"type": "appScreenshots", "attributes": {"fileName": name, "fileSize": len(raw)},
                     "relationships": {"appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": set_id}}}}}
    shot = must(*call("POST", "/v1/appScreenshots", body), f"reserve {name}")["data"]
    for op in shot["attributes"].get("uploadOperations") or []:
        part = raw[op["offset"]:op["offset"] + op["length"]]
        req = urllib.request.Request(op["url"], data=part, method=op.get("method", "PUT"),
                                     headers={h["name"]: h["value"] for h in op.get("requestHeaders") or []})
        with urllib.request.urlopen(req, timeout=120) as r:
            if r.status not in (200, 201, 204):
                sys.exit(f"upload of {name} part at {op['offset']} returned HTTP {r.status}")
    commit = {"data": {"type": "appScreenshots", "id": shot["id"],
                       "attributes": {"uploaded": True, "sourceFileChecksum": hashlib.md5(raw).hexdigest()}}}
    must(*call("PATCH", f"/v1/appScreenshots/{shot['id']}", commit), f"commit {name}")
    return shot["id"], name


def wait_delivered(ids, timeout_s=300):
    pending, t0 = dict(ids), time.time()
    while pending and time.time() - t0 < timeout_s:
        for sid, name in list(pending.items()):
            st, d = call("GET", f"/v1/appScreenshots/{sid}")
            state = ((d.get("data") or {}).get("attributes") or {}).get("assetDeliveryState") or {}
            if state.get("state") == "COMPLETE":
                print(f"  ready   {name}")
                pending.pop(sid)
            elif state.get("state") == "FAILED":
                print(f"  FAILED  {name}: {[e.get('code') for e in state.get('errors') or []]}")
                pending.pop(sid)
        if pending:
            time.sleep(5)
    for name in pending.values():
        print(f"  still processing after {timeout_s}s: {name}")


def main():
    dry, replace = "--dry-run" in sys.argv, "--replace" in sys.argv
    loc_id = editable_localization()
    for folder, display_type in SETS.items():
        files = sorted(f for f in os.listdir(os.path.join(SHOTS, folder))
                       if f.endswith(".png") and "FAILED" not in f)
        set_id = screenshot_set(loc_id, display_type, dry)
        existing = []
        if set_id:
            existing = must(*call("GET", f"/v1/appScreenshotSets/{set_id}/appScreenshots"), "list")["data"]
        print(f"{display_type}: {len(files)} file(s) to upload, {len(existing)} already in the set")
        if existing and not replace:
            print("  set already has screenshots; pass --replace to swap them — skipped")
            continue
        if dry:
            continue
        for s in existing:
            must(*call("DELETE", f"/v1/appScreenshots/{s['id']}"), "delete old screenshot")
        ids = [upload(set_id, os.path.join(SHOTS, folder, f)) for f in files]
        wait_delivered(ids)


if __name__ == "__main__":
    main()
