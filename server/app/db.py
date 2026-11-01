"""Stockage SQLite (un seul fichier, adapté à un usage domestique)."""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS programme (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    texte TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    titre TEXT NOT NULL,
    notions TEXT NOT NULL,          -- JSON list
    enonce TEXT NOT NULL,           -- Markdown + LaTeX, montré à l'élève
    cours TEXT NOT NULL DEFAULT '', -- rappel de cours, montré à l'élève
    corrige TEXT NOT NULL,          -- jamais envoyé au téléphone
    criteres TEXT NOT NULL,         -- JSON list
    duree_min INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'a_faire',   -- a_faire | valide
    validated_by TEXT,              -- prof | llm
    validated_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    photos TEXT NOT NULL,           -- JSON list de chemins
    status TEXT NOT NULL DEFAULT 'en_attente',  -- en_attente | valide | refuse | remplacee
    decided_by TEXT,                -- prof | llm
    decided_at TEXT,
    feedback TEXT,                  -- message affiché à l'élève
    llm_started INTEGER NOT NULL DEFAULT 0,
    llm_report TEXT,                -- JSON
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,          -- JSON (liste de blocs)
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    texte TEXT NOT NULL,
    used_on TEXT,                   -- date du devoir qui l'a utilisée
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fiches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    semaine TEXT NOT NULL UNIQUE,   -- ex. 2026-W44
    debut TEXT NOT NULL,
    fin TEXT NOT NULL,
    titre TEXT NOT NULL,
    contenu TEXT NOT NULL,          -- Markdown + LaTeX
    resume_prof TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            # Bases créées par une version précédente
            columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(assignments)")}
            if "cours" not in columns:
                self._conn.execute("ALTER TABLE assignments ADD COLUMN cours TEXT NOT NULL DEFAULT ''")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # --- kv -----------------------------------------------------------------

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_kv(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # --- programme ----------------------------------------------------------

    def get_programme(self) -> str:
        row = self.one("SELECT texte FROM programme WHERE id = 1")
        return row["texte"] if row else ""

    def set_programme(self, texte: str, now: datetime) -> None:
        self.execute(
            "INSERT INTO programme(id, texte, updated_at) VALUES(1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET texte = excluded.texte, updated_at = excluded.updated_at",
            (texte, now.isoformat()),
        )

    # --- devoirs ------------------------------------------------------------

    def get_assignment(self, date: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM assignments WHERE date = ?", (date,))

    def upsert_assignment(self, date: str, data: dict[str, Any], now: datetime) -> None:
        self.execute(
            """
            INSERT INTO assignments(date, titre, notions, enonce, cours, corrige, criteres, duree_min, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                titre = excluded.titre, notions = excluded.notions, enonce = excluded.enonce, cours = excluded.cours,
                corrige = excluded.corrige, criteres = excluded.criteres,
                duree_min = excluded.duree_min, created_at = excluded.created_at,
                status = 'a_faire', validated_by = NULL, validated_at = NULL
            """,
            (
                date,
                data["titre"],
                json.dumps(data["notions"], ensure_ascii=False),
                data["enonce"],
                data.get("cours", ""),
                data["corrige"],
                json.dumps(data["criteres_validation"], ensure_ascii=False),
                int(data["duree_estimee_min"]),
                now.isoformat(),
            ),
        )

    def recent_history(self, limit: int) -> list[dict[str, Any]]:
        """Derniers devoirs avec le résultat de la dernière soumission (le plus récent en premier)."""
        return self.all(
            """
            SELECT a.date, a.titre, a.notions, a.status, a.validated_by,
                   s.status AS derniere_soumission, s.feedback, s.llm_report,
                   (SELECT COUNT(*) FROM submissions s2 WHERE s2.assignment_id = a.id) AS nb_soumissions
            FROM assignments a
            LEFT JOIN submissions s ON s.id = (
                SELECT MAX(id) FROM submissions WHERE assignment_id = a.id
            )
            ORDER BY a.date DESC LIMIT ?
            """,
            (limit,),
        )

    # --- soumissions --------------------------------------------------------

    def get_submission(self, submission_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM submissions WHERE id = ?", (submission_id,))

    def latest_submission(self, assignment_id: int) -> dict[str, Any] | None:
        return self.one(
            "SELECT * FROM submissions WHERE assignment_id = ? ORDER BY id DESC LIMIT 1",
            (assignment_id,),
        )

    def sessions_between(self, debut: str, fin: str) -> list[dict[str, Any]]:
        """Devoirs entre deux dates (incluses) avec leur dernière copie, du plus ancien au plus récent."""
        return self.all(
            """
            SELECT a.date, a.titre, a.notions, a.enonce, a.status, a.validated_by,
                   s.status AS derniere_copie, s.feedback, s.llm_report
            FROM assignments a
            LEFT JOIN submissions s ON s.id = (
                SELECT MAX(id) FROM submissions WHERE assignment_id = a.id
            )
            WHERE a.date BETWEEN ? AND ?
            ORDER BY a.date
            """,
            (debut, fin),
        )

    # --- fiches de la semaine ---------------------------------------------------

    def list_fiches(self) -> list[dict[str, Any]]:
        return self.all("SELECT id, semaine, debut, fin, titre, created_at FROM fiches ORDER BY semaine DESC")

    def get_fiche(self, fiche_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM fiches WHERE id = ?", (fiche_id,))

    def fiche_for_week(self, semaine: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM fiches WHERE semaine = ?", (semaine,))

    def save_fiche(self, semaine: str, debut: str, fin: str, data: dict[str, Any], now: datetime) -> int:
        self.execute(
            """
            INSERT INTO fiches(semaine, debut, fin, titre, contenu, resume_prof, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(semaine) DO UPDATE SET
                titre = excluded.titre, contenu = excluded.contenu,
                resume_prof = excluded.resume_prof, created_at = excluded.created_at
            """,
            (semaine, debut, fin, data["titre"], data["contenu"], data["resume_prof"], now.isoformat()),
        )
        return self.fiche_for_week(semaine)["id"]

    # --- sujets suggérés par le professeur ------------------------------------

    def pending_suggestions(self) -> list[dict[str, Any]]:
        return self.all("SELECT id, texte, created_at FROM suggestions WHERE used_on IS NULL ORDER BY id")

    def add_suggestion(self, texte: str, now: datetime) -> int:
        return self.execute(
            "INSERT INTO suggestions(texte, created_at) VALUES(?, ?)", (texte, now.isoformat())
        ).lastrowid

    def delete_suggestion(self, suggestion_id: int) -> None:
        self.execute("DELETE FROM suggestions WHERE id = ? AND used_on IS NULL", (suggestion_id,))

    def suggestion_for(self, date: str) -> dict[str, Any] | None:
        """Sujet déjà attribué à ce jour (régénération), sinon le plus ancien en attente."""
        return self.one("SELECT * FROM suggestions WHERE used_on = ? ORDER BY id LIMIT 1", (date,)) or self.one(
            "SELECT * FROM suggestions WHERE used_on IS NULL ORDER BY id LIMIT 1"
        )

    def mark_suggestion_used(self, suggestion_id: int, date: str) -> None:
        self.execute("UPDATE suggestions SET used_on = ? WHERE id = ?", (date, suggestion_id))

    # --- conversation avec le professeur --------------------------------------

    def chat_history(self) -> list[dict[str, Any]]:
        return [
            {"role": r["role"], "content": json.loads(r["content"])}
            for r in self.all("SELECT role, content FROM chat_messages ORDER BY id")
        ]

    def append_chat(self, role: str, content: list[dict[str, Any]], now: datetime) -> None:
        self.execute(
            "INSERT INTO chat_messages(role, content, created_at) VALUES(?, ?, ?)",
            (role, json.dumps(content, ensure_ascii=False), now.isoformat()),
        )

    def clear_chat(self) -> None:
        self.execute("DELETE FROM chat_messages")
