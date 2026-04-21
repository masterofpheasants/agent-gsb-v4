"""
bot.py - Telegram bot dla Beskidzkiego Agenta
"""
import json
import logging
import os
import threading
import requests as http_requests
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

load_dotenv()

STAGES_DIR = Path(__file__).parent / "mapy" / "GSB_E.gpx"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")

_hour_cache: dict[int, int] = {}
_gps_cache: dict[int, tuple[float, float]] = {}
_pending: dict[int, dict] = {}


# ---------- Helpers ----------

def parse_message(text: str, user_id: int = 0):
    parts = text.strip().split()
    if len(parts) < 2:
        return None

    trip_date = None
    if len(parts) >= 3:
        try:
            trip_date = date.fromisoformat(parts[-1])
            parts = parts[:-1]
        except ValueError:
            pass

    start_hour = None
    if len(parts) >= 3:
        try:
            h = parts[-1].replace(":00", "").replace("h", "")
            start_hour = int(h)
            if 0 <= start_hour <= 23:
                parts = parts[:-1]
                _hour_cache[user_id] = start_hour
            else:
                start_hour = None
        except ValueError:
            pass

    if start_hour is None:
        start_hour = _hour_cache.get(user_id, 7)

    try:
        distance = float(parts[-1])
    except ValueError:
        return None

    location = " ".join(parts[:-1])
    return location, distance, trip_date, start_hour


def store_result(uid: str, data: dict):
    if not WEBAPP_URL:
        return
    try:
        clean = json.loads(json.dumps(data, default=str))
        http_requests.post(
            f"{WEBAPP_URL}/api/store",
            json={"uid": uid, "data": clean},
            timeout=5
        )
    except Exception as e:
        logging.warning(f"Nie mozna zapisac wyniku do webapp: {e}")


def get_strava_profile(user_id: int) -> dict | None:
    """Pobiera profil Strava użytkownika z webapp API."""
    if not WEBAPP_URL:
        return None
    try:
        resp = http_requests.get(
            f"{WEBAPP_URL}/api/strava/profile/{user_id}",
            timeout=5
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def run_agent(location: str, distance: float, trip_date: date, start_hour: int = 7, user_id: int = 0):
    try:
        from agent import run, _render, _narrative
        strava = get_strava_profile(user_id) if user_id else None
        result = run(
            gpx_path=STAGES_DIR,
            location=location,
            distance_km=distance,
            day=trip_date,
            samples=5,
            start_hour=start_hour,
            pace_kmh=3.0,
            strava_profile=strava,
        )
        result["narrative"] = _narrative(result.get("rows", []))
        result["soil_summary"] = result.get("soil_summary", "")
        return _render(result), result
    except ValueError as e:
        return f"Blad: {e}", None
    except FileNotFoundError as e:
        return f"Blad: {e}", None
    except Exception as e:
        logging.exception("Agent error")
        return f"Blad agenta: {e}", None


def _date_keyboard() -> InlineKeyboardMarkup:
    today = date.today()
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 Dziś", callback_data=f"date:{today.isoformat()}"),
        InlineKeyboardButton("📅 Jutro", callback_data=f"date:{(today + timedelta(1)).isoformat()}"),
        InlineKeyboardButton("📅 Pojutrze", callback_data=f"date:{(today + timedelta(2)).isoformat()}"),
    ]])


def _webapp_button(uid: str) -> InlineKeyboardMarkup | None:
    if not WEBAPP_URL:
        return None
    url = f"{WEBAPP_URL}/?uid={uid}"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Pokaż tabelę", web_app=WebAppInfo(url=url))
    ]])


# ---------- Handlery ----------

