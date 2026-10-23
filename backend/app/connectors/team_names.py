"""Team-name matching across sources.

Understat ("Manchester United"), football-data ("Man United"), Polymarket
("Manchester United FC") and FPL ("Man Utd") all spell teams differently.
Understat is the canonical name.  Matching is alias table first, then a
normalised-token comparison.
"""

from __future__ import annotations

import re
import unicodedata

# alias (normalised) → Understat name
ALIASES: dict[str, str] = {
    # EPL
    "man united": "Manchester United", "man utd": "Manchester United",
    "manchester united": "Manchester United",
    "man city": "Manchester City", "manchester city": "Manchester City",
    "spurs": "Tottenham", "tottenham hotspur": "Tottenham", "tottenham": "Tottenham",
    "wolves": "Wolverhampton Wanderers", "wolverhampton": "Wolverhampton Wanderers",
    "nott m forest": "Nottingham Forest", "nottm forest": "Nottingham Forest",
    "nottingham forest": "Nottingham Forest", "nott'm forest": "Nottingham Forest",
    "newcastle": "Newcastle United", "newcastle united": "Newcastle United",
    "west ham": "West Ham", "west ham united": "West Ham",
    "brighton": "Brighton", "brighton hove albion": "Brighton",
    "sheffield united": "Sheffield United", "sheffield utd": "Sheffield United",
    "leeds": "Leeds", "leeds united": "Leeds",
    "leicester": "Leicester", "leicester city": "Leicester",
    "ipswich": "Ipswich", "ipswich town": "Ipswich",
    "luton": "Luton", "luton town": "Luton",
    "afc bournemouth": "Bournemouth", "bournemouth": "Bournemouth",
    "norwich": "Norwich", "norwich city": "Norwich",
    "cardiff": "Cardiff", "cardiff city": "Cardiff",
    "huddersfield": "Huddersfield", "huddersfield town": "Huddersfield",
    "west brom": "West Bromwich Albion", "west bromwich albion": "West Bromwich Albion",
    "stoke": "Stoke", "stoke city": "Stoke",
    "swansea": "Swansea", "swansea city": "Swansea",
    "hull": "Hull", "hull city": "Hull",
    "coventry": "Coventry", "coventry city": "Coventry",
    "sunderland": "Sunderland", "sunderland afc": "Sunderland",
    "burnley": "Burnley",
    # La Liga
    "ath madrid": "Atletico Madrid", "atletico madrid": "Atletico Madrid",
    "club atletico de madrid": "Atletico Madrid", "atletico de madrid": "Atletico Madrid",
    "ath bilbao": "Athletic Club", "athletic club": "Athletic Club",
    "athletic bilbao": "Athletic Club",
    "betis": "Real Betis", "real betis": "Real Betis", "real betis balompie": "Real Betis",
    "sociedad": "Real Sociedad", "real sociedad": "Real Sociedad",
    "celta": "Celta Vigo", "celta vigo": "Celta Vigo", "rc celta": "Celta Vigo",
    "rc celta de vigo": "Celta Vigo", "celta de vigo": "Celta Vigo",
    "vallecano": "Rayo Vallecano", "rayo vallecano": "Rayo Vallecano",
    "espanol": "Espanyol", "espanyol": "Espanyol", "rcd espanyol": "Espanyol",
    "rcd espanyol de barcelona": "Espanyol",
    "alaves": "Alaves", "deportivo alaves": "Alaves",
    "la coruna": "Deportivo La Coruna", "deportivo la coruna": "Deportivo La Coruna",
    "rc deportivo": "Deportivo La Coruna", "rc deportivo a coruna": "Deportivo La Coruna",
    "deportivo a coruna": "Deportivo La Coruna", "deportivo": "Deportivo La Coruna",
    "santander": "Racing Santander", "racing santander": "Racing Santander",
    "racing club": "Racing Santander", "real racing club": "Racing Santander",
    "malaga": "Malaga", "malaga cf": "Malaga",
    "sevilla": "Sevilla", "sevilla fc": "Sevilla",
    "valencia": "Valencia", "valencia cf": "Valencia",
    "villarreal": "Villarreal", "villarreal cf": "Villarreal",
    "osasuna": "Osasuna", "ca osasuna": "Osasuna",
    "getafe": "Getafe", "getafe cf": "Getafe",
    "levante": "Levante", "levante ud": "Levante",
    "elche": "Elche", "elche cf": "Elche",
    "girona": "Girona", "girona fc": "Girona",
    "mallorca": "Mallorca", "rcd mallorca": "Mallorca",
    "las palmas": "Las Palmas", "ud las palmas": "Las Palmas",
    "leganes": "Leganes", "cd leganes": "Leganes",
    "valladolid": "Real Valladolid", "real valladolid": "Real Valladolid",
    "cadiz": "Cadiz", "cadiz cf": "Cadiz",
    "granada": "Granada", "granada cf": "Granada",
    "almeria": "Almeria", "ud almeria": "Almeria",
    "huesca": "SD Huesca", "sd huesca": "SD Huesca",
    "eibar": "Eibar", "sd eibar": "Eibar",
    "real oviedo": "Real Oviedo", "oviedo": "Real Oviedo",
    "barcelona": "Barcelona", "fc barcelona": "Barcelona",
    "real madrid": "Real Madrid", "real madrid cf": "Real Madrid",
}

_SUFFIXES = {"fc", "cf", "afc", "sc", "ud", "cd", "sd", "rcd", "ca", "club", "de", "the", "and"}


def normalise(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " ").replace(".", "")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name: str) -> set[str]:
    return {t for t in normalise(name).split() if t not in _SUFFIXES}


def to_canonical(name: str, candidates: list[str] | set[str] | None = None) -> str | None:
    """Map a source team name to the Understat name.

    With ``candidates`` (the Understat teams of the league-season), falls back
    to the candidate sharing the most name tokens; None when nothing matches.
    """
    n = normalise(name)
    if n in ALIASES:
        alias = ALIASES[n]
        if candidates is None or alias in candidates:
            return alias
    stripped = " ".join(t for t in n.split() if t not in _SUFFIXES)
    if stripped in ALIASES and (candidates is None or ALIASES[stripped] in candidates):
        return ALIASES[stripped]
    if not candidates:
        return None
    for c in candidates:
        if normalise(c) in (n, stripped):
            return c
    toks = _tokens(name)
    best, best_score = None, 0.0
    for c in candidates:
        ct = _tokens(c)
        if not ct or not toks:
            continue
        score = len(toks & ct) / len(toks | ct)
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.5 else None
