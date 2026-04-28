"""
Beskidzki Agent GSB
Python zbiera dane (GPX, pogoda, nawierzchnia, gleba, nazwy).
LLM tylko ocenia i rekomenduje.
ETA obliczane formułą Naismitha z korektą kondycji i nawierzchni.
Obsługuje: groq / claude / off
"""
from __future__ import annotations

import json
import math
import time as _time
import unicodedata
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
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"
GPX_PATH = Path(__file__).parent / "mapy" / "GSB_E.gpx"
WAYPOINTS_PATH = Path(__file__).parent / "mapy" / "GSB_waypoints.gpx"
SPLIT_THRESHOLD_KM = 30
N_SAMPLES = 6

# Naismith constants
NAISMITH_FLAT_KMH = 4.0
NAISMITH_ASCENT_MIN_PER_100M = 10
NAISMITH_DESCENT_MIN_PER_100M = 7.5
BREAK_MIN_PER_HOUR = 10

# Mnożniki kondycji
FITNESS_MULTIPLIER = {
    "bardzo dobra": 0.85,
    "dobra": 1.0,
    "przeciętna": 1.20,
    "niska": 1.40,
    "nieznana": 1.10,
}

# Mnożniki nawierzchni
SURFACE_MULTIPLIER = {
    "asfalt": 0.90,
    "beton": 0.90,
    "ubita (żwir)": 0.95,
    "kostka brukowa": 0.95,
    "żwir": 1.00,
    "grunt": 1.05,
    "ziemia": 1.05,
    "trawa": 1.10,
    "skała": 1.20,
    "korzenie": 1.20,
    "błoto": 1.25,
    "nieutwardzona": 1.05,
}


# ---------- Model ----------

@dataclass
class TrailPoint:
    lat: float
    lon: float
    ele: float = 0.0
    km: float = 0.0


# ---------- Naismith ETA ----------

def _naismith_minutes(dist_km: float, ascent_m: float, descent_m: float,
                      surface: str, fitness_level: str) -> float:
    """Oblicza czas w minutach formułą Naismitha z korektami."""
    flat_min = (dist_km / NAISMITH_FLAT_KMH) * 60
    ascent_min = (ascent_m / 100) * NAISMITH_ASCENT_MIN_PER_100M
    descent_min = (descent_m / 100) * NAISMITH_DESCENT_MIN_PER_100M
    march_min = (flat_min + ascent_min + descent_min) * SURFACE_MULTIPLIER.get(surface, 1.05)
    break_min = math.floor(march_min / 60) * BREAK_MIN_PER_HOUR
    fitness_mult = FITNESS_MULTIPLIER.get(fitness_level, 1.10)
    return (march_min + break_min) * fitness_mult


# ---------- Helpers ----------

def _haversine(a: TrailPoint, b: TrailPoint) -> float:
    R = 6371.0
    la1, la2 = math.radians(a.lat), math.radians(b.lat)
    dla = math.radians(b.lat - a.lat)
    dlo = math.radians(b.lon - a.lon)
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _normalize(s: str) -> str:
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


# ---------- GPX ----------

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
    for params in [
        {"q": name, "format": "json", "limit": 1, "countrycodes": "pl"},
        {"q": name, "format": "json", "limit": 1},
    ]:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params=params,
            headers={"User-Agent": "BeskidzkiAgent/1.0"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    raise ValueError(f"Nie znaleziono: '{name}'")


def _parse_location(loc: str) -> tuple[float, float]:
    parts = loc.split(",")
    if len(parts) == 2:
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            pass
    places = _load_named_places()
    loc_norm = _normalize(loc.strip())
    for plat, plon, name in places:
        name_norm = _normalize(name)
        if name_norm.startswith(loc_norm) or loc_norm in name_norm:
            return plat, plon
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
    66: "marzn. deszcz", 67: "marzn. deszcz",
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
# ZBIERANIE DANYCH (Python)
# ============================================================

def _fetch_weather(lat: float, lon: float, trip_date: date, eta_hour: int) -> dict:
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m,precipitation,wind_speed_10m,weather_code",
                "timezone": "Europe/Warsaw",
                "start_date": trip_date.isoformat(),
                "end_date": trip_date.isoformat(),
            },
            timeout=15,
        )
        r.raise_for_status()
        h = r.json()["hourly"]
        i = min(eta_hour, 23)
        return {
            "t": h["temperature_2m"][i],
            "mm": h["precipitation"][i],
            "wind": h["wind_speed_10m"][i],
            "sky": WMO.get(h["weather_code"][i], "?"),
        }
    except Exception:
        return {"t": 0, "mm": 0, "wind": 0, "sky": "?"}


