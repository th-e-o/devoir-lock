"""Tests du parcours complet avec un faux LLM et un faux bot Telegram."""

import io
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.service import Service
from app.signing import Signer

TZ = ZoneInfo("Europe/Paris")
PARENT = 111


class FakeTutor:
    def __init__(self):
        self.verdict = "valide"
        self.generated = 0
        self.consignes = []
        self.chat_calls = []

    async def generate_assignment(self, programme, history, consignes=None):
        self.generated += 1
        self.consignes.append(consignes)
        return {
            "titre": f"Suites récurrentes #{self.generated}",
            "notions": ["suites"],
            "enonce": "Soit $u_{n+1} = \\sqrt{2 + u_n}$...",
            "corrige": "On montre que $u_n \\to 2$.",
            "criteres_validation": ["Q1 et Q2 traitées"],
            "duree_estimee_min": 45,
        }

    async def grade(self, assignment, photos):
        assert photos and all(p[:2] == b"\xff\xd8" for p in photos)  # JPEG
        return {
            "verdict": self.verdict,
            "note_sur_20": 15 if self.verdict == "valide" else 6,
            "resume_parent": "Résumé",
            "retour_eleve": "Bien" if self.verdict == "valide" else "Refais la Q2",
            "points_a_revoir": [],
        }

    async def supervisor_turn(self, history, user_text, run_tool):
        self.chat_calls.append((len(history), user_text))
        result = await run_tool("modifier_programme", {"texte": f"Programme : {user_text}"})
        return [
            {"role": "user", "content": [{"type": "text", "text": user_text}]},
            {"role": "assistant", "content": [{"type": "text", "text": result}]},
        ], result


class FakeBot:
    def __init__(self):
        self.sent = []
        self.photos = []
        self.next_id = 100

    async def send_message(self, chat_id, text, keyboard=None, force_reply=False):
        self.next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": keyboard, "id": self.next_id})
        return self.next_id

    async def send_photos(self, chat_id, photos, caption=None):
        self.photos.append((chat_id, len(photos)))

    async def answer_callback(self, callback_id, text=""):
        pass

    async def remove_keyboard(self, chat_id, message_id):
        pass


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 30, 17, 0, tzinfo=TZ)

    def __call__(self):
        return self.now


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (3000, 2000), "white").save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        timezone=TZ,
        device_token="secret",
        device_id="tel",
        telegram_token="x",
        parent_chat_ids=frozenset({PARENT}),
    )
    clock = Clock()
    tutor, bot = FakeTutor(), FakeBot()
    signer = Signer(settings.key_path)
    service = Service(settings, Database(settings.db_path), signer, tutor, bot, now_fn=clock)
    client = TestClient(create_app(service, None, background=False))
    client.__enter__()
    yield service, tutor, bot, clock, client
    client.__exit__(None, None, None)


AUTH = {"Authorization": "Bearer secret"}


def callback(service, data, msg_id=1):
    return service.handle_update(
        {"callback_query": {"id": "cb", "data": data, "message": {"chat": {"id": PARENT}, "message_id": msg_id}}}
    )


def message(service, text, reply_to=None):
    msg = {"chat": {"id": PARENT}, "text": text}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to}
    return service.handle_update({"message": msg})


def test_auth_required(env):
    *_, client = env
    assert client.get("/api/today").status_code == 401
    assert client.get("/api/today", headers={"Authorization": "Bearer faux"}).status_code == 401


