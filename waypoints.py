"""waypoints.py - inteligentne probkowanie trasy

Wybiera punkty przy:
- szczytach i przeleczach (OSM: natural=peak, mountain_pass)
- zmianach nawierzchni / SAC scale
- zawsze: start i koniec odcinka
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Minimalna odleglosc miedzy dwoma punktami (unika duplikatow)
MIN_SPACING_KM = 2.0


@dataclass
class PoI:
    lat: float
    lon: float
    km: float        # dystans od poczatku GPX
    name: str
    kind: str        # "peak" | "pass" | "terrain_change" | "start" | "end"


def fetch_pois(seg, radius_m: int = 400) -> list[PoI]:
    """
    Pobiera szczyty i przelecze z OSM w otoczeniu odcinka.
    Uzywa bounding box calego odcinka.
    """
    if not seg:
        return []

    lats = [p.lat for p in seg]
    lons = [p.lon for p in seg]
    bbox = f"{min(lats)-0.01},{min(lons)-0.01},{max(lats)+0.01},{max(lons)+0.01}"

    query = f"""
[out:json][timeout:15];
(
  node["natural"="peak"]({bbox});
  node["mountain_pass"="yes"]({bbox});
  node["natural"="saddle"]({bbox});
);
out body;
"""
    try:
        r = requests.post(OVERPASS_URL, data={"data": query}, timeout=20, verify=False)
        r.raise_for_status()
        elements = r.json().get("elements", [])
        time.sleep(0.5)
    except Exception:
        return []

    pois = []
    for el in elements:
        lat = el.get("lat")
        lon = el.get("lon")
        if not lat or not lon:
            continue
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:pl") or ""
        kind = "pass" if (tags.get("mountain_pass") == "yes" or tags.get("natural") == "saddle") else "peak"
        if not name:
            name = "szczyt" if kind == "peak" else "przelecz"

        # Znajdz najblizszy punkt na trasie
        nearest = _nearest_on_seg(seg, lat, lon)
        if nearest is None:
            continue
        dist_to_trail = _haversine_ll(lat, lon, nearest.lat, nearest.lon)
        if dist_to_trail > radius_m / 1000:
            continue

        pois.append(PoI(lat=nearest.lat, lon=nearest.lon, km=nearest.km, name=name, kind=kind))

    return pois


def detect_terrain_changes(seg, surface_sample_km: float = 3.0) -> list[PoI]:
    """
    Wykrywa zmiany SAC scale co ~3 km przez probkowanie Overpass.
    Zwraca punkty przy zmianach trudnosci.
    """
    from surface import get_surface

    changes = []
    prev_sac = None
    step = max(1, int(len(seg) * surface_sample_km /
                      max(0.01, seg[-1].km - seg[0].km)))

    for i in range(0, len(seg), step):
        p = seg[i]
        info = get_surface(p.lat, p.lon, ele=p.ele)
        if info.sac_scale and info.sac_scale != prev_sac:
            if prev_sac is not None:  # pomijamy pierwsza zmiane (start)
                changes.append(PoI(
                    lat=p.lat, lon=p.lon, km=p.km,
                    name=info.sac_label or info.sac_scale,
                    kind="terrain_change"
                ))
            prev_sac = info.sac_scale

    return changes


def smart_picks(seg, include_peaks=True, include_terrain=True) -> list[PoI]:
    """
    Laczy start, szczyty/przelecze, zmiany terenu i koniec.
    Usuwa punkty blizej niz MIN_SPACING_KM.
    """
    candidates: list[PoI] = []

    # zawsze: start i koniec
    candidates.append(PoI(seg[0].lat, seg[0].lon, seg[0].km, "start", "start"))
    candidates.append(PoI(seg[-1].lat, seg[-1].lon, seg[-1].km, "koniec", "end"))

    if include_peaks:
        candidates += fetch_pois(seg)

    if include_terrain:
        candidates += detect_terrain_changes(seg)

    # Sortuj po km
    candidates.sort(key=lambda p: p.km)

    # Deduplikuj - usun punkty za blisko siebie
    filtered: list[PoI] = []
    for c in candidates:
        if not filtered or (c.km - filtered[-1].km) >= MIN_SPACING_KM:
            filtered.append(c)
        else:
            # Preferuj peak/pass nad terrain_change
            if c.kind in ("peak", "pass") and filtered[-1].kind == "terrain_change":
                filtered[-1] = c

    return filtered


def picks_to_trailpoints(picks: list[PoI], seg):
    """Zamienia PoI na TrailPoint z najblizszego punktu na trasie."""
    from agent import TrailPoint
    result = []
    for poi in picks:
        tp = _nearest_on_seg(seg, poi.lat, poi.lon)
        if tp:
            result.append((tp, poi.name, poi.kind))
    return result


# ---------- helpers ----------

def _nearest_on_seg(seg, lat, lon):
    if not seg:
        return None
    return min(seg, key=lambda p: _haversine_ll(p.lat, p.lon, lat, lon))


def _haversine_ll(lat1, lon1, lat2, lon2) -> float:
    import math
    R = 6371.0
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dla = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    h = math.sin(dla/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(dlo/2)**2
    return 2 * R * math.asin(math.sqrt(h))
