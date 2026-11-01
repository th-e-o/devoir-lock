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
PROF = 111


class FakeTutor:
    def __init__(self):
        self.verdict = "valide"
        self.generated = 0
        self.consignes = []
        self.chat_calls = []
        self.sujets = []
        self.reglages = []
        self.programmes = []
        self.progressions_vues = []
        self.plans_vus = []
        self.plans_calcules = []
        self.grades = 0
        self.seances = []
        self.fiches = []

    async def generate_assignment(self, programme, history, reglages, consignes=None, sujet=None, progression=None,
                                  plan_du_jour=None):
        self.progressions_vues.append(progression)
        self.plans_vus.append(plan_du_jour)
        self.generated += 1
        self.consignes.append(consignes)
        self.sujets.append(sujet)
        self.reglages.append(reglages)
        self.programmes.append(programme)
        return {
            "titre": f"Suites récurrentes #{self.generated}",
            "notions": ["suites"],
            "enonce": "Soit $u_{n+1} = \\sqrt{2 + u_n}$...",
            "cours": "Théorème du point fixe : si $u_n \\to \\ell$ et $f$ continue, $f(\\ell) = \\ell$.",
            "corrige": "On montre que $u_n \\to 2$.",
            "criteres_validation": ["Q1 et Q2 traitées"],
            "duree_estimee_min": 45,
        }

    async def make_plan(self, objectif, aujourd_hui, programme, progression, historique, plan_precedent):
        self.plans_calcules.append((objectif, aujourd_hui, plan_precedent))
        lundi = datetime.fromisoformat(aujourd_hui).date()
        lundi -= timedelta(days=lundi.weekday())
        semaines = [{"debut": (lundi + timedelta(weeks=i)).isoformat(), "theme": f"Thème {i}",
                     "notions": ["sommes"], "difficulte": 2 + i // 3, "minutes_par_jour": 45 if i else 40,
                     "objectif": f"Objectif {i}"} for i in range(6)]
        return {"strategie": "Monter progressivement", "realisme": "Ambitieux mais possible",
                "phases": [{"titre": "Bases", "debut": aujourd_hui, "fin": objectif["echeance"],
                            "objectif": "Consolider", "chapitres": ["sommes"], "difficulte": 2}],
                "semaines": semaines, "jalons": [{"date": objectif["echeance"], "intitule": "Concours", "critere": "Admis"}]}

    async def update_progress(self, progression, seance, reglages):
        self.seances.append(seance)
        notions = list((progression or {}).get("notions", []))
        notions.append({"notion": seance["notions"][0], "maitrise": "en_cours", "observations": "ok",
                        "derniere_seance": seance["date"]})
        return {"notions": notions, "points_forts": [], "difficultes": [], "prochaine_etape": "continuer"}

    async def weekly_sheet(self, semaine, seances, progression, reglages):
        self.fiches.append((semaine, seances))
        return {"titre": "Fiche suites", "contenu": "Rappel : $u_{n+1} = f(u_n)$", "resume_prof": "Bonne semaine"}

    async def grade(self, assignment, photos, reglages):
        self.grades += 1
        assert photos and all(p[:2] == b"\xff\xd8" for p in photos)  # JPEG
        return {
            "verdict": self.verdict,
            "note_sur_20": 15 if self.verdict == "valide" else 6,
            "resume_prof": "Résumé",
            "retour_eleve": "Bien" if self.verdict == "valide" else "Refais la Q2",
            "points_a_revoir": [],
        }

    async def supervisor_turn(self, history, user_text, run_tool, reglages):
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
        prof_chat_ids=frozenset({PROF}),
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
        {"callback_query": {"id": "cb", "data": data, "message": {"chat": {"id": PROF}, "message_id": msg_id}}}
    )


def message(service, text, reply_to=None):
    msg = {"chat": {"id": PROF}, "text": text}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to}
    return service.handle_update({"message": msg})


def test_auth_required(env):
    *_, client = env
    assert client.get("/api/today").status_code == 401
    assert client.get("/api/today", headers={"Authorization": "Bearer faux"}).status_code == 401


