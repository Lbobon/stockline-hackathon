"""
One push-to-talk utterance, transcribed WHILE it is recorded (used by WS /voice and WS /voice/command).

    audio_start  → Utterance(stt, mime, hint, on_partial)   opens the provider stream (if stt.streams)
    binary frame → feed(frame)                              forwarded to the provider at once
                     … on_partial(STTUpdate)                partial transcripts, to show while the caller speaks
    audio_end    → await text()                             end of audio: only the last words are left to finalize

With a streaming STT (Soniox, Deepgram, mock) the transcript is ready a few hundred ms after the release, instead
of transcribing the whole recording then. Every frame is also kept: if the live stream breaks (network, provider
error, no final words in time), text() transcribes the whole recording the classic way, so the utterance is never
lost. cancel() (barge-in, client gone) closes the provider stream.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing

from app.clients import STTClient
from app.clients.base import STTUpdate

from .vocabulary import stt_hint

# After audio_end, how long to wait for the live stream's final words. Soniox normally answers in ~0.8-1.6 s (measured
# with real speech); when it is slower, the words heard so far are used instead (a slow provider would be just as
# slow on the whole recording, which used to cost 5 s of waiting plus a full re-transcription).
FINAL_TIMEOUT_S = 2.5
FALLBACK_TIMEOUT_S = 8.0  # nothing heard live: the whole recording must come back within this, or the turn errors

# A transcript made only of these is a noise / hesitation, not a turn (kitchen noise, a breath, "euh…")
NOISE_WORDS = {"euh", "heu", "hum", "hm", "hmm", "mh", "mmh", "ah", "oh", "bah", "ben", "pff", "tss", "uh", "um"}


def is_noise(text: str) -> bool:
    """True when the transcript has no real word: empty, punctuation, or only hesitation sounds."""
    words = re.findall(r"[\w']+", text.lower())
    return not words or all(w in NOISE_WORDS for w in words)


class Utterance:
    def __init__(self, stt: STTClient, mime: str, hint: str | None, on_partial: Callable[[STTUpdate], Awaitable[None]]):
        self.stt, self.mime, self.hint, self.on_partial = stt, mime, hint, on_partial
        self.frames: list[bytes] = []
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()  # frames for the live stream, None = end of audio
        self.heard = ""  # the latest live transcript (final words + current guess), kept for a slow finalize
        self.started_at = time.perf_counter()
        self.first_word_at: float | None = None
        self.live = asyncio.create_task(self._listen()) if stt.streams else None

    def feed(self, frame: bytes) -> None:
        self.frames.append(frame)
        self.queue.put_nowait(frame)

    async def text(self) -> str:
        """Call at audio_end: the final transcript ("" for silence or a mere hesitation: not a turn)."""
        self.queue.put_nowait(None)
        released, path = time.perf_counter(), "live"
        text = None
        if self.live:
            try:
                text = await asyncio.wait_for(self.live, FINAL_TIMEOUT_S)  # on timeout the stream is cancelled
            except Exception as e:
                if self.heard.strip():  # the words already heard are near-final: use them rather than start over
                    text, path = self.heard, f"partial ({type(e).__name__})"
                else:
                    path = f"whole recording ({type(e).__name__}: {e})"
        if text is None:
            hint = await stt_hint(self.stt.name, self.hint)
            text = await asyncio.wait_for(self.stt.transcribe(b"".join(self.frames), self.mime, hint), FALLBACK_TIMEOUT_S)
            path = path if self.live else "whole recording"
        text = text.strip()
        if path != "live" or self.stt.name != "mock":
            first = f"{self.first_word_at - self.started_at:.1f}s" if self.first_word_at else "-"
            print(f"[ai] stt {self.stt.name}: {path}, first words {first}, release→text {time.perf_counter() - released:.2f}s, "
                  f"{len(text)} chars{' (noise: ignored)' if is_noise(text) and text else ''}", flush=True)  # fmt: skip
        return "" if is_noise(text) else text

    def cancel(self) -> None:
        if not self.live:
            return
        if not self.live.done():
            self.live.cancel()
        elif not self.live.cancelled():
            self.live.exception()  # a stream that already failed: nobody will read it, that's fine

    async def _audio(self) -> AsyncIterator[bytes]:
        while (frame := await self.queue.get()) is not None:
            yield frame

    async def _listen(self) -> str:
        hint = await stt_hint(self.stt.name, self.hint)  # cached; frames wait in the queue meanwhile
        shown = None
        # aclosing: leaving early (final words, cancellation) closes the provider's socket right away
        async with aclosing(self.stt.transcribe_stream(self._audio(), self.mime, hint)) as updates:  # type: ignore[type-var]
            async for update in updates:
                if update.text:
                    self.heard = update.text
                    self.first_word_at = self.first_word_at or time.perf_counter()
                if update.final:
                    return update.text
                if update.text != shown:
                    shown = update.text
                    await self.on_partial(update)
        return shown or ""
