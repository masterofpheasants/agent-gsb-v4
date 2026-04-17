"""
Beskidzki Agent — moduł pogody (MVP)

Wejście: plik GPX (eksport z mapy.com), nazwa/km punktu startu i końca.
Wyjście: prognoza wzdłuż odcinka + ostrzeżenia.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import gpxpy
import requests


# ---------- Model ----------

@dataclass
class TrailPoint:
    lat: float
    lon: float
    ele: float = 0.0
    km: float = 0.0  # od początku GPX


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


def load_gpx(path: str | Path):
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
    return pts, gpx.waypoints


def segment(pts, waypoints, start, end):
    """start/end: float (km) lub str (nazwa waypointu)."""
    def resolve(x):
        if isinstance(x, (int, float)):
            return min(range(len(pts)), key=lambda i: abs(pts[i].km - float(x)))
        needle = str(x).lower().strip()
        wp = next((w for w in waypoints if w.name and needle in w.name.lower()), None)
        if not wp:
            raise ValueError(f"Nie znaleziono punktu: {x}")
        ref = TrailPoint(wp.latitude, wp.longitude)
        return min(range(len(pts)), key=lambda i: _haversine(pts[i], ref))

    i, j = resolve(start), resolve(end)
    if i > j:
        i, j = j, i
    return pts[i:j + 1]


def sample_evenly(seg, n=5):
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


# ---------- Pogoda (Open-Meteo, bez klucza) ----------

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


def fetch(point: TrailPoint, day: date) -> list[Sample]:
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

def weather_for_route(gpx_path, start, end, day: date, samples=5, start_hour=8, pace_kmh=3.0):
    pts, wps = load_gpx(gpx_path)
    seg = segment(pts, wps, start, end)
    if not seg:
        raise ValueError("Pusty odcinek")
    picks = sample_evenly(seg, samples)

    base = datetime.combine(day, datetime.min.time()).replace(hour=start_hour)
    rows = []
    for p in picks:
        km_into = p.km - seg[0].km
        eta = base.fromtimestamp(base.timestamp() + km_into / pace_kmh * 3600)
        hour = fetch(p, eta.date())
        mid = min(hour, key=lambda s: abs((s.time - eta).total_seconds()))
        rows.append({
            "km": round(km_into, 1),
            "ele": round(p.ele),
            "eta": eta.strftime("%H:%M"),
            "t": mid.temp,
            "mm": mid.precip,
            "wind": mid.wind,
            "sky": WMO.get(mid.code, f"?{mid.code}"),
        })

    return {
        "date": day.isoformat(),
        "length_km": round(seg[-1].km - seg[0].km, 1),
        "ascent_m": _ascent(seg),
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


# ---------- CLI ----------

def _render(r):
    out = [
        f"📅 {r['date']}   📏 {r['length_km']} km   ⬆ {r['ascent_m']} m",
        "",
        f"{'km':>5} {'m n.p.m.':>8} {'ETA':>6} {'°C':>5} {'mm':>5} {'km/h':>5}  niebo",
    ]
    for w in r["rows"]:
        out.append(f"{w['km']:>5.1f} {w['ele']:>8} {w['eta']:>6} {w['t']:>5.1f} {w['mm']:>5.1f} {w['wind']:>5.1f}  {w['sky']}")
    out += ["", r["summary"]]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Beskidzki Agent — pogoda na trasie")
    ap.add_argument("gpx", help="Plik GPX (eksport z mapy.com: udostępnij → pobierz GPX)")
    ap.add_argument("--start", required=True, help="Nazwa punktu lub km od początku GPX")
    ap.add_argument("--end", required=True, help="Nazwa punktu lub km od początku GPX")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--start-hour", type=int, default=8)
    ap.add_argument("--pace", type=float, default=3.0, help="km/h")
    ap.add_argument("--samples", type=int, default=5)
    a = ap.parse_args()

    def parse(x):
        try: return float(x)
        except ValueError: return x

    res = weather_for_route(
        a.gpx, parse(a.start), parse(a.end),
        date.fromisoformat(a.date),
        samples=a.samples, start_hour=a.start_hour, pace_kmh=a.pace,
    )
    print(_render(res))


if __name__ == "__main__":
    main()
