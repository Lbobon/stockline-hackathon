"""Caller-adaptive voice (app/agent/caller.py): spotting the caller, reading their card, the prompt block, TTS stability."""

from types import SimpleNamespace

from app.agent import caller as caller_mod
from app.agent.caller import (
    STABILITY_CALM,
    STABILITY_LIVELY,
    load_caller,
    parse_card,
)
from app.clients.elevenlabs import ElevenLabsTTS

CARD = """# Boucherie El Baraka Noailles (C0001, `cli_0001`)

> Fiche persona.

## Les personnes

- **Hadj Mohamed Zerrouki** — Patron boucher · 06 11 47 82 30
  - Comment il/elle parle : Ancien de Noailles, voix posée, calme, appelle Rachid « mon fils ».
  - Tics : « Mon fils, tu me mets trois agneaux de Sisteron. »
- **Bilal Zerrouki** — Fils, gère les commandes · 07 52 18 63 07
  - Comment il/elle parle : Jeune, efficace, tutoie le commercial depuis des années.

## Comment leur parler

- Agneau de France uniquement.

## Ce qu'ils achètent et comment

Trois carcasses par jour.
"""


def test_parse_card_reads_register_pace_and_strips_phone_numbers():
    p = parse_card("cli_0001", CARD)
    assert p.name == "Boucherie El Baraka Noailles"
    assert p.tu is True  # "tutoie le commercial"
    assert p.pace == "calm"  # the first person: "voix posée, calme"
    assert p.stability == STABILITY_CALM
    assert "06 11" not in p.people and "Hadj Mohamed Zerrouki" in p.people
    assert "Agneau de France" in p.how_to_talk
    assert "Trois carcasses" not in p.people + p.how_to_talk


def test_prompt_is_moderate_and_never_imitates_accents():
    text = parse_card("cli_0001", CARD).prompt()
    assert "Tutoie la personne" in text
    assert "4 sur 10" in text
    assert "N'imite jamais un accent" in text


def test_how_to_talk_rules_win_over_how_they_speak():
    card = CARD.replace("- Agneau de France uniquement.", "- Le vouvoyer, « Hadj ».")
    assert parse_card("cli_0001", card).tu is False


def test_pace_and_register_defaults():
    lively = parse_card("cli_0002", "# X\n\n## Les personnes\n\n- **A**\n  - Comment il/elle parle : Chaleureuse, rit facilement.\n")
    assert (lively.pace, lively.stability, lively.tu) == ("lively", STABILITY_LIVELY, None)
    hurried = parse_card("cli_0003", "# Y\n\n## Les personnes\n\n- **B**\n  - Comment il/elle parle : Pas le temps, vouvoie.\n")
    assert (hurried.pace, hurried.stability, hurried.tu) == ("hurried", None, False)
    assert "Vouvoie la personne" in hurried.prompt()


def test_load_caller_reads_the_card_file(tmp_path, monkeypatch):
    (tmp_path / "context" / "clients").mkdir(parents=True)
    (tmp_path / "context" / "clients" / "c0001-boucherie-el-baraka-noailles.md").write_text(CARD, encoding="utf-8")
    monkeypatch.setattr(caller_mod, "settings", SimpleNamespace(data_dir=tmp_path))  # settings is frozen
    caller_mod._load.cache_clear()
    try:
        assert load_caller("cli_0001").name == "Boucherie El Baraka Noailles"
        assert load_caller("cli_0099") is None
        assert load_caller("not-an-id") is None
    finally:
        caller_mod._load.cache_clear()


def test_elevenlabs_sends_stability_only_when_given():
    tts = ElevenLabsTTS("key", model="eleven_v4_turbo")
    assert "voice_settings" not in tts.build_body("Bonjour")
    assert tts.build_body("Bonjour", 0.6)["voice_settings"] == {"stability": 0.6}


# ── wiring with the orchestra (#26) and the TTS ──


def test_the_card_locks_tu_vous_over_the_linguist(tmp_path, monkeypatch):
    from app.clients.mock_llm import MockLLM
    from app.orchestra.session import CallSession

    (tmp_path / "context" / "clients").mkdir(parents=True)
    card = CARD.replace("- Agneau de France uniquement.", "- Le vouvoyer, « Hadj ».")
    (tmp_path / "context" / "clients" / "c0001-boucherie-el-baraka-noailles.md").write_text(card, encoding="utf-8")
    monkeypatch.setattr(caller_mod, "settings", SimpleNamespace(data_dir=tmp_path))
    caller_mod._load.cache_clear()
    try:
        session = CallSession(llm=MockLLM(), tools=[])
        assert session.caller() is None  # nobody identified yet: nothing locked
        session.state.register = "tu"  # the Linguiste heard a "tu"
        session.state.client = {"id": "cli_0001", "name": "Boucherie El Baraka Noailles"}
        profile = session.caller()
        assert profile.name == "Boucherie El Baraka Noailles"
        assert (session.state.register, session.state.register_locked) == ("vous", True)
    finally:
        caller_mod._load.cache_clear()


def test_markdown_is_not_read_aloud():
    from app.services.voice_session import speakable

    assert speakable("**Parfait**, je note :\n- 10 kilos de veau\n- 2 colis de merguez") == "Parfait, je note :, 10 kilos de veau, 2 colis de merguez"
    assert speakable("1. Escalope de dinde") == "Escalope de dinde"
    assert speakable("Il reste 18.50 € le kilo, c'est un prix-plancher.") == "Il reste 18.50 € le kilo, c'est un prix-plancher."


def test_identifying_the_client_locks_the_register_at_once(tmp_path, monkeypatch):
    from app.orchestra.state import CallState

    (tmp_path / "context" / "clients").mkdir(parents=True)
    (tmp_path / "context" / "clients" / "c0003-le-riad.md").write_text(
        "# Le Riad (C0003)\n\n## Les personnes\n\n- **Fatima**\n  - Comment il/elle parle : Chaleureuse.\n\n"
        "## Comment leur parler\n\n- La tutoyer, elle y tient.\n", encoding="utf-8")  # fmt: skip
    monkeypatch.setattr(caller_mod, "settings", SimpleNamespace(data_dir=tmp_path))
    caller_mod._load.cache_clear()
    try:
        st = CallState()
        st.see_tool("scout", "find_client", {"query": "Riad"}, True, 5, {"client": {"id": "cli_0003", "name": "Le Riad"}})
        assert (st.client["id"], st.register, st.register_locked) == ("cli_0003", "tu", True)
    finally:
        caller_mod._load.cache_clear()
