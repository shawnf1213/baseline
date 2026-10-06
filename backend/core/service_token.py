"""The service token internal callers present to the API.

The Discord bot, the NFL/NBA publishers and the line monitors all call the
backend as machines, not as members. When member-facing endpoints are gated
on a session (operator ruling, 2026-10-05), these callers identify themselves
with `X-Service-Token: <BASELINE_SERVICE_TOKEN>` instead. The same variable
is set on both Railway services; the value never appears in code or logs.

Every caller goes through headers(): with the variable unset it returns {}
so a missing token degrades to "unauthenticated" rather than a crash.
"""
import os

ENV = "BASELINE_SERVICE_TOKEN"
HEADER = "X-Service-Token"


def token() -> str:
    return (os.getenv(ENV) or "").strip()


def headers() -> dict:
    t = token()
    return {HEADER: t} if t else {}
