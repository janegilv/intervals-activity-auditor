"""Access control for the web interface.

There is no login yet: every /api route depends on require_access(), which
currently lets every request through. Adding authentication later means
implementing it here (e.g. a session cookie or a token check) without
touching the routes. Until then, only expose the web interface on a
network you trust (LAN/VPN) or behind a reverse proxy that does the login.
"""
from fastapi import Request


def require_access(request: Request) -> None:
    return None
