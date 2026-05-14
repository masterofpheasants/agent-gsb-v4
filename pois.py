"""
pois.py - Pobieranie obiektów POI z OpenStreetMap (Overpass API)
Zapytania wysyłane per kategoria (unika 406).
"""
from __future__ import annotations

import logging
import math
import time
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

DIST_ON_TRAIL_M = 300
DIST_NEARBY_M = 10000  # testowo 10 km

# ============================================================
# KATEGORIE
# ============================================================

CATEGORIES = {
    "noclegi": {
        "label": "🏨 Noclegi",
        "queries": [
            'node["tourism"="hotel"]({bbox});',
            'node["tourism"="guest_house"]({bbox});',
            'node["tourism"="hostel"]({bbox});',
            'node["tourism"="apartment"]({bbox});',
            'node["tourism"="camp_site"]({bbox});',
            'node["tourism"="alpine_hut"]({bbox});',
            'node["tourism"="wilderness_hut"]({bbox});',
            'way["tourism"="alpine_hut"]({bbox});',
            'way["tourism"="wilderness_hut"]({bbox});',
            'way["building:use"="civic"]["tourism"]({bbox});',
        ],
    },
    "jedzenie": {
        "label": "🍽️ Jedzenie",
        "queries": [
            'node["amenity"="restaurant"]({bbox});',
            'node["amenity"="cafe"]({bbox});',
            'node["amenity"="bar"]({bbox});',
            'node["amenity"="pub"]({bbox});',
            'node["amenity"="fast_food"]({bbox});',
            'node["amenity"="biergarten"]({bbox});',
            'way["amenity"="restaurant"]({bbox});',
            'way["amenity"="cafe"]({bbox});',
            'way["amenity"="bar"]({bbox});',
        ],
    },
    "zakupy": {
        "label": "🛒 Zakupy",
        "queries": [
            'node["shop"="supermarket"]({bbox});',
            'node["shop"="convenience"]({bbox});',
            'node["shop"="general"]({bbox});',
            'node["shop"="greengrocer"]({bbox});',
            'node["shop"="outdoor"]({bbox});',
            'node["amenity"="atm"]({bbox});',
            'node["amenity"="bank"]({bbox});',
            'node["amenity"="vending_machine"]({bbox});',
        ],
    },
    "woda": {
        "label": "💧 Woda",
        "queries": [
            'node["natural"="spring"]({bbox});',
            'node["amenity"="drinking_water"]({bbox});',
            'node["man_made"="water_well"]({bbox});',
            'node["amenity"="water_point"]({bbox});',
            'node["emergency"="drinking_water"]({bbox});',
        ],
    },
    "bezpieczenstwo": {
        "label": "🆘 Bezpieczeństwo",
        "queries": [
            'node["emergency"="mountain_rescue"]({bbox});',
            'node["emergency"="assembly_point"]({bbox});',
            'node["emergency"="first_aid_kit"]({bbox});',
            'node["emergency"="rescue_box"]({bbox});',
            'node["amenity"="police"]({bbox});',
            'node["amenity"="ranger_station"]({bbox});',
        ],
    },
    "higiena": {
        "label": "🚻 Higiena/zdrowie",
        "queries": [
            'node["amenity"="toilets"]({bbox});',
            'node["amenity"="pharmacy"]({bbox});',
            'node["amenity"="hospital"]({bbox});',
            'node["amenity"="clinic"]({bbox});',
            'node["building"="toilets"]({bbox});',
        ],
    },
    "transport": {
        "label": "🚌 Transport",
        "queries": [
            'node["highway"="bus_stop"]({bbox});',
            'node["amenity"="bus_station"]({bbox});',
            'node["amenity"="fuel"]({bbox});',
        ],
    },
    "odpoczynek": {
        "label": "🪑 Odpoczynek",
        "queries": [
            'node["leisure"="picnic_table"]({bbox});',
            'node["amenity"="bench"]({bbox});',
            'node["amenity"="shelter"]({bbox});',
            'node["amenity"="bbq"]({bbox});',
            'node["tourism"="viewpoint"]({bbox});',
            'node["tourism"="picnic_site"]({bbox});',
        ],
    },
}

