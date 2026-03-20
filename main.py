"""
PariMalin API — Scraper de cotes ANJ
Récupère les cotes de tous les bookmakers ANJ français

Déploiement : Render.com (gratuit)
Lance : uvicorn main:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import json
import time
from datetime import datetime

app = FastAPI(title="PariMalin API", version="1.0.0")

# CORS — autorise PariMalin depuis GitHub Pages
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restreindre à ton URL GitHub Pages en prod
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── Cache en mémoire (évite de re-scraper à chaque requête) ──
CACHE = {}
CACHE_TTL = 300  # 5 minutes

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Referer": "https://www.google.fr/",
}

# ════════════════════════════════════════════════════════════
# SCRAPERS PAR BOOKMAKER
# Chaque scraper retourne une liste de matchs :
# [{
#   "equipe1": "PSG", "equipe2": "Lyon",
#   "competition": "Ligue 1", "heure": "21:00",
#   "cotes": {"1": 1.85, "X": 3.60, "2": 4.20}
# }]
# ════════════════════════════════════════════════════════════

async def scrape_betclic(sport: str, client: httpx.AsyncClient) -> list:
    """Betclic — API JSON publique"""
    sport_map = {
        "football": "FOOTBALL", "tennis": "TENNIS",
        "basket": "BASKETBALL", "rugby": "RUGBY_UNION"
    }
    try:
        url = f"https://www.betclic.fr/api/v2/competitions?sport={sport_map.get(sport,'FOOTBALL')}&limit=20"
        r = await client.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for comp in data.get("data", [])[:5]:
            comp_id = comp.get("id")
            r2 = await client.get(f"https://www.betclic.fr/api/v2/events?competitionId={comp_id}&limit=10", headers=HEADERS, timeout=10)
            if r2.status_code != 200:
                continue
            for event in r2.json().get("data", [])[:5]:
                odds = {}
                for market in event.get("markets", []):
                    if market.get("type") == "MATCH_WINNER":
                        for sel in market.get("selections", []):
                            label = sel.get("label", "")
                            price = sel.get("price", 0)
                            if "1" in label or event.get("team1", "") in label:
                                odds["1"] = price
                            elif "N" in label or "X" in label or "Nul" in label:
                                odds["X"] = price
                            elif "2" in label or event.get("team2", "") in label:
                                odds["2"] = price
                if len(odds) >= 2:
                    matches.append({
                        "equipe1": event.get("team1", "?"),
                        "equipe2": event.get("team2", "?"),
                        "competition": comp.get("name", ""),
                        "heure": event.get("startDate", "")[:16].replace("T", " "),
                        "cotes": odds
                    })
        return matches
    except Exception as e:
        print(f"Betclic error: {e}")
        return []


async def scrape_winamax(sport: str, client: httpx.AsyncClient) -> list:
    """Winamax — API JSON publique"""
    sport_map = {
        "football": "sport-1", "tennis": "sport-2",
        "basket": "sport-3", "rugby": "sport-12"
    }
    try:
        url = f"https://www.winamax.fr/api/matched-bets/sport/{sport_map.get(sport,'sport-1')}/top"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.winamax.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("matches", [])[:15]:
            odds = {}
            for market in event.get("mainOdds", {}).get("outcomes", []):
                label = market.get("label", "")
                price = market.get("odds", 0)
                if label in ["1", "Domicile"] or label == event.get("teams", ["",""])[0]:
                    odds["1"] = price
                elif label in ["N", "X", "Nul"]:
                    odds["X"] = price
                elif label in ["2", "Extérieur"] or label == event.get("teams", ["",""])[1]:
                    odds["2"] = price
            if len(odds) >= 2:
                teams = event.get("teams", ["?", "?"])
                matches.append({
                    "equipe1": teams[0] if len(teams) > 0 else "?",
                    "equipe2": teams[1] if len(teams) > 1 else "?",
                    "competition": event.get("competition", ""),
                    "heure": event.get("date", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Winamax error: {e}")
        return []


async def scrape_pmu(sport: str, client: httpx.AsyncClient) -> list:
    """PMU — API JSON publique"""
    try:
        url = "https://www.pmu.fr/api/v1/sport/events?sport=FOOTBALL&limit=20&offset=0"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.pmu.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("events", [])[:15]:
            odds = {}
            for outcome in event.get("odds", []):
                label = outcome.get("label", "")
                price = outcome.get("value", 0)
                if label == "1":
                    odds["1"] = price
                elif label in ["N", "X"]:
                    odds["X"] = price
                elif label == "2":
                    odds["2"] = price
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("homeTeam", "?"),
                    "equipe2": event.get("awayTeam", "?"),
                    "competition": event.get("competition", ""),
                    "heure": event.get("startDate", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"PMU error: {e}")
        return []


async def scrape_bwin(sport: str, client: httpx.AsyncClient) -> list:
    """Bwin — API interne"""
    sport_map = {"football": "fb", "tennis": "te", "basket": "ba"}
    try:
        url = f"https://sports.bwin.fr/api/sportsbetting/v1/betting/events?sport={sport_map.get(sport,'fb')}&category=fr&competition=&limit=20"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://sports.bwin.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("fixtures", [])[:15]:
            odds = {}
            for market in event.get("markets", []):
                if "Match Result" in market.get("name", "") or "1X2" in market.get("name", ""):
                    for sel in market.get("selections", []):
                        name = sel.get("name", {}).get("value", "")
                        price = sel.get("price", {}).get("odds", 0)
                        if name == "1": odds["1"] = price
                        elif name in ["X", "Draw"]: odds["X"] = price
                        elif name == "2": odds["2"] = price
            if len(odds) >= 2:
                name = event.get("name", {}).get("value", "? vs ?")
                parts = name.split(" vs ") if " vs " in name else name.split(" - ")
                matches.append({
                    "equipe1": parts[0].strip() if len(parts) > 0 else "?",
                    "equipe2": parts[1].strip() if len(parts) > 1 else "?",
                    "competition": event.get("league", {}).get("name", {}).get("value", ""),
                    "heure": event.get("startEventDate", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Bwin error: {e}")
        return []


async def scrape_vbet(sport: str, client: httpx.AsyncClient) -> list:
    """Vbet — API JSON"""
    sport_map = {"football": 1, "tennis": 2, "basket": 3, "rugby": 14}
    try:
        sport_id = sport_map.get(sport, 1)
        url = f"https://www.vbet.fr/api/top-events?sport_id={sport_id}&count=20"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.vbet.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("data", {}).get("events", [])[:15]:
            odds = {}
            for market in event.get("markets", []):
                if market.get("market_type") in ["1x2", "match_winner"]:
                    for outcome in market.get("outcomes", []):
                        otype = outcome.get("outcome_type", "")
                        price = outcome.get("price", 0)
                        if otype == "1": odds["1"] = price
                        elif otype in ["x", "X"]: odds["X"] = price
                        elif otype == "2": odds["2"] = price
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("home_team", "?"),
                    "equipe2": event.get("away_team", "?"),
                    "competition": event.get("league_name", ""),
                    "heure": event.get("start_time", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Vbet error: {e}")
        return []


async def scrape_netbet(sport: str, client: httpx.AsyncClient) -> list:
    """Netbet — API JSON"""
    sport_map = {"football": "soccer", "tennis": "tennis", "basket": "basketball"}
    try:
        url = f"https://www.netbet.fr/api/sports/{sport_map.get(sport,'soccer')}/featured"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.netbet.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("events", [])[:15]:
            odds = {}
            for market in event.get("markets", []):
                if market.get("type") in ["1x2", "match_result"]:
                    for sel in market.get("selections", []):
                        label = sel.get("label", "")
                        price = sel.get("odds", 0)
                        if label in ["1", "Home"]: odds["1"] = price
                        elif label in ["X", "Draw", "Nul"]: odds["X"] = price
                        elif label in ["2", "Away"]: odds["2"] = price
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("homeTeam", event.get("home", "?")),
                    "equipe2": event.get("awayTeam", event.get("away", "?")),
                    "competition": event.get("competition", ""),
                    "heure": event.get("startTime", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Netbet error: {e}")
        return []


async def scrape_unibet(sport: str, client: httpx.AsyncClient) -> list:
    """Unibet — API JSON"""
    sport_map = {"football": "football", "tennis": "tennis", "basket": "basketball"}
    try:
        url = f"https://www.unibet.fr/betting/sports/filter/{sport_map.get(sport,'football')}/all/matches/main.json"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.unibet.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("events", [])[:15]:
            odds = {}
            for betOffer in event.get("betOffers", []):
                if betOffer.get("betOfferType", {}).get("name") in ["1X2", "Match"]:
                    for outcome in betOffer.get("outcomes", []):
                        label = outcome.get("label", "")
                        price = outcome.get("odds", 0) / 1000  # Unibet odds * 1000
                        if label == "1": odds["1"] = round(price, 2)
                        elif label == "X": odds["X"] = round(price, 2)
                        elif label == "2": odds["2"] = round(price, 2)
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("homeName", "?"),
                    "equipe2": event.get("awayName", "?"),
                    "competition": event.get("groupName", ""),
                    "heure": event.get("start", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Unibet error: {e}")
        return []


async def scrape_parionssport(sport: str, client: httpx.AsyncClient) -> list:
    """Parions Sport (FDJ) — API JSON"""
    sport_map = {"football": "FOOTBALL", "tennis": "TENNIS", "basket": "BASKETBALL"}
    try:
        url = f"https://www.enligne.parionssport.fdj.fr/api/sportsbook/v2/events?sport={sport_map.get(sport,'FOOTBALL')}&limit=20"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.enligne.parionssport.fdj.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("data", [])[:15]:
            odds = {}
            for outcome in event.get("outcomes", []):
                label = str(outcome.get("label", ""))
                price = outcome.get("odds", 0)
                if label == "1": odds["1"] = price
                elif label in ["N", "X"]: odds["X"] = price
                elif label == "2": odds["2"] = price
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("homeTeamName", "?"),
                    "equipe2": event.get("awayTeamName", "?"),
                    "competition": event.get("competitionName", ""),
                    "heure": event.get("startDate", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"ParionsSport error: {e}")
        return []


async def scrape_betsson(sport: str, client: httpx.AsyncClient) -> list:
    """Betsson — API JSON"""
    sport_map = {"football": "football", "tennis": "tennis", "basket": "basketball"}
    try:
        url = f"https://www.betsson.fr/api/sportsbook/featured/{sport_map.get(sport,'football')}"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.betsson.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("events", [])[:15]:
            odds = {}
            for sel in event.get("betOffers", [{}])[0].get("outcomes", []):
                label = sel.get("label", "")
                price = sel.get("odds", 0)
                if label == "1": odds["1"] = price
                elif label in ["X", "Draw"]: odds["X"] = price
                elif label == "2": odds["2"] = price
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("homeName", "?"),
                    "equipe2": event.get("awayName", "?"),
                    "competition": event.get("league", ""),
                    "heure": event.get("start", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Betsson error: {e}")
        return []


async def scrape_genybet(sport: str, client: httpx.AsyncClient) -> list:
    """Genybet (filiale PMU)"""
    try:
        url = "https://www.genybet.fr/api/sport/events?sport=FOOTBALL&limit=20"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.genybet.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("events", [])[:15]:
            odds = {}
            for outcome in event.get("odds", []):
                label = outcome.get("label", "")
                price = outcome.get("value", 0)
                if label == "1": odds["1"] = price
                elif label in ["N", "X"]: odds["X"] = price
                elif label == "2": odds["2"] = price
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("homeTeam", "?"),
                    "equipe2": event.get("awayTeam", "?"),
                    "competition": event.get("competition", ""),
                    "heure": event.get("startDate", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Genybet error: {e}")
        return []


async def scrape_feelingbet(sport: str, client: httpx.AsyncClient) -> list:
    """Feelingbet"""
    try:
        url = "https://www.feelingbet.fr/api/events?sport=football&limit=20"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.feelingbet.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("data", [])[:15]:
            odds = {}
            for outcome in event.get("outcomes", []):
                label = outcome.get("label", "")
                price = outcome.get("price", 0)
                if label == "1": odds["1"] = price
                elif label in ["X", "N"]: odds["X"] = price
                elif label == "2": odds["2"] = price
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("home", "?"),
                    "equipe2": event.get("away", "?"),
                    "competition": event.get("competition", ""),
                    "heure": event.get("startTime", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Feelingbet error: {e}")
        return []


async def scrape_olybet(sport: str, client: httpx.AsyncClient) -> list:
    """Olybet"""
    try:
        url = "https://www.olybet.fr/api/sportsbook/events?sport=FOOTBALL&limit=20"
        r = await client.get(url, headers={**HEADERS, "Referer": "https://www.olybet.fr/"}, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        matches = []
        for event in data.get("events", [])[:15]:
            odds = {}
            for sel in event.get("selections", []):
                label = sel.get("label", "")
                price = sel.get("odds", 0)
                if label == "1": odds["1"] = price
                elif label in ["X", "N"]: odds["X"] = price
                elif label == "2": odds["2"] = price
            if len(odds) >= 2:
                matches.append({
                    "equipe1": event.get("homeName", "?"),
                    "equipe2": event.get("awayName", "?"),
                    "competition": event.get("leagueName", ""),
                    "heure": event.get("startTime", ""),
                    "cotes": odds
                })
        return matches
    except Exception as e:
        print(f"Olybet error: {e}")
        return []


# ════════════════════════════════════════════════════════════
# AGGREGATEUR — combine tous les bookmakers
# ════════════════════════════════════════════════════════════

SCRAPERS = {
    "Betclic":       scrape_betclic,
    "Winamax":       scrape_winamax,
    "PMU":           scrape_pmu,
    "Bwin":          scrape_bwin,
    "Vbet":          scrape_vbet,
    "Netbet":        scrape_netbet,
    "Unibet":        scrape_unibet,
    "Parions Sport": scrape_parionssport,
    "Betsson":       scrape_betsson,
    "Genybet":       scrape_genybet,
    "Feelingbet":    scrape_feelingbet,
    "Olybet":        scrape_olybet,
}


def normalize_name(name: str) -> str:
    """Normalise un nom d'équipe pour la comparaison"""
    return name.lower().strip().replace("-", " ").replace(".", "")


