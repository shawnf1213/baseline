"""Create the iOS signing credentials through the App Store Connect API and
write them as EAS *local* credentials, so `eas build --non-interactive` can
run without an Apple ID session.

    python scripts/apple_credentials.py

Reads  .secrets/AuthKey_8F66NL8552.p8   (the ASC API key — git-ignored)
Writes .secrets/dist_<serial>.p12       (Apple Distribution certificate + private key)
       .secrets/baseline_appstore.mobileprovision
       credentials.json                 (git-ignored; what eas.json's
                                         credentialsSource: "local" reads)

Idempotent where the API allows it: an existing bundle id and an existing
usable profile are reused; a certificate is always created fresh because
Apple never returns a private key (ours is generated here). Nothing secret
is printed.
"""
import base64
import datetime as dt
import json
import os
import secrets as pysecrets
import sys
import time
import urllib.error
import urllib.request

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SECRETS = os.path.join(ROOT, ".secrets")
KEY_ID = "8F66NL8552"
ISSUER_ID = "b81a68cc-d55f-48d8-b96c-64a793bdc525"
TEAM_ID = "WHD8WZRR58"
BUNDLE_ID = "com.baselineev.app"
APP_NAME = "Baseline"
API = "https://api.appstoreconnect.apple.com"


def token() -> str:
    key = open(os.path.join(SECRETS, f"AuthKey_{KEY_ID}.p8")).read()
    now = int(time.time())
    return jwt.encode({"iss": ISSUER_ID, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
                      key, algorithm="ES256", headers={"kid": KEY_ID, "typ": "JWT"})


def call(method: str, path: str, body: dict = None) -> tuple:
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token()}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw[:300]}


def fail(msg, payload=None):
    print("ERROR:", msg)
    if payload:
        print(json.dumps(payload, indent=2)[:1500])
    sys.exit(1)


def main():
    os.makedirs(SECRETS, exist_ok=True)

    # ── 1. bundle id (+ push capability) ─────────────────────────────────────
    st, d = call("GET", f"/v1/bundleIds?filter[identifier]={BUNDLE_ID}")
    if st != 200:
        fail("bundle id lookup", d)
    rows = [b for b in d.get("data", []) if b["attributes"].get("identifier") == BUNDLE_ID]
    if rows:
        bid = rows[0]["id"]
        print(f"bundle id exists: {BUNDLE_ID} ({bid})")
    else:
        st, d = call("POST", "/v1/bundleIds", {"data": {"type": "bundleIds", "attributes": {
            "identifier": BUNDLE_ID, "name": APP_NAME, "platform": "IOS"}}})
        if st not in (200, 201):
            fail("bundle id create", d)
        bid = d["data"]["id"]
        print(f"bundle id registered: {BUNDLE_ID} ({bid})")
    st, d = call("GET", f"/v1/bundleIds/{bid}/bundleIdCapabilities")
    caps = {c["attributes"].get("capabilityType") for c in d.get("data", [])} if st == 200 else set()
    if "PUSH_NOTIFICATIONS" not in caps:
        st, d = call("POST", "/v1/bundleIdCapabilities", {"data": {"type": "bundleIdCapabilities",
            "attributes": {"capabilityType": "PUSH_NOTIFICATIONS"},
            "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bid}}}}})
        if st not in (200, 201):
            fail("push capability", d)
        print("push notifications capability: enabled")
    else:
        print("push notifications capability: already on")

    # ── 2. distribution certificate (fresh private key, CSR, Apple signs) ────
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([
               x509.NameAttribute(NameOID.COMMON_NAME, APP_NAME),
               x509.NameAttribute(NameOID.ORGANIZATION_NAME, APP_NAME),
               x509.NameAttribute(NameOID.COUNTRY_NAME, "US")]))
           .sign(priv, hashes.SHA256()))
    csr_b64 = base64.b64encode(csr.public_bytes(serialization.Encoding.DER)).decode()
    st, d = call("POST", "/v1/certificates", {"data": {"type": "certificates", "attributes": {
        "certificateType": "DISTRIBUTION", "csrContent": csr_b64}}})
    if st not in (200, 201):
        fail("certificate create (Apple allows a limited number of distribution certificates — "
             "revoke an old one in the developer portal if this says the limit is reached)", d)
    cert_id = d["data"]["id"]
    attrs = d["data"]["attributes"]
    cert_der = base64.b64decode(attrs["certificateContent"])
    cert = x509.load_der_x509_certificate(cert_der)
    serial = attrs.get("serialNumber") or format(cert.serial_number, "X")
    print(f"distribution certificate created: id={cert_id} serial={serial} expires={attrs.get('expirationDate')}")
    p12_pw = pysecrets.token_urlsafe(18)
    p12 = pkcs12.serialize_key_and_certificates(
        name=APP_NAME.encode(), key=priv, cert=cert, cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(p12_pw.encode()))
    p12_path = os.path.join(SECRETS, f"dist_{serial}.p12")
    with open(p12_path, "wb") as f:
        f.write(p12)
    with open(os.path.join(SECRETS, f"dist_{serial}.p12.password"), "w") as f:
        f.write(p12_pw)

    # ── 3. App Store provisioning profile ────────────────────────────────────
    name = f"{APP_NAME} App Store {dt.date.today().isoformat()}"
    st, d = call("POST", "/v1/profiles", {"data": {"type": "profiles",
        "attributes": {"name": name, "profileType": "IOS_APP_STORE"},
        "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bid}},
                          "certificates": {"data": [{"type": "certificates", "id": cert_id}]}}}})
    if st not in (200, 201):
        fail("profile create", d)
    prof = d["data"]["attributes"]
    prof_path = os.path.join(SECRETS, "baseline_appstore.mobileprovision")
    with open(prof_path, "wb") as f:
        f.write(base64.b64decode(prof["profileContent"]))
    print(f"provisioning profile created: {prof['name']} (uuid {prof.get('uuid')}, expires {prof.get('expirationDate')})")

    # ── 4. EAS local credentials ─────────────────────────────────────────────
    creds = {"ios": {
        "provisioningProfilePath": os.path.relpath(prof_path, ROOT).replace("\\", "/"),
        "distributionCertificate": {"path": os.path.relpath(p12_path, ROOT).replace("\\", "/"),
                                    "password": p12_pw}}}
    with open(os.path.join(ROOT, "credentials.json"), "w") as f:
        json.dump(creds, f, indent=2)
    print("wrote credentials.json (git-ignored) — eas.json production profile uses credentialsSource: local")


if __name__ == "__main__":
    main()
