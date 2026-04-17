"""
bot.py - Telegram bot dla Beskidzkiego Agenta

Uzycie na telefonie:
  /start          - pomoc
  Jordanow 20     - prognoza od Jordanowa na 20 km
  49.123,19.456 15 - prognoza z GPS na 15 km

Uruchomienie:
  python bot.py
"""
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Laduj token z .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Sciezka do GPX - zakladamy ze jest w tym samym folderze
GPX_PATH = Path(__file__).parent / "gsb.gpx"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)


# ---------- Helpers ----------

def parse_message(text: str) -> tuple[str, float] | None:
    """
    Parsuje wiadomosc uzytkownika.
    Formaty:
      'Jordanow 20'
      'Babia Gora 15 2026-05-01'
      '49.123,19.456 10'
    Zwraca (lokalizacja, dystans) lub None jesli nie mozna sparsowac.
    """
    parts = text.strip().split()
    if len(parts) < 2:
        return None

    # Sprawdz czy ostatni element to data
    trip_date = date.today()
    if len(parts) >= 3:
        try:
            trip_date = date.fromisoformat(parts[-1])
            parts = parts[:-1]
        except ValueError:
            pass

    # Ostatni element to dystans
    try:
        distance = float(parts[-1])
    except ValueError:
        return None

    location = " ".join(parts[:-1])
    return location, distance, trip_date


def run_agent(location: str, distance: float, trip_date: date) -> str:
    """Odpala agenta i zwraca sformatowany wynik."""
    try:
        from agent import run, _render
        result = run(
            gpx_path=GPX_PATH,
            location=location,
            distance_km=distance,
            day=trip_date,
            samples=5,
            start_hour=8,
            pace_kmh=3.0,
        )
        return _render(result)
    except ValueError as e:
        return f"Blad: {e}"
    except FileNotFoundError:
        return "Blad: nie znaleziono pliku gsb.gpx. Upewnij sie ze jest w folderze bota."
    except Exception as e:
        logging.exception("Agent error")
        return f"Blad agenta: {e}"


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

# Pamietamy ostatnia lokalizacje GPS z telefonu (per user)
_gps_cache: dict[int, tuple[float, float]] = {}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uzytkownik wyslal lokalizacje GPS z telefonu."""
    loc = update.message.location
    user_id = update.effective_user.id
    _gps_cache[user_id] = (loc.latitude, loc.longitude)
    await update.message.reply_text(
        f"Zapisalem Twoja lokalizacje ({loc.latitude:.4f}, {loc.longitude:.4f}).\n"
        f"Teraz napisz ile km chcesz przejsc, np: 20"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Jesli uzytkownik wyslal GPS wczesniej i teraz podaje tylko liczbe
    if user_id in _gps_cache:
        try:
            distance = float(text)
            lat, lon = _gps_cache.pop(user_id)
            location = f"{lat},{lon}"
            await update.message.reply_text("Szukam trasy i pogody, chwileczke...")
            result = run_agent(location, distance, date.today())
            # Telegram ma limit 4096 znakow - podziel jesli za dlugie
            for chunk in _split(result):
                await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")
            return
        except ValueError:
            pass  # nie liczba - traktuj normalnie

    # Standardowy format: "miejscowosc km" lub "miejscowosc km data"
    parsed = parse_message(text)
    if not parsed:
        await update.message.reply_text(
            "Nie rozumiem. Przyklad: Jordanow 20\nLub wyslij lokalizacje GPS i napisz ile km."
        )
        return

    location, distance, trip_date = parsed
    await update.message.reply_text(f"Szukam trasy od '{location}' na {distance:.0f} km...")

    result = run_agent(location, distance, trip_date)
    for chunk in _split(result):
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


def _split(text: str, limit: int = 3800) -> list[str]:
    """Dzieli dlugi tekst na kawалki dla Telegrama."""
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
    if not TOKEN:
        raise RuntimeError("Brak BOT_TOKEN w pliku .env")
    if not GPX_PATH.exists():
        print(f"UWAGA: brak pliku {GPX_PATH} - bot uruchomiony ale nie bedzie dzialal bez GPX.")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot uruchomiony. Zatrzymaj przez Ctrl+C.")
    app.run_polling()


if __name__ == "__main__":
    main()