def match_events(events_by_bk: dict) -> list:
    """
    Croise les matchs de tous les bookmakers pour créer
    un objet unifié avec les cotes de chaque bookmaker
    """
    # Collecte tous les matchs avec leur bookmaker
    all_events = []
    for bk, events in events_by_bk.items():
        for ev in events:
            all_events.append({**ev, "_bk": bk})

    # Groupe par équipes (matching flou)
    grouped = {}
    for ev in all_events:
        key = f"{normalize_name(ev['equipe1'])}|{normalize_name(ev['equipe2'])}"
        key_rev = f"{normalize_name(ev['equipe2'])}|{normalize_name(ev['equipe1'])}"
        if key in grouped:
            grouped[key]["bookmakers"][ev["_bk"]] = ev["cotes"]
        elif key_rev in grouped:
            # Équipes inversées — inverser les cotes 1 et 2
            cotes = ev["cotes"].copy()
            if "1" in cotes and "2" in cotes:
                cotes["1"], cotes["2"] = cotes["2"], cotes["1"]
            grouped[key_rev]["bookmakers"][ev["_bk"]] = cotes
        else:
            grouped[key] = {
                "equipe1": ev["equipe1"],
                "equipe2": ev["equipe2"],
                "competition": ev["competition"],
                "heure": ev["heure"],
                "bookmakers": {ev["_bk"]: ev["cotes"]}
            }

    # Ne retourner que les matchs avec au moins 2 bookmakers
    return [v for v in grouped.values() if len(v["bookmakers"]) >= 2]


