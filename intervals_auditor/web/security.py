"""Access control for the web interface.

There is no login yet: every /api route depends on require_access(), which
lets every request through apart from the check below. Adding
authentication later means implementing it here (e.g. a session cookie or
a token check) without touching the routes. Until then, only expose the
web interface on a network you trust (LAN/VPN) or behind a reverse proxy
that does the login.

Changes must be sent as JSON (Content-Type: application/json). A browser
only sends that cross-origin after a CORS preflight, which this server
doesn't allow, so another website open in your browser can't acknowledge
issues or start jobs through it (CSRF). Keep this when adding a
cookie-based login.
"""
from fastapi import HTTPException, Request

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_access(request: Request) -> None:
    if request.method not in SAFE_METHODS and request.method != "DELETE":
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise HTTPException(415, "Send the request body as JSON (Content-Type: application/json)")
