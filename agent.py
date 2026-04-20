"""
Beskidzki Agent — prawdziwy agent z Groq LLM + tool use
"""
from __future__ import annotations

import json
import math
import time as _time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
import gpxpy
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- Config ----------

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
GPX_PATH = Path(__file__).parent / "mapy" / "GSB_E.gpx"
WAYPOINTS_PATH = Path(__file__).parent / "mapy" / "GSB_waypoints.gpx"


# ---------- Model ----------

@dataclass
class TrailPoint:
    lat: float
    lon: float
    ele: float = 0.0
    km: float = 0.0


# ---------- GPX ----------

def _haversine(a: TrailPoint, b: TrailPoint) -> float:
    R = 6371.0
    la1, la2 = math.radians(a.lat), math.radians(b.lat)
    dla = math.radians(b.lat - a.lat)
    dlo = math.radians(b.lon - a.lon)
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


_gpx_cache: list[TrailPoint] | None = None


def _load_gpx() -> list[TrailPoint]:
    global _gpx_cache
    if _gpx_cache is not None:
        return _gpx_cache
    with open(GPX_PATH, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    pts: list[TrailPoint] = []
    acc = 0.0
    prev = None
    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                tp = TrailPoint(p.latitude, p.longitude, p.elevation or 0.0)
                if prev:
                    acc += _haversine(prev, tp)
                tp.km = acc
                pts.append(tp)
                prev = tp
    _gpx_cache = pts
    return pts


_named_places: list[tuple[float, float, str]] | None = None


def _load_named_places() -> list[tuple[float, float, str]]:
    global _named_places
    if _named_places is not None:
        return _named_places
    if not WAYPOINTS_PATH.exists():
        _named_places = []
        return _named_places
    with open(WAYPOINTS_PATH, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    _named_places = [(w.latitude, w.longitude, w.name or "") for w in gpx.waypoints if w.name]
    return _named_places


# ---------- Lokalizacja ----------

def _geocode(name: str) -> tuple[float, float]:
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": name, "format": "json", "limit": 1, "countrycodes": "pl"},
        headers={"User-Agent": "BeskidzkiAgent/1.0"},
        timeout=10,
    )
    r.raise_for_status()
    results = r.json()
    if not results:
        raise ValueError(f"Nie znaleziono: '{name}'")
    return float(results[0]["lat"]), float(results[0]["lon"])


def _parse_location(loc: str) -> tuple[float, float]:
    parts = loc.split(",")
    if len(parts) == 2:
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            pass
    return _geocode(loc)


_geo_cache: dict[tuple, str] = {}


def _reverse_geocode(lat: float, lon: float) -> str:
    key = (round(lat, 3), round(lon, 3))
    if key in _geo_cache:
        return _geo_cache[key]
    places = _load_named_places()
    ref = TrailPoint(lat, lon)
    best_name, best_dist = None, 0.5
    for plat, plon, name in places:
        d = _haversine(ref, TrailPoint(plat, plon))
        if d < best_dist:
            best_dist = d
            best_name = name
    if best_name:
        _geo_cache[key] = best_name
        return best_name
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 14},
            headers={"User-Agent": "BeskidzkiAgent/1.0"},
            timeout=10, verify=False,
        )
        r.raise_for_status()
        data = r.json()
        addr = data.get("address", {})
        name = (data.get("name") or addr.get("peak") or addr.get("hamlet")
                or addr.get("village") or addr.get("town") or addr.get("city")
                or f"{lat:.3f},{lon:.3f}")
        _geo_cache[key] = name
        _time.sleep(1.1)
        return name
    except Exception:
        return f"{lat:.3f},{lon:.3f}"


# ---------- WMO ----------

WMO = {
    0: "bezchmurnie", 1: "gł. słonecznie", 2: "częściowe zachm.", 3: "zachmurzenie",
    45: "mgła", 48: "mgła osadz.", 51: "mżawka", 53: "mżawka", 55: "silna mżawka",
    61: "słaby deszcz", 63: "deszcz", 65: "ulewny deszcz",
    71: "słaby śnieg", 73: "śnieg", 75: "silny śnieg", 77: "krupa",
    80: "przelotny deszcz", 81: "przelotny deszcz", 82: "ulewa",
    85: "przelotny śnieg", 86: "silny przel. śnieg",
    95: "burza", 96: "burza z gradem", 99: "silna burza z gradem",
}

SURFACE_PL = {
    "asphalt": "asfalt", "concrete": "beton", "compacted": "ubita (żwir)",
    "fine_gravel": "drobny żwir", "gravel": "żwir", "ground": "grunt",
    "dirt": "ziemia", "earth": "ziemia", "grass": "trawa", "mud": "błoto",
    "sand": "piasek", "rock": "skała", "unpaved": "nieutwardzona",
    "paved": "utwardzona", "wood": "drewno", "roots": "korzenie",
    "paving_stones": "kostka brukowa", "cobblestone": "kocie łby",
}


