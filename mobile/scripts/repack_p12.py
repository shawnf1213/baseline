"""Re-encrypt the distribution .p12 referenced by credentials.json with the
legacy PKCS#12 scheme (3DES + SHA-1) that macOS `security import` accepts.
Same key, same certificate, same password — only the container changes.

    python scripts/repack_p12.py
"""
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
creds = json.load(open(os.path.join(ROOT, "credentials.json")))
p12_path = os.path.join(ROOT, creds["ios"]["distributionCertificate"]["path"])
pw = creds["ios"]["distributionCertificate"]["password"].encode()

key, cert, extra = pkcs12.load_key_and_certificates(open(p12_path, "rb").read(), pw)
legacy = (serialization.PrivateFormat.PKCS12.encryption_builder()
          .kdf_rounds(50000)
          .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
          .hmac_hash(hashes.SHA1())
          .build(pw))
out = pkcs12.serialize_key_and_certificates(b"Baseline", key, cert, extra or None, legacy)
with open(p12_path, "wb") as f:
    f.write(out)
# Round-trip check.
k2, c2, _ = pkcs12.load_key_and_certificates(out, pw)
assert c2.serial_number == cert.serial_number
print(f"repacked {os.path.relpath(p12_path, ROOT)} ({len(out)} bytes), serial {format(cert.serial_number, 'X')}, legacy PBES1 3DES/SHA1")