def _fetch_surface(lat: float, lon: float) -> dict:
    try:
        from surface import get_surface
        info = get_surface(lat, lon)
        surface_raw = getattr(info, "surface", "ground") or "ground"
        return {
            "surface": SURFACE_PL.get(surface_raw.replace(" *", "").strip(), surface_raw),
            "sac": getattr(info, "sac_label", "") or "",
        }
    except Exception:
        return {"surface": "grunt", "sac": ""}


def _fetch_soil(lat: float, lon: float, trip_date: date) -> dict:
    try:
        from soil import get_soil
        soil = get_soil(lat, lon, trip_date)
        return {"level": soil.level, "summary": soil.summary}
    except Exception:
        return {"level": "nieznany", "summary": ""}


def _slickness(mm: float, surface: str, soil_level: str) -> str:
    hard = {"asfalt", "beton", "ubita (żwir)", "utwardzona", "kostka brukowa", "kocie łby"}
    if surface in hard:
        return "mokro" if mm > 3 else "ok"
    if soil_level == "błoto" or (soil_level == "nasączone" and mm > 1):
        return "SLISKO!"
    if soil_level == "nasączone" or (soil_level == "lekko mokro" and mm > 1):
        return "mokro"
    if mm > 3:
        return "mokro"
    if mm > 0:
        return "lekko mokro"
    return "ok"


