"""Application configuration.

Values are loaded from environment variables so the same codebase runs
locally (via .env) and on any cloud platform without code changes.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration loaded once at import time."""

    secret_key: str

    reset_token: str
    data_file: str
    ip_votes_file: str
    question: str
    choices: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.reset_token == _RESET_TOKEN_PLACEHOLDER:
            warnings.warn(
                "RESET_TOKEN is the default placeholder value. "
                "Set a strong secret token via the RESET_TOKEN environment variable.",
                stacklevel=3,
            )


_RESET_TOKEN_PLACEHOLDER = "zmenit_pred_deploymentem"

config = AppConfig(
    secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-in-production"),
    reset_token=os.environ.get("RESET_TOKEN", _RESET_TOKEN_PLACEHOLDER),
    data_file=os.environ.get("DATA_FILE", "data/votes.json"),
    ip_votes_file=os.environ.get("IP_VOTES_FILE", "data/ip_votes.json"),
    question="Kolik otevřených záložek je ještě normální?",
    choices={
        "a": "1–5 (jsem organizovaný člověk)",
        "b": "6–20 (standardní uživatel)",
        "c": "21–50 (power user)",
        "d": "51+ (záložky jsou způsob života)",
    },
)

# Flat exports kept for backward-compatibility with app.py imports.
SECRET_KEY: str = config.secret_key
RESET_TOKEN: str = config.reset_token
DATA_FILE: str = config.data_file
IP_VOTES_FILE: str = config.ip_votes_file
QUESTION: str = config.question
CHOICES: dict[str, str] = config.choices