HELP = """Beskidzki Agent GSB

Wyslij:
  <miejscowosc> <km>
  <miejscowosc> <km> <godzina>
  <miejscowosc> <km> <godzina> <data>

Przyklady:
  Jordanow 20
  Jordanow 20 6
  Babia Gora 15 7:00 2026-05-10

Jesli nie podasz daty - wybierzesz ja przyciskiem.
Godzina startu jest zapamietywana (domyslnie 7:00).

Mozesz tez wyslac lokalizacje GPS i napisac ile km.

/connect_strava - polacz konto Strava
/strava - pokaz swoj profil Strava
/help - ta wiadomosc
"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def cmd_connect_strava(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not STRAVA_CLIENT_ID or not WEBAPP_URL:
        await update.message.reply_text("Integracja ze Strava nie jest skonfigurowana.")
        return

    callback_url = f"{WEBAPP_URL}/strava/callback"
    auth_url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&redirect_uri={callback_url}"
        f"&response_type=code"
        f"&scope=activity:read_all"
        f"&state={user_id}"
    )
    await update.message.reply_text(
        "Kliknij poniższy link aby połączyć konto Strava:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏃 Połącz ze Strava", url=auth_url)
        ]])
    )


async def cmd_strava(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    profile = get_strava_profile(user_id)
    if not profile:
        await update.message.reply_text(
            "Nie masz połączonego konta Strava.\nUżyj /connect_strava"
        )
        return

    stats = profile.get("stats", {})
    pace = profile.get("avg_pace_kmh", 0)
    fitness = profile.get("fitness_level", "nieznany")

    await update.message.reply_text(
        f"🏃 Strava: {profile.get('name', '')}\n"
        f"Kondycja: {fitness}\n"
        f"Średnie tempo: {pace:.1f} km/h\n"
        f"Aktywności (30 dni): {stats.get('recent_count', 0)}\n"
        f"Dystans (30 dni): {stats.get('recent_km', 0):.0f} km"
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    user_id = update.effective_user.id
    _gps_cache[user_id] = (loc.latitude, loc.longitude)
    await update.message.reply_text(
        f"Zapisalem Twoja lokalizacje ({loc.latitude:.4f}, {loc.longitude:.4f}).\n"
        f"Teraz napisz ile km chcesz przejsc, np: 20"
    )


async def _send_result(update: Update, text: str, raw: dict | None, uid: str):
    if raw:
        store_result(uid, raw)

    lines = text.split("\n")
    short = "\n".join(lines[:5])
    keyboard = _webapp_button(uid)

    if keyboard:
        await update.message.reply_text(
            f"```\n{short}\n```\nSzczegóły w tabeli 👇",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        for chunk in _split(text):
            await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if user_id in _gps_cache:
        try:
            distance = float(text)
            lat, lon = _gps_cache.pop(user_id)
            location = f"{lat},{lon}"
            start_hour = _hour_cache.get(user_id, 7)
            _pending[user_id] = {"location": location, "distance": distance, "start_hour": start_hour}
            await update.message.reply_text(
                "Na kiedy sprawdzić pogodę?",
                reply_markup=_date_keyboard()
            )
            return
        except ValueError:
            pass

    parsed = parse_message(text, user_id)
    if not parsed:
        await update.message.reply_text(
            "Nie rozumiem. Przyklad: Jordanow 20\nLub wyslij lokalizacje GPS i napisz ile km."
        )
        return

    location, distance, trip_date, start_hour = parsed

    if trip_date is not None:
        long_info = " To może chwilę potrwać (długa trasa)... ⏳" if distance > 30 else ""
        await update.message.reply_text(
            f"Szukam trasy od '{location}' na {distance:.0f} km, start {start_hour}:00, {trip_date}...{long_info}"
        )
        result_text, raw = run_agent(location, distance, trip_date, start_hour, user_id)
        uid = f"{user_id}_{location.replace(' ','_')}_{trip_date}"
        await _send_result(update, result_text, raw, uid)
    else:
        _pending[user_id] = {"location": location, "distance": distance, "start_hour": start_hour}
        await update.message.reply_text(
            f"Trasa od '{location}', {distance:.0f} km, start {start_hour}:00\nNa kiedy?",
            reply_markup=_date_keyboard()
        )


async def handle_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not query.data.startswith("date:"):
        return

    trip_date = date.fromisoformat(query.data.split(":")[1])
    pending = _pending.pop(user_id, None)

    if not pending:
        await query.edit_message_text("Sesja wygasła. Wyślij zapytanie ponownie.")
        return

    location = pending["location"]
    distance = pending["distance"]
    start_hour = pending["start_hour"]

    long_info = " To może chwilę potrwać (długa trasa)... ⏳" if distance > 30 else ""
    await query.edit_message_text(
        f"Szukam trasy od '{location}' na {distance:.0f} km, start {start_hour}:00, {trip_date}...{long_info}"
    )

    result_text, raw = run_agent(location, distance, trip_date, start_hour, user_id)
    uid = f"{user_id}_{location.replace(' ','_')}_{trip_date}"

    if raw:
        store_result(uid, raw)

    lines = result_text.split("\n")
    short = "\n".join(lines[:5])
    keyboard = _webapp_button(uid)

    if keyboard:
        await query.message.reply_text(
            f"```\n{short}\n```\nSzczegóły w tabeli 👇",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        for chunk in _split(result_text):
            await query.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


def _split(text: str, limit: int = 3800) -> list[str]:
    lines = text.split("\n")
    chunks, current = [], []
    length = 0
    for line in lines:
        if length + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


# ---------- Main ----------

def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("Brak BOT_TOKEN w zmiennych środowiskowych")
    if not WEBAPP_URL:
        print("UWAGA: brak WEBAPP_URL - tabela HTML niedostepna, tryb tekstowy.")

    from webapp import run_webapp
    t = threading.Thread(target=run_webapp, daemon=True)
    t.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("connect_strava", cmd_connect_strava))
    app.add_handler(CommandHandler("strava", cmd_strava))
    app.add_handler(CallbackQueryHandler(handle_date_callback, pattern="^date:"))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Bot uruchomiony. Zatrzymaj przez Ctrl+C.")
    app.run_polling()


if __name__ == "__main__":
    main()