async def test_prof_validates(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    state = client.get("/api/today", headers=AUTH).json()
    assert state["status"] == "a_faire"
    assert "corrige" not in state

    r = client.post("/api/today/submissions", headers=AUTH, files=[("photos", ("a.jpg", jpeg())), ("photos", ("b.jpg", jpeg()))])
    assert r.status_code == 200, r.text
    sub_id = r.json()["submission_id"]
    assert bot.photos == [(PROF, 2)]
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
    assert service.db.get_submission(sub_id)["decided_by"] == "prof"


async def test_prof_refuses_with_feedback(env):
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

    # Le professeur peut renverser la décision de l'IA.
    await callback(service, f"ok:{sub_id}")
    assert service.device_state()["status"] == "valide"


async def test_llm_validates_then_prof_cancels(env):
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
    assert service.decide(first, True, "prof") == "Cette copie a été remplacée par une plus récente."


async def test_next_day_relocks(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    sub_id = (await service.create_submission([jpeg()]))["submission_id"]
    service.decide(sub_id, True, "prof")
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


async def test_shortcut_status_and_student_page(env):
    service, tutor, bot, clock, client = env
    assert client.get("/api/statut", params={"k": "faux"}).status_code == 401
    assert client.get("/api/statut", params={"k": "secret"}).text == "bloque"
    await service.ensure_today_assignment()
    sub_id = (await service.create_submission([jpeg()]))["submission_id"]
    assert client.get("/api/statut", params={"k": "secret"}).text == "bloque"
    service.decide(sub_id, True, "prof")
    assert client.get("/api/statut", params={"k": "secret"}).text == "libre"
    clock.now += timedelta(days=1)
    assert client.get("/api/statut", params={"k": "secret"}).text == "bloque"

    page = client.get("/eleve")
    assert page.status_code == 200 and "Devoir du jour" in page.text
    assert client.get("/static/katex.min.js").status_code == 200
    assert client.get("/manifest.webmanifest").json()["start_url"] == "/eleve"


async def test_default_programme_and_settings(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    assert "début de première année" in tutor.programmes[-1]
    assert tutor.reglages[-1]["duree_min"] == 30
    assert "début" in tutor.reglages[-1]["niveau"]
    service.set_reglages("MPSI, second semestre", 40)
    clock.now += timedelta(days=1)
    await service.ensure_today_assignment()
    assert tutor.reglages[-1] == {"niveau": "MPSI, second semestre", "duree_min": 40}
    with pytest.raises(ValueError):
        service.set_reglages("x", 500)


async def test_suggested_subjects_used_in_order(env):
    service, tutor, bot, clock, client = env
    await message(service, "/sujet récurrence forte")
    service.add_suggestion("sommes télescopiques")
    await service.ensure_today_assignment()
    assert tutor.sujets[-1] == "récurrence forte"
    # Régénérer le même jour garde le même sujet.
    await service.ensure_today_assignment(consignes="plus court", force=True)
    assert tutor.sujets[-1] == "récurrence forte"
    clock.now += timedelta(days=1)
    await service.ensure_today_assignment()
    assert tutor.sujets[-1] == "sommes télescopiques"
    clock.now += timedelta(days=1)
    await service.ensure_today_assignment()
    assert tutor.sujets[-1] is None


async def test_prof_interface(env):
    service, tutor, bot, clock, client = env
    await message(service, "/prof")
    link = bot.sent[-1]["text"]
    token = link.split("#k=")[1].split()[0]
    assert token == service.prof_token()
    auth = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/prof").status_code == 401
    assert client.get("/api/prof", headers=AUTH).status_code == 401  # le code élève ne suffit pas
    assert client.get("/prof").status_code == 200

    r = client.put("/api/prof/reglages", headers=auth, json={"niveau": "début de MPSI", "duree_min": 25})
    assert r.status_code == 200 and r.json()["duree_min"] == 25
    assert client.put("/api/prof/reglages", headers=auth, json={"niveau": "", "duree_min": 25}).status_code == 400

    sid = client.post("/api/prof/sujets", headers=auth, json={"texte": "nombres complexes"}).json()["id"]
    client.post("/api/prof/sujets", headers=auth, json={"texte": "binôme"})
    client.delete(f"/api/prof/sujets/{sid}", headers=auth)
    client.put("/api/prof/programme", headers=auth, json={"texte": "Chapitre 1 : logique"})

    state = client.post("/api/prof/regenerer", headers=auth, json={"consignes": "plus facile"}).json()
    assert tutor.sujets[-1] == "binôme" and tutor.consignes[-1] == "plus facile"
    assert tutor.programmes[-1] == "Chapitre 1 : logique"
    assert state["devoir"]["corrige"] and state["reglages"]["duree_min"] == 25 and state["sujets"] == []

    assert "débloqué" in client.post("/api/prof/debloquer", headers=auth).json()["message"]
    assert client.get("/api/statut", params={"k": "secret"}).text == "libre"

    # Nouveau lien : l'ancien ne marche plus.
    await message(service, "/prof nouveau")
    assert client.get("/api/prof", headers=auth).status_code == 401


def test_parent_chat_ids_still_accepted(monkeypatch, tmp_path):
    from app.config import load_settings
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVICE_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.delenv("PROF_CHAT_IDS", raising=False)
    monkeypatch.setenv("PARENT_CHAT_IDS", "12, 34")
    assert load_settings().prof_chat_ids == {12, 34}
    monkeypatch.setenv("PROF_CHAT_IDS", "56")
    assert load_settings().prof_chat_ids == {56}


async def test_progress_log_after_prof_and_llm_decisions(env):
    service, tutor, bot, clock, client = env
    await service.ensure_today_assignment()
    assert tutor.progressions_vues[-1] is None
    sub_id = (await service.create_submission([jpeg()]))["submission_id"]
    await callback(service, f"ok:{sub_id}")
    # Le professeur a décidé : l'IA analyse quand même la copie pour le carnet, sans changer la décision.
    await service.tick()
    await service.wait_background()
    sub = service.db.get_submission(sub_id)
    assert tutor.grades == 1 and sub["decided_by"] == "prof" and sub["llm_report"]
    assert tutor.seances[-1]["decision"] == "valide" and tutor.seances[-1]["analyse_de_la_copie"]["note_sur_20"] == 15
    assert len(service.progression()["notions"]) == 1
    await service.tick()
    await service.wait_background()
    assert len(tutor.seances) == 1  # pas deux fois

    # Jour suivant : le générateur reçoit le carnet ; copie corrigée par l'IA, pas de seconde analyse.
    clock.now += timedelta(days=1)
    await service.ensure_today_assignment()
    assert tutor.progressions_vues[-1]["notions"][0]["notion"] == "suites"
    sub2 = (await service.create_submission([jpeg()]))["submission_id"]
    clock.now += timedelta(minutes=16)
    await service.tick()
    await service.wait_background()
    await service.tick()
    await service.wait_background()
    assert tutor.grades == 2 and len(tutor.seances) == 2
    assert service.db.get_submission(sub2)["decided_by"] == "llm"

    await message(service, "/progression")
    assert "suites" in bot.sent[-1]["text"] and "Prochaine étape" in bot.sent[-1]["text"]


async def test_weekly_sheet(env):
    service, tutor, bot, clock, client = env
    await message(service, "/fiche")
    assert "Aucun devoir" in bot.sent[-1]["text"]

    await service.ensure_today_assignment()  # vendredi 30 octobre
    clock.now = datetime(2026, 11, 1, 17, 0, tzinfo=TZ)  # dimanche 17 h : trop tôt
    await service.tick()
    await service.wait_background()
    assert tutor.fiches == []
    clock.now = datetime(2026, 11, 1, 18, 5, tzinfo=TZ)
    await service.tick()
    await service.wait_background()
    assert len(tutor.fiches) == 1
    semaine, seances = tutor.fiches[0]
    assert "2026-10-26" in semaine and [x["date"] for x in seances] == ["2026-10-30", "2026-11-01"]
    assert "Fiche de la semaine prête" in bot.sent[-1]["text"]
    await service.tick()
    await service.wait_background()
    assert len(tutor.fiches) == 1  # une seule par semaine

    fiches = client.get("/api/fiches", headers=AUTH).json()
    assert [f["semaine"] for f in fiches] == ["2026-W44"]
    eleve = client.get(f"/api/fiches/{fiches[0]['id']}", headers=AUTH).json()
    assert eleve["contenu"].startswith("Rappel") and "resume_prof" not in eleve
    prof = {"Authorization": f"Bearer {service.prof_token()}"}
    assert client.get(f"/api/fiches/{fiches[0]['id']}", headers=prof).json()["resume_prof"] == "Bonne semaine"
    assert client.get("/api/fiches").status_code == 401
    overview = client.get("/api/prof", headers=prof).json()
    assert overview["fiches"][0]["titre"] == "Fiche suites"
    # Régénération manuelle : remplace la fiche de la semaine.
    assert client.post("/api/prof/fiche", headers=prof).status_code == 200
    assert len(client.get("/api/fiches", headers=AUTH).json()) == 1


async def test_goal_plan_drives_daily_homework(env):
    service, tutor, bot, clock, client = env
    prof = {"Authorization": f"Bearer {service.prof_token()}"}
    goal = {"depart": "début de MPSI", "objectif": "intégrer l'X", "echeance": "2028-04-15", "minutes_max": 40}
    assert client.put("/api/prof/objectif", headers=prof, json={**goal, "echeance": "2020-01-01"}).status_code == 400
    r = client.put("/api/prof/objectif", headers=prof, json=goal)
    assert r.status_code == 200, r.text
    overview = r.json()
    assert overview["objectif"]["objectif"] == "intégrer l'X"
    assert overview["plan_du_jour"]["semaine"]["theme"] == "Thème 0"
    # minutes_par_jour plafonné au temps disponible (45 demandé par le plan, 40 disponibles)
    assert all(w["minutes_par_jour"] <= 40 for w in overview["plan"]["semaines"])
    assert any("Plan mis à jour" in m["text"] for m in bot.sent)

    await service.ensure_today_assignment()
    vu = tutor.plans_vus[-1]
    assert vu["semaine"]["theme"] == "Thème 0" and vu["jours_restants"] > 500
    assert tutor.reglages[-1]["duree_min"] == 40

    etat = client.get("/api/today", headers=AUTH).json()
    assert etat["objectif"]["semaine"]["theme"] == "Thème 0" and etat["cours"].startswith("Théorème")
    assert "corrige" not in etat and "realisme" not in json.dumps(etat)

    # Semaine suivante : le plan suit.
    clock.now += timedelta(days=7)
    await service.ensure_today_assignment()
    assert tutor.plans_vus[-1]["semaine"]["theme"] == "Thème 1"

    # Dimanche 19 h : révision hebdomadaire, une seule fois, avec le plan précédent.
    n = len(tutor.plans_calcules)
    clock.now = datetime(2026, 11, 8, 19, 30, tzinfo=TZ)
    await service.tick()
    await service.wait_background()
    assert len(tutor.plans_calcules) == n + 1 and tutor.plans_calcules[-1][2] is not None
    await service.tick()
    await service.wait_background()
    assert len(tutor.plans_calcules) == n + 1

    await message(service, "/objectif")
    assert "intégrer l'X" in bot.sent[-1]["text"] and "Réalisme" in bot.sent[-1]["text"]

    # Plan périmé (plus aucune semaine ne couvre aujourd'hui) : recalcul automatique.
    clock.now += timedelta(weeks=8)
    await service.tick()
    await service.wait_background()
    assert len(tutor.plans_calcules) == n + 2

    assert client.delete("/api/prof/objectif", headers=prof).json()["objectif"] is None
    clock.now += timedelta(days=1)
    await service.ensure_today_assignment()
    assert tutor.plans_vus[-1] is None and tutor.reglages[-1]["duree_min"] == 30


def test_old_database_gets_course_column(tmp_path):
    import sqlite3
    from app.db import Database
    path = tmp_path / "ancienne.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE assignments (id INTEGER PRIMARY KEY, date TEXT UNIQUE, titre TEXT NOT NULL, "
                 "notions TEXT NOT NULL, enonce TEXT NOT NULL, corrige TEXT NOT NULL, criteres TEXT NOT NULL, "
                 "duree_min INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'a_faire', validated_by TEXT, "
                 "validated_at TEXT, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO assignments(date, titre, notions, enonce, corrige, criteres, duree_min, created_at) "
                 "VALUES('2026-10-30', 't', '[]', 'e', 'c', '[]', 45, 'x')")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.get_assignment("2026-10-30")["cours"] == ""
