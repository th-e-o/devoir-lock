"""Logique métier : devoir du jour, soumissions, validation professeur / LLM, bot du professeur."""

import asyncio
import io
import secrets
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageOps

from .config import Settings
from .db import Database
from .llm import LLMError
from .signing import Signer

log = logging.getLogger(__name__)

MAX_PHOTOS = 12
PHOTO_MAX_SIDE = 2000
CHAT_MAX_MESSAGES = 80  # au-delà, la conversation avec l'IA repart de zéro
GENERATION_RETRY = timedelta(minutes=10)
GRADING_RETRY = timedelta(minutes=5)

DEFAULT_NIVEAU = "début de première année de classe préparatoire scientifique (MPSI / MP2I)"
DEFAULT_DUREE_MIN = 30

DEFAULT_PROGRAMME = """\
Élève en début de première année de classe préparatoire (MPSI / MP2I). Exercices de niveau \
début de sup : questions guidées, calculs à mener proprement et à justifier, une seule question \
demandant une petite initiative.

Progression des premières semaines, dans l'ordre (environ un chapitre toutes les une à deux \
semaines, en revenant régulièrement sur les chapitres précédents) :
1. Logique et raisonnements : quantificateurs, implication, contraposée, absurde, récurrence, \
analyse-synthèse.
2. Calculs algébriques : sommes et produits, changements d'indice, sommes télescopiques, \
coefficients binomiaux, formule du binôme.
3. Inégalités, valeur absolue, partie entière.
4. Nombres complexes : forme algébrique et trigonométrique, racines n-ièmes, interprétation \
géométrique.
5. Fonctions usuelles : exponentielle, logarithme, puissances, fonctions trigonométriques et \
réciproques, fonctions hyperboliques.
6. Dérivation et étude de fonctions, primitives et intégrales simples, équations différentielles \
linéaires d'ordre 1 et d'ordre 2 à coefficients constants.
7. Ensembles et applications : injections, surjections, bijections.
8. Suites réelles : limites, suites monotones et adjacentes, suites récurrentes u(n+1) = f(u(n)).
"""

HELP = """\
Commandes :
/prof - lien vers l'interface professeur (niveau, durée, sujets, programme)
/statut - état du devoir du jour
/devoir - énoncé du jour
/corrige - corrigé du jour
/debloquer - valider le devoir du jour sans copie
/regenerer [consignes] - nouveau devoir du jour
/programme - programme actuel
/nouvelle - repartir d'une conversation vierge avec l'IA
/sujet <texte> - suggérer un sujet pour un prochain devoir
Tout autre message est envoyé à l'IA qui gère le programme (ex. « on passe aux nombres \
complexes lundi, insiste sur les racines n-ièmes »)."""


class Tutor(Protocol):
    async def generate_assignment(
        self,
        programme: str,
        history: list[dict[str, Any]],
        reglages: dict[str, Any],
        consignes: str | None = None,
        sujet: str | None = None,
    ) -> dict[str, Any]: ...

    async def grade(self, assignment: dict[str, Any], photos: list[bytes], reglages: dict[str, Any]) -> dict[str, Any]: ...

    async def supervisor_turn(
        self, history: list[dict[str, Any]], user_text: str, run_tool: Any, reglages: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]: ...


class Bot(Protocol):
    async def send_message(
        self, chat_id: int, text: str, keyboard: list[list[dict[str, str]]] | None = None, force_reply: bool = False
    ) -> int: ...

    async def send_photos(self, chat_id: int, photos: list[bytes], caption: str | None = None) -> None: ...

    async def answer_callback(self, callback_id: str, text: str = "") -> None: ...

    async def remove_keyboard(self, chat_id: int, message_id: int) -> None: ...


