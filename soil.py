"""soil.py - analiza nasaczenia gruntu na podstawie historii opadow

Uzywa Open-Meteo Historical API (ostatnie 3 dni, bez klucza).
Liczy wskaznik nasaczenia gleby uwzgledniajac "pamiec" opadow:
  - deszcz 1 dzien temu wazy 1.0
  - deszcz 2 dni temu wazy 0.5
  - deszcz 3 dni temu wazy 0.25

Wynik: SoilState z poziomem nasaczenia i ostrzezeniem.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


WEIGHTS = {1: 1.0, 2: 0.5, 3: 0.25}  # wagi dla dni wstecz

# Progi nasaczenia (mm wazone)
THRESHOLDS = {
    "sucho":     (0,    5),
    "lekko":     (5,    15),
    "nasaczone": (15,   30),
    "bloto":     (30,   999),
}


@dataclass
class SoilState:
    weighted_mm: float       # wazona suma opadow
    raw_mm: list[float]      # [dzis-1, dzis-2, dzis-3]
    level: str               # "sucho" | "lekko" | "nasaczone" | "bloto"

    def warning(self, surface: str, inferred: bool = False) -> str | None:
        """Ostrzezenie o kondycji nawierzchni na podstawie historii."""
        # Nawierzchnie podatne na rozmakanie
        soft = {"ground", "dirt", "mud", "grass", "roots", "unpaved"}
        # Usun gwiazdke z inferred surface
        clean_surface = surface.replace(" *", "").strip()

        if self.level == "sucho":
            return None

        if clean_surface not in soft and not inferred:
            # twarda nawierzchnia, brak ryzyka
            return None

        msgs = {
            "lekko":     "Grunt lekko wilgotny - uwaga na sliska trawe.",
            "nasaczone": "Grunt nasaczony ({:.0f}mm/3dni) - bloto na podejsciach mozliwe.".format(self.weighted_mm),
            "bloto":     "Grunt mocno nasaczony ({:.0f}mm/3dni) - bloto pewne, podejscia bardzo sliskie!".format(self.weighted_mm),
        }
        return msgs.get(self.level)

    def summary(self) -> str:
        days = ", ".join(f"{mm:.1f}mm" for mm in self.raw_mm)
        return f"Opady ostatnie 3 dni: {days} | grunt: {self.level} (wskaznik {self.weighted_mm:.1f})"


def _level(weighted_mm: float) -> str:
    for name, (lo, hi) in THRESHOLDS.items():
        if lo <= weighted_mm < hi:
            return name
    return "bloto"


def fetch_soil_state(lat: float, lon: float, today: date) -> SoilState:
    """Pobiera dane historyczne i liczy nasaczenie gruntu."""
    start = today - timedelta(days=3)
    end   = today - timedelta(days=1)

    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/archive",
            params={
                "latitude":  lat,
                "longitude": lon,
                "daily":     "precipitation_sum",
                "timezone":  "Europe/Warsaw",
                "start_date": start.isoformat(),
                "end_date":   end.isoformat(),
            },
            timeout=15,
            verify=False,
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        times  = daily.get("time", [])
        precip = daily.get("precipitation_sum", [])

        # Mapuj date -> mm
        by_date = {t: p for t, p in zip(times, precip) if p is not None}

        raw = []
        weighted = 0.0
        for days_back in (1, 2, 3):
            d = (today - timedelta(days=days_back)).isoformat()
            mm = by_date.get(d, 0.0) or 0.0
            raw.append(mm)
            weighted += mm * WEIGHTS[days_back]

        return SoilState(
            weighted_mm=round(weighted, 1),
            raw_mm=raw,
            level=_level(weighted),
        )

    except Exception:
        return SoilState(weighted_mm=0, raw_mm=[0, 0, 0], level="sucho")


def enrich_with_soil(rows: list[dict], today: date) -> list[dict]:
    """
    Dodaje dane o nasaczeniu gruntu do kazdego wiersza.
    Grupuje punkty blisko siebie (unika wielokrotnych zapytan dla tej samej lokalizacji).
    """
    # Jedno zapytanie na caly odcinek - uzyj srodkowego punktu
    # (grunt nasacza sie w podobnym stopniu na krotkich odcinkach)
    if not rows:
        return rows

    mid = rows[len(rows) // 2]
    soil = fetch_soil_state(mid["lat"], mid["lon"], today)

    for row in rows:
        row["soil"]         = soil
        row["soil_summary"] = soil.summary()
        row["soil_warning"] = soil.warning(
            row.get("surface", "ground"),
            inferred="*" in row.get("surface", "")
        )

    return rows