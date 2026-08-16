"""سرورِ کوچکِ HTTP کنارِ ربات — ورودیِ شرتکاتِ iOS.

عمداً با `asyncio` خام نوشته شده و نه یک وب‌فریم‌ورک: دو مسیر بیشتر ندارد و اضافه‌کردنِ
یک وابستگیِ تازه برای همین دو مسیر ارزشش را ندارد. روی همان event loopِ ربات اجرا
می‌شود، پس بعد از نوشتن در دیتابیس مستقیم `bot.send_message` صدا می‌زند — نیازی به
polling روی دیتابیس یا کامپوننتِ جداگانه نیست.

مسیرها:
  GET  /health         → `{"ok": true}` — برای تستِ «آیا گوشیم اصلاً به سرور می‌رسد؟»

  POST /s/<token>      → مسیرِ ساده، همانی که میان‌برِ سه‌اکشنی می‌زند. بدنه **خامِ**
      متن است؛ برای ویس `?audio=m4a` و بدنه خودِ فایلِ صوتی. هیچ JSONای دستِ کاربر
      ساخته نمی‌شود — چون هر فیلدِ اضافه در فرمِ شرتکات یک قدمِ اضافه در راه‌اندازی است.

  POST /ingest         → مسیرِ کامل با بدنه‌ی JSON (کنترلِ بیشتر، مثلاً request_id دستی):
      {"token": "...", "request_id": "...", "text": "..."}
      {"token": "...", "request_id": "...", "audio_b64": "...", "audio_format": "m4a"}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Optional
from urllib.parse import parse_qs, unquote

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
    method, target = parts[0].upper(), parts[1]

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
    return method, target, body


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


def _authenticate(token: str) -> int:
    try:
        return ingest.resolve_user(token)
    except ingest.IngestAuthError as exc:
        # عمداً بدونِ جزئیات؛ اسکنِ کور نباید بفهمد کدام توکن نزدیک بوده.
        raise _BadRequest(401, "توکن نامعتبر است.") from exc


async def _handle_simple(bot, token: str, query: dict[str, list[str]],
                         body: bytes) -> tuple[int, dict]:
    """`POST /s/<token>` — بدنه خودِ متن یا خودِ فایلِ صوتی است.

    میان‌برِ سه‌اکشنی همین را می‌زند: نه هدری لازم است، نه JSONای، نه فیلدِ اضافه‌ای.
    """
    user_id = _authenticate(token)

    audio_format = (query.get("audio") or [""])[0]
    if audio_format:
        if not body:
            raise _BadRequest(400, "فایلِ صوتی خالی است")
        audio = (body, ingest.normalize_audio_format(audio_format))
        text = ""
    else:
        audio = None
        try:
            text = body.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise _BadRequest(400, "متن باید UTF-8 باشد") from exc
        if not text:
            raise _BadRequest(400, "بدنه‌ی خالی — چیزی نفرستادی")

    # میان‌برِ ساده فیلدی برای UUID ندارد، پس کلیدِ تکراری‌نشدن از محتوا ساخته می‌شود.
    request_id = (query.get("id") or [""])[0].strip()[:_MAX_REQUEST_ID] \
        or ingest.fallback_request_id(user_id, text, audio)

    result = await ingest.handle(bot, user_id=user_id, request_id=request_id,
                                 text=text, audio=audio)
    return _status_for(result), {"ok": result.ok, "status": result.status,
                                 "message": result.message}


def _status_for(result) -> int:
    if result.status == "rate_limited":
        return 429
    return 200 if result.ok else 500


async def _handle_ingest(bot, body: bytes) -> tuple[int, dict]:
    data = _parse_payload(body)
    user_id = _authenticate(str(data.get("token") or ""))

    audio = _decode_audio(data)
    text = str(data.get("text") or "")
    if not text.strip() and audio is None:
        raise _BadRequest(400, "یکی از text یا audio_b64 لازم است")

    request_id = str(data.get("request_id") or "").strip()[:_MAX_REQUEST_ID] \
        or ingest.fallback_request_id(user_id, text, audio)

    result = await ingest.handle(bot, user_id=user_id, request_id=request_id,
                                text=text, audio=audio)
    return _status_for(result), {"ok": result.ok, "status": result.status,
                                 "message": result.message}


async def _client(bot, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        try:
            method, target, body = await _read_request(reader)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise _BadRequest(400, "درخواست ناقص") from exc

        raw_path, _, raw_query = target.partition("?")
        path = unquote(raw_path)
        query = parse_qs(raw_query)

        if path == "/health":
            status, payload = (200, {"ok": True}) if method == "GET" \
                else (405, {"ok": False, "message": "فقط GET"})
        elif path == "/ingest":
            if method != "POST":
                status, payload = 405, {"ok": False, "message": "فقط POST"}
            else:
                status, payload = await _handle_ingest(bot, body)
        elif path.startswith("/s/"):
            if method != "POST":
                status, payload = 405, {"ok": False, "message": "فقط POST"}
            else:
                status, payload = await _handle_simple(bot, path[3:], query, body)
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
