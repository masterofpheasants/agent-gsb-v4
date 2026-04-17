# Beskidzki Agent

Agent AI wspierający wędrówki po Beskidach.

## Status

MVP: prognoza pogody wzdłuż odcinka trasy z GPX (eksport z mapy.com).

Planowane moduły: Strava, mapy.com/mapa-turystyczna.pl, noclegi/sklepy/bankomaty (OSM), nawierzchnia (AllTrails/OSM).

## Instalacja

```bash
pip install -r requirements.txt
```

## Użycie

1. mapy.com → trasa → *Udostępnij* → *Pobierz GPX* → `trasa.gpx`
2. Uruchom:

```bash
python agent.py trasa.gpx --start "Hala Miziowa" --end "Babia Góra" --date 2026-04-20
python agent.py trasa.gpx --start 0 --end 12.5 --start-hour 7 --pace 2.8
```

### Parametry

| Flag | Opis | Domyślnie |
|---|---|---|
| `gpx` | plik GPX | — |
| `--start` | nazwa waypointu lub km od początku | — |
| `--end` | jw. | — |
| `--date` | YYYY-MM-DD | dziś |
| `--start-hour` | godzina wyjścia | 8 |
| `--pace` | km/h | 3.0 |
| `--samples` | ilość próbek pogodowych | 5 |

## Źródła danych

- **Open-Meteo** — pogoda, bez klucza API
- **GPX** — dowolny eksport (mapy.com, mapa-turystyczna.pl, Garmin)

## Licencja

MIT