TAG_ICONS = {
    "tourism=hotel": "🏨",
    "tourism=guest_house": "🏡",
    "tourism=hostel": "🛏️",
    "tourism=apartment": "🏠",
    "tourism=camp_site": "⛺",
    "tourism=alpine_hut": "🏔️",
    "tourism=wilderness_hut": "🛖",
    "amenity=shelter": "🏚️",
    "amenity=restaurant": "🍽️",
    "amenity=cafe": "☕",
    "amenity=bar": "🍺",
    "amenity=pub": "🍻",
    "amenity=fast_food": "🍔",
    "amenity=biergarten": "🍻",
    "shop=supermarket": "🛒",
    "shop=convenience": "🛒",
    "shop=general": "🛒",
    "shop=greengrocer": "🥬",
    "shop=outdoor": "🎒",
    "amenity=atm": "💳",
    "amenity=bank": "🏦",
    "amenity=vending_machine": "🎰",
    "natural=spring": "💧",
    "amenity=drinking_water": "🚰",
    "man_made=water_well": "🪣",
    "amenity=water_point": "🚰",
    "emergency=mountain_rescue": "🆘",
    "emergency=first_aid_kit": "🩹",
    "emergency=rescue_box": "📦",
    "emergency=assembly_point": "🔴",
    "amenity=police": "👮",
    "amenity=ranger_station": "🌲",
    "amenity=toilets": "🚻",
    "building=toilets": "🚻",
    "amenity=pharmacy": "💊",
    "amenity=hospital": "🏥",
    "amenity=clinic": "🏥",
    "highway=bus_stop": "🚌",
    "amenity=bus_station": "🚌",
    "amenity=fuel": "⛽",
    "leisure=picnic_table": "🪑",
    "amenity=bench": "🪑",
    "amenity=bbq": "🔥",
    "tourism=viewpoint": "👁️",
    "tourism=picnic_site": "🧺",
}


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dla = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    h = math.sin(dla/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(dlo/2)**2
    return 2 * R * math.asin(math.sqrt(h))


def _nearest_on_trail(trail_pts, lat, lon) -> tuple[float, float]:
    best_dist, best_km = float("inf"), 0.0
    for p in trail_pts:
        d = _haversine_m(p["lat"], p["lon"], lat, lon)
        if d < best_dist:
            best_dist = d
            best_km = p["km"]
    return best_dist, best_km


def _get_icon(tags: dict) -> str:
    for key in ("tourism", "amenity", "shop", "natural", "man_made",
                "emergency", "highway", "leisure", "building"):
        val = tags.get(key)
        if val:
            icon = TAG_ICONS.get(f"{key}={val}")
            if icon:
                return icon
    return "📌"


def _get_category_for_tags(tags: dict) -> tuple[str, str]:
    """Zwraca (category_id, category_label)."""
    for cat_id, cat_data in CATEGORIES.items():
        for q in cat_data["queries"]:
            # Wyciągnij key=value z query string
            import re
            m = re.search(r'"([^"]+)"="([^"]+)"', q)
            if m:
                key, val = m.group(1), m.group(2)
                if tags.get(key) == val:
                    return cat_id, cat_data["label"]
    return "inne", "📌 Inne"


def _fetch_category(bbox: str, cat_id: str) -> list[dict]:
    """Pobiera POI dla jednej kategorii."""
    cat_data = CATEGORIES.get(cat_id, {})
    queries = cat_data.get("queries", [])
    if not queries:
        return []

    lines = "\n  ".join(q.replace("{bbox}", bbox) for q in queries)
    query = f"[out:json][timeout:20];\n(\n  {lines}\n);\nout center;"

    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=25,
            verify=False,
        )
        if resp.status_code != 200:
            logging.warning(f"Overpass {cat_id}: status {resp.status_code}")
            return []
        elements = resp.json().get("elements", [])
        logging.warning(f"Overpass {cat_id}: {len(elements)} elements")
        return elements
    except Exception as e:
        logging.warning(f"Overpass {cat_id} error: {e}")
        return []


