"""تبدیل فرمت صوت با ffmpeg.

ویس تلگرام ogg/opus است؛ مدل فال‌بک صوتی (gpt-4o-mini-audio) فقط mp3/wav
می‌پذیرد. این ماژول فقط هنگام رسیدن به آن فال‌بک صدا را به mp3 تبدیل می‌کند.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def ogg_to_mp3(ogg_bytes: bytes) -> bytes:
    """بایت‌های ogg را با ffmpeg به mp3 تبدیل می‌کند (از stdin به stdout)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0", "-f", "mp3", "-ac", "1", "-ar", "16000", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(input=ogg_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg transcode failed: {err.decode(errors='ignore')[:200]}")
    return out
