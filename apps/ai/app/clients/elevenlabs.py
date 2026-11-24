"""ElevenLabs TTS.

synthesize    POST /v1/text-to-speech/{voice}/stream → mp3 chunks (audio/mpeg), one sentence per request
open_stream   WS /v1/text-to-speech/{voice}/stream-input: the text goes in word by word as the LLM writes it and
              the audio comes back while it is still writing (the voice session uses it when the Garde-fou is not
              blocking; a blocking guard needs whole sentences, checked before they are heard)
    → {"text": " ", "voice_settings": {...}}            open (the text must be one space)
    → {"text": "deux caisses "} … {"text": "…", "flush": true}   words; flush = generate what is buffered now
    → {"text": ""}                                         end of input
    ← {"audio": "<base64 mp3>"} … {"isFinal": true}
"""

import asyncio
import base64
import json
from collections.abc import AsyncIterator

import httpx

from .base import TTSClient, TTSResult
from .http import body_chunks, http_client, post_stream, provider_error
from .ws import open_ws

PREVIOUS_CHARS = 300  # context for the prosody of the next sentence; no measured cost on the first byte (~0.16 s)


class TTSStream:
    """One streaming-input synthesis: `send(text)` words as they come, `end()`, read `chunks()`. `cancel()` on barge-in."""

    mime = "audio/mpeg"

    def __init__(self, url: str, headers: dict[str, str], init: dict):
        self.inbox: asyncio.Queue[dict] = asyncio.Queue()
        self.out: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._run(url, headers, init))

    def send(self, text: str, flush: bool = False) -> None:
        if flush:  # an empty text would end the input: a space
            self.inbox.put_nowait({"text": text or " ", "flush": True})
        elif text:
            self.inbox.put_nowait({"text": text})

    def end(self) -> None:
        self.inbox.put_nowait({"text": ""})

    def cancel(self) -> None:
        self.task.cancel()

    async def chunks(self) -> AsyncIterator[bytes]:
        while (c := await self.out.get()) is not None:
            if isinstance(c, Exception):
                raise c
            yield c

    async def _run(self, url: str, headers: dict[str, str], init: dict) -> None:
        try:
            async with open_ws(url, headers, "elevenlabs tts") as ws:
                await ws.send(json.dumps(init))

                async def pump() -> None:
                    while True:
                        m = await self.inbox.get()
                        await ws.send(json.dumps(m))
                        if m["text"] == "":
                            return

                writer = asyncio.create_task(pump())
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("error") or (msg.get("message") and "audio" not in msg):
                            raise provider_error("elevenlabs tts", msg.get("code") or "ws", str(msg.get("message") or msg.get("error")))
                        if msg.get("audio"):
                            self.out.put_nowait(base64.b64decode(msg["audio"]))
                        if msg.get("isFinal"):
                            break
                finally:
                    writer.cancel()
            self.out.put_nowait(None)
        except asyncio.CancelledError:
            self.out.put_nowait(None)
            raise
        except Exception as e:  # noqa: BLE001  # handed to the reader of chunks()
            self.out.put_nowait(e)


class ElevenLabsTTS(TTSClient):
    name = "elevenlabs"
    ws_base = "wss://api.elevenlabs.io"  # tests point it at a local fake

    def __init__(
        self,
        api_key: str,
        model: str = "eleven_flash_v2_5",  # ~100 ms, French OK, cheapest; eleven_v4_turbo = same latency, more expressive
        voice: str = "JBFqnCBsd6RMkjVDRZzb",  # a premade multilingual voice; pick a French one in your ElevenLabs library
        language: str = "fr",
        http: httpx.AsyncClient | None = None,
    ):
        self.api_key, self.model, self.voice, self.language, self.http = api_key, model, voice, language, http

    def build_body(self, text: str, stability: float | None = None, previous_text: str | None = None) -> dict:
        body: dict = {"text": text, "model_id": self.model}
        if "multilingual_v2" not in self.model:  # language_code is refused by eleven_multilingual_v2
            body["language_code"] = self.language
        if stability is not None:  # lower = more expressive, higher = steadier (default 0.5)
            body["voice_settings"] = {"stability": stability}
        if previous_text:  # what was just said: the new sentence continues its intonation instead of restarting
            body["previous_text"] = previous_text[-PREVIOUS_CHARS:]
        return body

    async def synthesize(
        self, text: str, voice: str | None = None, stability: float | None = None, previous_text: str | None = None
    ) -> TTSResult:
        res = await post_stream(
            self.http or http_client(),
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice or self.voice}/stream?output_format=mp3_44100_128",
            who="elevenlabs tts",
            headers={"xi-api-key": self.api_key},
            json=self.build_body(text, stability, previous_text),
        )
        return TTSResult("audio/mpeg", body_chunks(res))

    def open_stream(self, voice: str | None = None, stability: float | None = None) -> TTSStream:
        """Open the streaming-input socket now (before the first word: the handshake is not on the critical path)."""
        lang = "" if "multilingual_v2" in self.model else f"&language_code={self.language}"
        url = (f"{self.ws_base}/v1/text-to-speech/{voice or self.voice}/stream-input"
               f"?model_id={self.model}&output_format=mp3_44100_128&inactivity_timeout=60{lang}")  # fmt: skip
        init: dict = {"text": " ", "generation_config": {"chunk_length_schedule": [50, 90, 120, 150]}}
        if stability is not None:
            init["voice_settings"] = {"stability": stability}
        return TTSStream(url, {"xi-api-key": self.api_key}, init)
