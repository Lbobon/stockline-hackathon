"""
Hands-free turns: the mic stays open for the whole call, the server decides when the caller has finished.

    one STT stream   audio_start {continuous: true} … frames … audio_end: one live stream (Soniox) for the call
    endpoint         Soniox's <end> token (the caller paused at the end of a sentence); for a provider without one,
                     SILENCE_S without a new word
    finished?        is_complete(text): a sentence that ends on « et », « de », « pour », « euh »… is not finished;
                     one the STT punctuated (. ? !) is; otherwise Jev decides in ≈150 ms (no Jev: finished)
    early start      the voice session starts the answer AT the endpoint, behind a gate: nothing is heard, shown or
                     written to the ERP until the turn is confirmed (finished, or MAX_WAIT_S more of silence). If the
                     caller goes on talking first, the draft is thrown away and their sentence continues; most of the
                     time it is kept, so the check and the extra wait cost nothing.
    barge-in         new words while the agent is answering (not an echo of its own voice): the voice session cuts
                     the audio and cancels the turn

This module only listens and decides; voice_session.VoiceConnection runs, gates, keeps or drops the turns.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing

from app.agent import jev
from app.clients import STTClient
from app.clients.base import STTUpdate
from app.clients.jev import yes_no_question

from .utterance import is_noise
from .vocabulary import stt_hint

SILENCE_S = 0.9  # no endpoint token from the provider: this long without a new word ends the sentence
MAX_WAIT_S = 1.2  # « not finished » but nothing more comes: answer anyway
JEV_TIMEOUT_MS = 350

# A sentence ending on one of these is not finished (« je voudrais du… », « et puis euh »)
TRAILING = {
    "et", "ou", "mais", "donc", "alors", "puis", "de", "du", "des", "le", "la", "les", "un", "une", "à", "au", "aux",
    "pour", "avec", "sans", "sur", "dans", "en", "que", "qui", "quoi", "comme", "parce", "si", "mon", "ma", "mes",
    "votre", "vos", "ton", "ta", "tes", "son", "sa", "ses", "ce", "cette", "ces", "je", "j", "tu", "il", "elle", "on",
    "nous", "vous", "ils", "elles", "euh", "heu", "hum", "ben", "bah", "genre", "enfin", "aussi", "plus", "moins",
}  # fmt: skip


def trailing_open(text: str) -> bool:
    """True when the text visibly stops mid-sentence: a last word that needs a follow-up, or a trailing comma."""
    t = text.strip()
    if t.endswith((",", "-", "…")):
        return True
    words = re.findall(r"[\w']+", t.lower())
    return bool(words) and words[-1].split("'")[-1] in TRAILING


# Measured (Jev, 8 sentences): an intro, a question, an order, « Oui. » ≥ 0.79; « je voudrais deux caisses de », « alors je
# vais prendre », « euh attendez je regarde » ≤ 0.11; ≈140 ms once the connection is warm.
FINISHED = "La phrase du client est complète : il ne s'est pas arrêté au milieu (une présentation, une question, une demande ou un « oui » sont complets)."


async def is_complete(text: str) -> bool:
    """Has the caller finished their turn? Visible cut-offs are not; ends with . ? ! are; otherwise Jev, if set."""
    t = text.strip()
    if trailing_open(t):
        return False
    if t.endswith(("?", "!", ".")) or not jev.available():  # the STT's own punctuation: it heard a sentence end
        return True
    a = await jev.decide(
        {"client_au_telephone": t},
        {"done": yes_no_question(FINISHED)},
        timeout_ms=JEV_TIMEOUT_MS,
    )
    return a is None or a["done"] >= 0.35  # unsure: answering beats a silence


BACKCHANNEL = {"oui", "ouais", "ok", "okay", "d'accord", "daccord", "voilà", "exact", "exactement", "mmh", "hm", "ah", "bon", "très", "bien", "parfait", "super"}  # fmt: skip


def backchannel(heard: str) -> bool:
    """« oui oui », « d'accord », « mmh »: the caller listening, not taking the turn (never cuts the agent)."""
    words = re.findall(r"[\w']+", heard.lower())
    return bool(words) and len(words) <= 3 and all(w in BACKCHANNEL for w in words)