# ============================================================
# NARZĘDZIA
# ============================================================

def tool_get_trail_segment(location: str, distance_km: float, start_hour: int = 7) -> dict:
    pts = _load_gpx()
    lat, lon = _parse_location(location)
    ref = TrailPoint(lat, lon)
    idx = min(range(len(pts)), key=lambda i: _haversine(pts[i], ref))
    start_km = pts[idx].km
    seg = [p for p in pts[idx:] if p.km <= start_km + distance_km]
    if not seg:
        return {"error": "Odcinek wykracza poza GPX"}

    dist_to_trail = _haversine(ref, pts[idx])
    ascent = int(sum(max(0, seg[i].ele - seg[i-1].ele) for i in range(1, len(seg))))
    length_km = round(seg[-1].km - seg[0].km, 1)

    n = 6
    step = max(1, len(seg) // (n - 1))
    samples = [seg[min(i * step, len(seg)-1)] for i in range(n)]

    points = []
    pace_kmh = 3.0
    for p in samples:
        km_into = round(p.km - seg[0].km, 1)
        eta_hour = start_hour + int(km_into / pace_kmh)
        points.append({
            "km": km_into,
            "lat": round(p.lat, 5),
            "lon": round(p.lon, 5),
            "ele": round(p.ele),
            "eta_hour": min(eta_hour, 23),
        })

    return {
        "start_name": _reverse_geocode(pts[idx].lat, pts[idx].lon),
        "dist_to_trail_km": round(dist_to_trail, 2),
        "length_km": length_km,
        "ascent_m": ascent,
        "points": points,
    }


def tool_get_weather(lat: float, lon: float, trip_date: str, eta_hour: int) -> dict:
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m,precipitation,wind_speed_10m,weather_code",
                "timezone": "Europe/Warsaw",
                "start_date": trip_date, "end_date": trip_date,
            },
            timeout=15,
        )
        r.raise_for_status()
        h = r.json()["hourly"]
        i = min(eta_hour, 23)
        return {
            "temp_c": h["temperature_2m"][i],
            "precip_mm": h["precipitation"][i],
            "wind_kmh": h["wind_speed_10m"][i],
            "sky": WMO.get(h["weather_code"][i], "?"),
        }
    except Exception as e:
        return {"error": str(e)}


def tool_get_surface_info(lat: float, lon: float) -> dict:
    try:
        from surface import get_surface
        info = get_surface(lat, lon)
        surface_raw = getattr(info, "surface", "ground") or "ground"
        return {
            "surface": SURFACE_PL.get(surface_raw.replace(" *", "").strip(), surface_raw),
            "sac_scale": getattr(info, "sac_scale", None),
            "sac_label": getattr(info, "sac_label", None),
        }
    except Exception as e:
        return {"surface": "grunt", "error": str(e)}


def tool_get_named_place(lat: float, lon: float) -> dict:
    return {"name": _reverse_geocode(lat, lon), "lat": lat, "lon": lon}


def tool_get_soil_condition(lat: float, lon: float, trip_date: str) -> dict:
    try:
        from soil import get_soil
        soil = get_soil(lat, lon, date.fromisoformat(trip_date))
        return {
            "level": soil.level,
            "precip_3d_mm": soil.precip_3d,
            "summary": soil.summary,
        }
    except Exception as e:
        return {"level": "nieznany", "error": str(e)}


def tool_get_pois(lat_min: float, lon_min: float, lat_max: float, lon_max: float) -> dict:
    try:
        bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
        query = f"""
[out:json][timeout:15];
(
  node["natural"="peak"]({bbox});
  node["natural"="saddle"]({bbox});
  node["mountain_pass"="yes"]({bbox});
);
out body;
"""
        r = requests.post("https://overpass-api.de/api/interpreter",
                          data={"data": query}, timeout=20, verify=False)
        r.raise_for_status()
        elements = r.json().get("elements", [])
        pois = []
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("name:pl") or ""
            kind = "przełęcz" if (tags.get("mountain_pass") == "yes" or
                                   tags.get("natural") == "saddle") else "szczyt"
            if name:
                pois.append({"name": name, "kind": kind,
                             "lat": el["lat"], "lon": el["lon"]})
        return {"pois": pois[:15]}
    except Exception as e:
        return {"pois": [], "error": str(e)}


