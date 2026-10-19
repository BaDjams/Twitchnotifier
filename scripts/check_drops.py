#!/usr/bin/env python3
"""Relevé des campagnes de drops Twitch + alertes.

Lancé toutes les 30 min par GitHub Actions :
  1. récupère toutes les campagnes actives/annoncées (API interne Twitch) ;
  2. met à jour la liste des jeux détectés (docs/data/games.json) ;
  3. compare avec l'état précédent (state/state.json) et alerte pour les jeux suivis ;
  4. écrit docs/data/*.json pour le site.

Usage local :
  TWITCH_TOKEN=xxx python scripts/check_drops.py --dry-run
  python scripts/check_drops.py --fixture tests/sample_campaigns.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import Event, dispatch, send_public, topic_for  # noqa: E402
from twitch import ApiChanged, AuthError, TwitchClient, TwitchError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "docs" / "config.json"
DATA = ROOT / "docs" / "data"
STATE = ROOT / "state" / "state.json"

DEFAULT_CONFIG = {
    "timezone": "Europe/Paris",
    "followed_games": {},
    "notify": {"new_game": True, "announced": True, "started": True, "ending_soon_hours": 24},
    "channels": {"ntfy": True, "discord": True, "email": True},
    # Alertes publiques : un sujet ntfy par jeu, auquel chaque visiteur s'abonne.
    # Laisser ntfy_prefix vide pour désactiver.
    "public": {"ntfy_prefix": "", "ntfy_server": "https://ntfy.sh", "announced": True},
    "api": {},
}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def site_url() -> str | None:
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return None


def fmt_minutes(m: int) -> str:
    h, mn = divmod(int(m), 60)
    return f"{h}h{mn:02d}" if h and mn else (f"{h}h" if h else f"{mn} min")


def describe(c: dict, tz: ZoneInfo) -> str:
    start, end = parse(c["startAt"]), parse(c["endAt"])
    fmt = "%d/%m %Hh%M"
    lines = [c["name"] or ""]
    if start and end:
        lines.append(f"Du {start.astimezone(tz).strftime(fmt)} au {end.astimezone(tz).strftime(fmt)}")
    if c["drops"]:
        lines.append(f"{len(c['drops'])} drop(s) · jusqu'à {fmt_minutes(max(d['minutes'] for d in c['drops']))} de visionnage")
        rewards = [r["name"] for d in c["drops"] for r in d["rewards"] if r.get("name")]
        if rewards:
            more = f" (+{len(rewards) - 5})" if len(rewards) > 5 else ""
            lines.append("Récompenses : " + ", ".join(dict.fromkeys(rewards[:5])) + more)
    lines.append(f"Chaînes : {', '.join(c['channels'][:5])}{'…' if c['channelCount'] > 5 else ''}"
                 if c["channelCount"] else "Chaînes : toutes celles qui ont les drops activés")
    return "\n".join(lines)


def campaign_url(c: dict) -> str:
    return f"https://www.twitch.tv/drops/campaigns?dropID={c['id']}"


def prefix_of(cfg: dict) -> str:
    return ((cfg.get("public") or {}).get("ntfy_prefix") or "").strip()


def remember_past_games(past, games, cfg) -> None:
    """Jeux dont les campagnes sont finies : on les garde au catalogue (sans alerte)."""
    prefix = prefix_of(cfg)
    for c in past:
        g = dict(c["game"])
        if prefix:
            g["topic"] = topic_for(prefix, g.get("slug") or g["id"])
        known = games.setdefault(g["id"], {**g, "firstSeen": c.get("startAt") or c.get("endAt")})
        known.update({k: v for k, v in g.items() if v})
        if (c.get("endAt") or "") > (known.get("lastCampaignEnd") or ""):
            known["lastCampaignEnd"] = c["endAt"]


def is_followed(game: dict, followed: dict) -> bool:
    """Suivi par identifiant Twitch, ou par nom pour les jeux ajoutés à la main."""
    if game["id"] in followed:
        return True
    names = {str(v).strip().lower() for v in followed.values()}
    return (game.get("name") or "").strip().lower() in names


def build_events(campaigns, cfg, state, games, now, tz):
    """Renvoie (alertes privées du propriétaire, alertes publiques [(sujet, alerte)])."""
    events: list[Event] = []
    public: list[tuple[str, Event]] = []
    pub_cfg = {**DEFAULT_CONFIG["public"], **(cfg.get("public") or {})}
    prefix = (pub_cfg.get("ntfy_prefix") or "").strip()
    notify = {**DEFAULT_CONFIG["notify"], **(cfg.get("notify") or {})}
    followed = cfg.get("followed_games") or {}
    first_run = not state.get("initialized")
    notified = state.setdefault("notified", {})

    # 1. Jeux détectés automatiquement
    for c in campaigns:
        g = c["game"]
        if prefix:
            g["topic"] = topic_for(prefix, g.get("slug") or g["id"])
        known = games.get(g["id"])
        if known is None:
            games[g["id"]] = {**g, "firstSeen": iso(now), "lastSeen": iso(now)}
            if not first_run and notify.get("new_game"):
                events.append(Event(
                    kind="new_game", title=g["name"],
                    message=describe(c, tz)
                    + ("\n★ Jeu déjà suivi" if is_followed(g, followed) else "\nCoche-le sur ton tableau de bord pour le suivre."),
                    url=campaign_url(c), image=g.get("boxArt"),
                ))
            if not first_run and prefix:
                public.append((topic_for(prefix, "nouveaux-jeux"), Event(
                    kind="new_game", title=g["name"], message=describe(c, tz),
                    url=campaign_url(c), image=g.get("boxArt"))))
        else:
            known.update({k: v for k, v in g.items() if v})
            known["lastSeen"] = iso(now)
        if (c.get("endAt") or "") > (games[g["id"]].get("lastCampaignEnd") or ""):
            games[g["id"]]["lastCampaignEnd"] = c["endAt"]

    # 2. Campagnes des jeux suivis
    hours = notify.get("ending_soon_hours") or 0
    for c in sorted(campaigns, key=lambda x: x["startAt"] or ""):
        entry = notified.setdefault(c["id"], {"kinds": [], "seen": iso(now)})
        entry["seen"] = iso(now)
        kinds = set(entry["kinds"])
        start, end = parse(c["startAt"]), parse(c["endAt"])
        started = c["status"] == "ACTIVE" or (start is not None and start <= now)
        title = f"{c['game']['name']}"
        image = c.get("image") or c["game"].get("boxArt")

        # Alertes publiques : tous les jeux, sur le sujet du jeu.
        if prefix:
            topic = c["game"]["topic"]
            if started and "p_started" not in kinds:
                kinds.update({"p_started", "p_announced"})
                public.append((topic, Event(kind="started", title=title, message=describe(c, tz),
                                            url=campaign_url(c), image=image)))
            elif not started and "p_announced" not in kinds:
                kinds.add("p_announced")
                if pub_cfg.get("announced"):
                    public.append((topic, Event(kind="announced", title=title, message=describe(c, tz),
                                                url=campaign_url(c), image=image)))

        if not is_followed(c["game"], followed):
            entry["kinds"] = sorted(kinds)
            continue

        if started and "started" not in kinds:
            kinds.update({"started", "announced"})
            if notify.get("started"):
                events.append(Event(kind="started", title=title, message=describe(c, tz),
                                    url=campaign_url(c), image=image))
        elif not started and "announced" not in kinds:
            kinds.add("announced")
            if notify.get("announced"):
                events.append(Event(kind="announced", title=title, message=describe(c, tz),
                                    url=campaign_url(c), image=image))

        if hours and started and end and "ending" not in kinds and end - now <= timedelta(hours=hours):
            kinds.add("ending")
            left = end - now
            h = int(left.total_seconds() // 3600)
            events.append(Event(kind="ending", title=title,
                                message=f"Plus que {h} h pour finir : {c['name']}\n"
                                        f"Fin le {end.astimezone(tz).strftime('%d/%m à %Hh%M')}",
                                url=campaign_url(c), image=image))
        entry["kinds"] = sorted(kinds)

    # On oublie les campagnes disparues depuis plus de 30 jours.
    cutoff = now - timedelta(days=30)
    for cid in [k for k, v in notified.items() if (parse(v.get("seen")) or now) < cutoff]:
        del notified[cid]

    if first_run:
        state["initialized"] = True
        events = [Event(kind="info", title="Surveillance active",
                        message=f"{len(games)} jeux et {len(campaigns)} campagnes détectés. "
                                f"Choisis tes jeux sur ton tableau de bord.",
                        url=site_url())]
        public = []  # pas de déluge au premier passage
    return events, public


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="affiche les alertes sans les envoyer")
    ap.add_argument("--fixture", help="fichier JSON de campagnes (test hors ligne)")
    ap.add_argument("--now", help="date simulée ISO (test)")
    ap.add_argument("--test-alert", action="store_true", help="envoie une alerte de test et quitte")
    args = ap.parse_args()

    cfg = {**DEFAULT_CONFIG, **load(CONFIG, {})}
    tz = ZoneInfo(cfg.get("timezone") or "Europe/Paris")
    now = parse(args.now) if args.now else datetime.now(timezone.utc)
    state = load(STATE, {})
    games = load(DATA / "games.json", {})

    if args.test_alert:
        errs = dispatch([Event(kind="info", title="Test", message="Les alertes fonctionnent 👍",
                               url=site_url())], cfg, site_url(), args.dry_run)
        return 1 if errs else 0

    status = {"checkedAt": iso(now), "ok": True, "error": None}
    try:
        if args.fixture:
            campaigns = load(Path(args.fixture), [])
        else:
            client = TwitchClient(os.environ.get("TWITCH_TOKEN", ""), cfg.get("api"))
            client.validate()
            campaigns = client.campaigns()
    except (AuthError, ApiChanged, TwitchError, OSError) as exc:
        hint = {
            AuthError: "Relance « python scripts/login.py » puis mets à jour le secret TWITCH_TOKEN.",
            ApiChanged: "Twitch a changé son API : récupère les nouveaux hashes (voir README) "
                        "et mets-les dans docs/config.json → api.hashes.",
        }.get(type(exc), "Erreur réseau ou côté Twitch, nouvel essai au prochain passage.")
        print(f"ERREUR : {exc}\n{hint}", file=sys.stderr)
        status.update(ok=False, error=f"{exc} — {hint}")
        prev = load(DATA / "status.json", {})
        status["lastSuccess"] = prev.get("lastSuccess")
        save(DATA / "status.json", status)
        # Alerte au plus toutes les 12 h, sauf erreurs réseau passagères (pas d'alerte).
        last = parse(state.get("lastErrorAlert"))
        if not isinstance(exc, OSError) and (not last or now - last > timedelta(hours=12)):
            state["lastErrorAlert"] = iso(now)
            dispatch([Event(kind="error", title="Le relevé des drops ne marche plus",
                            message=f"{exc}\n{hint}", url=site_url())], cfg, site_url(), args.dry_run)
        save(STATE, state)
        return 1

    past = [c for c in campaigns if c.get("status") == "EXPIRED"]
    campaigns = [c for c in campaigns if c.get("status") != "EXPIRED"]
    remember_past_games(past, games, cfg)
    events, public = build_events(campaigns, cfg, state, games, now, tz)
    state.pop("lastErrorAlert", None)

    campaigns.sort(key=lambda c: c["endAt"] or "")
    pub_cfg = {**DEFAULT_CONFIG["public"], **(cfg.get("public") or {})}
    prefix = (pub_cfg.get("ntfy_prefix") or "").strip()
    save(DATA / "campaigns.json", {
        "updatedAt": iso(now),
        "ntfy": {"server": pub_cfg["ntfy_server"], "prefix": prefix,
                 "newGames": topic_for(prefix, "nouveaux-jeux")} if prefix else None,
        "campaigns": campaigns,
    })
    save(DATA / "games.json", dict(sorted(games.items(), key=lambda kv: (kv[1].get("name") or "").lower())))
    status["lastSuccess"] = iso(now)
    save(DATA / "status.json", status)

    errors = dispatch(events, cfg, site_url(), args.dry_run)
    errors += send_public(public, cfg, site_url(), args.dry_run)
    save(STATE, state)
    print(f"{len(campaigns)} campagnes, {len(games)} jeux connus, {len(events)} alerte(s).")
    if errors:
        print("Canaux en échec :", "; ".join(errors), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
