"""One-time interactive helper to authorize this tool against your Strava
account and obtain a refresh token for STRAVA_REFRESH_TOKEN in .env.

You only need to run this once (the refresh token doesn't expire unless you
revoke access on Strava). It requires a Strava API application:

1. Create one at https://www.strava.com/settings/api (free).
   - "Authorization Callback Domain" can just be: localhost
2. Put the app's Client ID and Client Secret into .env as
   STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET, then run:

     python strava_auth_setup.py

This never touches activity data -- it only performs the OAuth handshake.
"""
import os
import urllib.parse

import requests
from dotenv import load_dotenv

load_dotenv()

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
REDIRECT_URI = "http://localhost/exchange_token"
SCOPE = "activity:read_all"


def main():
    client_id = os.getenv("STRAVA_CLIENT_ID", "").strip()
    client_secret = os.getenv("STRAVA_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            "Missing STRAVA_CLIENT_ID or STRAVA_CLIENT_SECRET in .env.\n"
            "Create an app at https://www.strava.com/settings/api first."
        )

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    print("1. Open this URL in a browser and authorize the app:\n")
    print(f"   {url}\n")
    print("2. Strava will redirect to a localhost URL that fails to load --")
    print("   that's expected, nothing is running on localhost. Copy the")
    print("   'code' query parameter's value from that browser address bar.\n")

    code = input("Paste the code here: ").strip()
    if not code:
        raise SystemExit("No code entered; aborting.")

    response = requests.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    })
    response.raise_for_status()
    payload = response.json()

    print("\nSuccess. Add this line to your .env:\n")
    print(f"STRAVA_REFRESH_TOKEN={payload['refresh_token']}")
    print(
        "\n(This refresh token doesn't expire on its own; you won't need to "
        "run this setup again unless you revoke the app's access on Strava.)"
    )


if __name__ == "__main__":
    main()
