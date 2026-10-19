"""Envoi des alertes : ntfy, Discord (webhook) et email (SMTP).

Chaque canal ne s'active que si ses secrets sont présents ET qu'il est coché
dans docs/config.json -> "channels".
"""
from __future__ import annotations

import html
import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

TWITCH_PURPLE = 0x9146FF
KIND_LABEL = {
    "started": "🟢 Drops lancés",
    "announced": "📅 Drops annoncés",
    "ending": "⏳ Fin proche",
    "new_game": "🆕 Nouveau jeu avec drops",
    "error": "⚠️ Problème",
    "info": "ℹ️ Info",
}
KIND_TAG = {
    "started": "green_circle",
    "announced": "calendar",
    "ending": "hourglass",
    "new_game": "new",
    "error": "warning",
    "info": "information_source",
}


def _post_json(url: str, payload: dict, headers: dict | None = None) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "twitch-drops-watch", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


class Event(dict):
    """Une alerte : kind, title, message, url, image."""


def enabled_channels(cfg: dict) -> dict[str, bool]:
    wanted = (cfg.get("channels") or {})
    env = os.environ
    return {
        "ntfy": wanted.get("ntfy", True) and bool(env.get("NTFY_TOPIC")),
        "discord": wanted.get("discord", True) and bool(env.get("DISCORD_WEBHOOK")),
        "email": wanted.get("email", True) and bool(env.get("SMTP_HOST") and env.get("MAIL_TO")),
    }


# -- ntfy public (un sujet par jeu, pour les visiteurs du site) ----------------
def topic_for(prefix: str, key: str) -> str:
    """Nom de sujet ntfy valide : [A-Za-z0-9_-], 64 caractères max."""
    import re
    clean = re.sub(r"[^a-z0-9_-]+", "-", (key or "").lower()).strip("-") or "jeu"
    return f"{prefix}-{clean}"[:64].rstrip("-")


