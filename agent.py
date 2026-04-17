"""
Beskidzki Agent — moduł pogody

Użycie:
  python agent.py gsb.gpx --from "Wołosate" --distance 24
  python agent.py gsb.gpx --from "49.3621,22.7012" --distance 15 --date 2026-05-01
"""
from __future__ import annotations

import argparse
import math
import time as _time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import gpxpy
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from surface import enrich_rows, check_distance_warning
from waypoints import smart_picks, picks_to_trailpoints
from soil import enrich_with_soil


# ---------- Model ----------

@dataclass
class TrailPoint:
    lat: float
    lon: float
    ele: float = 0.0
    km: float = 0.0


@dataclass
class Sample:
    point: TrailPoint
    time: datetime
    temp: float
    precip: float
    wind: float
    code: int


# ---------- GPX ----------

def _haversine(a: TrailPoint, b: TrailPoint) -> float:
    R = 6371.0
    la1, la2 = math.radians(a.lat), math.radians(b.lat)
    dla = math.radians(b.lat - a.lat)
    dlo = math.radians(b.lon - a.lon)
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def load_gpx(path: str | Path) -> list[TrailPoint]:
    with open(path, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    pts: list[TrailPoint] = []
    acc = 0.0
    prev: TrailPoint | None = None
    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                tp = TrailPoint(p.latitude, p.longitude, p.elevation or 0.0)
                if prev:
                    acc += _haversine(prev, tp)
                tp.km = acc
                pts.append(tp)
                prev = tp
    if not pts:
        raise ValueError("Brak śladu w GPX")
    return pts


# ---------- Lokalizacja ----------

def parse_location(loc: str) -> tuple[float, float]:
    """'49.123,22.456' → (lat, lon)   lub   'Wołosate' → Nominatim geocode."""
    parts = loc.split(",")
    if len(parts) == 2:
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            pass
    return geocode(loc)


def geocode(name: str) -> tuple[float, float]:
    """Miejscowość/szczyt → (lat, lon)."""
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


def nearest_idx(pts: list[TrailPoint], lat: float, lon: float) -> int:
    ref = TrailPoint(lat, lon)
    return min(range(len(pts)), key=lambda i: _haversine(pts[i], ref))


def get_segment(pts: list[TrailPoint], lat: float, lon: float, distance_km: float) -> list[TrailPoint]:
    idx = nearest_idx(pts, lat, lon)
    start_km = pts[idx].km
    seg = [p for p in pts[idx:] if p.km <= start_km + distance_km]
    if not seg:
        raise ValueError("Odcinek wykracza poza GPX")
    return seg


def sample_evenly(seg: list[TrailPoint], n: int = 5) -> list[TrailPoint]:
    if len(seg) <= n:
        return seg
    start_km, end_km = seg[0].km, seg[-1].km
    step = (end_km - start_km) / (n - 1)
    picks, target, i = [], start_km, 0
    for _ in range(n):
        while i < len(seg) - 1 and seg[i].km < target:
            i += 1
        picks.append(seg[i])
        target += step
    return picks


# ---------- Geokodowanie odwrotne ----------

_geo_cache: dict[tuple, str] = {}


def reverse_geocode(lat: float, lon: float) -> str:
    key = (round(lat, 3), round(lon, 3))
    if key in _geo_cache:
        return _geo_cache[key]
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 14},
            headers={"User-Agent": "BeskidzkiAgent/1.0"},
            timeout=10,
            verify=False,
        )
        r.raise_for_status()
        data = r.json()
        addr = data.get("address", {})
        name = (
            data.get("name")
            or addr.get("peak")
            or addr.get("hamlet")
            or addr.get("village")
            or addr.get("town")
            or addr.get("city")
            or f"{lat:.3f},{lon:.3f}"
        )
        _geo_cache[key] = name
        _time.sleep(1.1)  # Nominatim: max 1 req/s
        return name
    except Exception:
        return f"{lat:.3f},{lon:.3f}"


# ---------- Pogoda (Open-Meteo) ----------

WMO = {
    0: "bezchmurnie", 1: "gł. słonecznie", 2: "częściowe zachm.", 3: "zachmurzenie",
    45: "mgła", 48: "mgła osadz.",
    51: "mżawka", 53: "mżawka", 55: "silna mżawka",
    61: "słaby deszcz", 63: "deszcz", 65: "ulewny deszcz",
    66: "marzn. deszcz", 67: "marzn. deszcz",
    71: "słaby śnieg", 73: "śnieg", 75: "silny śnieg", 77: "krupa",
    80: "przelotny deszcz", 81: "przelotny deszcz", 82: "ulewa",
    85: "przelotny śnieg", 86: "silny przel. śnieg",
    95: "burza", 96: "burza z gradem", 99: "silna burza z gradem",
}