# ════════════════════════════════════════════════════════════
# ENDPOINTS API
# ════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {
        "name": "PariMalin API",
        "version": "1.0.0",
        "endpoints": ["/cotes", "/health"],
        "bookmakers": list(SCRAPERS.keys())
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/cotes")
async def get_cotes(sport: str = "football"):
    """
    Retourne les cotes de tous les bookmakers ANJ pour un sport donné.
    
    sports disponibles: football, tennis, basket, rugby
    """
    cache_key = f"{sport}"
    now = time.time()

    # Vérifier le cache
    if cache_key in CACHE and (now - CACHE[cache_key]["ts"]) < CACHE_TTL:
        cached = CACHE[cache_key]
        return {
            "sport": sport,
            "matchs": cached["matchs"],
            "bookmakers_disponibles": cached["bks_ok"],
            "bookmakers_erreur": cached["bks_err"],
            "cached": True,
            "cache_age_seconds": int(now - cached["ts"]),
            "updated_at": cached["updated_at"]
        }

    # Scraper tous les bookmakers en parallèle
    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=False,  # Ignore SSL errors
        timeout=httpx.Timeout(15.0)
    ) as client:
        tasks = {bk: scraper(sport, client) for bk, scraper in SCRAPERS.items()}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    events_by_bk = {}
    bks_ok = []
    bks_err = []

    for bk, result in zip(tasks.keys(), results):
        if isinstance(result, Exception) or not result:
            bks_err.append(bk)
        else:
            events_by_bk[bk] = result
            bks_ok.append(bk)

    # Croiser les matchs
    matchs = match_events(events_by_bk)

    # Mettre en cache
    CACHE[cache_key] = {
        "matchs": matchs,
        "bks_ok": bks_ok,
        "bks_err": bks_err,
        "ts": now,
        "updated_at": datetime.now().strftime("%H:%M:%S")
    }

    return {
        "sport": sport,
        "matchs": matchs,
        "bookmakers_disponibles": bks_ok,
        "bookmakers_erreur": bks_err,
        "cached": False,
        "updated_at": datetime.now().strftime("%H:%M:%S")
    }


@app.get("/cotes/force-refresh")
async def force_refresh(sport: str = "football"):
    """Force un nouveau scraping en ignorant le cache"""
    cache_key = f"{sport}"
    if cache_key in CACHE:
        del CACHE[cache_key]
    return await get_cotes(sport)