def send_public(items: list[tuple[str, Event]], cfg: dict, site_url: str | None, dry_run: bool = False) -> list[str]:
    """Publie chaque alerte sur le sujet public de son jeu. Renvoie les erreurs."""
    if not items:
        return []
    server = ((cfg.get("public") or {}).get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    headers = {}
    if os.environ.get("NTFY_TOKEN"):  # un compte ntfy relève les limites quotidiennes
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
    errors = []
    for topic, e in items:
        if dry_run:
            print(f"[dry-run] public → {topic} : {KIND_LABEL[e['kind']]} · {e['title']}")
            continue
        p = {
            "topic": topic,
            "title": f"{KIND_LABEL[e['kind']]} · {e['title']}",
            "message": e["message"],
            "tags": [KIND_TAG[e["kind"]]],
            "priority": 4 if e["kind"] == "started" else 3,
        }
        if e.get("url") or site_url:
            p["click"] = e.get("url") or site_url
        if e.get("image"):
            p["attach"] = e["image"]
        try:
            _post_json(server, p, headers)
        except Exception as exc:
            errors.append(f"{topic} : {exc}")
    if not dry_run:
        print(f"ntfy public : {len(items) - len(errors)}/{len(items)} publication(s)")
    return errors


# -- ntfy -----------------------------------------------------------------------
def send_ntfy(events: list[Event], site_url: str | None) -> None:
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    topic = os.environ["NTFY_TOPIC"]
    headers = {}
    if os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"

    # Au-delà de 8 alertes d'un coup, on envoie un résumé pour ne pas spammer.
    if len(events) > 8:
        lines = [f"{KIND_LABEL[e['kind']]} · {e['title']}" for e in events]
        payloads = [{
            "title": f"Twitch Drops : {len(events)} nouveautés",
            "message": "\n".join(lines[:30]),
            "tags": ["video_game"],
            **({"click": site_url} if site_url else {}),
        }]
    else:
        payloads = []
        for e in events:
            p = {
                "title": f"{KIND_LABEL[e['kind']]} · {e['title']}",
                "message": e["message"],
                "tags": [KIND_TAG[e["kind"]]],
                "priority": 4 if e["kind"] in ("started", "error") else 3,
            }
            if e.get("url") or site_url:
                p["click"] = e.get("url") or site_url
            if e.get("image"):
                p["attach"] = e["image"]
            payloads.append(p)

    for p in payloads:
        _post_json(server, {"topic": topic, **p}, headers)


# -- Discord --------------------------------------------------------------------
def send_discord(events: list[Event], site_url: str | None) -> None:
    url = os.environ["DISCORD_WEBHOOK"]
    embeds = []
    for e in events:
        emb = {
            "title": f"{KIND_LABEL[e['kind']]} · {e['title']}"[:256],
            "description": e["message"][:4000],
            "color": 0xE5484D if e["kind"] == "error" else TWITCH_PURPLE,
        }
        if e.get("url") or site_url:
            emb["url"] = e.get("url") or site_url
        if e.get("image"):
            emb["thumbnail"] = {"url": e["image"]}
        embeds.append(emb)
    for i in range(0, len(embeds), 10):  # 10 embeds max par message Discord
        _post_json(url, {"username": "Twitch Drops", "embeds": embeds[i:i + 10]})


# -- Email ----------------------------------------------------------------------
def send_email(events: list[Event], site_url: str | None) -> None:
    env = os.environ
    host = env["SMTP_HOST"]
    port = int(env.get("SMTP_PORT") or 587)
    user, password = env.get("SMTP_USER"), env.get("SMTP_PASS")
    sender = env.get("MAIL_FROM") or user or "twitch-drops@localhost"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = env["MAIL_TO"]
    if len(events) == 1:
        msg["Subject"] = f"{KIND_LABEL[events[0]['kind']]} · {events[0]['title']}"
    else:
        msg["Subject"] = f"Twitch Drops : {len(events)} nouveautés"

    text = "\n\n".join(f"{KIND_LABEL[e['kind']]} · {e['title']}\n{e['message']}" for e in events)
    if site_url:
        text += f"\n\nTout voir : {site_url}"
    msg.set_content(text)

    rows = []
    for e in events:
        img = (f'<img src="{html.escape(e["image"])}" width="60" style="border-radius:6px">'
               if e.get("image") else "")
        link = e.get("url") or site_url
        title = html.escape(e["title"])
        if link:
            title = f'<a href="{html.escape(link)}" style="color:#9146ff">{title}</a>'
        rows.append(
            f'<tr><td style="padding:10px;vertical-align:top">{img}</td>'
            f'<td style="padding:10px"><div style="font-size:12px;color:#888">{KIND_LABEL[e["kind"]]}</div>'
            f'<div style="font-size:16px;font-weight:600">{title}</div>'
            f'<div style="white-space:pre-line;color:#444">{html.escape(e["message"])}</div></td></tr>'
        )
    footer = (f'<p><a href="{html.escape(site_url)}" style="color:#9146ff">Ouvrir mon tableau de bord</a></p>'
              if site_url else "")
    msg.add_alternative(
        f'<div style="font-family:system-ui,sans-serif"><table>{"".join(rows)}</table>{footer}</div>',
        subtype="html",
    )

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            if user:
                s.login(user, password or "")
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            if user:
                s.login(user, password or "")
            s.send_message(msg)


SENDERS = {"ntfy": send_ntfy, "discord": send_discord, "email": send_email}


def dispatch(events: list[Event], cfg: dict, site_url: str | None, dry_run: bool = False) -> list[str]:
    """Envoie les alertes sur chaque canal actif. Renvoie la liste des erreurs."""
    if not events:
        return []
    errors = []
    channels = enabled_channels(cfg)
    if dry_run:
        for e in events:
            print(f"[dry-run] {KIND_LABEL[e['kind']]} · {e['title']} — {e['message'].splitlines()[0] if e['message'] else ''}")
        print(f"[dry-run] canaux actifs : {[k for k, v in channels.items() if v] or 'aucun'}")
        return []
    if not any(channels.values()):
        print("Aucun canal d'alerte configuré (secrets manquants ?) :", len(events), "alerte(s) non envoyée(s).")
    for name, on in channels.items():
        if not on:
            continue
        try:
            SENDERS[name](events, site_url)
            print(f"{name} : {len(events)} alerte(s) envoyée(s)")
        except Exception as exc:  # un canal en panne ne bloque pas les autres
            errors.append(f"{name} : {exc}")
            print(f"Échec {name} : {exc}")
    return errors
