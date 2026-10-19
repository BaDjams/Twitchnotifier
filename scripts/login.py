#!/usr/bin/env python3
"""Connexion à Twitch par code (comme sur une TV) pour obtenir le jeton TWITCH_TOKEN.

À lancer UNE fois sur ton PC :  python scripts/login.py
Aucun mot de passe n'est saisi ici : tu valides le code sur twitch.tv/activate.
Le jeton affiché est à coller dans le secret GitHub TWITCH_TOKEN, et nulle part ailleurs.
"""
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twitch import DEFAULT_CLIENT_ID, DEFAULT_USER_AGENT  # noqa: E402

HEADERS = {
    "Client-Id": DEFAULT_CLIENT_ID,
    "User-Agent": DEFAULT_USER_AGENT,
    "X-Device-Id": secrets.token_hex(16),
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
}


def post(url, data):
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode(), HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    status, dev = post("https://id.twitch.tv/oauth2/device", {"client_id": DEFAULT_CLIENT_ID, "scopes": ""})
    if status != 200:
        sys.exit(f"Impossible d'obtenir un code ({status}) : {dev}")
    print("\n1. Ouvre :", dev["verification_uri"])
    print("2. Entre le code :", dev["user_code"])
    print("   (le navigateur va s'ouvrir, connecte-toi avec ton compte Twitch habituel)\n")
    try:
        webbrowser.open(dev["verification_uri"])
    except Exception:
        pass

    deadline = time.time() + dev.get("expires_in", 1800)
    while time.time() < deadline:
        time.sleep(dev.get("interval", 5))
        status, tok = post("https://id.twitch.tv/oauth2/token", {
            "client_id": DEFAULT_CLIENT_ID,
            "device_code": dev["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if status == 200:
            print("✅ Connecté. Copie ce jeton dans le secret GitHub TWITCH_TOKEN :\n")
            print(tok["access_token"])
            print("\nNe le partage pas : il donne accès à ton compte Twitch.")
            return
        print(".", end="", flush=True)
    sys.exit("\nCode expiré, relance le script.")


if __name__ == "__main__":
    main()
