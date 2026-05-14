"""
pois.py - Pobieranie obiektów POI z OpenStreetMap (Overpass API)
Kategorie: noclegi, jedzenie, zakupy, woda, bezpieczeństwo, higiena, transport, odpoczynek
"""
from __future__ import annotations

import math
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Dystanse
DIST_ON_TRAIL_M = 300   # "na szlaku"
DIST_NEARBY_M = 1000    # "w pobliżu"

# ============================================================
# DEFINICJE KATEGORII
# ============================================================

CATEGORIES = {
    "noclegi": {
        "label": "🏨 Noclegi",
        "tags": [
            ("tourism", "hotel"),
            ("tourism", "guest_house"),
            ("tourism", "hostel"),
            ("tourism", "apartment"),
            ("tourism", "camp_site"),
            ("tourism", "alpine_hut"),
            ("tourism", "wilderness_hut"),
            ("amenity", "shelter"),
            ("shelter_type", "*"),
        ],
    },
    "jedzenie": {
        "label": "🍽️ Jedzenie",
        "tags": [
            ("amenity", "restaurant"),
            ("amenity", "cafe"),
            ("amenity", "bar"),
            ("amenity", "pub"),
            ("amenity", "fast_food"),
            ("amenity", "biergarten"),
            ("amenity", "food_court"),
        ],
    },
    "zakupy": {
        "label": "🛒 Zakupy",
        "tags": [
            ("shop", "*"),
            ("amenity", "atm"),
            ("amenity", "bank"),
            ("amenity", "vending_machine"),
            ("amenity", "marketplace"),
        ],
    },
    "woda": {
        "label": "💧 Woda",
        "tags": [
            ("natural", "spring"),
            ("amenity", "drinking_water"),
            ("man_made", "water_well"),
            ("amenity", "water_point"),
            ("emergency", "drinking_water"),
        ],
    },
    "bezpieczenstwo": {
        "label": "🆘 Bezpieczeństwo",
        "tags": [
            ("emergency", "mountain_rescue"),
            ("emergency", "assembly_point"),
            ("emergency", "first_aid_kit"),
            ("emergency", "rescue_box"),
            ("amenity", "police"),
            ("amenity", "ranger_station"),
        ],
    },
    "higiena": {
        "label": "🚻 Higiena/zdrowie",
        "tags": [
            ("amenity", "toilets"),
            ("amenity", "pharmacy"),
            ("amenity", "hospital"),
            ("amenity", "clinic"),
            ("amenity", "doctors"),
            ("building", "toilets"),
        ],
    },
    "transport": {
        "label": "🚌 Transport",
        "tags": [
            ("highway", "bus_stop"),
            ("amenity", "bus_station"),
            ("public_transport", "stop_position"),
            ("amenity", "fuel"),
        ],
    },
    "odpoczynek": {
        "label": "🪑 Odpoczynek",
        "tags": [
            ("leisure", "picnic_table"),
            ("amenity", "bench"),
            ("amenity", "shelter"),
            ("amenity", "bbq"),
            ("leisure", "nature_reserve"),
            ("tourism", "viewpoint"),
            ("tourism", "picnic_site"),
        ],
    },
}

# Ikony per tag
TAG_ICONS = {
    ("tourism", "hotel"): "🏨",
    ("tourism", "guest_house"): "🏡",
    ("tourism", "hostel"): "🛏️",
    ("tourism", "apartment"): "🏠",
    ("tourism", "camp_site"): "⛺",
    ("tourism", "alpine_hut"): "🏔️",
    ("tourism", "wilderness_hut"): "🛖",
    ("amenity", "shelter"): "🏚️",
    ("amenity", "restaurant"): "🍽️",
    ("amenity", "cafe"): "☕",
    ("amenity", "bar"): "🍺",
    ("amenity", "pub"): "🍻",
    ("amenity", "fast_food"): "🍔",
    ("shop", "*"): "🛒",
    ("amenity", "atm"): "💳",
    ("amenity", "bank"): "🏦",
    ("amenity", "vending_machine"): "🎰",
    ("natural", "spring"): "💧",
    ("amenity", "drinking_water"): "🚰",
    ("man_made", "water_well"): "🪣",
    ("emergency", "mountain_rescue"): "🆘",
    ("emergency", "first_aid_kit"): "🩹",
    ("emergency", "rescue_box"): "📦",
    ("amenity", "police"): "👮",
    ("amenity", "toilets"): "🚻",
    ("amenity", "pharmacy"): "💊",
    ("amenity", "hospital"): "🏥",
    ("highway", "bus_stop"): "🚌",
    ("amenity", "fuel"): "⛽",
    ("leisure", "picnic_table"): "🪑",
    ("amenity", "bench"): "🪑",
    ("amenity", "bbq"): "🔥",
    ("tourism", "viewpoint"): "👁️",
    ("tourism", "picnic_site"): "🧺",
    ("amenity", "ranger_station"): "🌲",
}


