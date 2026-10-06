"""The service token the bot presents to the backend — see
backend/core/service_token.py, of which this is a copy: the bot package does
not import from backend/core, so the four lines live here too. Keep both the
same.
"""
import os

ENV = "BASELINE_SERVICE_TOKEN"
HEADER = "X-Service-Token"


def token() -> str:
    return (os.getenv(ENV) or "").strip()


def headers() -> dict:
    t = token()
    return {HEADER: t} if t else {}
