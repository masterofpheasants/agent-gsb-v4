"""surface.py - nawierzchnia i trudnosc szlaku z OSM Overpass API"""
from __future__ import annotations
import time
from dataclasses import dataclass
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

SAC = {
    "hiking":                    ("T1 - latwy",           "niskie"),
    "mountain_hiking":           ("T2 - gorski",          "umiarkowane"),
    "demanding_mountain_hiking": ("T3 - wymagajacy",      "wysokie"),
    "alpine_hiking":             ("T4 - alpejski",        "bardzo wysokie"),
    "demanding_alpine_hiking":   ("T5 - trudny alpejski", "ekstremalne"),
    "difficult_alpine_hiking":   ("T6 - wspinaczkowy",    "ekstremalne"),
}

SURFACE_RISK = {
    "asphalt": 0, "paved": 0, "concrete": 0,
    "compacted": 1, "gravel": 1, "fine_gravel": 1,
    "ground": 2, "dirt": 2, "mud": 2,
    "grass": 2, "rock": 2, "stone": 2,
    "roots": 2, "wood": 2, "unpaved": 1,
}

RISK_LABEL = {0: "bezpieczna", 1: "uwaga", 2: "sliskie"}

# Realistyczne tempo dla planowania
MAX_KM_PER_DAY = 30


@dataclass
class SurfaceInfo:
    lat: float
    lon: float
    surface: str = "ground"
    sac_scale: str = ""
    trail_visibility: str = ""
    highway: str = ""
    inferred: bool = False

    @property
    def sac_label(self) -> str:
        return SAC.get(self.sac_scale, ("", ""))[0] if self.sac_scale else ""

    @property
    def rain_risk(self) -> int:
        return SURFACE_RISK.get(self.surface, 1)

    def rain_risk_label(self, precip_mm: float) -> str:
        """Pokazuje ryzyko tylko przy opadach."""
        if precip_mm < 0.5:
            return ""
        prefix = "~" if self.inferred else ""
        return prefix + RISK_LABEL[self.rain_risk]

    def warning(self, precip_mm: float, wind_kmh: float) -> str | None:
        msgs = []
        if precip_mm > 1 and self.rain_risk == 2:
            tag = ("~" if self.inferred else "") + self.surface
            msgs.append(f"Sliska nawierzchnia ({tag}) przy opadach!")
        elif precip_mm > 3 and self.rain_risk == 1:
            msgs.append(f"Nawierzchnia ({self.surface}) moze byc sliska.")
        sac_risk = SAC.get(self.sac_scale, ("", "niskie"))[1]
        if sac_risk in ("wysokie", "bardzo wysokie", "ekstremalne") and precip_mm > 1:
            msgs.append(f"Trudny teren ({self.sac_label}) + opady = ryzyko.")
        if wind_kmh > 50 and self.sac_scale in (
            "alpine_hiking", "demanding_alpine_hiking", "difficult_alpine_hiking"
        ):
            msgs.append(f"Silny wiatr ({wind_kmh:.0f} km/h) na eksponowanym terenie!")
        return "  ".join(msgs) if msgs else None


def _infer_surface(highway: str, sac_scale: str, ele: float) -> str:
    if sac_scale in ("alpine_hiking", "demanding_alpine_hiking", "difficult_alpine_hiking"):
        return "rock"
    if sac_scale in ("demanding_mountain_hiking", "mountain_hiking"):
        return "ground"
    if ele > 1400:
        return "rock"
    if ele > 900:
        return "ground"
    if highway == "track":
        return "compacted"
    if highway in ("path", "footway", "bridleway"):
        return "ground"
    if highway in ("residential", "service", "unclassified"):
        return "asphalt"
    return "ground"


_cache: dict[tuple, SurfaceInfo] = {}


def get_surface(lat: float, lon: float, ele: float = 0, radius_m: int = 100) -> SurfaceInfo:
    key = (round(lat, 4), round(lon, 4))
    if key in _cache:
        return _cache[key]

    query = f"""
[out:json][timeout:10];
way(around:{radius_m},{lat},{lon})[highway];
out tags;
"""
    try:
        r = requests.post(OVERPASS_URL, data={"data": query}, timeout=15, verify=False)
        r.raise_for_status()
        elements = r.json().get("elements", [])

        info = SurfaceInfo(lat=lat, lon=lon)
        best = None
        for el in elements:
            tags = el.get("tags", {})
            if tags.get("highway", "") in ("path", "track", "footway", "steps", "bridleway"):
                best = tags
                break
        if not best and elements:
            best = elements[0].get("tags", {})

        if best:
            info.highway          = best.get("highway", "")
            info.sac_scale        = best.get("sac_scale", "")
            info.trail_visibility = best.get("trail_visibility", "")
            osm_surface           = best.get("surface", "")
            if osm_surface:
                info.surface  = osm_surface
                info.inferred = False
            else:
                info.surface  = _infer_surface(info.highway, info.sac_scale, ele)
                info.inferred = True
        else:
            info.surface  = _infer_surface("", "", ele)
            info.inferred = True

        _cache[key] = info
        time.sleep(0.5)
        return info

    except Exception:
        info = SurfaceInfo(lat=lat, lon=lon)
        info.surface  = _infer_surface("", "", ele)
        info.inferred = True
        return info


def check_distance_warning(distance_km: float, ascent_m: int) -> str | None:
    """Ostrzega gdy dystans lub przewyzszenie przekraczaja dzienne mozliwosci."""
    msgs = []
    if distance_km > MAX_KM_PER_DAY:
        days = distance_km / MAX_KM_PER_DAY
        msgs.append(
            f"Odcinek {distance_km:.0f} km to wiecej niz jeden dzien marszu "
            f"(~{MAX_KM_PER_DAY} km/dzien). Rozwaz podzial na {days:.1f} dni."
        )
    if ascent_m > 2000:
        msgs.append(f"Przewyzszenie {ascent_m} m jest bardzo duze jak na jeden dzien.")
    return "  ".join(msgs) if msgs else None


def enrich_rows(rows: list[dict]) -> list[dict]:
    for row in rows:
        s = get_surface(row["lat"], row["lon"], ele=row.get("ele", 0))
        row["surface"]   = s.surface + (" *" if s.inferred else "")
        row["sac"]       = s.sac_label
        row["rain_risk"] = s.rain_risk_label(row["mm"])
        row["warning"]   = s.warning(row["mm"], row["wind"])
    return rows