# ============================================================
# OVERPASS QUERY
# ============================================================

def _build_query(bbox: str, categories: list[str]) -> str:
    selected_tags = []
    for cat in categories:
        if cat in CATEGORIES:
            for key, value in CATEGORIES[cat]["tags"]:
                if value == "*":
                    selected_tags.append(f'node["{key}"]({bbox});')
                    selected_tags.append(f'way["{key}"]({bbox});')
                else:
                    selected_tags.append(f'node["{key}"="{value}"]({bbox});')
                    selected_tags.append(f'way["{key}"="{value}"]({bbox});')

    tags_str = "\n  ".join(selected_tags)
    return f"""
[out:json][timeout:20];
(
  {tags_str}
);
out center;
"""


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dla = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _nearest_point_on_trail(trail_pts: list, lat: float, lon: float) -> tuple[float, float]:
    """Zwraca (dist_m, km_on_trail) do najbliższego punktu na szlaku."""
    best_dist = float("inf")
    best_km = 0.0
    for p in trail_pts:
        d = _haversine_m(p["lat"], p["lon"], lat, lon)
        if d < best_dist:
            best_dist = d
            best_km = p["km"]
    return best_dist, best_km


def _get_icon(tags: dict) -> str:
    for (key, value), icon in TAG_ICONS.items():
        if value == "*":
            if key in tags:
                return icon
        else:
            if tags.get(key) == value:
                return icon
    return "📌"


def _get_category(tags: dict) -> str:
    for cat_id, cat_data in CATEGORIES.items():
        for key, value in cat_data["tags"]:
            if value == "*":
                if key in tags:
                    return cat_id
            else:
                if tags.get(key) == value:
                    return cat_id
    return "inne"


# ============================================================
# GŁÓWNA FUNKCJA
# ============================================================

def fetch_pois(trail_pts: list, categories: list[str]) -> list[dict]:
    """
    Pobiera POI z Overpass dla wybranych kategorii.
    trail_pts: lista dict {lat, lon, km} — punkty trasy
    categories: lista kluczy z CATEGORIES
    Zwraca listę POI posortowaną po km na szlaku.
    """
    if not trail_pts or not categories:
        return []

    lats = [p["lat"] for p in trail_pts]
    lons = [p["lon"] for p in trail_pts]
    # Powiększ bbox o ~1 km
    margin = 0.01
    bbox = f"{min(lats)-margin},{min(lons)-margin},{max(lats)+margin},{max(lons)+margin}"

    query = _build_query(bbox, categories)

    try:
        resp = requests.post(
            OVERPASS_URL, 
            data={"data": query}, 
            timeout=25, 
            verify=False,
            headers={"Accept": "application/json"}
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except Exception as e:
        return [{"error": str(e)}]

    pois = []
    seen = set()

    for el in elements:
        # Pobierz współrzędne (node lub way z center)
        if el["type"] == "node":
            lat = el.get("lat")
            lon = el.get("lon")
        else:
            center = el.get("center", {})
            lat = center.get("lat")
            lon = center.get("lon")

        if not lat or not lon:
            continue

        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:pl") or ""

        # Deduplikacja po nazwie i pozycji
        key = (round(lat, 4), round(lon, 4))
        if key in seen:
            continue
        seen.add(key)

        dist_m, km_on_trail = _nearest_point_on_trail(trail_pts, lat, lon)

        if dist_m > DIST_NEARBY_M:
            continue

        proximity = "na szlaku" if dist_m <= DIST_ON_TRAIL_M else "w pobliżu"
        icon = _get_icon(tags)
        category = _get_category(tags)

        # Dodatkowe info
        extra = []
        if tags.get("opening_hours"):
            extra.append(f"⏰ {tags['opening_hours']}")
        if tags.get("phone") or tags.get("contact:phone"):
            extra.append(f"📞 {tags.get('phone') or tags.get('contact:phone')}")
        if tags.get("website") or tags.get("contact:website"):
            extra.append(f"🌐 {tags.get('website') or tags.get('contact:website')}")

        pois.append({
            "name": name or _default_name(tags, category),
            "icon": icon,
            "category": category,
            "category_label": CATEGORIES.get(category, {}).get("label", "📌 Inne"),
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "dist_m": round(dist_m),
            "km": round(km_on_trail, 1),
            "proximity": proximity,
            "extra": extra,
            "tags": {k: v for k, v in tags.items() if k in (
                "name", "opening_hours", "phone", "website",
                "amenity", "tourism", "shop", "emergency", "natural",
                "contact:phone", "contact:website"
            )},
        })

    # Sortuj po km na szlaku, potem po dystansie
    pois.sort(key=lambda x: (x["km"], x["dist_m"]))
    return pois


def _default_name(tags: dict, category: str) -> str:
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
    """Zwraca słownik {id: label} dla wszystkich kategorii."""
    return {k: v["label"] for k, v in CATEGORIES.items()}
