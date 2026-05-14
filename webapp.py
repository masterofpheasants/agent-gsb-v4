"""
webapp.py - Flask serwer dla Telegram Web App
"""
import os
import json
import threading
import requests
from flask import Flask, request, jsonify, render_template_string
from pathlib import Path

app = Flask(__name__)

_strava_tokens: dict[str, dict] = {}
_lock = threading.Lock()

# Wyniki zapisywane do pliku — przeżywają restart
RESULTS_FILE = Path("/tmp/gsb_results.json")
STRAVA_FILE = Path("/tmp/gsb_strava.json")
POIS_FILE = Path("/tmp/gsb_pois.json")


def _load_results() -> dict:
    try:
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_results(results: dict):
    try:
        RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_strava() -> dict:
    try:
        return json.loads(STRAVA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_strava(tokens: dict):
    try:
        STRAVA_FILE.write_text(json.dumps(tokens, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_pois() -> dict:
    try:
        return json.loads(POIS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pois(pois: dict):
    try:
        POIS_FILE.write_text(json.dumps(pois, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# Załaduj przy starcie
with _lock:
    _strava_tokens = _load_strava()

HTML = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>GSB Agent</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e8eaf0;
    --muted: #7a7f9a;
    --green: #4caf82;
    --yellow: #f5c842;
    --red: #e05a5a;
    --blue: #5b9bd5;
    --orange: #e08c5a;
    --radius: 12px;
    --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 14px;
    padding: 12px;
    min-height: 100vh;
  }
  .header {
    background: var(--card);
    border-radius: var(--radius);
    padding: 14px 16px;
    margin-bottom: 12px;
    border: 1px solid var(--border);
  }
  .header h1 { font-size: 16px; font-weight: 700; color: var(--green); margin-bottom: 4px; }
  .header .meta { color: var(--muted); font-size: 12px; display: flex; gap: 16px; flex-wrap: wrap; }
  .header .meta span { display: flex; align-items: center; gap: 4px; }
  .soil-box {
    background: var(--card); border-radius: var(--radius); padding: 10px 14px;
    margin-bottom: 12px; border: 1px solid var(--border); font-size: 12px; color: var(--muted);
  }
  .table-wrap { overflow-x: auto; border-radius: var(--radius); border: 1px solid var(--border); margin-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; min-width: 560px; }
  thead tr { background: #12151f; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  thead th { padding: 8px 10px; text-align: right; white-space: nowrap; font-weight: 600; }
  thead th.left { text-align: left; }
  tbody tr { border-top: 1px solid var(--border); transition: background 0.15s; }
  tbody tr:hover { background: rgba(255,255,255,0.03); }
  tbody td { padding: 9px 10px; text-align: right; white-space: nowrap; font-size: 13px; }
  tbody td.left { text-align: left; }
  .km { font-weight: 700; color: var(--blue); }
  .eta { color: var(--muted); font-size: 12px; }
  .temp { font-weight: 600; }
  .temp.cold { color: var(--blue); }
  .temp.ok { color: var(--green); }
  .temp.warm { color: var(--orange); }
  .rain.dry { color: var(--green); }
  .rain.light { color: var(--yellow); }
  .rain.heavy { color: var(--red); }
  .place { font-weight: 500; max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
  .sac { font-size: 11px; color: var(--muted); }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .badge-ok { background: rgba(76,175,130,0.15); color: var(--green); }
  .badge-warn { background: rgba(245,200,66,0.15); color: var(--yellow); }
  .badge-danger { background: rgba(224,90,90,0.15); color: var(--red); }
  .summary-box {
    background: var(--card); border-radius: var(--radius); padding: 14px 16px;
    margin-bottom: 12px; border: 1px solid var(--border); font-size: 13px; line-height: 1.6;
  }
  .summary-box h2 { font-size: 13px; color: var(--muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
  .warning {
    background: rgba(224,90,90,0.1); border: 1px solid rgba(224,90,90,0.3);
    border-radius: var(--radius); padding: 10px 14px; margin-bottom: 12px; color: var(--red); font-size: 12px;
  }
  .poi { font-size: 15px; }
  .no-data { text-align: center; padding: 60px 20px; color: var(--muted); }

  .tabs { display: flex; gap: 8px; margin-bottom: 12px; }
  .tab-btn {
    flex: 1; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius);
    background: var(--card); color: var(--muted); font-size: 13px; cursor: pointer;
    transition: all 0.15s;
  }
  .tab-btn.active { background: var(--green); color: #fff; border-color: var(--green); font-weight: 600; }

  .poi-item {
    background: var(--card); border-radius: var(--radius); padding: 12px 14px;
    margin-bottom: 8px; border: 1px solid var(--border);
  }
  .poi-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .poi-icon { font-size: 18px; }
  .poi-name { font-weight: 600; font-size: 14px; flex: 1; }
  .poi-km { color: var(--blue); font-weight: 700; font-size: 13px; }
  .poi-meta { color: var(--muted); font-size: 11px; display: flex; gap: 10px; flex-wrap: wrap; }
  .poi-proximity-trail { color: var(--green); font-size: 11px; font-weight: 600; }
  .poi-proximity-near { color: var(--yellow); font-size: 11px; }
  .poi-extra { margin-top: 6px; font-size: 11px; color: var(--muted); }
  .poi-cat-header {
    font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px;
    padding: 8px 0 4px; font-weight: 600;
  }
</style>
</head>
<body>
<div id="app">
  <div class="no-data">⏳ Ładowanie...</div>
</div>

<script>
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }

function tempClass(t) {
  if (t < 5) return 'cold';
  if (t < 18) return 'ok';
  return 'warm';
}
function rainClass(mm) {
  if (mm < 0.1) return 'dry';
  if (mm < 2) return 'light';
  return 'heavy';
}

const SURFACE_PL = {
  asphalt: "asfalt", concrete: "beton", "concrete:lanes": "beton (pasy)",
  "concrete:plates": "płyty betonowe", paving_stones: "kostka brukowa",
  sett: "kamień ciosany", cobblestone: "kocie łby", unhewn_cobblestone: "kamień polny",
  metal: "metal", wood: "drewno", tiles: "płytki", paved: "utwardzona",
  unpaved: "nieutwardzona", compacted: "ubita (żwir)", fine_gravel: "drobny żwir",
  gravel: "żwir", pebblestone: "otoczaki", dirt: "ziemia", earth: "ziemia",
  grass: "trawa", grass_paver: "trawa/kratka", ground: "grunt", mud: "błoto",
  sand: "piasek", woodchips: "zrębki", snow: "śnieg", ice: "lód",
  clay: "glina", rock: "skała", roots: "korzenie", stone: "kamień",
};

function translateSurface(s) {
  if (!s) return "";
  return SURFACE_PL[s.trim().replace(" *", "")] || s;
}

function slickBadge(slick) {
  if (!slick || slick === '-') return '';
  const s = slick.toLowerCase();
  if (s.includes('ok')) return `<span class="badge badge-ok">${slick}</span>`;
  if (s.includes('mokro') || s.includes('lekko')) return `<span class="badge badge-warn">${slick}</span>`;
  return `<span class="badge badge-danger">${slick}</span>`;
}

function poiIcon(kind) {
  const icons = { shelter: '🏠', summit: '⛰️', water: '💧', shop: '🛒', atm: '💳', restaurant: '🍽️', camping: '⛺' };
  return icons[kind] || '📌';
}

function socksHtml(r) {
  if (!r.socks || !r.socks.length) return '';
  const socksText = r.socks.join(", ").toLowerCase();
  const emoji = socksText.includes("wodoodporne") ? "🧦💧" :
                socksText.includes("przygotuj") ? "🧦🎒" : "🧦";
  return `<div class="soil-box">${emoji} ${r.socks.join(" ")}</div>`;
}

function tableHtml(rows) {
  let html = `<div class="table-wrap"><table>
    <thead><tr>
      <th class="left">Miejsce</th><th>km</th><th>ETA</th><th>°C</th>
      <th>mm</th><th>km/h</th><th class="left">Niebo</th>
      <th class="left">Podłoże</th><th class="left">Śliskość</th><th class="left">SAC</th>
    </tr></thead><tbody>`;
  for (const w of rows) {
    const tc = tempClass(w.t);
    const rc = rainClass(w.mm);
    html += `<tr>
      <td class="left"><span class="place">${w.poi_kind ? `<span class="poi">${poiIcon(w.poi_kind)}</span> ` : ''}${w.place || ''}</span></td>
      <td class="km">${(+w.km).toFixed(1)}</td>
      <td class="eta">${w.eta || ''}</td>
      <td class="temp ${tc}">${Math.round(w.t)}</td>
      <td class="rain ${rc}">${(+w.mm).toFixed(1)}</td>
      <td>${(+w.wind).toFixed(1)}</td>
      <td class="left">${w.sky || ''}</td>
      <td class="left">${translateSurface(w.surface || '')}</td>
      <td class="left">${slickBadge(w.slickness || '')}</td>
      <td class="left sac">${w.sac || ''}</td>
    </tr>`;
  }
  html += `</tbody></table></div>`;
  return html;
}

function renderPart(r) {
  let html = '';

  if (r.recommendation) {
    const rec = r.recommendation;
    const isGo = rec.includes('Idź');
    const isShorten = rec.includes('Skróć');
    const cls = isGo ? 'badge-ok' : isShorten ? 'badge-warn' : 'badge-danger';
    const emoji = isGo ? '✅' : isShorten ? '⚠️' : '🚫';
    html += `<div class="summary-box"><span class="badge ${cls}">${emoji} ${rec}</span>
      ${r.recommendation_reason ? `<p style="margin-top:8px;color:var(--muted);font-size:12px">${r.recommendation_reason}</p>` : ''}
    </div>`;
  }

  if (r.warnings && r.warnings.length) {
    r.warnings.forEach(w => { html += `<div class="warning">⚠️ ${w}</div>`; });
  }

  html += socksHtml(r);

  if (r.soil_summary) html += `<div class="soil-box">🌱 ${r.soil_summary}</div>`;

  html += tableHtml(r.rows || []);

  if (r.summary) html += `<div class="summary-box"><h2>Podsumowanie</h2>${r.summary}</div>`;

  return html;
}

function render(r) {
  if (r.agent_response && !(r.rows && r.rows.length)) {
    document.getElementById('app').innerHTML = `<div class="summary-box"><h2>Odpowiedź agenta</h2>${r.agent_response}</div>`;
    return;
  }

  if (r.part2) {
    document.getElementById('app').innerHTML =
      `<div class="soil-box" style="text-align:center;font-size:13px">📍 ${r.part_label || 'Część 1'}</div>` +
      renderPart(r) +
      `<div class="soil-box" style="text-align:center;font-size:13px;margin-top:12px">📍 ${r.part2.part_label || 'Część 2'}</div>` +
      renderPart(r.part2);
    return;
  }

  let html = `<div class="header">
    <h1>📍 ${r.start_name || ''}</h1>
    <div class="meta">
      <span>📅 ${r.date || ''}</span>
      <span>📏 ${r.length_km} km</span>
      <span>⛰️ +${r.ascent_m} m</span>
      ${r.dist_to_trail_km > 0 ? `<span>🔗 ${r.dist_to_trail_km} km od szlaku</span>` : ''}
    </div>
  </div>`;

  html += renderPart(r);

  if (r.narrative) {
    html += `<div class="summary-box"><h2>Opis trasy</h2>${r.narrative}</div>`;
  }

  document.getElementById('app').innerHTML = html;
}

let _routeData = null;
let _poisData = null;
let _activeTab = 'weather';

function renderTabs() {
  return `<div class="tabs">
    <button class="tab-btn ${_activeTab==='weather'?'active':''}" onclick="switchTab('weather')">🌤️ Pogoda</button>
    <button class="tab-btn ${_activeTab==='pois'?'active':''}" onclick="switchTab('pois')">📍 Obiekty</button>
  </div>`;
}

function switchTab(tab) {
  _activeTab = tab;
  if (tab === 'weather' && _routeData) {
    showWeather(_routeData);
  } else if (tab === 'pois') {
    showPois();
  }
}

function showWeather(data) {
  _activeTab = 'weather';
  let html = renderTabs();
  if (data.part2) {
    html += `<div class="soil-box" style="text-align:center;font-size:13px">📍 ${data.part_label || 'Część 1'}</div>` +
      renderPart(data) +
      `<div class="soil-box" style="text-align:center;font-size:13px;margin-top:12px">📍 ${data.part2.part_label || 'Część 2'}</div>` +
      renderPart(data.part2);
    document.getElementById('app').innerHTML = html;
    return;
  }
  html += `<div class="header">
    <h1>📍 ${data.start_name || ''}</h1>
    <div class="meta">
      <span>📅 ${data.date || ''}</span>
      <span>📏 ${data.length_km} km</span>
      <span>⛰️ +${data.ascent_m} m</span>
      ${data.estimated_time ? `<span>⏱️ ${data.estimated_time} (do ~${data.eta_end})</span>` : ''}
      ${data.dist_to_trail_km > 0 ? `<span>🔗 ${data.dist_to_trail_km} km od szlaku</span>` : ''}
    </div>
  </div>`;
  html += renderPart(data);
  if (data.narrative) html += `<div class="summary-box"><h2>Opis trasy</h2>${data.narrative}</div>`;
  document.getElementById('app').innerHTML = html;
}

function showPois() {
  _activeTab = 'pois';
  let html = renderTabs();
  if (!_poisData || !_poisData.length) {
    html += `<div class="no-data">Brak danych o obiektach.<br>Wróć do bota i wybierz kategorie.</div>`;
    document.getElementById('app').innerHTML = html;
    return;
  }

  // Grupuj po kategorii
  const byCat = {};
  for (const p of _poisData) {
    const cat = p.category_label || '📌 Inne';
    if (!byCat[cat]) byCat[cat] = [];
    byCat[cat].push(p);
  }

  for (const [cat, items] of Object.entries(byCat)) {
    html += `<div class="poi-cat-header">${cat}</div>`;
    for (const p of items) {
      const proxClass = p.proximity === 'na szlaku' ? 'poi-proximity-trail' : 'poi-proximity-near';
      html += `<div class="poi-item">
        <div class="poi-header">
          <span class="poi-icon">${p.icon}</span>
          <span class="poi-name">${p.name}</span>
          <span class="poi-km">${p.km} km</span>
        </div>
        <div class="poi-meta">
          <span class="${proxClass}">${p.proximity} (${p.dist_m} m)</span>
        </div>
        ${p.extra && p.extra.length ? `<div class="poi-extra">${p.extra.join(' · ')}</div>` : ''}
      </div>`;
    }
  }
  document.getElementById('app').innerHTML = html;
}

async function loadData() {
  const params = new URLSearchParams(window.location.search);
  const uid = params.get('uid') || 'demo';
  const tab = params.get('tab') || 'weather';

  try {
    const [routeResp, poisResp] = await Promise.all([
      fetch(`/api/result?uid=${encodeURIComponent(uid)}`),
      fetch(`/api/pois?uid=${encodeURIComponent(uid)}`),
    ]);

    if (routeResp.ok) {
      const data = await routeResp.json();
      if (data && !data.error) _routeData = data;
    }
    if (poisResp.ok) {
      const pois = await poisResp.json();
      if (Array.isArray(pois)) _poisData = pois;
    }
  } catch(e) {
    // ignore
  }

  if (!_routeData) {
    document.getElementById('app').innerHTML = `<div class="no-data">Brak danych. Wyślij zapytanie do bota.</div>`;
    return;
  }

  if (tab === 'pois') {
    showPois();
  } else {
    showWeather(_routeData);
  }
}

loadData();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/result")
def api_result():
    uid = request.args.get("uid", "")
    with _lock:
        results = _load_results()
        data = results.get(uid)
    if not data:
        return jsonify({"error": "no data"}), 404
    return jsonify(data)


@app.route("/api/store", methods=["POST"])
def api_store():
    payload = request.get_json()
    uid = payload.get("uid", "")
    data = payload.get("data", {})
    with _lock:
        results = _load_results()
        results[uid] = data
        # Zachowaj tylko ostatnie 50 wyników
        if len(results) > 50:
            oldest = sorted(results.keys())[:-50]
            for k in oldest:
                del results[k]
        _save_results(results)
    return jsonify({"ok": True})


STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")


@app.route("/strava/callback")
def strava_callback():
    code = request.args.get("code")
    state = request.args.get("state", "")
    state_parts = state.split(":")
    user_id = state_parts[0]
    days = int(state_parts[1]) if len(state_parts) > 1 else 30

    if not code or not user_id:
        return "Brakuje parametrów.", 400

    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }, timeout=10)

    if resp.status_code != 200:
        return "Błąd autoryzacji Strava.", 400

    token_data = resp.json()
    access_token = token_data.get("access_token")
    athlete = token_data.get("athlete", {})

    import time as _t
    since = int(_t.time()) - days * 86400
    acts_resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"after": since, "per_page": 50},
        timeout=10,
    )
    activities = acts_resp.json() if acts_resp.status_code == 200 else []

    hikes = [a for a in activities if a.get("type") in ("Hike", "Walk", "TrailRun")]
    recent_km = sum(a.get("distance", 0) for a in hikes) / 1000
    recent_count = len(hikes)

    avg_pace_kmh = 3.0
    if hikes:
        speeds = []
        for a in hikes:
            dist = a.get("distance", 0)
            t = a.get("moving_time", 0)
            if dist > 0 and t > 0:
                speeds.append((dist / 1000) / (t / 3600))
        if speeds:
            avg_pace_kmh = sum(speeds) / len(speeds)

    if recent_km > 150:
        fitness = "bardzo dobra"
    elif recent_km > 80:
        fitness = "dobra"
    elif recent_km > 30:
        fitness = "przeciętna"
    else:
        fitness = "niska"

    profile = {
        "name": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "athlete_id": athlete.get("id"),
        "access_token": access_token,
        "refresh_token": token_data.get("refresh_token"),
        "stats": {"recent_count": recent_count, "recent_km": round(recent_km, 1)},
        "avg_pace_kmh": round(avg_pace_kmh, 2),
        "fitness_level": fitness,
    }

    with _lock:
        _strava_tokens[str(user_id)] = profile
        _save_strava(_strava_tokens)

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>body{{font-family:sans-serif;text-align:center;padding:40px;background:#0f1117;color:#e8eaf0}}</style>
</head><body>
<h2>✅ Połączono ze Stravą!</h2>
<p>Witaj, {profile["name"]}!</p>
<p>Kondycja: <strong>{fitness}</strong></p>
<p>Dystans ({days} dni): <strong>{recent_km:.0f} km</strong></p>
<p>Możesz wrócić do bota Telegram.</p>
</body></html>"""


@app.route("/api/strava/profile/<user_id>")
def strava_profile(user_id):
    with _lock:
        profile = _strava_tokens.get(str(user_id))
    if not profile:
        return jsonify({"error": "no profile"}), 404
    safe = {k: v for k, v in profile.items() if k not in ("access_token", "refresh_token")}
    return jsonify(safe)


@app.route("/api/store_pois", methods=["POST"])
def api_store_pois():
    payload = request.get_json()
    uid = payload.get("uid", "")
    pois = payload.get("pois", [])
    with _lock:
        all_pois = _load_pois()
        all_pois[uid] = pois
        if len(all_pois) > 50:
            oldest = sorted(all_pois.keys())[:-50]
            for k in oldest:
                del all_pois[k]
        _save_pois(all_pois)
    return jsonify({"ok": True, "count": len(pois)})


@app.route("/api/pois")
def api_pois():
    uid = request.args.get("uid", "")
    with _lock:
        all_pois = _load_pois()
        data = all_pois.get(uid, [])
    return jsonify(data)


def run_webapp():
    port = int(os.environ.get("WEBAPP_PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    run_webapp()