def enrich_with_next_distance(pois: list[dict]) -> list[dict]:
    """Dla sklepów i jedzenia dodaje info o odległości do następnego obiektu."""
    target_cats = {"jedzenie", "zakupy"}
    relevant = [p for p in pois if p.get("category") in target_cats]

    for i, poi in enumerate(relevant):
        next_same = next(
            (p for p in relevant[i+1:] if p["category"] == poi["category"]),
            None
        )
        if next_same:
            diff_km = round(next_same["km"] - poi["km"], 1)
            poi["next_same_km"] = diff_km
            poi["next_same_name"] = next_same["name"]
        else:
            poi["next_same_km"] = None
            poi["next_same_name"] = None

    return pois


def fetch_pois(trail_pts: list, categories: list[str]) -> list[dict]:
    """
    Pobiera POI z Overpass dla wybranych kategorii.
    Zapytania wysyłane osobno per kategoria.
    """
    if not trail_pts or not categories:
        return []

    lats = [p["lat"] for p in trail_pts]
    lons = [p["lon"] for p in trail_pts]
    margin = 0.01
    bbox = (f"{min(lats)-margin},{min(lons)-margin},"
            f"{max(lats)+margin},{max(lons)+margin}")

    pois = []
    seen = set()

    for cat_id in categories:
        elements = _fetch_category(bbox, cat_id)
        time.sleep(0.5)  # grzeczność wobec Overpass

        for el in elements:
            if el["type"] == "node":
                lat, lon = el.get("lat"), el.get("lon")
            else:
                center = el.get("center", {})
                lat, lon = center.get("lat"), center.get("lon")

            if not lat or not lon:
                continue

            key = (round(lat, 4), round(lon, 4))
            if key in seen:
                continue
            seen.add(key)

            tags = el.get("tags", {})
            dist_m, km_on_trail = _nearest_on_trail(trail_pts, lat, lon)

            if dist_m > DIST_NEARBY_M:
                continue

            name = tags.get("name") or tags.get("name:pl") or ""
            proximity = "na szlaku" if dist_m <= DIST_ON_TRAIL_M else "w pobliżu"
            icon = _get_icon(tags)
            cat_label = CATEGORIES.get(cat_id, {}).get("label", "📌 Inne")

            extra = []
            if tags.get("opening_hours"):
                extra.append(f"⏰ {tags['opening_hours']}")
            if tags.get("phone") or tags.get("contact:phone"):
                extra.append(f"📞 {tags.get('phone') or tags.get('contact:phone')}")
            if tags.get("website") or tags.get("contact:website"):
                url = tags.get("website") or tags.get("contact:website")
                extra.append(f"🌐 {url}")

            pois.append({
                "name": name or _default_name(cat_id),
                "icon": icon,
                "category": cat_id,
                "category_label": cat_label,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "dist_m": round(dist_m),
                "km": round(km_on_trail, 1),
                "proximity": proximity,
                "extra": extra,
            })

    logging.warning(f"POI bbox: {bbox}, seen: {len(seen)}, kept: {len(pois)}")
    pois.sort(key=lambda x: (x["km"], x["dist_m"]))
    return enrich_with_next_distance(pois)


def _default_name(category: str) -> str:
    defaults = {
        "noclegi": "Nocleg",
        "jedzenie": "Restauracja/bar",
        "zakupy": "Sklep",
        "woda": "Źródło wody",
        "bezpieczenstwo": "Punkt ratunkowy",
        "higiena": "Toaleta/apteka",
        "transport": "Przystanek",
        "odpoczynek": "Miejsce odpoczynku",
    }
    return defaults.get(category, "Obiekt")


def get_category_labels() -> dict[str, str]:
    return {k: v["label"] for k, v in CATEGORIES.items()}