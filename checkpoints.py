"""
checkpoints.py - Punkty kontrolne GSB
Wymagają zdjęcia podczas przejścia szlaku.
"""
from __future__ import annotations
import math

CHECKPOINTS = [
    {"id": 1,  "name": "Ustroń Zdrój – stacja PKP",                "lat": 49.7211108, "lon": 18.8156131},
    {"id": 2,  "name": "Równica – Tablica GSB przy Gościńcu PTTK", "lat": 49.7197608, "lon": 18.8524600},
    {"id": 3,  "name": "Stożek – Tablica GSB obok schroniska PTTK","lat": 49.6041178, "lon": 18.8232058},
    {"id": 4,  "name": "Barania Góra – Tablica GSB obok wieży",     "lat": 49.6113814, "lon": 19.0106206},
    {"id": 5,  "name": "Abrahamów – Stacja Turystyczna",            "lat": 49.5770428, "lon": 19.1858706},
    {"id": 6,  "name": "Mędralowa – znak informacyjny",             "lat": 49.6137144, "lon": 19.4673783},
    {"id": 7,  "name": "Babia Góra – znak informacyjny",            "lat": 49.5730867, "lon": 19.5294461},
    {"id": 8,  "name": "Naroże – tablica przy schronie",            "lat": 49.6477381, "lon": 19.7035742},
    {"id": 9,  "name": "Turbacz – punkt triangulacyjny",            "lat": 49.5433608, "lon": 20.1184972},
    {"id": 10, "name": "Lubań – wieża widokowa",                    "lat": 49.4893653, "lon": 20.3390064},
    {"id": 11, "name": "Hala Łabowska – schronisko PTTK",          "lat": 49.4725900, "lon": 20.8104431},
    {"id": 12, "name": "Rotunda – cmentarz wojenny",                "lat": 49.4744789, "lon": 21.2352411},
    {"id": 13, "name": "Pustelnia św. Jana z Dukli",                "lat": 49.5151397, "lon": 21.6761772},
    {"id": 14, "name": "Wisłoczek – Studencka Baza Namiotowa",      "lat": 49.5124719, "lon": 21.8828500},
    {"id": 15, "name": "Wahalowski Wierch – szczyt 666 m",          "lat": 49.3700836, "lon": 22.0446744},
    {"id": 16, "name": "Okrąglik – szczyt",                         "lat": 49.1470497, "lon": 22.3662522},
    {"id": 17, "name": "Brzegi Górne – tablica GSB",                "lat": 49.1416472, "lon": 22.5686044},
    {"id": 18, "name": "Halicz – szczyt",                           "lat": 49.0721589, "lon": 22.7688269},
    {"id": 19, "name": "Wołosate – początek/koniec GSB",            "lat": 49.0664144, "lon": 22.6803839},
]

CHECKPOINT_RADIUS_KM = 1.0  # punkt kontrolny w zasięgu jeśli trasa przechodzi w odległości 1 km


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dla = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    h = math.sin(dla/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(dlo/2)**2
    return 2 * R * math.asin(math.sqrt(h))


def find_checkpoints_on_route(trail_pts: list[dict]) -> list[dict]:
    """
    Sprawdza które punkty kontrolne leżą na trasie (+/- 1 km).
    trail_pts: lista dict {lat, lon, km}
    Zwraca listę punktów kontrolnych z polem km_on_trail.
    """
    found = []
    for cp in CHECKPOINTS:
        best_dist = float("inf")
        best_km = 0.0
        for p in trail_pts:
            d = _haversine_km(p["lat"], p["lon"], cp["lat"], cp["lon"])
            if d < best_dist:
                best_dist = d
                best_km = p["km"]
        if best_dist <= CHECKPOINT_RADIUS_KM:
            found.append({
                "id": cp["id"],
                "name": cp["name"],
                "lat": cp["lat"],
                "lon": cp["lon"],
                "dist_km": round(best_dist, 2),
                "km_on_trail": round(best_km, 1),
            })
    # Sortuj po km na trasie
    found.sort(key=lambda x: x["km_on_trail"])
    return found
