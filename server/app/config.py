"""Configuration lue depuis les variables d'environnement (voir .env.example)."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Variable d'environnement manquante : {name}")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    timezone: ZoneInfo
    # Code d'accès de l'élève (page élève et Raccourci iPhone)
    device_token: str
    # Identifiant de l'appareil, inclus dans les jetons de déblocage signés
    device_id: str
    telegram_token: str
    # Chats Telegram des professeurs
    prof_chat_ids: frozenset[int] = field(default_factory=frozenset)
    model: str = "claude-opus-5-5"
    # Délai avant validation automatique par le LLM
    prof_timeout_minutes: int = 15
    # Heure (locale) à partir de laquelle le devoir du jour est généré
    generation_hour: int = 5

    @property
    def db_path(self) -> Path:
        return self.data_dir / "devoirs.sqlite3"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def key_path(self) -> Path:
        return self.data_dir / "signing_key.pem"


def load_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    chat_ids = frozenset(
        # PARENT_CHAT_IDS : ancien nom, encore accepté
        int(x)
        for x in _env("PROF_CHAT_IDS", os.environ.get("PARENT_CHAT_IDS", "")).replace(" ", "").split(",")
        if x
    )
    return Settings(
        data_dir=data_dir,
        timezone=ZoneInfo(_env("TIMEZONE", "Europe/Paris")),
        device_token=_env("DEVICE_TOKEN"),
        device_id=_env("DEVICE_ID", "telephone-eleve"),
        telegram_token=_env("TELEGRAM_BOT_TOKEN"),
        prof_chat_ids=chat_ids,
        model=_env("CLAUDE_MODEL", "claude-opus-5-5"),
        prof_timeout_minutes=int(_env("PROF_TIMEOUT_MINUTES", os.environ.get("PARENT_TIMEOUT_MINUTES", "15"))),
        generation_hour=int(_env("GENERATION_HOUR", "5")),
    )