def fetch_weather(point: TrailPoint, day: date) -> list[Sample]:
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": point.lat,
            "longitude": point.lon,
            "hourly": "temperature_2m,precipitation,wind_speed_10m,weather_code",
            "timezone": "Europe/Warsaw",
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
        },
        timeout=15,
    )
    r.raise_for_status()
    h = r.json()["hourly"]
    return [
        Sample(
            point=point,
            time=datetime.fromisoformat(h["time"][i]),
            temp=h["temperature_2m"][i],
            precip=h["precipitation"][i],
            wind=h["wind_speed_10m"][i],
            code=h["weather_code"][i],
        )
        for i in range(len(h["time"]))
    ]


# ---------- Orkiestracja ----------

def run(gpx_path, location, distance_km, day, samples=5, start_hour=8, pace_kmh=3.0):
    pts = load_gpx(gpx_path)
    lat, lon = parse_location(location)

    start_idx = nearest_idx(pts, lat, lon)
    start_pt = pts[start_idx]
    start_name = reverse_geocode(start_pt.lat, start_pt.lon)
    dist_to_trail = _haversine(TrailPoint(lat, lon), start_pt)

    seg = get_segment(pts, lat, lon, distance_km)

    # Inteligentne probkowanie: szczyty, przelecze, zmiany terenu
    poi_list = smart_picks(seg, include_peaks=True, include_terrain=True)
    picks_with_meta = picks_to_trailpoints(poi_list, seg)

    # Fallback + uzupelnienie: jesli za malo punktow, dodaj rownomierne
    MIN_POINTS = max(samples, 4)
    if len(picks_with_meta) < MIN_POINTS:
        even = [(p, "", "even") for p in sample_evenly(seg, MIN_POINTS)]
        existing_kms = {p.km for p, _, _ in picks_with_meta}
        for tp, name, kind in even:
            if not any(abs(tp.km - k) < 2.0 for k in existing_kms):
                picks_with_meta.append((tp, name, kind))
                existing_kms.add(tp.km)
        picks_with_meta.sort(key=lambda x: x[0].km)

    base = datetime.combine(day, datetime.min.time()).replace(hour=start_hour)
    rows = []
    for p, poi_name, poi_kind in picks_with_meta:
        km_into = p.km - seg[0].km
        eta = base.fromtimestamp(base.timestamp() + km_into / pace_kmh * 3600)
        hour = fetch_weather(p, eta.date())
        mid = min(hour, key=lambda s: abs((s.time - eta).total_seconds()))
        place = poi_name if poi_name and poi_name not in ("start", "koniec") else reverse_geocode(p.lat, p.lon)
        rows.append({
            "km": round(km_into, 1),
            "ele": round(p.ele),
            "eta": eta.strftime("%H:%M"),
            "place": place,
            "lat": p.lat,
            "lon": p.lon,
            "t": mid.temp,
            "mm": mid.precip,
            "wind": mid.wind,
            "sky": WMO.get(mid.code, f"?{mid.code}"),
            "poi_kind": poi_kind,
        })

    enrich_rows(rows)
    enrich_with_soil(rows, day)
    ascent = _ascent(seg)
    length_km = round(seg[-1].km - seg[0].km, 1)
    dist_warn = check_distance_warning(length_km, ascent)

    return {
        "date": day.isoformat(),
        "start_name": start_name,
        "dist_to_trail_km": round(dist_to_trail, 2),
        "length_km": length_km,
        "ascent_m": ascent,
        "dist_warning": dist_warn,
        "rows": rows,
        "summary": _summary(rows),
    }


def _ascent(seg):
    return int(sum(max(0, seg[i].ele - seg[i - 1].ele) for i in range(1, len(seg))))


def _summary(rows):
    precip = sum(r["mm"] for r in rows)
    wind = max(r["wind"] for r in rows)
    tmin = min(r["t"] for r in rows)
    tmax = max(r["t"] for r in rows)
    warn = []
    if precip > 5: warn.append("⚠ opady")
    if wind > 40: warn.append("⚠ wiatr")
    if tmin < 0: warn.append("⚠ mróz")
    if any(r["sky"].startswith("burza") for r in rows): warn.append("⚠ burza")
    base = f"{tmin:.0f}–{tmax:.0f}°C · Σ{precip:.1f} mm · wiatr max {wind:.0f} km/h"
    return base + ("  " + " ".join(warn) if warn else "")


def _poi_icon(kind):
    icons = {"peak": "^", "pass": "~v", "terrain_change": ">>", "start": ">", "end": "[]"}
    return icons.get(kind, " ")


def _narrative(rows):
    parts = []
    for w in rows:
        wind_desc = "wiatr słaby" if w["wind"] < 20 else "wiatr umiarkowany" if w["wind"] < 40 else "silny wiatr"
        surface_note = f", nawierzchnia {w['rain_risk']}" if w.get("rain_risk") else ""
        parts.append(f"{w['place']} ({w['eta']}): {w['sky']}, {w['t']:.0f}°C, {wind_desc}{surface_note}.")
    return " → ".join(parts)


def _poi_icon(kind):
    icons = {"peak": "^", "pass": "~v", "terrain_change": ">>", "start": ">", "end": "[]"}
    return icons.get(kind, " ")


