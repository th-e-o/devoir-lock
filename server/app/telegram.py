"""Client minimal de l'API Bot Telegram (long polling, pas besoin d'URL publique)."""

import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

MAX_TEXT = 4000  # limite Telegram : 4096 caractères


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks or [""]


class TelegramError(RuntimeError):
    pass


class TelegramBot:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def _call(self, method: str, data: dict[str, Any] | None = None, files: Any = None) -> Any:
        resp = await self._client.post(f"{self._base}/{method}", data=data, files=files)
        payload = resp.json()
        if not payload.get("ok"):
            raise TelegramError(f"{method}: {payload.get('description')}")
        return payload["result"]

    async def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        data: dict[str, Any] = {"timeout": timeout, "allowed_updates": json.dumps(["message", "callback_query"])}
        if offset is not None:
            data["offset"] = offset
        return await self._call("getUpdates", data)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        keyboard: list[list[dict[str, str]]] | None = None,
        force_reply: bool = False,
    ) -> int:
        """Envoie un texte (découpé si trop long). Le clavier est attaché au dernier morceau.
        Renvoie l'identifiant du dernier message envoyé."""
        chunks = split_text(text)
        message_id = 0
        for i, chunk in enumerate(chunks):
            data: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if i == len(chunks) - 1:
                if keyboard:
                    data["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
                elif force_reply:
                    data["reply_markup"] = json.dumps({"force_reply": True})
            message_id = (await self._call("sendMessage", data))["message_id"]
        return message_id

    async def send_photos(self, chat_id: int, photos: list[bytes], caption: str | None = None) -> None:
        # sendMediaGroup accepte 2 à 10 photos ; on envoie par paquets de 10.
        for start in range(0, len(photos), 10):
            batch = photos[start : start + 10]
            if len(batch) == 1:
                data: dict[str, Any] = {"chat_id": chat_id}
                if caption and start == 0:
                    data["caption"] = caption
                await self._call("sendPhoto", data, files={"photo": ("page.jpg", batch[0], "image/jpeg")})
                continue
            media = []
            files = {}
            for i, photo in enumerate(batch):
                name = f"p{start + i}"
                item = {"type": "photo", "media": f"attach://{name}"}
                if caption and start == 0 and i == 0:
                    item["caption"] = caption
                media.append(item)
                files[name] = (f"{name}.jpg", photo, "image/jpeg")
            await self._call("sendMediaGroup", {"chat_id": chat_id, "media": json.dumps(media)}, files=files)

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    async def remove_keyboard(self, chat_id: int, message_id: int) -> None:
        try:
            await self._call(
                "editMessageReplyMarkup",
                {"chat_id": chat_id, "message_id": message_id, "reply_markup": json.dumps({"inline_keyboard": []})},
            )
        except TelegramError as e:  # message trop ancien ou déjà modifié : sans importance
            log.debug("remove_keyboard: %s", e)
