"""Le Garde-fou: every sentence the agent is about to say is checked against the ERP by Jev, before the client hears it.

    agent sentence ──► claim_kinds()      nothing checkable (« Je regarde. ») → said at once, no Jev call
                   └─► Jev, one request   « supported »  : every value is in the ERP facts (or repeats the client)
                       (≈130 ms warm)     « contradicts »: a value differs from the ERP, or a discount / promise nobody granted
                                          « source »     : which ERP fact the sentence is about
                   ──► verdict            verified ✓ · contradicted ✗ · unverified ? · unchecked (Jev too slow: fail open)

GUARD_MODE=block: a contradicted sentence is never spoken; the voice agent stops and re-says it from the ERP fact
(services/voice_session.py). shadow: the verdicts are shown, nothing is blocked. off: no checks.
The check runs while the TTS synthesizes the sentence, so a verified sentence costs no extra latency.
"""

from __future__ import annotations

import itertools
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from app.agent import jev
from app.clients.jev import choice_question, yes_no_question

from .claims import claim_kinds
from .evidence import EvidenceBook, Item, french_day, highlights
from .numbers import numbers_in

MODES = ("off", "shadow", "block")


def mode_from_env() -> str:
    m = os.environ.get("GUARD_MODE", "shadow").strip().lower()
    return m if m in MODES else "shadow"


# Thresholds (eval: python -m app.guard.eval)
BLOCK_CONTRADICTS = float(os.environ.get("GUARD_BLOCK_AT", 0.7))  # P(contradicts) to block…
BLOCK_SUPPORTED_MAX = 0.3  # …and P(supported) below this
VERIFIED_AT = 0.6
STRONG_CONTRADICTS, STRONG_MISMATCH = 0.8, 0.85  # block mode stops only these (eval: every true catch is above)

SUPPORTED = yes_no_question(
    "Chaque prix, quantité, date, disponibilité, remise ou état de commande que la phrase de l'agent affirme est appuyé par "
    "les faits de l'ERP, avec la même valeur, ou reprend simplement ce que le client vient de dire.",
    yes="tout ce que la phrase affirme figure dans les faits de l'ERP (ou répète le client), avec les mêmes valeurs",
    no="la phrase affirme au moins une valeur absente des faits de l'ERP ou différente de celle des faits",
)
CONTRADICTS = yes_no_question(
    "La phrase de l'agent contredit les faits de l'ERP : elle donne pour un même produit, une même commande ou une même livraison "
    "une valeur différente (prix, quantité disponible, total, date, état), dit disponible ce qui est en rupture, ou accorde au client "
    "une remise, un geste commercial, un prix spécial ou une chose gratuite qu'aucun fait de l'ERP ne prévoit. "
    "Transmettre une demande, proposer de vérifier ou poser une question n'est pas une contradiction.",
    yes="la phrase contredit l'ERP ou promet quelque chose que l'ERP ne prévoit pas",
    no="la phrase est conforme à l'ERP, ou parle d'autre chose que les faits",
)


PROMISES = yes_no_question(
    "L'agent accorde ou promet lui-même au client une remise, un pourcentage, un prix spécial, un geste commercial, une chose gratuite "
    "ou une heure garantie. Dire qu'il ne peut pas le promettre, transmettre la demande ou poser une question, ce n'est pas promettre.",
    yes="l'agent accorde ou promet quelque chose au client",
    no="l'agent n'accorde ni ne promet rien (il informe, demande, refuse ou transmet)",
)
MISMATCH = yes_no_question(
    "Compare chaque nombre de la phrase de l'agent (prix en euros, kilos, pièces, total) avec le nombre des faits de l'ERP pour le même "
    "produit ou la même commande : au moins un nombre est différent (au-delà d'un arrondi), ou la phrase annonce plus que le disponible à la "
    "vente. Un arrondi à l'euro ou au kilo près n'est PAS une différence (372 euros pour 372,48 est juste). "
    "Le prix annoncé au client doit être son prix client (prixClientHT) quand il est connu, pas le prix catalogue. "
    "Une quantité que le client vient de demander n'est pas une erreur.",
    yes="au moins un nombre dit par l'agent diffère de l'ERP",
    no="les nombres dits correspondent à l'ERP, ou la phrase ne contient aucun nombre de l'ERP",
)
OFFER_TOOLS = {"retention_offer"}  # an ERP offer the agent may announce


