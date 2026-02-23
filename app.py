"""
Anketa – Záložkový průzkum
Flask backend: hlasování, výsledky, reset s tokenem.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, url_for

load_dotenv()  # načte .env lokálně; na Render.com jsou proměnné v dashboardu

from config import CHOICES, DATA_FILE, QUESTION, RESET_TOKEN

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Pomocné funkce pro práci s daty
# ---------------------------------------------------------------------------

def _ensure_data_file() -> None:
    """Vytvoří data/votes.json pokud neexistuje."""
    path = Path(DATA_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _write_votes({key: 0 for key in CHOICES})
        logger.info("Vytvořen nový soubor s hlasy: %s", DATA_FILE)


def _read_votes() -> dict:
    """Načte hlasy ze souboru. Vrátí prázdný slovník při chybě."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Zajisti, že každá volba existuje
        for key in CHOICES:
            data.setdefault(key, 0)
        return data
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Nelze načíst hlasy (%s), inicializuji nové.", exc)
        votes = {key: 0 for key in CHOICES}
        _write_votes(votes)
        return votes


def _write_votes(votes: dict) -> None:
    """Zapíše hlasy do souboru."""
    Path(DATA_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(votes, f, ensure_ascii=False, indent=2)


def _compute_stats(votes: dict) -> list[dict]:
    """
    Vrátí seznam diktů pro každou možnost:
    { key, label, count, percent, bar_width }
    """
    total = sum(votes.values())
    stats = []
    for key, label in CHOICES.items():
        count = votes.get(key, 0)
        percent = round(count / total * 100) if total > 0 else 0
        stats.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "percent": percent,
            }
        )
    return stats, total


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Hlavní stránka s formulářem pro hlasování."""
    _ensure_data_file()
    return render_template("index.html", question=QUESTION, choices=CHOICES, view="vote")


@app.route("/results")
def results():
    """Zobrazí výsledky bez hlasování."""
    _ensure_data_file()
    votes = _read_votes()
    stats, total = _compute_stats(votes)

    # Zpráva po resetu předaná query parametrem
    reset_status = request.args.get("reset")
    flash_msg = None
    flash_type = None
    if reset_status == "ok":
        flash_msg = "✓ Hlasování bylo úspěšně resetováno."
        flash_type = "success"
    elif reset_status == "denied":
        flash_msg = "✗ Nesprávný token – reset nebyl proveden."
        flash_type = "error"

    return render_template(
        "index.html",
        question=QUESTION,
        choices=CHOICES,
        view="results",
        stats=stats,
        total=total,
        flash_msg=flash_msg,
        flash_type=flash_type,
    )


@app.route("/vote", methods=["POST"])
def vote():
    """Uloží hlas a přesměruje na výsledky."""
    choice = request.form.get("choice", "").strip().lower()

    if choice not in CHOICES:
        logger.warning("Neplatná volba: %r", choice)
        return render_template(
            "index.html",
            question=QUESTION,
            choices=CHOICES,
            view="vote",
            flash_msg="✗ Vyber prosím platnou možnost.",
            flash_type="error",
        ), 400

    votes = _read_votes()
    votes[choice] += 1
    _write_votes(votes)
    logger.info("Hlas uložen: %s (celkem %s)", choice, votes[choice])

    return redirect(url_for("results"))


@app.route("/reset", methods=["POST"])
def reset():
    """Resetuje všechny hlasy. Vyžaduje správný token."""
    token = request.form.get("token", "")

    if token != RESET_TOKEN:
        logger.warning("Nesprávný reset token.")
        return redirect(url_for("results", reset="denied"))

    votes = {key: 0 for key in CHOICES}
    _write_votes(votes)
    logger.info("Hlasování bylo resetováno.")
    return redirect(url_for("results", reset="ok"))


# ---------------------------------------------------------------------------
# Spuštění (lokálně)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _ensure_data_file()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
