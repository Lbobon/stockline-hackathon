"""
Adapt the voice agent to the caller: once the client on the line is identified (CallState.client, usually by the
Éclaireur before the first agent turn), each turn gets a short "how to talk to this person" block from the client's
card (DATA_DIR/context/clients/cNNNN-*.md) and a matching TTS stability. A card that says tu / vous locks the
register (CallSession.caller(), orchestra/session.py): the Linguiste keeps it.

Mirroring is moderate on purpose (MIRROR_LEVEL, 4 out of 10): register (tu/vous), pace, warmth and an occasional
expression of the caller's. Never an accent, a dialect or another language: imitating a caller's accent reads as
mockery.

    load_caller("cli_0001")                  → CallerProfile(name="Boucherie El Baraka Noailles", tu=True, …)
    profile.prompt()                         → text appended at the end of the system prompt
    profile.stability                        → 0.6 (calm caller: steadier voice) / 0.4 (lively) / None (default)
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.config import settings

MIRROR_LEVEL = 4  # out of 10: how far the agent mirrors the caller's way of speaking

CLIENT_ID = re.compile(r"\bcli_(\d{4})\b")

# Words of the "Comment il/elle parle" lines (see data/context/clients/*.md) → how the voice should sound
CALM = ("calme", "posée", "posé", "habitué", "voix basse", "parle peu", "prudent", "très formel", "réservé")
LIVELY = ("s'emballe", "rit facilement", "bonne humeur", "sympathique", "chaleureu", "passion", "raconte sa semaine",
          "longues conversations", "prend des nouvelles", "tutoie vite")  # fmt: skip
HURRIED = ("parle vite", "pas le temps", "phrases courtes", "droit au but", "raccroche vite", "trois phrases",
           "dix secondes", "entre deux services", "ton sec")  # fmt: skip
TU = ("tutoie", "tutoyer")
VOUS = ("vouvoie", "vouvoyer", "très formel")

STABILITY_CALM, STABILITY_LIVELY = 0.6, 0.4  # ElevenLabs default is 0.5; moderate on both sides
PHONE = re.compile(r"\s*·\s*(?:\+33\s?|0)[1-9](?:[\s.]?\d{2}){4}")


@dataclass
class CallerProfile:
    client_id: str
    name: str
    people: str  # the "## Les personnes" section, phone numbers removed
    how_to_talk: str  # the "## Comment leur parler" section ("" if the card has none)
    tu: bool | None = None  # True: tutoyer, False: vouvoyer, None: not said (vouvoyer by default)
    pace: str = "normal"  # "calm" | "lively" | "hurried" | "normal"
    traits: list[str] = field(default_factory=list)

    @property
    def stability(self) -> float | None:
        return {"calm": STABILITY_CALM, "lively": STABILITY_LIVELY}.get(self.pace)

    def prompt(self) -> str:
        register = {
            True: "Tutoie la personne pendant tout l'appel (c'est l'habitude avec ce client), même quand elle dit « vous » "
            "en parlant de l'entreprise. Exemple de ton : « Non, il ne m'en reste plus, mais je te préviens dès qu'il en "
            "arrive. Tu en veux combien ? »",
            False: "Vouvoie la personne pendant tout l'appel, même si elle est familière.",
            None: "Vouvoie la personne, sauf si elle te tutoie la première.",
        }[self.tu]
        pace = {
            "calm": "La personne est posée : parle calmement, sans précipiter, phrases simples.",
            "lively": "La personne est chaleureuse et vivante : sois souriant et un peu plus expressif, sans en faire trop.",
            "hurried": "La personne est pressée : va droit au but, une phrase courte, pas de politesses en plus.",
            "normal": "Garde un ton naturel et professionnel.",
        }[self.pace]
        parts = [
            f"# Interlocuteur identifié : {self.name} ({self.client_id})",
            "Comment ces personnes parlent (fiche client) :",
            self.people,
            f"Comment leur parler :\n{self.how_to_talk}" if self.how_to_talk else "",
            f"Adapte ta façon de parler à cet interlocuteur, avec une intensité modérée ({MIRROR_LEVEL} sur 10) :",
            f"- {register}",
            f"- {pace}",
            "- Tu peux reprendre de temps en temps un de ses mots (« comme d'hab », « la même chose que mardi »), "
            "pas à chaque phrase.",
            "- N'imite jamais un accent, un dialecte ou une autre langue, et ne caricature pas ses tics. "
            "Tu restes le commercial de l'entreprise, poli et chaleureux.",
        ]
        return "\n".join(p for p in parts if p)


def load_caller(client_id: str) -> CallerProfile | None:
    """The caller profile from the client's card, or None if there is no card for this id."""
    m = CLIENT_ID.fullmatch(client_id)
    return _load(m[1]) if m else None


@lru_cache(maxsize=256)
def _load(number: str) -> CallerProfile | None:
    folder = settings.data_dir / "context" / "clients"
    try:
        card = next(iter(sorted(folder.glob(f"c{number}-*.md"))), None)
        text = card.read_text(encoding="utf-8") if card else ""
    except OSError:
        return None
    return parse_card(f"cli_{number}", text) if text else None


def parse_card(client_id: str, text: str) -> CallerProfile:
    title = re.search(r"^#\s+(.+?)(?:\s+\(C\d+.*)?$", text, re.M)
    people = PHONE.sub("", section(text, "Les personnes"))
    how_to_talk = section(text, "Comment leur parler")
    speech = " ".join(re.findall(r"Comment il/elle parle\s*:\s*(.+)", text)).lower()
    rules = how_to_talk.lower()

    tu = register(rules)  # "Comment leur parler" decides ("Le vouvoyer"), then how they speak ("tutoie le commercial")
    if tu is None:
        tu = register(speech)
    # The first person on the card is the one who usually calls: their style decides the pace
    first = (re.findall(r"Comment il/elle parle\s*:\s*(.+)", text) or [""])[0].lower()
    pace = (
        "hurried" if any(w in first for w in HURRIED)
        else "lively" if any(w in first for w in LIVELY)
        else "calm" if any(w in first for w in CALM)
        else "normal"
    )  # fmt: skip
    return CallerProfile(
        client_id=client_id,
        name=title[1].strip() if title else client_id,
        people=people,
        how_to_talk=how_to_talk,
        tu=tu,
        pace=pace,
        traits=[t.strip() for t in re.split(r"[,.]", first) if t.strip()],
    )


def register(text: str) -> bool | None:
    """True = tutoyer, False = vouvoyer, None = the text doesn't say."""
    return True if any(w in text for w in TU) else False if any(w in text for w in VOUS) else None


def section(text: str, heading: str) -> str:
    """The body of '## <heading>' up to the next '## ' heading, trimmed."""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s|\Z)", text, re.M)
    return m[1].strip() if m else ""
