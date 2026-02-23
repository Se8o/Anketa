"""Anketa – Záložkový průzkum

Flask voting application with thread-safe persistent JSON storage.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, url_for

load_dotenv()

from config import CHOICES, DATA_FILE, QUESTION, RESET_TOKEN  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------


class VoteTally(TypedDict):
    key: str
    label: str
    count: int
    percent: int


class VoteStore:
    """Thread-safe persistent vote store backed by an atomic-write JSON file.

    All public methods acquire a reentrant lock so the store is safe for use
    under Gunicorn's threaded or gevent workers.
    """

    def __init__(self, path: str | Path, choices: dict[str, str]) -> None:
        self._path = Path(path)
        self._choices = choices
        self._lock = threading.Lock()
        self._ensure_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cast(self, choice: str) -> None:
        """Increment the vote counter for *choice*.

        Raises:
            KeyError: if *choice* is not a recognised option.
        """
        if choice not in self._choices:
            raise KeyError(f"Unknown choice: {choice!r}")

        with self._lock:
            votes = self._read()
            votes[choice] += 1
            self._write(votes)

        logger.info("Vote cast: %s (option total: %d)", choice, votes[choice])

    def tally(self) -> tuple[list[VoteTally], int]:
        """Return per-choice statistics and the grand total."""
        with self._lock:
            votes = self._read()

        total = sum(votes.values())
        stats: list[VoteTally] = [
            {
                "key": key,
                "label": label,
                "count": (count := votes.get(key, 0)),
                "percent": round(count / total * 100) if total else 0,
            }
            for key, label in self._choices.items()
        ]
        return stats, total

    def reset(self) -> None:
        """Zero out all vote counters."""
        with self._lock:
            self._write({key: 0 for key in self._choices})

        logger.info("Vote store reset.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write({key: 0 for key in self._choices})
            logger.info("Initialised vote store at %s", self._path)

    def _read(self) -> dict[str, int]:
        """Deserialise votes from disk, re-initialising on corrupt data."""
        try:
            data: dict[str, int] = json.loads(
                self._path.read_text(encoding="utf-8")
            )
            for key in self._choices:
                data.setdefault(key, 0)
            return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Corrupt vote store (%s). Re-initialising.", exc)
            fresh = {key: 0 for key in self._choices}
            self._write(fresh)
            return fresh

    def _write(self, votes: dict[str, int]) -> None:
        """Write votes atomically via a temp file + rename.

        This prevents partial reads if the process is interrupted mid-write.
        """
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(votes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    store = VoteStore(DATA_FILE, CHOICES)

    # ------------------------------------------------------------------
    # Security headers
    # ------------------------------------------------------------------

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            question=QUESTION,
            choices=CHOICES,
            view="vote",
        )

    @app.route("/results")
    def results():
        stats, total = store.tally()

        flash: dict[str, str] | None = None
        match request.args.get("reset"):
            case "ok":
                flash = {
                    "msg": "Hlasování bylo úspěšně resetováno.",
                    "type": "success",
                }
            case "denied":
                flash = {
                    "msg": "Nesprávný token – reset nebyl proveden.",
                    "type": "error",
                }

        return render_template(
            "index.html",
            question=QUESTION,
            choices=CHOICES,
            view="results",
            stats=stats,
            total=total,
            flash=flash,
        )

    @app.route("/vote", methods=["POST"])
    def vote():
        choice = request.form.get("choice", "").strip().lower()
        try:
            store.cast(choice)
        except KeyError:
            logger.warning("Invalid vote choice: %r", choice)
            return (
                render_template(
                    "index.html",
                    question=QUESTION,
                    choices=CHOICES,
                    view="vote",
                    flash={"msg": "Vyber prosím platnou možnost.", "type": "error"},
                ),
                400,
            )
        return redirect(url_for("results"))

    @app.route("/reset", methods=["POST"])
    def reset():
        token = request.form.get("token", "")
        # hmac.compare_digest prevents timing-oracle attacks on the token.
        if not hmac.compare_digest(token, RESET_TOKEN):
            logger.warning("Reset attempted with an invalid token.")
            return redirect(url_for("results", reset="denied"))

        store.reset()
        return redirect(url_for("results", reset="ok"))

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(exc):
        return render_template("error.html", code=404, message="Stránka nenalezena."), 404

    @app.errorhandler(500)
    def server_error(exc):
        logger.exception("Unhandled server error")
        return render_template("error.html", code=500, message="Interní chyba serveru."), 500

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (Gunicorn entry point: gunicorn app:app)
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