def collect_trail_data(location: str, distance_km: float, trip_date: date,
                       start_hour: int = 7, strava_profile=None) -> dict:
    """Python zbiera wszystkie dane o trasie. ETA obliczane formułą Naismitha."""
    fitness_level = "nieznana"
    if strava_profile:
        fitness_level = strava_profile.get("fitness_level", "nieznana")

    pts = _load_gpx()
    lat, lon = _parse_location(location)
    ref = TrailPoint(lat, lon)
    idx = min(range(len(pts)), key=lambda i: _haversine(pts[i], ref))
    start_pt = pts[idx]
    dist_to_trail = _haversine(ref, start_pt)
    start_name = _reverse_geocode(start_pt.lat, start_pt.lon)

    start_km = start_pt.km
    seg = [p for p in pts[idx:] if p.km <= start_km + distance_km]
    if not seg:
        raise ValueError("Odcinek wykracza poza GPX")

    ascent = int(sum(max(0, seg[i].ele - seg[i-1].ele) for i in range(1, len(seg))))
    descent = int(sum(max(0, seg[i-1].ele - seg[i].ele) for i in range(1, len(seg))))
    length_km = round(seg[-1].km - seg[0].km, 1)

    n = N_SAMPLES
    step = max(1, len(seg) // (n - 1))
    samples = [seg[min(i * step, len(seg)-1)] for i in range(n)]

    mid = samples[len(samples)//2]
    soil = _fetch_soil(mid.lat, mid.lon, trip_date)

    rows = []
    start_total_min = start_hour * 60

    for i, p in enumerate(samples):
        km_into = round(p.km - seg[0].km, 1)

        # Oblicz przewyższenia od startu do tego punktu
        seg_to_point = [s for s in seg if s.km <= seg[0].km + km_into]
        asc_to_point = sum(max(0, seg_to_point[j].ele - seg_to_point[j-1].ele)
                          for j in range(1, len(seg_to_point))) if len(seg_to_point) > 1 else 0
        desc_to_point = sum(max(0, seg_to_point[j-1].ele - seg_to_point[j].ele)
                           for j in range(1, len(seg_to_point))) if len(seg_to_point) > 1 else 0

        # Pobierz nawierzchnię dla obliczenia ETA
        surface_info = _fetch_surface(p.lat, p.lon)
        surface_for_eta = surface_info["surface"]

        # Naismith ETA
        elapsed_min = _naismith_minutes(km_into, asc_to_point, desc_to_point,
                                        surface_for_eta, fitness_level)
        eta_total_min = start_total_min + int(elapsed_min)
        eta_hour = min(eta_total_min // 60, 23)
        eta_min = eta_total_min % 60
        eta_str = f"{eta_hour:02d}:{eta_min:02d}"

        weather = _fetch_weather(p.lat, p.lon, trip_date, eta_hour)
        place = _reverse_geocode(p.lat, p.lon)
        slick = _slickness(weather["mm"], surface_for_eta, soil["level"])

        rows.append({
            "km": km_into,
            "eta": eta_str,
            "t": weather["t"],
            "mm": weather["mm"],
            "wind": weather["wind"],
            "sky": weather["sky"],
            "surface": surface_for_eta,
            "sac": surface_info["sac"],
            "slickness": slick,
            "place": place,
            "lat": round(p.lat, 5),
            "lon": round(p.lon, 5),
            "ele": round(p.ele),
        })

    tmin = min(r["t"] for r in rows)
    tmax = max(r["t"] for r in rows)
    precip = sum(r["mm"] for r in rows)
    wind_max = max(r["wind"] for r in rows)
    summary = f"{tmin:.0f}–{tmax:.0f}°C · Σ{precip:.1f} mm · wiatr max {wind_max:.0f} km/h"

    # Szacowany czas całkowity
    total_min = int(_naismith_minutes(length_km, ascent, descent, "grunt", fitness_level))
    total_h = total_min // 60
    total_m = total_min % 60
    eta_end_str = f"{min((start_total_min + total_min) // 60, 23):02d}:{(start_total_min + total_min) % 60:02d}"

    return {
        "date": trip_date.isoformat(),
        "start_name": start_name,
        "dist_to_trail_km": round(dist_to_trail, 2),
        "length_km": length_km,
        "ascent_m": ascent,
        "descent_m": descent,
        "estimated_time": f"{total_h}h {total_m:02d}min",
        "eta_end": eta_end_str,
        "soil_summary": soil["summary"],
        "rows": rows,
        "summary": summary,
        "recommendation": "",
        "recommendation_reason": "",
        "socks": [],
        "warnings": [],
    }


# ============================================================
# OCENA LLM
# ============================================================

LLM_SYSTEM = """Jesteś doświadczonym przewodnikiem górskim Głównego Szlaku Beskidzkiego.
Bądź obiektywny, lekko surowy.
Doradzasz osobie, która niesie plecak ok 8-10 kg. Ma kije trekingowe, ale nie jest zawodowym sportowcem. Idzie w butach trailowych, ma skarpetki wodoodporne.
Dostajesz gotowe dane o trasie (pogoda, nawierzchnia, gleba).
Twoje zadanie: ocenić trasę i zwrócić TYLKO JSON:

{
  "recommendation": "Idź / Skróć trasę / Zostań w domu",
  "recommendation_reason": "1-2 zdania uzasadnienia po polsku",
  "warnings": ["ostrzeżenie 1", "ostrzeżenie 2"],
  "socks": ["zalecane skarpetki: wodoodporne / przygotuj / zwykłe"],
  "summary_note": "1 zdanie ogólnego wrażenia"
}

Kryteria:
- Idź: dobra pogoda, brak zagrożeń
- Skróć trasę: opady >5mm lub wiatr >40km/h lub ślisko
- Załóż skarpety wodoodporne: jeśli mokro lub gleba nasączona
- Przygotuj skarpety wodoodporne: jeśli przewidywane są opady lub gleba będzie mokra w środku dnia
- Zostań w domu: burza, silny mróz <-10°C, ulewny deszcz, SLISKO! na większości trasy

Zwróć TYLKO JSON. Żadnego tekstu ani markdown."""


def _build_prompt(data: dict, strava_profile=None) -> str:
    strava_info = ""
    if strava_profile:
        strava_info = (
            f"\nKondycja użytkownika: {strava_profile.get('fitness_level', 'nieznana')}, "
            f"tempo {strava_profile.get('avg_pace_kmh', 3.0):.1f} km/h, "
            f"dystans 30 dni: {strava_profile.get('stats', {}).get('recent_km', 0):.0f} km."
        )
    rows_summary = [
        f"km {r['km']:.1f} ({r['eta']}): {r['t']:.0f}°C, {r['mm']:.1f}mm, "
        f"{r['wind']:.0f}km/h, {r['sky']}, {r['surface']}, śliskość: {r['slickness']}"
        for r in data["rows"]
    ]
    return (
        f"Trasa: {data['start_name']}, {data['length_km']} km, "
        f"+{data['ascent_m']} m, -{data.get('descent_m', 0)} m, "
        f"szacowany czas: {data.get('estimated_time', '?')}, "
        f"koniec ok. {data.get('eta_end', '?')}, data: {data['date']}\n"
        f"Gleba: {data.get('soil_summary', 'nieznana')}\n"
        f"{strava_info}\n"
        f"Punkty trasy:\n" + "\n".join(rows_summary)
    )


def _apply_evaluation(data: dict, content: str) -> dict:
    try:
        clean = content.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        evaluation = json.loads(clean.strip())
        data["recommendation"] = evaluation.get("recommendation", "Idź")
        data["recommendation_reason"] = evaluation.get("recommendation_reason", "")
        data["socks"] = evaluation.get("socks", [])
        data["warnings"] = evaluation.get("warnings", [])
        if evaluation.get("summary_note"):
            data["summary"] += f" — {evaluation['summary_note']}"
    except Exception:
        pass
    return data


def _llm_evaluate_groq(data: dict, strava_profile=None) -> dict:
    import os, time
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return data
    prompt = _build_prompt(data, strava_profile)
    for _ in range(3):
        try:
            resp = requests.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": LLM_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 512,
                    "temperature": 0.2,
                },
                timeout=30,
            )
            if resp.status_code == 429:
                time.sleep(10)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _apply_evaluation(data, content)
        except Exception:
            continue
    return data


def _llm_evaluate_claude(data: dict, strava_profile=None) -> dict:
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return data
    prompt = _build_prompt(data, strava_profile)
    try:
        resp = requests.post(
            CLAUDE_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 512,
                "system": LLM_SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"]
        return _apply_evaluation(data, content)
    except Exception:
        return data


def llm_evaluate(data: dict, strava_profile=None, llm_provider: str = "groq") -> dict:
    if llm_provider == "off":
        return data
    if llm_provider == "claude":
        return _llm_evaluate_claude(data, strava_profile)
    return _llm_evaluate_groq(data, strava_profile)


# ============================================================
# GŁÓWNA FUNKCJA
# ============================================================

def run_segment(location: str, distance_km: float, trip_date: date,
                start_hour: int = 7, strava_profile=None,
                llm_provider: str = "groq") -> dict:
    data = collect_trail_data(location, distance_km, trip_date, start_hour, strava_profile)
    return llm_evaluate(data, strava_profile, llm_provider)


def _midpoint_location(location: str, distance_km: float, start_hour: int,
                       strava_profile=None) -> tuple[str, int]:
    """Znajdź punkt środkowy trasy z realistycznym ETA (Naismith)."""
    pts = _load_gpx()
    lat, lon = _parse_location(location)
    ref = TrailPoint(lat, lon)
    idx = min(range(len(pts)), key=lambda i: _haversine(pts[i], ref))
    start_km = pts[idx].km
    half_km = distance_km / 2
    seg = [p for p in pts[idx:] if p.km <= start_km + distance_km]
    if not seg:
        return location, start_hour

    mid_pts = [p for p in seg if p.km <= start_km + half_km]
    if not mid_pts:
        return location, start_hour
    mid_pt = mid_pts[-1]

    # Oblicz ETA do punktu środkowego formułą Naismitha
    fitness_level = strava_profile.get("fitness_level", "nieznana") if strava_profile else "nieznana"
    seg_half = mid_pts
    asc = sum(max(0, seg_half[i].ele - seg_half[i-1].ele) for i in range(1, len(seg_half)))
    desc = sum(max(0, seg_half[i-1].ele - seg_half[i].ele) for i in range(1, len(seg_half)))
    elapsed_min = _naismith_minutes(half_km, asc, desc, "grunt", fitness_level)
    mid_total_min = start_hour * 60 + int(elapsed_min)
    mid_hour = min(mid_total_min // 60, 23)

    places = _load_named_places()
    ref_mid = TrailPoint(mid_pt.lat, mid_pt.lon)
    best_name, best_dist = None, 5.0
    for plat, plon, name in places:
        d = _haversine(ref_mid, TrailPoint(plat, plon))
        if d < best_dist:
            best_dist = d
            best_name = name

    if best_name:
        return best_name, mid_hour
    return f"{mid_pt.lat:.5f},{mid_pt.lon:.5f}", mid_hour


def run_split(location: str, distance_km: float, trip_date: date,
              start_hour: int = 7, strava_profile=None,
              llm_provider: str = "groq") -> list[dict]:
    if distance_km <= SPLIT_THRESHOLD_KM:
        result = run_segment(location, distance_km, trip_date, start_hour,
                             strava_profile, llm_provider)
        return [result]

    half = round(distance_km / 2, 1)
    mid_location, mid_hour = _midpoint_location(location, distance_km, start_hour, strava_profile)

    result1 = run_segment(location, half, trip_date, start_hour, strava_profile, llm_provider)
    result1["part"] = 1
    result1["part_label"] = f"Część 1: km 0–{half:.0f}"

    result2 = run_segment(mid_location, half, trip_date, mid_hour, strava_profile, llm_provider)
    result2["part"] = 2
    result2["part_label"] = f"Część 2: km {half:.0f}–{distance_km:.0f}"

    return [result1, result2]


# ============================================================
# KOMPATYBILNOŚĆ Z BOT.PY
# ============================================================

def run(gpx_path, location, distance_km, day, samples=5, start_hour=7,
        pace_kmh=3.0, strava_profile=None, llm_provider="groq"):
    results = run_split(location, distance_km, day, start_hour,
                        strava_profile, llm_provider)
    if len(results) == 1:
        return results[0]
    combined = results[0]
    combined["part2"] = results[1]
    return combined


def _render(r: dict) -> str:
    def _render_single(d: dict) -> str:
        lines = [
            f"📍 {d.get('start_name', '')}",
            f"📅 {d.get('date', '')}  📏 {d.get('length_km', '')} km  ⛰️ +{d.get('ascent_m', '')} m",
        ]
        if d.get("estimated_time"):
            lines += [f"⏱️ Szacowany czas: {d['estimated_time']} (koniec ~{d.get('eta_end', '?')})"]
        if d.get("soil_summary"):
            lines += ["", f"🌱 {d['soil_summary']}"]
        rec = d.get("recommendation", "")
        reason = d.get("recommendation_reason", "")
        if rec:
            emoji = "✅" if "Idź" in rec else "⚠️" if "Skróć" in rec else "🚫"
            lines += ["", f"{emoji} {rec}", reason]
        if socks := d.get("socks", []):
            socks_text = " ".join(socks).lower()
            emoji = "🧦💧" if "wodoodporne" in socks_text else "🧦🎒" if "przygotuj" in socks_text else "🧦"
            lines += [""] + [f"{emoji} {s}" for s in socks]
        if d.get("warnings"):
            lines += [""] + [f"⚠️ {w}" for w in d["warnings"]]
        lines += ["", d.get("summary", "")]
        return "\n".join(lines)

    if "part2" in r:
        return (f"━━━ {r.get('part_label', 'Część 1')} ━━━\n{_render_single(r)}\n\n"
                f"━━━ {r['part2'].get('part_label', 'Część 2')} ━━━\n{_render_single(r['part2'])}")
    return _render_single(r)


def _narrative(rows: list) -> str:
    return ""


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="location", required=True)
    ap.add_argument("--distance", type=float, required=True)
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--start-hour", type=int, default=7)
    ap.add_argument("--llm", default="groq", choices=["groq", "claude", "off"])
    a = ap.parse_args()
    results = run_split(
        location=a.location,
        distance_km=a.distance,
        trip_date=date.fromisoformat(a.date),
        start_hour=a.start_hour,
        llm_provider=a.llm,
    )
    for r in results:
        print(json.dumps(r, ensure_ascii=False, indent=2))