async def test_parent_validates(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    state = client.get("/api/today", headers=AUTH).json()
    assert state["status"] == "a_faire"
    assert "corrige" not in state

    r = client.post("/api/today/submissions", headers=AUTH, files=[("photos", ("a.jpg", jpeg())), ("photos", ("b.jpg", jpeg()))])
    assert r.status_code == 200, r.text
    sub_id = r.json()["submission_id"]
    assert bot.photos == [(PARENT, 2)]
    assert bot.sent[-1]["keyboard"][0][0]["callback_data"] == f"ok:{sub_id}"
    assert client.get("/api/today", headers=AUTH).json()["status"] == "en_correction"

    await callback(service, f"ok:{sub_id}")
    state = client.get("/api/today", headers=AUTH).json()
    assert state["status"] == "valide"
    payload = service.signer.verify(state["unlock_token"])
    assert payload["date"] == "2026-10-30" and payload["device"] == "tel"

    # Après 15 min, le LLM ne doit plus rien faire.
    clock.now += timedelta(minutes=16)
    await service.tick()
    await service.wait_background()
    assert service.db.get_submission(sub_id)["decided_by"] == "parent"


async def test_parent_refuses_with_feedback(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    sub_id = (await service.create_submission([jpeg()]))["submission_id"]
    await callback(service, f"ko:{sub_id}")
    prompt = bot.sent[-1]
    await message(service, "La Q2 n'est pas justifiée", reply_to=prompt["id"])
    state = service.device_state()
    assert state["status"] == "a_faire"
    assert state["derniere_copie"] == {"status": "refuse", "feedback": "La Q2 n'est pas justifiée"}


async def test_llm_after_timeout(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    tutor.verdict = "insuffisant"
    sub_id = (await service.create_submission([jpeg()]))["submission_id"]

    clock.now += timedelta(minutes=14)
    await service.tick()
    await service.wait_background()
    assert service.db.get_submission(sub_id)["status"] == "en_attente"

    clock.now += timedelta(minutes=2)
    await service.tick()
    await service.wait_background()
    sub = service.db.get_submission(sub_id)
    assert (sub["status"], sub["decided_by"], sub["feedback"]) == ("refuse", "llm", "Refais la Q2")
    assert json.loads(sub["llm_report"])["note_sur_20"] == 6

    # Le parent peut renverser la décision de l'IA.
    await callback(service, f"ok:{sub_id}")
    assert service.device_state()["status"] == "valide"


async def test_llm_validates_then_parent_cancels(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    sub_id = (await service.create_submission([jpeg()]))["submission_id"]
    clock.now += timedelta(minutes=16)
    await service.tick()
    await service.wait_background()
    assert service.device_state()["status"] == "valide"
    await callback(service, f"ko:{sub_id}")
    assert service.device_state()["status"] == "a_faire"


async def test_new_submission_replaces_pending(env):
    service, *_ = env
    await service.ensure_today_assignment()
    first = (await service.create_submission([jpeg()]))["submission_id"]
    await service.create_submission([jpeg()])
    assert service.decide(first, True, "parent") == "Cette copie a été remplacée par une plus récente."


async def test_next_day_relocks(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    sub_id = (await service.create_submission([jpeg()]))["submission_id"]
    service.decide(sub_id, True, "parent")
    assert service.device_state()["status"] == "valide"
    clock.now += timedelta(days=1)
    assert service.device_state()["status"] == "en_preparation"
    await service.wait_background()
    assert tutor.generated == 2
    assert service.device_state()["status"] == "a_faire"


async def test_commands(env):
    service, tutor, bot, clock, client = env
    await message(service, "/debloquer")
    assert service.device_state()["status"] == "valide"

    await message(service, "On commence les séries entières")
    assert service.db.get_programme() == "Programme : On commence les séries entières"
    await message(service, "Et insiste sur le rayon")
    assert tutor.chat_calls[-1][0] == 2  # l'historique est conservé

    clock.now += timedelta(days=1)
    await service.ensure_today_assignment()
    await message(service, "/regenerer plus court")
    assert tutor.consignes[-1] == "plus court"


async def test_unauthorized_chat_ignored(env):
    service, tutor, bot, *_ = env
    await service.handle_update({"message": {"chat": {"id": 999}, "text": "/debloquer"}})
    assert service.db.get_assignment(service.today()) is None
    await service.handle_update({"message": {"chat": {"id": 999}, "text": "/start"}})
    assert "999" in bot.sent[-1]["text"]


async def test_llm_failure_retries_without_spam(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    sub_id = (await service.create_submission([jpeg()]))["submission_id"]
    original = tutor.grade

    async def broken(assignment, photos):
        raise RuntimeError("API indisponible")

    tutor.grade = broken
    clock.now += timedelta(minutes=16)
    sent_before = len(bot.sent)
    for _ in range(5):  # plusieurs passages de la boucle, à 20 s d'intervalle
        await service.tick()
        await service.wait_background()
        clock.now += timedelta(seconds=20)
    warnings = [m for m in bot.sent[sent_before:] if "impossible" in m["text"]]
    assert len(warnings) == 1
    assert service.db.get_submission(sub_id)["status"] == "en_attente"

    tutor.grade = original
    clock.now += timedelta(minutes=5)
    await service.tick()
    await service.wait_background()
    assert service.db.get_submission(sub_id)["status"] == "valide"