def normalize_photo(raw: bytes) -> bytes:
    """Redresse (EXIF), réduit à 2000 px max et réencode en JPEG."""
    with Image.open(io.BytesIO(raw)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()


class Service:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        signer: Signer,
        tutor: Tutor,
        bot: Bot,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.s = settings
        self.db = db
        self.signer = signer
        self.tutor = tutor
        self.bot = bot
        self._now_fn = now_fn or (lambda: datetime.now(self.s.timezone))
        self._generation_lock = asyncio.Lock()
        self._chat_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._last_generation_attempt: dict[str, datetime] = {}
        self._grading_failed_at: dict[int, datetime] = {}
        if not self.db.get_programme():
            self.db.set_programme(DEFAULT_PROGRAMME, self.now())

    # --- utilitaires ----------------------------------------------------------

    def now(self) -> datetime:
        return self._now_fn()

    def today(self) -> str:
        return self.now().date().isoformat()

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_background(self) -> None:
        """Attend la fin des tâches de fond (utile pour les tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def notify_profs(self, text: str, keyboard: list[list[dict[str, str]]] | None = None) -> list[tuple[int, int]]:
        sent = []
        for chat_id in self.s.prof_chat_ids:
            try:
                sent.append((chat_id, await self.bot.send_message(chat_id, text, keyboard=keyboard)))
            except Exception:
                log.exception("Envoi Telegram impossible vers %s", chat_id)
        return sent

    # --- réglages du professeur ----------------------------------------------

    def reglages(self) -> dict[str, Any]:
        return {
            "niveau": self.db.get_kv("niveau") or DEFAULT_NIVEAU,
            "duree_min": int(self.db.get_kv("duree_min") or DEFAULT_DUREE_MIN),
        }

    def set_reglages(self, niveau: str, duree_min: int) -> None:
        niveau = niveau.strip()
        if not niveau:
            raise ValueError("Le niveau ne peut pas être vide.")
        if not 10 <= int(duree_min) <= 120:
            raise ValueError("La durée doit être comprise entre 10 et 120 minutes.")
        self.db.set_kv("niveau", niveau)
        self.db.set_kv("duree_min", str(int(duree_min)))

    def add_suggestion(self, texte: str) -> int:
        texte = texte.strip()
        if not texte:
            raise ValueError("Sujet vide.")
        return self.db.add_suggestion(texte, self.now())

    def prof_token(self, rotate: bool = False) -> str:
        """Code d'accès de l'interface professeur, créé au premier besoin (lien envoyé par /prof)."""
        token = self.db.get_kv("prof_token")
        if rotate or not token:
            token = secrets.token_urlsafe(24)
            self.db.set_kv("prof_token", token)
        return token

    def prof_link(self) -> str:
        base = self.db.get_kv("public_base_url") or "https://VOTRE-SERVEUR"
        return f"{base}/prof#k={self.prof_token()}"

    def prof_overview(self) -> dict[str, Any]:
        """Tout ce qu'affiche l'interface professeur."""
        state = self.device_state()
        today = self.db.get_assignment(self.today())
        latest = self.db.latest_submission(today["id"]) if today else None
        history = []
        for h in self.db.recent_history(20):
            report = json.loads(h["llm_report"]) if h.get("llm_report") else None
            history.append({
                "date": h["date"],
                "titre": h["titre"],
                "notions": json.loads(h["notions"]),
                "statut": h["status"],
                "valide_par": h["validated_by"],
                "nb_copies": h["nb_soumissions"],
                "note": report.get("note_sur_20") if report else None,
            })
        return {
            "date": state["date"],
            "statut": state["status"],
            "devoir": {
                "titre": today["titre"],
                "notions": json.loads(today["notions"]),
                "enonce": today["enonce"],
                "corrige": today["corrige"],
                "duree_min": today["duree_min"],
            } if today else None,
            "derniere_copie": {
                "statut": latest["status"],
                "decide_par": latest["decided_by"],
                "retour": latest["feedback"],
                "rapport_ia": json.loads(latest["llm_report"]) if latest["llm_report"] else None,
            } if latest else None,
            "reglages": self.reglages(),
            "programme": self.db.get_programme(),
            "sujets": self.db.pending_suggestions(),
            "historique": history,
        }

    # --- devoir du jour -------------------------------------------------------

    async def ensure_today_assignment(self, consignes: str | None = None, force: bool = False) -> dict[str, Any]:
        date = self.today()
        async with self._generation_lock:
            existing = self.db.get_assignment(date)
            if existing and not force:
                return existing
            if existing and existing["status"] == "valide":
                raise ValueError("Le devoir du jour est déjà validé.")
            self._last_generation_attempt[date] = self.now()
            history = [self._history_entry(h) for h in self.db.recent_history(15)]
            suggestion = self.db.suggestion_for(date)
            data = await self.tutor.generate_assignment(
                self.db.get_programme(),
                history,
                self.reglages(),
                consignes=consignes,
                sujet=suggestion["texte"] if suggestion else None,
            )
            if suggestion:
                self.db.mark_suggestion_used(suggestion["id"], date)
            if existing:
                # Les copies envoyées pour l'ancien énoncé n'ont plus d'objet.
                self.db.execute(
                    "UPDATE submissions SET status = 'remplacee' WHERE assignment_id = ? AND status = 'en_attente'",
                    (existing["id"],),
                )
            self.db.upsert_assignment(date, data, self.now())
        assignment = self.db.get_assignment(date)
        await self.notify_profs(
            f"📘 Devoir du {date} : {assignment['titre']}\n"
            f"Notions : {', '.join(json.loads(assignment['notions']))} · ~{assignment['duree_min']} min\n\n"
            f"{assignment['enonce']}"
        )
        return assignment

    @staticmethod
    def _history_entry(h: dict[str, Any]) -> dict[str, Any]:
        report = json.loads(h["llm_report"]) if h.get("llm_report") else None
        return {
            "date": h["date"],
            "titre": h["titre"],
            "notions": json.loads(h["notions"]),
            "statut": h["status"],
            "valide_par": h["validated_by"],
            "nb_soumissions": h["nb_soumissions"],
            "retour": h["feedback"],
            "points_a_revoir": report.get("points_a_revoir") if report else None,
        }

    def _schedule_generation_if_needed(self) -> None:
        date = self.today()
        if self.db.get_assignment(date) or self._generation_lock.locked():
            return
        last = self._last_generation_attempt.get(date)
        if last and self.now() - last < GENERATION_RETRY:
            return
        self._spawn(self._generate_safely())

    async def _generate_safely(self) -> None:
        try:
            await self.ensure_today_assignment()
        except Exception as e:
            log.exception("Génération du devoir impossible")
            await self.notify_profs(
                f"⚠️ Je n'arrive pas à générer le devoir du jour ({e}). Nouvel essai dans 10 min. "
                "Vous pouvez aussi débloquer le téléphone avec /debloquer."
            )

    def device_state(self) -> dict[str, Any]:
        """État renvoyé à l'app iPhone."""
        date = self.today()
        assignment = self.db.get_assignment(date)
        if not assignment:
            self._schedule_generation_if_needed()
            return {"date": date, "status": "en_preparation"}
        state: dict[str, Any] = {
            "date": date,
            "titre": assignment["titre"],
            "enonce": assignment["enonce"],
            "duree_min": assignment["duree_min"],
        }
        latest = self.db.latest_submission(assignment["id"])
        if latest and latest["status"] != "remplacee":
            state["derniere_copie"] = {"status": latest["status"], "feedback": latest["feedback"]}
        if assignment["status"] == "valide":
            state["status"] = "valide"
            state["unlock_token"] = self.signer.sign(
                {"device": self.s.device_id, "date": date, "assignment": assignment["id"]}
            )
        elif latest and latest["status"] == "en_attente":
            state["status"] = "en_correction"
        else:
            state["status"] = "a_faire"
        return state

    # --- soumissions ----------------------------------------------------------

    async def create_submission(self, raw_photos: list[bytes]) -> dict[str, Any]:
        if not raw_photos:
            raise ValueError("Aucune photo reçue.")
        if len(raw_photos) > MAX_PHOTOS:
            raise ValueError(f"Trop de photos (maximum {MAX_PHOTOS}).")
        assignment = self.db.get_assignment(self.today())
        if not assignment:
            raise ValueError("Pas encore de devoir aujourd'hui.")
        if assignment["status"] == "valide":
            raise ValueError("Le devoir du jour est déjà validé.")
        try:
            photos = [normalize_photo(p) for p in raw_photos]
        except Exception as e:
            raise ValueError("Une des photos est illisible.") from e

        now = self.now()
        folder = self.s.photos_dir / assignment["date"]
        folder.mkdir(parents=True, exist_ok=True)
        self.db.execute(
            "UPDATE submissions SET status = 'remplacee' WHERE assignment_id = ? AND status = 'en_attente'",
            (assignment["id"],),
        )
        cur = self.db.execute(
            "INSERT INTO submissions(assignment_id, photos, created_at) VALUES(?, '[]', ?)",
            (assignment["id"], now.isoformat()),
        )
        submission_id = cur.lastrowid
        paths = []
        for i, photo in enumerate(photos, 1):
            path = folder / f"copie{submission_id}_p{i}.jpg"
            path.write_bytes(photo)
            paths.append(str(path))
        self.db.execute("UPDATE submissions SET photos = ? WHERE id = ?", (json.dumps(paths), submission_id))

        deadline = (now + timedelta(minutes=self.s.prof_timeout_minutes)).strftime("%H:%M")
        for chat_id in self.s.prof_chat_ids:
            try:
                await self.bot.send_photos(chat_id, photos, caption=f"Copie : {assignment['titre']}")
                await self.bot.send_message(chat_id, f"Corrigé de référence :\n\n{assignment['corrige']}")
                await self.bot.send_message(
                    chat_id,
                    f"📸 Copie reçue ({len(photos)} page(s)). Sans réponse de votre part avant "
                    f"{deadline}, l'IA corrigera et décidera.",
                    keyboard=self._decision_keyboard(submission_id),
                )
            except Exception:
                log.exception("Envoi de la copie impossible vers %s", chat_id)
        return {"submission_id": submission_id, "status": "en_attente"}

    @staticmethod
    def _decision_keyboard(submission_id: int) -> list[list[dict[str, str]]]:
        return [[
            {"text": "✅ Valider", "callback_data": f"ok:{submission_id}"},
            {"text": "❌ Refuser", "callback_data": f"ko:{submission_id}"},
        ]]

    def decide(
        self,
        submission_id: int,
        accepted: bool,
        by: str,
        feedback: str | None = None,
        report: dict[str, Any] | None = None,
    ) -> str:
        """Applique une décision. Renvoie un message de compte rendu."""
        sub = self.db.get_submission(submission_id)
        if not sub:
            return "Copie introuvable."
        if sub["status"] == "remplacee":
            return "Cette copie a été remplacée par une plus récente."
        assignment = self.db.one("SELECT * FROM assignments WHERE id = ?", (sub["assignment_id"],))
        if by == "llm" and sub["status"] != "en_attente":
            return "Déjà décidé par le professeur."
        if by == "prof" and sub["decided_by"] == "prof" and sub["status"] == ("valide" if accepted else "refuse"):
            return "C'était déjà votre décision."

        now = self.now().isoformat()
        status = "valide" if accepted else "refuse"
        self.db.execute(
            "UPDATE submissions SET status = ?, decided_by = ?, decided_at = ?, "
            "feedback = COALESCE(?, feedback), llm_report = COALESCE(?, llm_report) WHERE id = ?",
            (status, by, now, feedback, json.dumps(report, ensure_ascii=False) if report else None, submission_id),
        )
        if accepted:
            self.db.execute(
                "UPDATE assignments SET status = 'valide', validated_by = ?, validated_at = ? WHERE id = ?",
                (by, now, assignment["id"]),
            )
            return "Devoir validé, le téléphone se débloque."
        latest = self.db.latest_submission(assignment["id"])
        if assignment["status"] == "valide" and latest and latest["id"] == submission_id:
            self.db.execute(
                "UPDATE assignments SET status = 'a_faire', validated_by = NULL, validated_at = NULL WHERE id = ?",
                (assignment["id"],),
            )
            return "Validation annulée, le téléphone se rebloque."
        return "Copie refusée, l'élève doit recommencer."

    async def grade_with_llm(self, submission_id: int) -> None:
        sub = self.db.get_submission(submission_id)
        if not sub or sub["status"] != "en_attente":
            return
        assignment = self.db.one("SELECT * FROM assignments WHERE id = ?", (sub["assignment_id"],))
        photos = [Path(p).read_bytes() for p in json.loads(sub["photos"])]
        try:
            report = await self.tutor.grade(assignment, photos, self.reglages())
        except Exception as e:
            log.exception("Correction automatique impossible")
            first_failure = submission_id not in self._grading_failed_at
            self._grading_failed_at[submission_id] = self.now()
            self.db.execute("UPDATE submissions SET llm_started = 0 WHERE id = ?", (submission_id,))
            if first_failure:
                await self.notify_profs(
                    f"⚠️ Correction automatique impossible ({e}). Nouvel essai toutes les 5 min ; "
                    "vous pouvez aussi valider ou refuser vous-même."
                )
            return
        self._grading_failed_at.pop(submission_id, None)
        accepted = report["verdict"] == "valide"
        outcome = self.decide(submission_id, accepted, "llm", feedback=report["retour_eleve"], report=report)
        if outcome == "Déjà décidé par le professeur.":
            # Le professeur a répondu pendant la correction : on garde le rapport pour l'historique.
            self.db.execute(
                "UPDATE submissions SET llm_report = ? WHERE id = ?",
                (json.dumps(report, ensure_ascii=False), submission_id),
            )
            return
        icon = "✅" if accepted else "❌"
        await self.notify_profs(
            f"{icon} L'IA a corrigé la copie : {report['verdict']} ({report['note_sur_20']}/20).\n"
            f"{report['resume_prof']}\n\n"
            f"Retour donné à l'élève : {report['retour_eleve']}\n\n"
            "Vous pouvez encore changer la décision :",
            keyboard=self._decision_keyboard(submission_id),
        )

    # --- boucle périodique ----------------------------------------------------

    async def tick(self) -> None:
        now = self.now()
        if now.hour >= self.s.generation_hour:
            self._schedule_generation_if_needed()
        limit = (now - timedelta(minutes=self.s.prof_timeout_minutes)).isoformat()
        overdue = self.db.all(
            "SELECT id FROM submissions WHERE status = 'en_attente' AND llm_started = 0 AND created_at <= ?",
            (limit,),
        )
        for row in overdue:
            failed_at = self._grading_failed_at.get(row["id"])
            if failed_at and now - failed_at < GRADING_RETRY:
                continue
            self.db.execute("UPDATE submissions SET llm_started = 1 WHERE id = ?", (row["id"],))
            self._spawn(self.grade_with_llm(row["id"]))

    # --- Telegram -------------------------------------------------------------

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
        elif "message" in update:
            await self._handle_message(update["message"])

    async def _handle_callback(self, cq: dict[str, Any]) -> None:
        chat_id = cq["message"]["chat"]["id"]
        if chat_id not in self.s.prof_chat_ids:
            await self.bot.answer_callback(cq["id"], "Non autorisé")
            return
        action, _, raw_id = cq.get("data", "").partition(":")
        if action not in ("ok", "ko") or not raw_id.isdigit():
            await self.bot.answer_callback(cq["id"])
            return
        submission_id = int(raw_id)
        outcome = self.decide(submission_id, action == "ok", "prof")
        await self.bot.answer_callback(cq["id"], outcome[:190])
        await self.bot.remove_keyboard(chat_id, cq["message"]["message_id"])
        if action == "ko" and outcome.startswith(("Copie refusée", "Validation annulée")):
            prompt_id = await self.bot.send_message(
                chat_id,
                f"{outcome}\nRépondez à ce message pour expliquer à l'élève ce qui ne va pas (facultatif).",
                force_reply=True,
            )
            self.db.set_kv(f"feedback_prompt:{chat_id}:{prompt_id}", str(submission_id))
        else:
            await self.bot.send_message(chat_id, outcome)
        others = f"Décision d'un autre professeur : {outcome}"
        for other in self.s.prof_chat_ids - {chat_id}:
            await self.bot.send_message(other, others)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        if text.startswith("/start"):
            await self.bot.send_message(
                chat_id,
                f"Identifiant de ce chat : {chat_id}\n"
                "Ajoutez-le à PROF_CHAT_IDS dans la configuration du serveur.\n\n" + HELP,
            )
            return
        if chat_id not in self.s.prof_chat_ids or not text:
            return

        reply_to = msg.get("reply_to_message")
        if reply_to:
            key = f"feedback_prompt:{chat_id}:{reply_to['message_id']}"
            sub_id = self.db.get_kv(key)
            if sub_id:
                self.db.execute("UPDATE submissions SET feedback = ? WHERE id = ?", (text, int(sub_id)))
                await self.bot.send_message(chat_id, "Message transmis à l'élève.")
                return

        command, _, arg = text.partition(" ")
        command = command.split("@")[0].lower()
        try:
            if command in ("/aide", "/help"):
                await self.bot.send_message(chat_id, HELP)
            elif command == "/statut":
                await self.bot.send_message(chat_id, self._status_text())
            elif command == "/devoir":
                a = self.db.get_assignment(self.today())
                await self.bot.send_message(chat_id, f"{a['titre']}\n\n{a['enonce']}" if a else "Pas encore de devoir.")
            elif command == "/corrige":
                a = self.db.get_assignment(self.today())
                await self.bot.send_message(chat_id, a["corrige"] if a else "Pas encore de devoir.")
            elif command == "/debloquer":
                await self.bot.send_message(chat_id, self.manual_unlock())
            elif command == "/regenerer":
                await self.bot.send_message(chat_id, "Génération en cours…")
                await self.ensure_today_assignment(consignes=arg.strip() or None, force=True)
            elif command == "/programme":
                r = self.reglages()
                await self.bot.send_message(
                    chat_id,
                    f"Niveau : {r['niveau']}\nDurée visée : {r['duree_min']} min\n\n{self.db.get_programme()}",
                )
            elif command == "/prof":
                if arg.strip() == "nouveau":
                    self.prof_token(rotate=True)
                await self.bot.send_message(
                    chat_id,
                    f"Interface professeur :\n{self.prof_link()}\n\n"
                    "Ce lien donne accès à l'interface : ne le partagez pas. "
                    "« /prof nouveau » le remplace (l'ancien ne marche plus).",
                )
            elif command == "/sujet":
                if not arg.strip():
                    pending = self.db.pending_suggestions()
                    listing = "\n".join(f"- {x['texte']}" for x in pending) or "(aucun)"
                    await self.bot.send_message(chat_id, f"Sujets en attente :\n{listing}\n\nAjouter : /sujet <texte>")
                else:
                    self.add_suggestion(arg)
                    await self.bot.send_message(
                        chat_id, f"Sujet ajouté ({len(self.db.pending_suggestions())} en attente)."
                    )
            elif command == "/nouvelle":
                self.db.clear_chat()
                await self.bot.send_message(chat_id, "Nouvelle conversation.")
            elif command.startswith("/"):
                await self.bot.send_message(chat_id, HELP)
            else:
                await self.bot.send_message(chat_id, await self.supervisor_chat(text))
        except (ValueError, LLMError) as e:
            await self.bot.send_message(chat_id, f"⚠️ {e}")

    def _status_text(self) -> str:
        state = self.device_state()
        labels = {
            "en_preparation": "devoir en préparation",
            "a_faire": "à faire (téléphone bloqué)",
            "en_correction": "copie en attente de correction (téléphone bloqué)",
            "valide": "validé (téléphone débloqué)",
        }
        text = f"{state['date']} : {labels[state['status']]}"
        if "titre" in state:
            text += f"\n{state['titre']}"
        if state.get("derniere_copie"):
            text += f"\nDernière copie : {state['derniere_copie']['status']}"
        return text

    def manual_unlock(self) -> str:
        assignment = self.db.get_assignment(self.today())
        now = self.now().isoformat()
        if not assignment:
            # Pas de devoir (génération en échec, jour off…) : on crée une entrée « libre ».
            self.db.upsert_assignment(
                self.today(),
                {"titre": "Journée libre", "notions": [], "enonce": "Pas de devoir aujourd'hui.",
                 "corrige": "", "criteres_validation": [], "duree_estimee_min": 0},
                self.now(),
            )
            assignment = self.db.get_assignment(self.today())
        self.db.execute(
            "UPDATE assignments SET status = 'valide', validated_by = 'prof', validated_at = ? WHERE id = ?",
            (now, assignment["id"]),
        )
        self.db.execute(
            "UPDATE submissions SET status = 'remplacee' WHERE assignment_id = ? AND status = 'en_attente'",
            (assignment["id"],),
        )
        return "Téléphone débloqué pour aujourd'hui."

    # --- conversation avec l'IA (professeur) ----------------------------------

    async def supervisor_chat(self, text: str) -> str:
        async with self._chat_lock:
            history = self.db.chat_history()
            note = ""
            if len(history) > CHAT_MAX_MESSAGES:
                self.db.clear_chat()
                history = []
                note = "(Conversation trop longue : je repars de zéro. Le programme et l'historique sont conservés.)\n\n"
            new_messages, answer = await self.tutor.supervisor_turn(history, text, self._run_tool, self.reglages())
            now = self.now()
            for m in new_messages:
                self.db.append_chat(m["role"], m["content"], now)
            return note + answer

    async def _run_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "lire_programme":
            return json.dumps(
                {
                    "programme": self.db.get_programme(),
                    "reglages": self.reglages(),
                    "sujets_en_attente": [x["texte"] for x in self.db.pending_suggestions()],
                },
                ensure_ascii=False,
            )
        if name == "regler_niveau":
            self.set_reglages(args["niveau"], args["duree_min"])
            return f"Réglages enregistrés : {self.reglages()}"
        if name == "ajouter_sujet":
            self.add_suggestion(args["sujet"])
            return f"Sujet ajouté ({len(self.db.pending_suggestions())} en attente)."
        if name == "modifier_programme":
            self.db.set_programme(args["texte"], self.now())
            return "Programme mis à jour."
        if name == "historique_devoirs":
            n = max(1, min(60, int(args["nombre"])))
            return json.dumps([self._history_entry(h) for h in self.db.recent_history(n)], ensure_ascii=False)
        if name == "voir_devoir_du_jour":
            a = self.db.get_assignment(self.today())
            if not a:
                return "Pas encore de devoir aujourd'hui."
            return json.dumps(
                {k: a[k] for k in ("date", "titre", "notions", "enonce", "corrige", "criteres", "status")},
                ensure_ascii=False,
            )
        if name == "regenerer_devoir_du_jour":
            a = await self.ensure_today_assignment(consignes=args["consignes"], force=True)
            return f"Nouveau devoir généré : {a['titre']}"
        raise ValueError(f"Outil inconnu : {name}")
