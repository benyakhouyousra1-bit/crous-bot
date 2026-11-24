#!/usr/bin/env python3
"""
Bot de veille CROUS -> Discord
Surveille les logements CROUS dispo sur Paris et envoie une notif Discord
dès qu'un nouveau logement apparaît.

Installation :  pip install requests
Lancement    :  DISCORD_WEBHOOK="https://discord.com/api/webhooks/..." python3 crous_bot.py
"""

import json
import os
import random
import sys
import time
from datetime import datetime

import requests

# ================== CONFIG ==================
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK", "")
INTERVALLE = int(os.environ.get("INTERVALLE", "60"))   # secondes entre 2 vérifs
MENTION = os.environ.get("MENTION", "@everyone")      # "" pour ne pas pinger
FICHIER_VUS = "logements_vus.json"
DUREE = int(os.environ.get("DUREE", "0"))  # durée max en secondes (0 = infini)

TOOL_ID = 48  # identifiant de la campagne CROUS (celui de ton lien)
# Zone de ton lien : Paris intra-muros
BOUNDS = {"ouest": 2.224122, "nord": 48.902156, "est": 2.4697602, "sud": 48.8155755}
# ============================================

API_URL = f"https://trouverunlogement.lescrous.fr/api/fr/search/{TOOL_ID}"
SITE_URL = (
    f"https://trouverunlogement.lescrous.fr/tools/{TOOL_ID}/search?bounds="
    f"{BOUNDS['ouest']}_{BOUNDS['nord']}_{BOUNDS['est']}_{BOUNDS['sud']}&locationName=Paris"
)
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Origin": "https://trouverunlogement.lescrous.fr",
    "Referer": SITE_URL,
}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def payload(page):
    return {
        "idTool": TOOL_ID,
        "need_aggregation": False,
        "page": page,
        "pageSize": 24,
        "sector": None,
        "occupationModes": [],
        "location": [
            {"lon": BOUNDS["ouest"], "lat": BOUNDS["nord"]},
            {"lon": BOUNDS["est"], "lat": BOUNDS["sud"]},
        ],
        "residence": None,
        "precision": 6,
        "equipment": [],
        "price": {"max": 10000000},
        "area": {"min": 0},
        "toolMechanism": "residual",
    }


def recuperer_logements():
    """Renvoie la liste de tous les logements dispo dans la zone."""
    tous, page = [], 1
    while True:
        r = requests.post(API_URL, json=payload(page), headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json().get("results", {})
        items = data.get("items", []) or []
        tous.extend(items)
        total = (data.get("total") or {}).get("value", len(tous))
        if not items or len(tous) >= total:
            return tous
        page += 1


def prix(l):
    rent = l.get("rent") or {}
    mn, mx = rent.get("min"), rent.get("max")
    if mn is None:
        return "?"
    mn, mx = mn / 100 if mn > 5000 else mn, (mx / 100 if mx and mx > 5000 else mx)
    return f"{mn:.0f} €" if not mx or mx == mn else f"{mn:.0f} – {mx:.0f} €"


def surface(l):
    a = l.get("area") or {}
    mn, mx = a.get("min"), a.get("max")
    if mn is None:
        return "?"
    return f"{mn} m²" if not mx or mx == mn else f"{mn} – {mx} m²"


def embed(l):
    res = l.get("residence") or {}
    lid = l.get("id")
    return {
        "title": f"🏠 {l.get('label') or 'Logement'} — {res.get('label', '')}"[:256],
        "url": f"https://trouverunlogement.lescrous.fr/tools/{TOOL_ID}/accommodations/{lid}",
        "color": 0x2ECC71,
        "fields": [
            {"name": "💶 Loyer", "value": prix(l), "inline": True},
            {"name": "📐 Surface", "value": surface(l), "inline": True},
            {"name": "📍 Adresse", "value": (res.get("address") or "?")[:1024], "inline": False},
        ],
        "footer": {"text": f"ID {lid} • fonce réserver !"},
        "timestamp": datetime.utcnow().isoformat(),
    }


def discord(content=None, embeds=None):
    body = {"username": "Veille CROUS Paris"}
    if content:
        body["content"] = content
    if embeds:
        body["embeds"] = embeds
    for _ in range(3):
        r = requests.post(WEBHOOK_URL, json=body, timeout=20)
        if r.status_code == 429:  # rate limit Discord
            time.sleep(float(r.json().get("retry_after", 2)))
            continue
        r.raise_for_status()
        return


def charger_vus():
    try:
        with open(FICHIER_VUS) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def sauver_vus(vus):
    with open(FICHIER_VUS, "w") as f:
        json.dump(sorted(vus), f)


def main():
    if "discord.com/api/webhooks" not in WEBHOOK_URL:
        sys.exit("❌ Mets l'URL de ton webhook Discord (variable DISCORD_WEBHOOK).")

    premier_lancement = not os.path.exists(FICHIER_VUS)
    vus = charger_vus()
    if premier_lancement:
        discord(f"✅ Bot lancé — vérification toutes les {INTERVALLE}s.\n🔗 {SITE_URL}")
    erreurs = 0
    debut = time.time()

    while not DUREE or time.time() - debut < DUREE:
        try:
            logements = recuperer_logements()
            ids = {l.get("id") for l in logements}
            nouveaux = [l for l in logements if l.get("id") not in vus]
            log(f"{len(logements)} logement(s) dispo, {len(nouveaux)} nouveau(x)")

            if nouveaux:
                # Discord : max 10 embeds par message
                for i in range(0, len(nouveaux), 10):
                    lot = nouveaux[i:i + 10]
                    txt = f"{MENTION} 🚨 **{len(nouveaux)} nouveau(x) logement(s) CROUS à Paris !**" if i == 0 else None
                    discord(txt, [embed(l) for l in lot])

            # on ne garde que ce qui est encore en ligne -> si un logement
            # disparaît puis revient, tu seras renotifié
            vus = ids
            sauver_vus(vus)
            if erreurs >= 5:
                discord("✅ Connexion au CROUS rétablie.")
            erreurs = 0

        except Exception as e:
            erreurs += 1
            log(f"Erreur ({erreurs}) : {e}")
            if erreurs == 5:
                try:
                    discord(f"⚠️ Le bot n'arrive plus à joindre le CROUS : `{e}`")
                except Exception:
                    pass

        time.sleep(INTERVALLE + random.randint(0, 15))


if __name__ == "__main__":
    main()