TOOL_MAP = {
    "get_trail_segment": tool_get_trail_segment,
    "get_weather": tool_get_weather,
    "get_surface_info": tool_get_surface_info,
    "get_named_place": tool_get_named_place,
    "get_soil_condition": tool_get_soil_condition,
    "get_pois": tool_get_pois,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_trail_segment",
            "description": "Wycina odcinek trasy GSB od podanej lokalizacji. Zwraca punkty z km, lat, lon, ele, eta_hour.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "Nazwa miejscowości lub 'lat,lon'"},
                    "distance_km": {"type": "number"},
                    "start_hour": {"type": "integer"},
                },
                "required": ["location", "distance_km"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Prognoza pogody dla punktu (lat, lon) w danym dniu i godzinie.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                    "trip_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "eta_hour": {"type": "integer"},
                },
                "required": ["lat", "lon", "trip_date", "eta_hour"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_surface_info",
            "description": "Nawierzchnia i trudność trasy (SAC scale) dla punktu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                },
                "required": ["lat", "lon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_named_place",
            "description": "Nazwa miejsca (szczyt, schronisko, miejscowość) dla współrzędnych.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                },
                "required": ["lat", "lon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_soil_condition",
            "description": "Wilgotność gleby — czy szlak będzie błotnisty.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                    "trip_date": {"type": "string"},
                },
                "required": ["lat", "lon", "trip_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pois",
            "description": "Szczyty i przełęcze z OSM dla obszaru trasy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat_min": {"type": "number"},
                    "lon_min": {"type": "number"},
                    "lat_max": {"type": "number"},
                    "lon_max": {"type": "number"},
                },
                "required": ["lat_min", "lon_min", "lat_max", "lon_max"],
            },
        },
    },
]

SYSTEM_PROMPT = """Jesteś Beskidzkim Agentem GSB — doświadczonym przewodnikiem górskim.

Pomagasz planować jednodniowe odcinki Głównego Szlaku Beskidzkiego (GSB).

Workflow:
1. Wywołaj get_trail_segment → dostaniesz punkty trasy
2. Dla każdego punktu wywołaj get_weather
3. Dla 2-3 kluczowych punktów wywołaj get_surface_info i get_named_place
4. Wywołaj get_soil_condition dla środka trasy
5. Opcjonalnie get_pois dla obszaru trasy

Następnie napisz odpowiedź po polsku zawierającą:
- Nagłówek: lokalizacja, data, dystans, przewyższenie
- Tabelę: km | ETA | °C | mm | wiatr | niebo | podłoże | miejsce
- Ocenę śliskości i warunków
- Konkretną rekomendację (idź / skróć / zostań w domu)
- Ostrzeżenia jeśli są

Bądź konkretny. Używaj emoji sparingly."""


# ============================================================
# PĘTLA AGENTA
# ============================================================

def run_agent(location: str, distance_km: float, trip_date: date,
              start_hour: int = 7, groq_api_key: str = "") -> str:
    import os
    api_key = groq_api_key or os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("Brak GROQ_API_KEY w zmiennych środowiskowych")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Zaplanuj wędrówkę GSB:\n"
            f"Start: {location}\n"
            f"Dystans: {distance_km} km\n"
            f"Data: {trip_date.isoformat()}\n"
            f"Godzina startu: {start_hour}:00"
        )},
    ]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for _ in range(15):
        payload = {
            "model": GROQ_MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "max_tokens": 4096,
            "temperature": 0.2,
        }

        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        messages.append(msg)

        if not msg.get("tool_calls"):
            return msg.get("content", "Brak odpowiedzi agenta.")

        for tc in msg["tool_calls"]:
            fn_name = tc["function"]["name"]
            fn_args = json.loads(tc["function"]["arguments"])
            fn = TOOL_MAP.get(fn_name)
            result = fn(**fn_args) if fn else {"error": f"Nieznane narzędzie: {fn_name}"}
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })

    return "Agent przekroczył limit rund."


# ============================================================
# KOMPATYBILNOŚĆ Z BOT.PY
# ============================================================

def run(gpx_path, location, distance_km, day, samples=5, start_hour=7, pace_kmh=3.0):
    import os
    response = run_agent(
        location=location,
        distance_km=distance_km,
        trip_date=day,
        start_hour=start_hour,
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
    )
    return {"agent_response": response, "date": day.isoformat(), "rows": [], "summary": ""}


def _render(r: dict) -> str:
    return r.get("agent_response", "Brak odpowiedzi.")


def _narrative(rows: list) -> str:
    return ""


def _slickness(row: dict) -> str:
    return ""


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse, os
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="location", required=True)
    ap.add_argument("--distance", type=float, required=True)
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--start-hour", type=int, default=7)
    a = ap.parse_args()
    print(run_agent(
        location=a.location,
        distance_km=a.distance,
        trip_date=date.fromisoformat(a.date),
        start_hour=a.start_hour,
    ))