def _slickness(row) -> str:
    """Inline ocena sliskosci: ok / mokro / slisko! na podstawie pogody + historii."""
    mm       = row.get("mm", 0)
    surface  = row.get("surface", "ground").replace(" *", "").strip()
    soil_lvl = row.get("soil", None)
    soil_level = soil_lvl.level if soil_lvl else "sucho"

    soft = {"ground", "dirt", "mud", "grass", "roots", "unpaved", "rock", "stone", "wood"}
    hard = {"asphalt", "paved", "concrete", "compacted"}

    if surface in hard:
        if mm > 3:
            return "mokro"
        return "ok"

    # miekka nawierzchnia
    if soil_level == "bloto" or (soil_level == "nasaczone" and mm > 1):
        return "SLISKO!"
    if soil_level == "nasaczone" or (soil_level == "lekko" and mm > 1):
        return "mokro"
    if mm > 3:
        return "mokro"
    if mm > 0:
        return "lekko mokro"
    return "ok"

SURFACE_PL = {
    "asphalt":            "asfalt",
    "concrete":           "beton",
    "concrete:lanes":     "beton (pasy)",
    "concrete:plates":    "płyty betonowe",
    "paving_stones":      "kostka brukowa",
    "sett":               "kamień ciosany",
    "cobblestone":        "kocie łby",
    "unhewn_cobblestone": "kamień polny",
    "metal":              "metal",
    "wood":               "drewno",
    "tiles":              "płytki",
    "paved":              "utwardzona",
    "unpaved":            "nieutwardzona",
    "compacted":          "ubita (żwir)",
    "fine_gravel":        "drobny żwir",
    "gravel":             "żwir",
    "pebblestone":        "otoczaki",
    "dirt":               "ziemia",
    "earth":              "ziemia",
    "grass":              "trawa",
    "grass_paver":        "trawa/kratka",
    "ground":             "grunt",
    "mud":                "błoto",
    "sand":               "piasek",
    "woodchips":          "zrębki",
    "snow":               "śnieg",
    "ice":                "lód",
    "clay":               "glina",
    "rock":               "skała",
    "roots":              "korzenie",
    "stone":              "kamień",
}

def _translate_surface(s: str) -> str:
    return SURFACE_PL.get(s.strip().replace(" *", ""), s)

def _render(r):
    out = [
        f"Lokalizacja: {r['start_name']}  (odl. {r['dist_to_trail_km']} km od szlaku)",
        f"Data: {r['date']}   Dystans: {r['length_km']} km   Podejscie: {r['ascent_m']} m",
    ]

    if r["rows"] and r["rows"][0].get("soil_summary"):
        out += ["", r["rows"][0]["soil_summary"]]

    out += [
        "",
        f"{'km':>5} {'ETA':>6} {'C':>4} {'mm':>5} {'km/h':>5}  {'niebo':<20} {'podloze':<16} {'sliskos.':<10} {'SAC':<24} miejsce",
        "-" * 120,
    ]

    for w in r["rows"]:
        print(f"DEBUG surface: '{w.get('surface', '?')}'")  # usuń po debugowaniu
        slick = _slickness(w)
        out.append(
            f"{w['km']:>5.1f} {w['eta']:>6} {w['t']:>4.0f} "
            f"{w['mm']:>5.1f} {w['wind']:>5.1f}  {w['sky']:<20} "
            f"{_translate_surface(w.get('surface', '?')):<16} {slick:<10} "
            f"{w.get('sac', ''):<24} {_poi_icon(w.get('poi_kind',''))} {w['place']}"
        )

    out += ["", r["summary"]]

    if r.get("dist_warning"):
        out += ["", "! DYSTANS: " + r["dist_warning"]]

    out += ["", _narrative(r["rows"])]
    logging.warning(f"SURFACE DEBUG: '{w.get('surface', '?')}'")
    return "\n".join(out)


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(description="Beskidzki Agent — pogoda na trasie GSB")
    ap.add_argument("gpx", help="Plik GPX całego szlaku")
    ap.add_argument("--from", dest="location", required=True,
                    help="Twoja lokalizacja: nazwa miejscowości lub 'lat,lon'")
    ap.add_argument("--distance", type=float, required=True,
                    help="Ile km chcesz przejść")
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="Data wędrówki YYYY-MM-DD (domyślnie: dziś)")
    ap.add_argument("--start-hour", type=int, default=8,
                    help="Godzina wyjścia (domyślnie: 8)")
    ap.add_argument("--pace", type=float, default=3.0,
                    help="Tempo km/h (domyślnie: 3.0)")
    ap.add_argument("--samples", type=int, default=5,
                    help="Liczba próbek pogodowych (domyślnie: 5)")
    a = ap.parse_args()

    res = run(
        a.gpx, a.location, a.distance,
        date.fromisoformat(a.date),
        samples=a.samples, start_hour=a.start_hour, pace_kmh=a.pace,
    )
    print(_render(res))


if __name__ == "__main__":
    main()