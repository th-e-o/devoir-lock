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
    # Jeton partagé avec l'app Android (en-tête Authorization: Bearer ...)
    device_token: str
    # Identifiant de l'appareil, inclus dans les jetons de déblocage signés
    device_id: str
    telegram_token: str
    # Chats Telegram autorisés (parents / superviseurs)
    parent_chat_ids: frozenset[int] = field(default_factory=frozenset)
    model: str = "claude-opus-5-5"
    # Délai avant validation automatique par le LLM
    parent_timeout_minutes: int = 15
    # Heure (locale) à partir de laquelle le devoir du jour est généré
    generation_hour: int = 5
    # Durée de travail visée pour un devoir
    target_minutes: int = 45
    student_level: str = "classe préparatoire scientifique (MPSI / MP2I, première année)"

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
        int(x) for x in _env("PARENT_CHAT_IDS", "").replace(" ", "").split(",") if x
    )
    return Settings(
        data_dir=data_dir,
        timezone=ZoneInfo(_env("TIMEZONE", "Europe/Paris")),
        device_token=_env("DEVICE_TOKEN"),
        device_id=_env("DEVICE_ID", "telephone-eleve"),
        telegram_token=_env("TELEGRAM_BOT_TOKEN"),
        parent_chat_ids=chat_ids,
        model=_env("CLAUDE_MODEL", "claude-opus-5-5"),
        parent_timeout_minutes=int(_env("PARENT_TIMEOUT_MINUTES", "15")),
        generation_hour=int(_env("GENERATION_HOUR", "5")),
        target_minutes=int(_env("TARGET_MINUTES", "45")),
        student_level=_env(
            "STUDENT_LEVEL", "classe préparatoire scientifique (MPSI / MP2I, première année)"
        ),
    )