@dataclass
class Verdict:
    id: str
    turn: int
    sentence: str
    kinds: list[str]
    status: str  # none | verified | contradicted | unverified | unchecked
    blocked: bool = False  # a strong contradiction AND mode block: never spoken
    strong: bool = False  # clear enough to block (in shadow mode: « would have been blocked »)
    mode: str = "shadow"
    start: int | None = None  # offsets in the turn's agent text (the UI marks that span)
    end: int | None = None
    p_supported: float | None = None
    p_contradicts: float | None = None
    p_promises: float | None = None
    p_mismatch: float | None = None
    reason: str | None = None  # contradicted: « contredit » (a value differs from the ERP) or « promesse » (nobody granted it)
    source: dict | None = None  # {label, tool, values: ["prix 14,20 €", …], confidence}
    said: list[str] = field(default_factory=list)  # the numbers heard in the sentence, as said
    ms: int | None = None
    corrected_by: str | None = None  # block mode: id of the correction that replaced it

    def wire(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        return {"type": "guard", **{_camel(k): v for k, v in d.items()}}


_CATALOGUE = {"listPrice", "price"}  # a catalogue price said to a client with his own price is an error, never a "rounding"


def _erp_numbers(x: Any, out: set[float], depth: int = 0, skip: frozenset = frozenset()) -> None:
    if depth > 5:
        return
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        out.add(float(x))
    elif isinstance(x, str):
        for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", x):  # a date: its day number is said (« jeudi 26 »)
            out.add(float(int(m[3])))
        for m in re.finditer(r"(?<![\w-])(\d+(?:[.,]\d+)?)(?![\w-])", x):
            out.add(float(m[1].replace(",", ".")))
    elif isinstance(x, dict):
        for k, v in x.items():
            if k not in skip:
                _erp_numbers(v, out, depth + 1, skip)
    elif isinstance(x, list):
        for v in x[:30]:
            _erp_numbers(v, out, depth + 1, skip)


def _close(said: float, erp: float) -> bool:
    """Same number, or the ERP number rounded the way the agent says it (to the unit, to 2 %)."""
    return abs(said - erp) < 0.011 or abs(said - erp) <= max(1.0, 0.02 * abs(erp)) and (said == round(erp) or said == int(erp) or abs(said - erp) <= 0.5)


def quoted_erp_numbers(sentence: str, items: list[Item]) -> bool:
    """True when the sentence quotes two ERP numbers or more, each to the decimal (« 87 kg, 382,5 en stock dont 295,4
    réservés »): figures read off the ERP, Jev only doubts what they mean. Not a contradiction to flag."""
    said = numbers_in(sentence)
    if len(said) < 2 or all(n == int(n) for n in said):
        return False
    erp: set[float] = set()
    for it in items:
        if it.data is not None:
            _erp_numbers(it.data, erp)
    return all(any(abs(n - x) <= 0.05 or n == round(x) for x in erp) for n in said)


def rounded_erp_numbers(sentence: str, items: list[Item], heard: list[str] | None = None) -> bool:
    """True when the sentence says numbers and each one is a number of the call's ERP facts (exact or rounded).
    Catalogue prices don't count (except in get_price, where `price` IS the client's price)."""
    said = numbers_in(sentence)
    if not said:
        return False
    erp: set[float] = {n for h in heard or [] for n in numbers_in(h)}  # repeating the client's own number is fine
    for it in items:
        if it.data is not None:
            _erp_numbers(it.data, erp, skip=frozenset({"listPrice"}) if it.tool == "get_price" else frozenset(_CATALOGUE))
    return all(any(_close(n, x) for x in erp) for n in said)


def _camel(k: str) -> str:
    head, *rest = k.split("_")
    return head + "".join(w.capitalize() for w in rest)


Emit = Callable[[dict], Awaitable[None]]


class Guard:
    """One per conversation (call or chat). `check(sentence)` never raises and never takes longer than its timeout."""

    _ids = itertools.count(1)

    def __init__(self, state: Any = None, mode: str | None = None, emit: Emit | None = None, timeout_ms: int | None = None):
        self.state = state  # orchestra CallState (facts, tool_calls, span, emit), or None for the text chat
        self.mode = mode or mode_from_env()
        self.book = EvidenceBook()
        self._emit = emit or (state.emit if state is not None and hasattr(state, "emit") else None)
        self.timeout_ms = timeout_ms or int(os.environ.get("GUARD_TIMEOUT_MS", 900 if self.mode == "block" else 2500))
        self.verdicts: list[Verdict] = []
        self.heard: list[str] = []  # the client's last sentences: a quantity the client just asked for needs no ERP source
        self.corrections = 0
        self._today_set = False

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def blocking(self) -> bool:
        return self.mode == "block" and jev.available()

    def client_said(self, text: str) -> None:
        if text.strip():
            self.heard = [*self.heard[-2:], text.strip()]

    def add_brief(self, text: str) -> None:
        """Outbound / radar call: the hidden brief was built from the ERP (basket, prices, stock), so it is evidence."""
        self.book.add_fact("brief", "Consigne d'appel (préparée depuis l'ERP)", text[:2500], tool="brief")

    async def _today(self) -> None:
        if self._today_set:
            return
        self._today_set = True
        from datetime import date, timedelta

        from app.agent.prompts import get_meta

        today = (await get_meta()).get("today")
        if today:
            d = date.fromisoformat(today)
            self.book.add_fact("meta", "Date du jour (ERP)",
                               f"Aujourd'hui : {french_day(today)} {d.year} ; demain : {french_day((d + timedelta(days=1)).isoformat())} ; "
                               f"après-demain : {french_day((d + timedelta(days=2)).isoformat())}.", tool="meta")  # fmt: skip

    async def emit(self, msg: dict) -> None:
        if self._emit:
            try:
                await self._emit(msg)
            except Exception:
                pass

    def turn_no(self) -> int:
        return int(getattr(self.state, "turn_no", 0) or 0)

    async def check(self, sentence: str, start: int | None = None, end: int | None = None) -> Verdict:
        kinds = claim_kinds(sentence)
        v = Verdict(id=f"g{next(self._ids)}", turn=self.turn_no(), sentence=sentence.strip(), kinds=kinds, status="none",
                    mode=self.mode, start=start, end=end)  # fmt: skip
        if not self.enabled:
            return v
        if not kinds or not jev.available():
            self.verdicts.append(v)
            await self.emit(v.wire())
            return v
        started = time.perf_counter()
        await self._today()
        if self.state is not None:
            self.book.sync(self.state)
        items = self.book.view()
        span = self.state.span("guard", f"vérifie « {sentence.strip()[:48]} »") if self.state is not None and hasattr(self.state, "span") else None
        if span:
            await span.__aenter__()
        try:
            a = await jev.decide(self._jev_state(sentence, items), self._questions(items), timeout_ms=self.timeout_ms)
        finally:
            if span:
                await span.__aexit__(None, None, None)
        v.ms = int((time.perf_counter() - started) * 1000)
        v.said = [_say(n) for n in numbers_in(sentence)]
        if a is None:
            v.status = "unchecked"
        else:
            self._decide(v, a, items, sentence)
        self.verdicts.append(v)
        await self.emit(v.wire())
        return v

    def _jev_state(self, sentence: str, items: list[Item]) -> dict:
        return {
            "phrase_de_l_agent": sentence.strip(),
            "ce_que_le_client_a_dit": " / ".join(self.heard) or "(rien)",
            "faits_erp": {f"f{i}": it.text for i, it in enumerate(items)} or {"f0": "(aucun fait chargé)"},
        }

    def _questions(self, items: list[Item]) -> dict:
        options = {f"f{i}": it.label for i, it in enumerate(items)}
        options["aucun"] = "aucun fait de l'ERP ne parle de ce que dit la phrase"
        return {
            "supported": SUPPORTED,
            "contradicts": CONTRADICTS,
            "promises": PROMISES,
            "mismatch": MISMATCH,
            "source": choice_question("De quel fait de l'ERP la phrase de l'agent parle-t-elle ?", options),
        }

    def _decide(self, v: Verdict, a: dict, items: list[Item], sentence: str) -> None:
        sup, con, src = a.get("supported"), a.get("contradicts"), a.get("source")
        v.p_supported = round(sup, 3) if isinstance(sup, float) else None
        v.p_contradicts = round(con, 3) if isinstance(con, float) else None
        item = None
        if src is not None and getattr(src, "choice", "aucun") != "aucun" and src.confidence >= 0.4:
            idx = int(src.choice[1:]) if src.choice[1:].isdigit() else -1
            item = items[idx] if 0 <= idx < len(items) else None
        if item is not None:
            v.source = {"label": item.label, "tool": item.tool, "values": highlights(item.data, numbers_in(sentence), tool=item.tool), "confidence": round(src.confidence, 2)}
        mm = a.get("mismatch")
        v.p_mismatch = round(mm, 3) if isinstance(mm, float) else None
        promise = a.get("promises")
        v.p_promises = round(promise, 3) if isinstance(promise, float) else None
        offered = any(i.tool in OFFER_TOOLS for i in items)
        if v.p_promises is not None and v.p_promises >= 0.85 and not offered and (v.p_supported or 0) < VERIFIED_AT and {"remise", "prix", "date"} & set(v.kinds):
            v.status = "contradicted"  # a discount / free item / guaranteed time nobody in the ERP granted
            v.reason = "promesse"
            v.strong = True
        elif (safe := rounded_erp_numbers(sentence, items, self.heard)) and (v.p_contradicts or 0) < 0.85:
            # every number said is an ERP number (rounded as the agent is told to): Jev's doubt is about the rounding
            v.status = "verified" if (v.p_supported or 0) >= 0.3 else "unverified"
        elif quoted_erp_numbers(sentence, items):
            v.status = "verified" if (v.p_supported or 0) >= 0.3 else "unverified"
        elif (max(v.p_contradicts or 0, v.p_mismatch or 0) >= BLOCK_CONTRADICTS and (v.p_supported or 0) <= BLOCK_SUPPORTED_MAX
              or not safe and (v.p_mismatch or 0) >= 0.85 and (v.p_supported or 0) < 0.75):  # a number no ERP fact has
            v.status = "contradicted"
            v.reason = "contredit"
            # block only a clear contradiction: a doubtful one is flagged ✗ but still spoken (the sim's borderline recaps)
            v.strong = (v.p_contradicts or 0) >= STRONG_CONTRADICTS or (v.p_mismatch or 0) >= STRONG_MISMATCH
        elif v.p_supported is not None and v.p_supported >= VERIFIED_AT and (v.p_contradicts or 0) < 0.5:
            v.status = "verified"
        else:
            v.status = "unverified"
        v.blocked = v.status == "contradicted" and v.strong and self.blocking

    # ── block mode: the correction the voice agent gets instead of the client hearing the sentence ──

    def source_text(self, v: Verdict) -> str:
        if not v.source:
            return "aucun fait de l'ERP ne le prévoit"
        item = next((i for i in self.book.items.values() if i.label == v.source["label"]), None)
        return item.text if item else v.source["label"]

    def correction_note(self, v: Verdict, said_before: str) -> str:
        self.corrections += 1
        heard = f"Le client a entendu jusqu'ici : « {said_before.strip()} ». " if said_before.strip() else "Le client n'a encore rien entendu de ta réponse. "
        return (
            "[Garde-fou — note interne, pas dite par le client, ne la lis jamais] "
            f"Ta phrase « {v.sentence} » n'a PAS été dite au client : elle ne correspond pas à l'ERP. {heard}"
            f"Ce que dit l'ERP : {self.source_text(v)}. "
            "Reprends maintenant avec la bonne information, en une ou deux phrases naturelles, comme si tu continuais ta réponse : "
            "pas d'excuse appuyée, ne parle ni de vérification automatique ni de garde-fou. Si l'ERP ne permet pas d'affirmer la chose "
            "(remise, horaire, geste), dis simplement que tu ne peux pas le promettre et que tu transmets à Leïla. "
            "Puis termine ce que tu avais à dire (question ou récapitulatif)."
        )

    def summary(self) -> dict:
        checked = [v for v in self.verdicts if v.status != "none"]
        ms = sorted(v.ms for v in checked if v.ms is not None)
        return {
            "mode": self.mode,
            "sentences": len(self.verdicts),
            "checked": len(checked),
            "verified": sum(v.status == "verified" for v in checked),
            "contradicted": sum(v.status == "contradicted" for v in checked),
            "blocked": sum(v.blocked for v in checked),
            "strong": sum(v.strong for v in checked),
            "unverified": sum(v.status == "unverified" for v in checked),
            "unchecked": sum(v.status == "unchecked" for v in checked),
            "corrections": self.corrections,
            "medianMs": ms[len(ms) // 2] if ms else None,
            "flags": [{"sentence": v.sentence, "status": v.status, "blocked": v.blocked, "source": (v.source or {}).get("label")}
                      for v in checked if v.status == "contradicted"],  # fmt: skip
        }


def _say(n: float) -> str:
    from .numbers import fmt_number

    return fmt_number(n)