def same_words(a: str, b: str) -> bool:
    return re.findall(r"[\w']+", a.lower()) == re.findall(r"[\w']+", b.lower())


def echo_of(heard: str, agent_text: str) -> bool:
    """The mic picked up the agent's own voice (no headset, echo cancellation leaking): the words heard are a run of
    what the agent just said, in its order, 2 to 12 words. A real interruption (« non attendez ») has words of its own;
    one word (« merci ») is never taken for an echo."""
    words = re.findall(r"[\w']+", heard.lower())
    said = re.findall(r"[\w']+", agent_text.lower())
    n = len(words)
    return 2 <= n <= 12 and any(said[i : i + n] == words for i in range(len(said) - n + 1))


class Listener:
    """One live STT stream for a hands-free call. Calls `on_words(text)` with the words since the last committed turn
    as they are recognised, and `on_endpoint(text)` with the final words since that turn when the caller pauses."""

    def __init__(
        self,
        stt: STTClient,
        mime: str,
        hint: str | None,
        on_words: Callable[[str, STTUpdate], Awaitable[None]],
        on_endpoint: Callable[[str], Awaitable[None]],
    ):
        self.stt, self.mime, self.hint = stt, mime, hint
        self.on_words, self.on_endpoint = on_words, on_endpoint
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.committed = 0  # characters of the final transcript already answered
        self.stable = ""  # final words so far (whole call)
        self.last_change = time.perf_counter()
        self.heard = ""  # everything heard so far, provisional words included
        self.fired = 0  # length of the transcript at the last endpoint fired: the turn's words end here
        self.task = asyncio.create_task(self._listen())
        self.silence = asyncio.create_task(self._silence())

    def feed(self, frame: bytes) -> None:
        self.queue.put_nowait(frame)

    def since(self, text: str) -> str:
        return text[self.committed :].strip()

    def commit(self, upto: int) -> None:
        """The turn made of the final words up to `upto` is answered: later words belong to the next turn."""
        self.committed = max(self.committed, upto)

    def close(self) -> None:
        """Mic closed: no more audio (the provider finalizes, a last endpoint may follow)."""
        self.queue.put_nowait(None)

    def cancel(self) -> None:
        for t in (self.task, self.silence):
            if not t.done():
                t.cancel()

    async def _audio(self) -> AsyncIterator[bytes]:
        while (frame := await self.queue.get()) is not None:
            yield frame

    async def _fire(self, whole: str) -> None:
        text = self.since(whole)
        if text and not is_noise(text) and len(whole) > self.fired:
            self.fired = len(whole)
            await self.on_endpoint(text)

    async def _listen(self) -> None:
        hint = await stt_hint(self.stt.name, self.hint)
        try:
            async with aclosing(self.stt.transcribe_stream(self._audio(), self.mime, hint)) as updates:  # type: ignore[type-var]
                async for u in updates:
                    self.stable = u.stable or (u.text if u.final else self.stable)
                    if u.text != self.heard:
                        self.heard = u.text
                        self.last_change = time.perf_counter()
                        words = self.since(u.text)
                        if words and not is_noise(words):
                            await self.on_words(words, u)
                    if u.endpoint or u.final:
                        await self._fire(self.stable)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  # the stream broke (network, provider): say so in the log, the session reopens it
            print(f"[voice] hands-free stream: {type(e).__name__}: {e}", flush=True)

    async def _silence(self) -> None:
        """Endpoint by silence, for providers without an endpoint token (and as a safety net)."""
        while True:
            await asyncio.sleep(0.15)
            if time.perf_counter() - self.last_change >= SILENCE_S and len(self.heard) > self.fired:
                await self._fire(self.heard)  # no final words yet maybe: the provisional ones
