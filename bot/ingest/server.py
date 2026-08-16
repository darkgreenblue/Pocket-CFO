"""سرورِ کوچکِ HTTP کنارِ ربات — ورودیِ شرتکاتِ iOS.

عمداً با `asyncio` خام نوشته شده و نه یک وب‌فریم‌ورک: دو مسیر بیشتر ندارد و اضافه‌کردنِ
یک وابستگیِ تازه برای همین دو مسیر ارزشش را ندارد. روی همان event loopِ ربات اجرا
می‌شود، پس بعد از نوشتن در دیتابیس مستقیم `bot.send_message` صدا می‌زند — نیازی به
polling روی دیتابیس یا کامپوننتِ جداگانه نیست.

مسیرها:
  GET  /health   → `{"ok": true}` — برای تستِ «آیا گوشیم اصلاً به سرور می‌رسد؟»
  POST /ingest   → بدنه‌ی JSON:
      {"token": "...", "request_id": "...", "text": "..."}
      {"token": "...", "request_id": "...", "audio_b64": "...", "audio_format": "m4a"}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Optional

from bot.config import settings
from bot.services import ingest

logger = logging.getLogger(__name__)

_MAX_HEADER_BYTES = 16 * 1024
_MAX_REQUEST_ID = 100


class _BadRequest(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _response(status: int, payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
              405: "Method Not Allowed", 413: "Payload Too Large",
              429: "Too Many Requests", 500: "Internal Server Error"}.get(status, "OK")
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    return head + body


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, bytes]:
    """(method, path, body) — بدنه فقط تا سقفِ تعریف‌شده خوانده می‌شود."""
    head = await reader.readuntil(b"\r\n\r\n")
    if len(head) > _MAX_HEADER_BYTES:
        raise _BadRequest(413, "هدر بیش از حد بزرگ است")
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        raise _BadRequest(400, "درخواست نامعتبر")
    method, path = parts[0].upper(), parts[1].split("?")[0]

    length = 0
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError as exc:
                raise _BadRequest(400, "Content-Length نامعتبر") from exc
    if length > settings.ingest_max_body_bytes:
        raise _BadRequest(413, "حجم درخواست بیش از حد مجاز است")
    body = await reader.readexactly(length) if length else b""
    return method, path, body


def _parse_payload(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _BadRequest(400, "بدنه باید JSON معتبر باشد") from exc
    if not isinstance(data, dict):
        raise _BadRequest(400, "بدنه باید یک آبجکت JSON باشد")
    return data


def _decode_audio(data: dict) -> Optional[tuple[bytes, str]]:
    raw = (data.get("audio_b64") or "").strip()
    if not raw:
        return None
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise _BadRequest(400, "audio_b64 قابلِ decode نیست") from exc
    if not blob:
        raise _BadRequest(400, "فایلِ صوتی خالی است")
    return blob, ingest.normalize_audio_format(data.get("audio_format") or "")


async def _handle_ingest(bot, body: bytes) -> tuple[int, dict]:
    data = _parse_payload(body)
    try:
        user_id = ingest.resolve_user(str(data.get("token") or ""))
    except ingest.IngestAuthError:
        # عمداً بدونِ جزئیات؛ اسکنِ کور نباید بفهمد کدام توکن نزدیک بوده.
        return 401, {"ok": False, "message": "توکن نامعتبر است."}

    request_id = str(data.get("request_id") or "").strip()[:_MAX_REQUEST_ID]
    if not request_id:
        raise _BadRequest(400, "request_id لازم است")

    audio = _decode_audio(data)
    text = str(data.get("text") or "")
    if not text.strip() and audio is None:
        raise _BadRequest(400, "یکی از text یا audio_b64 لازم است")

    result = await ingest.handle(bot, user_id=user_id, request_id=request_id,
                                text=text, audio=audio)
    status = 429 if result.status == "rate_limited" else (200 if result.ok else 500)
    return status, {"ok": result.ok, "status": result.status, "message": result.message}


async def _client(bot, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        try:
            method, path, body = await _read_request(reader)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise _BadRequest(400, "درخواست ناقص") from exc

        if path == "/health":
            status, payload = (200, {"ok": True}) if method == "GET" \
                else (405, {"ok": False, "message": "فقط GET"})
        elif path == "/ingest":
            if method != "POST":
                status, payload = 405, {"ok": False, "message": "فقط POST"}
            else:
                status, payload = await _handle_ingest(bot, body)
        else:
            status, payload = 404, {"ok": False, "message": "یافت نشد"}
    except _BadRequest as exc:
        status, payload = exc.status, {"ok": False, "message": exc.message}
    except Exception:  # noqa: BLE001
        logger.exception("خطای غیرمنتظره در سرورِ ورودی")
        status, payload = 500, {"ok": False, "message": "خطای داخلی"}

    try:
        writer.write(_response(status, payload))
        await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


class IngestServer:
    def __init__(self) -> None:
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self, bot) -> None:
        if not settings.ingest_enabled:
            logger.info("درِ دومِ ورودی غیرفعال است (INGEST_TOKENS خالی).")
            return

        async def handler(reader, writer):
            await _client(bot, reader, writer)

        self._server = await asyncio.start_server(
            handler, settings.ingest_host, settings.ingest_port
        )
        logger.info("درِ دومِ ورودی روی %s:%s باز شد (%d توکن).",
                    settings.ingest_host, settings.ingest_port, len(settings.ingest_tokens))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
