"""Accès à l'API GraphQL interne de Twitch (la même que l'appli Android).

Seule la bibliothèque standard Python est utilisée : rien à installer.
Les identifiants de requêtes (hashes) changent parfois côté Twitch : ils sont
surchargeables dans docs/config.json -> "api", sans toucher au code.
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request

# Client « Twitch Smart TV » : le seul qui accepte encore la connexion par code
# (device flow) sans mot de passe ; l'ID de l'appli Android renvoie « invalid client ».
# Le jeton est lié à ce client : il faut utiliser le même pour les appels GQL.
DEFAULT_CLIENT_ID = "ue6666qo983tsx6so1t0vnawi233wa"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 7.1; Smart Box C1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
DEFAULT_HASHES = {
    "ViewerDropsDashboard": "c16bb890cc8ce7647a96ee69cd313d423a378a3dedadf630a1017cde18975feb",
    "DropCampaignDetails": "039277bf98f3130929262cc7c6efd9c141ca3749cb6dca442fc8ead9a53f77c1",
}
GQL_URL = "https://gql.twitch.tv/gql"


class TwitchError(Exception):
    """Erreur générique de l'API."""


class AuthError(TwitchError):
    """Jeton absent, expiré ou révoqué : il faut relancer scripts/login.py."""


class ApiChanged(TwitchError):
    """Twitch a modifié son API (hash périmé) : mettre à jour config.json -> api."""


def _http(method: str, url: str, headers: dict, body: bytes | None = None, timeout: int = 30):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


class TwitchClient:
    def __init__(self, token: str, api_cfg: dict | None = None):
        api_cfg = api_cfg or {}
        self.token = token.strip().removeprefix("OAuth ").strip()
        self.client_id = api_cfg.get("client_id") or DEFAULT_CLIENT_ID
        self.user_agent = api_cfg.get("user_agent") or DEFAULT_USER_AGENT
        self.hashes = {**DEFAULT_HASHES, **(api_cfg.get("hashes") or {})}
        self.device_id = secrets.token_hex(16)
        self.user_id: str | None = None
        self.login: str | None = None
        self.scopes: list[str] = []

    # -- authentification -------------------------------------------------
    def validate(self) -> None:
        if not self.token:
            raise AuthError("Aucun jeton Twitch (secret TWITCH_TOKEN vide).")
        status, data = _http(
            "GET",
            "https://id.twitch.tv/oauth2/validate",
            {"Authorization": f"OAuth {self.token}"},
        )
        if status != 200:
            raise AuthError(f"Jeton Twitch refusé ({status}) : relance scripts/login.py.")
        self.user_id = str(data.get("user_id"))
        self.login = data.get("login")
        self.scopes = data.get("scopes") or []

    # -- GraphQL ------------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "Client-Id": self.client_id,
            "Authorization": f"OAuth {self.token}",
            "User-Agent": self.user_agent,
            "X-Device-Id": self.device_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _op(self, name: str, variables: dict) -> dict:
        return {
            "operationName": name,
            "variables": variables,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": self.hashes[name]}},
        }

    def gql(self, ops: list[dict]) -> list[dict]:
        status, data = _http("POST", GQL_URL, self._headers(), json.dumps(ops).encode())
        if status == 401:
            raise AuthError("Twitch a refusé le jeton (401) : relance scripts/login.py.")
        if status != 200 or not isinstance(data, list):
            raise TwitchError(f"Réponse GQL inattendue ({status}) : {str(data)[:300]}")
        for item in data:
            for err in item.get("errors") or []:
                msg = err.get("message", "")
                if "PersistedQueryNotFound" in msg:
                    raise ApiChanged(
                        f"Hash périmé pour {item.get('extensions', {}).get('operationName', '?')}"
                    )
                if msg in ("service error", "service timeout", "service unavailable"):
                    continue  # erreur passagère, la donnée peut être partielle
                raise TwitchError(f"Erreur GQL : {msg}")
        return data

    # -- campagnes ------------------------------------------------------------
    def campaigns(self) -> list[dict]:
        """Campagnes actives/annoncées (avec détail des drops) + terminées récemment (résumé)."""
        if not self.user_id:
            self.validate()
        dash = self.gql([self._op("ViewerDropsDashboard", {"fetchRewardCampaigns": False})])[0]
        user = (dash.get("data") or {}).get("currentUser")
        if user is None:
            raise AuthError("Pas d'utilisateur courant : jeton invalide ?")
        everything = user.get("dropCampaigns") or []
        if not everything:
            print(f"Attention : Twitch ne renvoie aucune campagne pour {self.login} "
                  f"(client {self.client_id}, droits du jeton : {self.scopes or 'aucun'}).")
        summary = [c for c in everything if c.get("status") in ("ACTIVE", "UPCOMING")]
        expired = [c for c in everything if c.get("status") == "EXPIRED" and c.get("game")]

        details: dict[str, dict] = {}
        ids = [c["id"] for c in summary]
        for i in range(0, len(ids), 20):  # Twitch accepte des lots de requêtes
            chunk = ids[i:i + 20]
            ops = [
                self._op("DropCampaignDetails", {"channelLogin": self.user_id, "dropID": cid})
                for cid in chunk
            ]
            for resp in self.gql(ops):
                camp = ((resp.get("data") or {}).get("user") or {}).get("dropCampaign")
                if camp:
                    details[camp["id"]] = camp

        out = []
        for c in summary:
            merged = {**c, **details.get(c["id"], {})}
            if merged.get("game"):
                out.append(normalize(merged))
        # Les campagnes terminées servent seulement à connaître les jeux « en pause ».
        out += [normalize(c) for c in expired]
        return out


def normalize(c: dict) -> dict:
    """Réduit une campagne brute au strict nécessaire pour le site et les alertes."""
    game = c.get("game") or {}
    allow = c.get("allow") or {}
    channels = []
    if allow.get("isEnabled", True) and allow.get("channels"):
        channels = [ch.get("name") or ch.get("displayName") for ch in allow["channels"]]
        channels = [x for x in channels if x]

    drops = []
    for d in c.get("timeBasedDrops") or []:
        drops.append({
            "name": d.get("name"),
            "minutes": d.get("requiredMinutesWatched") or 0,
            "startAt": d.get("startAt"),
            "endAt": d.get("endAt"),
            "rewards": [
                {
                    "name": (e.get("benefit") or {}).get("name"),
                    "image": (e.get("benefit") or {}).get("imageAssetURL"),
                }
                for e in (d.get("benefitEdges") or [])
            ],
        })
    drops.sort(key=lambda d: d["minutes"])

    return {
        "id": c["id"],
        "name": c.get("name"),
        "status": c.get("status"),
        "startAt": c.get("startAt"),
        "endAt": c.get("endAt"),
        "description": c.get("description") or "",
        "image": c.get("imageURL"),
        "detailsURL": c.get("detailsURL"),
        "linkURL": c.get("accountLinkURL"),
        "owner": (c.get("owner") or {}).get("name"),
        "game": {
            "id": str(game.get("id")),
            "name": game.get("displayName") or game.get("name"),
            "slug": game.get("slug"),
            "boxArt": game.get("boxArtURL"),
        },
        "channels": channels[:100],
        "channelCount": len(channels),
        "drops": drops,
    }
