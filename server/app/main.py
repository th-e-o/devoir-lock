"""Point d'entrée : page web de l'élève + API + bot Telegram + boucle périodique.

Lancement : uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import load_settings
from .db import Database
from .llm import ClaudeTutor, LLMError
from .service import Service
from .signing import Signer
from .telegram import TelegramBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("devoir")
# httpx écrit chaque URL appelée : celles de Telegram contiennent le jeton du bot.
logging.getLogger("httpx").setLevel(logging.WARNING)

STATIC_DIR = Path(__file__).parent / "static"


async def telegram_loop(service: Service, bot: TelegramBot) -> None:
    offset_raw = service.db.get_kv("telegram_offset")
    offset = int(offset_raw) if offset_raw else None
    delay = 1
    while True:
        try:
            updates = await bot.get_updates(offset)
            delay = 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("getUpdates : %s (nouvel essai dans %ss)", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            service.db.set_kv("telegram_offset", str(offset))
            # Chaque update dans sa propre tâche : une conversation avec l'IA ne bloque pas les boutons.
            service._spawn(_safe(service.handle_update(update)))


async def _safe(coro) -> None:
    try:
        await coro
    except Exception:
        log.exception("Erreur pendant le traitement d'un message Telegram")


async def tick_loop(service: Service) -> None:
    while True:
        try:
            await service.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Erreur dans la boucle périodique")
        await asyncio.sleep(20)


def build_service() -> tuple[Service, TelegramBot]:
    settings = load_settings()
    bot = TelegramBot(settings.telegram_token)
    service = Service(
        settings=settings,
        db=Database(settings.db_path),
        signer=Signer(settings.key_path),
        tutor=ClaudeTutor(settings.model),
        bot=bot,
    )
    return service, bot


def create_app(service: Service | None = None, bot: TelegramBot | None = None, background: bool = True) -> FastAPI:
    holder: dict[str, Service] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal service, bot
        if service is None:
            service, bot = build_service()
        holder["service"] = service
        tasks = []
        if background:
            tasks.append(asyncio.create_task(tick_loop(service)))
            if bot is not None:
                tasks.append(asyncio.create_task(telegram_loop(service, bot)))
        log.info("Clé publique à mettre dans l'app : %s", service.signer.public_key_b64())
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="Devoir du jour", lifespan=lifespan)

    def get_service() -> Service:
        return holder["service"]

    def require_device(
        authorization: Annotated[str | None, Header()] = None,
        svc: Service = Depends(get_service),
    ) -> Service:
        expected = f"Bearer {svc.s.device_token}"
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="Jeton d'appareil invalide")
        return svc

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def remember_public_url(request: Request, call_next):
        # Adresse publique (vue derrière Caddy), pour le lien envoyé par /prof.
        if request.url.scheme == "https" and "service" in holder:
            base = f"https://{request.url.netloc}"
            if holder["service"].db.get_kv("public_base_url") != base:
                holder["service"].db.set_kv("public_base_url", base)
        return await call_next(request)

    def require_prof(
        authorization: Annotated[str | None, Header()] = None,
        svc: Service = Depends(get_service),
    ) -> Service:
        expected = f"Bearer {svc.prof_token()}"
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="Lien professeur invalide : demandez-en un avec /prof sur Telegram")
        return svc

    def student_or_prof(
        authorization: Annotated[str | None, Header()] = None,
        svc: Service = Depends(get_service),
    ) -> tuple[Service, bool]:
        """Renvoie (service, est_professeur)."""
        given = (authorization or "").encode()
        if hmac.compare_digest(given, f"Bearer {svc.prof_token()}".encode()):
            return svc, True
        if hmac.compare_digest(given, f"Bearer {svc.s.device_token}".encode()):
            return svc, False
        raise HTTPException(status_code=401, detail="Code d'accès invalide")

    @app.get("/api/fiches")
    async def list_sheets(access: tuple[Service, bool] = Depends(student_or_prof)) -> list[dict]:
        return access[0].db.list_fiches()

    @app.get("/api/fiches/{fiche_id}")
    async def get_sheet(fiche_id: int, access: tuple[Service, bool] = Depends(student_or_prof)) -> dict:
        svc, is_prof = access
        fiche = svc.db.get_fiche(fiche_id)
        if not fiche:
            raise HTTPException(status_code=404, detail="Fiche introuvable")
        if not is_prof:
            fiche.pop("resume_prof")  # destiné au professeur
        return fiche

    @app.get("/prof")
    def prof_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "prof.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/prof")
    async def prof_overview(svc: Service = Depends(require_prof)) -> dict:
        return svc.prof_overview()

    @app.put("/api/prof/reglages")
    async def prof_settings(
        niveau: Annotated[str, Body()], duree_min: Annotated[int, Body()], svc: Service = Depends(require_prof)
    ) -> dict:
        try:
            svc.set_reglages(niveau, duree_min)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return svc.reglages()

    @app.put("/api/prof/objectif")
    async def prof_goal(
        depart: Annotated[str, Body()],
        objectif: Annotated[str, Body()],
        echeance: Annotated[str, Body()],
        minutes_max: Annotated[int, Body()],
        svc: Service = Depends(require_prof),
    ) -> dict:
        try:
            svc.set_objectif(depart, objectif, echeance, minutes_max)
            await svc.replan()
        except (ValueError, LLMError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return svc.prof_overview()

    @app.delete("/api/prof/objectif")
    async def prof_clear_goal(svc: Service = Depends(require_prof)) -> dict:
        svc.clear_objectif()
        return svc.prof_overview()

    @app.post("/api/prof/plan")
    async def prof_replan(svc: Service = Depends(require_prof)) -> dict:
        try:
            await svc.replan()
        except (ValueError, LLMError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return svc.prof_overview()

    @app.put("/api/prof/programme")
    async def prof_programme(texte: Annotated[str, Body(embed=True)], svc: Service = Depends(require_prof)) -> dict:
        svc.db.set_programme(texte.strip(), svc.now())
        return {"ok": True}

    @app.post("/api/prof/sujets")
    async def prof_add_subject(texte: Annotated[str, Body(embed=True)], svc: Service = Depends(require_prof)) -> dict:
        try:
            return {"id": svc.add_suggestion(texte)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.delete("/api/prof/sujets/{suggestion_id}")
    async def prof_delete_subject(suggestion_id: int, svc: Service = Depends(require_prof)) -> dict:
        svc.db.delete_suggestion(suggestion_id)
        return {"ok": True}

    @app.post("/api/prof/regenerer")
    async def prof_regenerate(
        consignes: Annotated[str, Body(embed=True)] = "", svc: Service = Depends(require_prof)
    ) -> dict:
        try:
            await svc.ensure_today_assignment(consignes=consignes.strip() or None, force=True)
        except (ValueError, LLMError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return svc.prof_overview()

    @app.post("/api/prof/fiche")
    async def prof_weekly_sheet(svc: Service = Depends(require_prof)) -> dict:
        try:
            return await svc.generate_weekly_sheet()
        except (ValueError, LLMError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/prof/debloquer")
    async def prof_unlock(svc: Service = Depends(require_prof)) -> dict:
        return {"message": svc.manual_unlock()}

    @app.get("/eleve")
    def student_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "eleve.html", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest(k: str | None = None, svc: Service = Depends(get_service)) -> JSONResponse:
        # Avec le bon code, l'icône de l'écran d'accueil s'ouvre directement sur les devoirs :
        # sur iPhone, l'app ajoutée à l'écran d'accueil ne partage pas le stockage de Safari.
        start_url = "/eleve"
        if k and hmac.compare_digest(k.encode(), svc.s.device_token.encode()):
            start_url = f"/eleve#k={k}"
        return JSONResponse(
            {
                "name": "Devoir du jour",
                "short_name": "Devoir",
                "start_url": start_url,
                "display": "standalone",
                "background_color": "#ffffff",
                "theme_color": "#1e5aa8",
                "icons": [{"src": "/static/icon.png", "sizes": "512x512", "type": "image/png"}],
            },
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/statut", response_class=PlainTextResponse)
    async def shortcut_status(k: Annotated[str, Query()], svc: Service = Depends(get_service)) -> str:
        """Pour l'app Raccourcis : « libre » ou « bloque », en texte brut (aucun des deux ne contient l'autre)."""
        if not hmac.compare_digest(k.encode(), svc.s.device_token.encode()):
            raise HTTPException(status_code=401, detail="Jeton d'appareil invalide")
        return "libre" if svc.device_state()["status"] == "valide" else "bloque"

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/public-key")
    def public_key(svc: Service = Depends(get_service)) -> dict:
        return {"public_key": svc.signer.public_key_b64(), "device_id": svc.s.device_id}

    @app.get("/api/today")
    async def today(svc: Service = Depends(require_device)) -> dict:
        return svc.device_state()

    @app.post("/api/today/submissions")
    async def submit(
        photos: Annotated[list[UploadFile], File()],
        svc: Service = Depends(require_device),
    ) -> dict:
        raw = [await p.read() for p in photos]
        try:
            return await svc.create_submission(raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return app


app = create_app()
