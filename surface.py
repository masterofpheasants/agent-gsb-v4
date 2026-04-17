"""
surface.py — nawierzchnia i trudność szlaku z OpenStreetMap (Overpass API)

Tagi OSM:
  surface     : grunt, skała, asfalt, żwir, drewno...
  sac_scale   : T1 (łatwy) → T6 (alpinizm)
  trail_visibility: excellent → no
  highway     : path, track, footway...
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

# ---------- Stałe ----------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Trudność SAC Scale → opis + próg ryzyka przy deszczu
SAC = {
    "hiking":                  ("T1 – łatwy",         "niskie"),
    "mountain_hiking":         ("T2 – górski",        "umiarkowane"),
    "demanding_mountain_hiking": ("T3 – wymagający",  "wysokie"),
    "alpine_hiking":           ("T4 – alpejski",      "bardzo wysokie"),
    "demanding_alpine_hiking": ("T5 – trudny alpejski", "ekstremalne"),
    "difficult_alpine_hiking": ("T6 – wspinaczkowy",  "ekstremalne"),
}

# Nawierzchnie → śliskość przy deszczu (0=ok, 1=uwaga, 2=niebezpiecznie)
SURFACE_RISK = {
    "asphalt":        0, "paved":       0, "concrete":    0,
    "compacted":      1, "gravel":      1, "fine_gravel": 1,
    "ground":         2, "dirt":        2, "mud":         2,
    "grass":          2, "rock":        2, "stone":       2,
    "roots":          2, "wood":        2, "unpaved":     1,
}

RISK_LABEL = {0: "✅ bezpieczna", 1: "⚠ uwaga", 2: "🔴 ślisko"}


# ---------- Model ----------

@dataclass
class SurfaceInfo:
    lat: float
    lon: float
    surface: str = "nieznana"
    sac_scale: str = ""
    trail_visibility: str = ""
    highway: str = ""

    @property
    def sac_label(self) -> str:
        return SAC.get(self.sac_scale, ("", ""))[0] if self.sac_scale else ""

    @property
    def rain_risk(self) -> int:
        return SURFACE_RISK.get(self.surface, 1)

    @property
    def rain_risk_label(self) -> str:
        return RISK_LABEL[self.rain_risk]

    def warning(self, precip_mm: float, wind_kmh: float) -> str | None:
        """Zwraca ostrzeżenie jeśli warunki + nawierzchnia = ryzyko."""
        msgs = []
        if precip_mm > 1 and self.rain_risk == 2:
            msgs.append(f"🔴 Śliska nawierzchnia ({self.surface}) przy opadach!")
        elif precip_mm > 3 and self.rain_risk == 1:
            msgs.append(f"⚠ Nawierzchnia ({self.surface}) może być śliska.")
        sac_risk = SAC.get(self.sac_scale, ("", "niskie"))[1]
        if sac_risk in ("wysokie", "bardzo wysokie", "ekstremalne") and precip_mm > 1:
            msgs.append(f"⚠ Trudny teren ({self.sac_label}) + opady = zwiększone ryzyko.")
        if wind_kmh > 50 and self.sac_scale in ("alpine_hiking", "demanding_alpine_hiking", "difficult_alpine_hiking"):
            msgs.append(f"🔴 Silny wiatr ({wind_kmh:.0f} km/h) na eksponowanym terenie!")
        return "  ".join(msgs) if msgs else None


# ---------- Overpass ----------

_cache: dict[tuple, SurfaceInfo] = {}


def get_surface(lat: float, lon: float, radius_m: int = 30) -> SurfaceInfo:
    key = (round(lat, 4), round(lon, 4))
    if key in _cache:
        return _cache[key]

    query = f"""
[out:json][timeout:10];
way(around:{radius_m},{lat},{lon})[highway];
out tags;
"""
    try:
        r = requests.post(OVERPASS_URL, data={"data": query}, timeout=15)
        r.raise_for_status()
        elements = r.json().get("elements", [])

        info = SurfaceInfo(lat=lat, lon=lon)

        # Wybierz najlepszy way (preferuj path/track, nie drogi asfaltowe)
        best = None
        for el in elements:
            tags = el.get("tags", {})
            hw = tags.get("highway", "")
            if hw in ("path", "track", "footway", "steps", "bridleway"):
                best = tags
                break
        if not best and elements:
            best = elements[0].get("tags", {})

        if best:
            info.surface          = best.get("surface", "nieznana")
            info.sac_scale        = best.get("sac_scale", "")
            info.trail_visibility = best.get("trail_visibility", "")
            info.highway          = best.get("highway", "")

        _cache[key] = info
        time.sleep(0.5)
        return info

    except Exception as e:
        return SurfaceInfo(lat=lat, lon=lon)


def enrich_rows(rows: list[dict]) -> list[dict]:
    """
    Dodaje dane o nawierzchni do każdego wiersza z weather_for_route.
    rows: lista dict z kluczami: lat, lon, mm, wind, ...
    """
    for row in rows:
        s = get_surface(row["lat"], row["lon"])
        row["surface"]    = s.surface
        row["sac"]        = s.sac_label
        row["rain_risk"]  = s.rain_risk_label
        row["warning"]    = s.warning(row["mm"], row["wind"])
    return rows