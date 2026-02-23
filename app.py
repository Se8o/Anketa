"""Anketa – Záložkový průzkum

Flask voting application with thread-safe persistent JSON storage
and cookie-based one-vote-per-user enforcement.
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
from flask import Flask, make_response, redirect, render_template, request, url_for

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
# Cookie settings
# ---------------------------------------------------------------------------

_VOTED_COOKIE = "anketa_voted_gen"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 rok


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

    Vote integrity is enforced by a *generation* counter embedded in the JSON.
    When the poll is reset the generation increments, which automatically
    invalidates all existing voted-cookies without any server-side session.

    Schema of the JSON file:
        { "generation": <int>, "a": <int>, "b": <int>, ... }
    """

    def __init__(self, path: str | Path, choices: dict[str, str]) -> None:
        self._path = Path(path)
        self._choices = choices
        self._lock = threading.Lock()
        self._ensure_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cast(self, choice: str) -> int:
        """Increment the vote counter for *choice* and return the
        current generation so the caller can set an anti-double-vote cookie.

        Raises:
            KeyError: if *choice* is not a recognised option.
        """
        if choice not in self._choices:
            raise KeyError(f"Unknown choice: {choice!r}")

        with self._lock:
            votes = self._read()
            votes[choice] += 1
            self._write(votes)
            generation = votes["generation"]

        logger.info(
            "Vote cast: %s (option total: %d, generation: %d)",
            choice, votes[choice], generation,
        )
        return generation

    def current_generation(self) -> int:
        """Return the current vote generation without modifying any data."""
        with self._lock:
            return self._read()["generation"]

    def tally(self) -> tuple[list[VoteTally], int]:
        """Return per-choice statistics and the grand total."""
        with self._lock:
            votes = self._read()

        total = sum(votes[k] for k in self._choices)
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
        """Zero out all vote counters and increment the generation.

        Incrementing the generation automatically invalidates all existing
        voted-cookies without requiring any additional state.
        """
        with self._lock:
            votes = self._read()
            new_votes = {key: 0 for key in self._choices}
            new_votes["generation"] = votes["generation"] + 1
            self._write(new_votes)

        logger.info("Vote store reset (new generation: %d).", new_votes["generation"])

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write(self._fresh_votes())
            logger.info("Initialised vote store at %s", self._path)

    def _fresh_votes(self, generation: int = 1) -> dict:
        return {"generation": generation, **{key: 0 for key in self._choices}}

    def _read(self) -> dict:
        """Deserialise votes from disk, re-initialising on corrupt data."""
        try:
            data: dict = json.loads(self._path.read_text(encoding="utf-8"))
            for key in self._choices:
                data.setdefault(key, 0)
            data.setdefault("generation", 1)
            return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Corrupt vote store (%s). Re-initialising.", exc)
            fresh = self._fresh_votes()
            self._write(fresh)
            return fresh

    def _write(self, votes: dict) -> None:
        """Write votes atomically via a temp file + rename."""
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
    # Helpers
    # ------------------------------------------------------------------

    def _has_voted(generation: int) -> bool:
        """Return True if the current request carries a valid voted cookie."""
        return request.cookies.get(_VOTED_COOKIE) == str(generation)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        gen = store.current_generation()
        if _has_voted(gen):
            # User already voted – send to results with an info message.
            return redirect(url_for("results", already_voted="1"))

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
        reset_status = request.args.get("reset")
        already_voted = request.args.get("already_voted")

        if reset_status == "ok":
            flash = {
                "msg": "Hlasování bylo úspěšně resetováno.",
                "type": "success",
            }
        elif reset_status == "denied":
            flash = {
                "msg": "Nesprávný token – reset nebyl proveden.",
                "type": "error",
            }
        elif already_voted == "1":
            flash = {
                "msg": "Již jsi hlasoval/a. Níže jsou aktuální výsledky.",
                "type": "info",
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
        gen = store.current_generation()

        # Double-submit guard – cookie already present for this generation.
        if _has_voted(gen):
            logger.info("Duplicate vote blocked by cookie (generation %d).", gen)
            return redirect(url_for("results", already_voted="1"))

        choice = request.form.get("choice", "").strip().lower()
        try:
            gen = store.cast(choice)
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

        response = make_response(redirect(url_for("results")))
        response.set_cookie(
            _VOTED_COOKIE,
            str(gen),
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
        )
        return response

    @app.route("/reset", methods=["POST"])
    def reset():
        token = request.form.get("token", "")
        if not hmac.compare_digest(token, RESET_TOKEN):
            logger.warning("Reset attempted with an invalid token.")
            return redirect(url_for("results", reset="denied"))

        store.reset()
        # On reset the generation increments server-side – existing voted-cookies
        # become stale automatically; no need to expire them manually.
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
