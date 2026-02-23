"""Anketa – Záložkový průzkum

Flask voting application with thread-safe persistent JSON storage,
cookie-based one-vote-per-user enforcement, and a session-protected
admin panel for poll management.
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
from flask import (
    Flask,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

load_dotenv()

from config import CHOICES, DATA_FILE, QUESTION, RESET_TOKEN, SECRET_KEY  # noqa: E402

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
_ADMIN_SESSION_KEY = "is_admin"


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
        """Increment the vote counter for *choice* and return the current
        generation so the caller can set an anti-double-vote cookie.

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
    app.secret_key = SECRET_KEY
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

    def _is_admin() -> bool:
        """Return True if the current session is authenticated as admin."""
        return session.get(_ADMIN_SESSION_KEY) is True

    # ------------------------------------------------------------------
    # Public routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        gen = store.current_generation()
        if _has_voted(gen):
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

    # ------------------------------------------------------------------
    # Admin routes
    # ------------------------------------------------------------------

    @app.route("/admin")
    def admin():
        """Admin panel – shows login form or management panel based on session."""
        stats, total = store.tally()
        return render_template(
            "admin.html",
            is_admin=_is_admin(),
            stats=stats,
            total=total,
        )

    @app.route("/admin/login", methods=["POST"])
    def admin_login():
        """Validate the reset token and start an admin session."""
        token = request.form.get("token", "")
        if hmac.compare_digest(token, RESET_TOKEN):
            session[_ADMIN_SESSION_KEY] = True
            logger.info("Admin session started.")
            return redirect(url_for("admin"))

        logger.warning("Failed admin login attempt.")
        return render_template(
            "admin.html",
            is_admin=False,
            stats=None,
            total=None,
            flash={"msg": "Nesprávný token. Přístup odepřen.", "type": "error"},
        ), 403

    @app.route("/admin/reset", methods=["POST"])
    def admin_reset():
        """Reset all votes. Requires an active admin session."""
        if not _is_admin():
            logger.warning("Unauthorised reset attempt (no admin session).")
            return redirect(url_for("admin"))

        store.reset()
        return redirect(url_for("admin", reset="ok"))

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        """End the admin session."""
        session.pop(_ADMIN_SESSION_KEY, None)
        logger.info("Admin session ended.")
        return redirect(url_for("index"))

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
