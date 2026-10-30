"""Point d'entrée : page web de l'élève + API + bot Telegram + boucle périodique.

Lancement : uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import load_settings
from .db import Database
from .llm import ClaudeTutor
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
        tutor=ClaudeTutor(settings.model, settings.student_level, settings.target_minutes),
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

    @app.get("/eleve")
    def student_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "eleve.html", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest() -> JSONResponse:
        return JSONResponse(
            {
                "name": "Devoir du jour",
                "short_name": "Devoir",
                "start_url": "/eleve",
                "display": "standalone",
                "background_color": "#ffffff",
                "theme_color": "#1e5aa8",
                "icons": [{"src": "/static/icon.png", "sizes": "512x512", "type": "image/png"}],
            },
            media_type="application/manifest+json",
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
