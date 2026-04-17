"""
bot.py - Telegram bot dla Beskidzkiego Agenta
"""
import json
import logging
import os
import threading
import requests as http_requests
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

GPX_PATH = Path(__file__).parent / "gsb.gpx"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

WEBAPP_URL = os.environ.get("WEBAPP_URL", "")


# ---------- Helpers ----------

def parse_message(text: str):
    parts = text.strip().split()
    if len(parts) < 2:
        return None

    trip_date = date.today()
    if len(parts) >= 3:
        try:
            trip_date = date.fromisoformat(parts[-1])
            parts = parts[:-1]
        except ValueError:
            pass

    try:
        distance = float(parts[-1])
    except ValueError:
        return None

    location = " ".join(parts[:-1])
    return location, distance, trip_date


def store_result(uid: str, data: dict):
    """Zapisuje wynik do webapp API."""
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


def run_agent(location: str, distance: float, trip_date: date):
    """Odpala agenta, zwraca (tekst, dict_surowy)."""
    try:
        from agent import run, _render, _narrative, _slickness
        result = run(
            gpx_path=GPX_PATH,
            location=location,
            distance_km=distance,
            day=trip_date,
            samples=5,
            start_hour=8,
            pace_kmh=3.0,
        )
        for w in result.get("rows", []):
            w["slickness"] = _slickness(w)
        result["narrative"] = _narrative(result.get("rows", []))
        result["soil_summary"] = result["rows"][0].get("soil_summary", "") if result.get("rows") else ""
        return _render(result), result
    except ValueError as e:
        return f"Blad: {e}", None
    except FileNotFoundError:
        return "Blad: nie znaleziono pliku gsb.gpx.", None
    except Exception as e:
        logging.exception("Agent error")
        return f"Blad agenta: {e}", None


# ---------- Handlery ----------

HELP = """Beskidzki Agent GSB

Wyslij:
  <miejscowosc> <km>
  <miejscowosc> <km> <data>

Przyklady:
  Jordanow 20
  Babia Gora 15 2026-05-10
  Ustron 30

Mozesz tez wyslac lokalizacje GPS z Telegrama,
a potem napisac ile km chcesz przejsc.

/help - ta wiadomosc
"""

_gps_cache: dict[int, tuple[float, float]] = {}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    user_id = update.effective_user.id
    _gps_cache[user_id] = (loc.latitude, loc.longitude)
    await update.message.reply_text(
        f"Zapisalem Twoja lokalizacje ({loc.latitude:.4f}, {loc.longitude:.4f}).\n"
        f"Teraz napisz ile km chcesz przejsc, np: 20"
    )


def _webapp_button(uid: str) -> InlineKeyboardMarkup | None:
    if not WEBAPP_URL:
        return None
    url = f"{WEBAPP_URL}/?uid={uid}"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Pokaż tabelę", web_app=WebAppInfo(url=url))
    ]])


async def _send_result(update: Update, text: str, raw: dict | None, uid: str):
    if raw:
        store_result(uid, raw)

    lines = text.split("\n")
    short = "\n".join(lines[:4])
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
            await update.message.reply_text("Szukam trasy i pogody, chwileczke...")
            result_text, raw = run_agent(location, distance, date.today())
            uid = f"{user_id}_{int(date.today().strftime('%Y%m%d'))}"
            await _send_result(update, result_text, raw, uid)
            return
        except ValueError:
            pass

    parsed = parse_message(text)
    if not parsed:
        await update.message.reply_text(
            "Nie rozumiem. Przyklad: Jordanow 20\nLub wyslij lokalizacje GPS i napisz ile km."
        )
        return

    location, distance, trip_date = parsed
    await update.message.reply_text(f"Szukam trasy od '{location}' na {distance:.0f} km...")

    result_text, raw = run_agent(location, distance, trip_date)
    uid = f"{user_id}_{location.replace(' ','_')}_{trip_date}"
    await _send_result(update, result_text, raw, uid)


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
    if not GPX_PATH.exists():
        print(f"UWAGA: brak pliku {GPX_PATH} - bot uruchomiony ale nie bedzie dzialal bez GPX.")
    if not WEBAPP_URL:
        print("UWAGA: brak WEBAPP_URL - tabela HTML niedostepna, tryb tekstowy.")

    # Uruchom Flask webapp w tle
    from webapp import run_webapp
    t = threading.Thread(target=run_webapp, daemon=True)
    t.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Bot uruchomiony. Zatrzymaj przez Ctrl+C.")
    app.run_polling()


if __name__ == "__main__":
    main()