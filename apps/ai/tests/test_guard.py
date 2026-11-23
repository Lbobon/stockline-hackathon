"""Le Garde-fou: numbers said in words, claim detection, Jev's verdict rules, and the Speaker gate (block mode)."""

import asyncio

from app.clients.jev import Choice
from app.clients.mock_speech import MockTTS
from app.guard import Guard, claim_kinds, until
from app.guard import guard as guard_mod
from app.guard.evidence import EvidenceBook, highlights
from app.guard.numbers import numbers_in
from app.services.voice_session import Speaker


def test_numbers_said_in_words():
    assert numbers_in("quatorze euros vingt le kilo") == [14.2]
    assert numbers_in("quatre-vingt-sept kilos") == [87]
    assert numbers_in("soixante et un kilos et demi") == [61.5]
    assert numbers_in("deux mille quatre cent vingt-trois euros soixante-dix") == [2423.7]
    assert numbers_in("dix pour cent") == [10]
    assert numbers_in("un instant, je regarde") == []


def test_only_sentences_with_a_claim_are_checked():
    assert claim_kinds("Bonjour, ici l'assistant vocal de Médina Viandes.") == []
    assert claim_kinds("Je regarde.") == []
    assert "prix" in claim_kinds("Le gigot est à quatorze euros vingt le kilo.")
    assert "stock" in claim_kinds("Les merguez sont en rupture.")
    assert "date" in claim_kinds("Votre commande est livrée mercredi.")
    assert "remise" in claim_kinds("Je vous fais dix pour cent de remise.")


def test_evidence_renames_prices_so_jev_never_confuses_them():
    book = EvidenceBook()
    book.add_tool("get_price", {"clientId": "cli_1", "productId": "prd_1"}, {"price": 13.6, "listPrice": 14.2})
    item = next(iter(book.items.values()))
    assert '"prixClientHT":13.6' in item.text and '"prixCatalogueHT":14.2' in item.text
    assert highlights({"price": 13.6}, [13.6], tool="get_price") == ["prix client 13,60 €"]
    assert highlights({"deliveryDate": "2026-11-25"}) == ["livraison mercredi 25 novembre"]


def fake_jev(monkeypatch, answers):
    """jev.decide → `answers(sentence)` (dict of probabilities), no network."""

    async def decide(state, questions, timeout_ms=800):
        a = answers(state["phrase_de_l_agent"])
        return {**a, "source": Choice("f0", 0.95, {"f0": 0.95})}

    monkeypatch.setattr(guard_mod.jev, "decide", decide)
    monkeypatch.setattr(guard_mod.jev, "available", lambda: True)


def wrong_price(sentence: str) -> dict:
    bad = "douze" in sentence
    return {"supported": 0.05 if bad else 0.95, "contradicts": 0.95 if bad else 0.05, "mismatch": 0.9 if bad else 0.05, "promises": 0.02}


async def test_verdicts_verified_contradicted_and_promise(monkeypatch):
    fake_jev(monkeypatch, lambda s: {"supported": 0.2, "contradicts": 0.1, "mismatch": 0.1, "promises": 0.97} if "remise" in s else wrong_price(s))
    g = Guard(mode="block")
    g._today_set = True
    g.book.add_tool("get_price", {"productId": "prd_1"}, {"price": 13.6})
    ok = await g.check("Le gigot est à treize euros soixante le kilo.")
    bad = await g.check("Le gigot est à douze euros le kilo.")
    promise = await g.check("Je vous fais dix pour cent de remise.")
    small_talk = await g.check("Je regarde.")
    assert ok.status == "verified" and not ok.blocked and ok.source and ok.source["values"] == ["prix client 13,60 €"]
    assert bad.status == "contradicted" and bad.blocked and bad.reason == "contredit"
    assert promise.status == "contradicted" and promise.reason == "promesse"
    assert small_talk.status == "none"
    assert g.summary()["blocked"] == 2


async def test_figures_quoted_to_the_decimal_are_not_flagged(monkeypatch):
    """Physical stock is hidden from Jev (« 300 dispo » must not match 382 on hand), so a sentence quoting it exactly
    looks contradicted: figures read off the ERP to the decimal are not a lie to flag."""
    fake_jev(monkeypatch, lambda s: {"supported": 0.1, "contradicts": 0.9, "mismatch": 0.9, "promises": 0.02})
    g = Guard(mode="block")
    g._today_set = True
    g.book.add_tool("check_product", {"query": "gigot"}, {"name": "Gigot", "unit": "kg", "available": 87.11, "onHand": 382.5, "reserved": 295.39})
    quoted = await g.check("Disponible : 87 kg (382,5 kg en stock, dont 295,4 kg déjà réservés).")
    wrong = await g.check("On en a plus de trois cents kilos disponibles.")
    assert quoted.status != "contradicted" and not quoted.blocked
    assert wrong.status == "contradicted" and wrong.blocked


async def test_figures_of_the_cited_fact_are_never_blocked(monkeypatch):
    fake_jev(monkeypatch, lambda s: {"supported": 0.04, "contradicts": 0.97, "mismatch": 0.36, "promises": 0.29})
    g = Guard(mode="block")
    g._today_set = True
    g.book.add_tool("validate_order", {"orderId": "ord_1"}, {"number": "CMD-1", "status": "confirmed", "totalHt": 372.48, "deliveryDate": "2026-11-26"})
    v = await g.check("C'est confirmé, livraison jeudi 26 novembre pour 372 euros hors taxes.")
    assert v.status == "unverified" and not v.blocked
    wrong = await g.check("C'est confirmé, livraison jeudi 26 novembre pour 290 euros hors taxes.")
    assert wrong.status == "contradicted" and wrong.blocked


async def test_shadow_mode_never_blocks(monkeypatch):
    fake_jev(monkeypatch, wrong_price)
    v = await Guard(mode="shadow").check("Le gigot est à douze euros le kilo.")
    assert v.status == "contradicted" and not v.blocked


async def test_speaker_never_says_a_contradicted_sentence(monkeypatch):
    fake_jev(monkeypatch, wrong_price)
    sent: list = []

    async def send(msg):
        sent.append(msg)

    audio: list[bytes] = []

    async def send_bytes(b):
        audio.append(b)

    g = Guard(mode="block")
    g._today_set = True
    sp = Speaker(MockTTS(), send, send_bytes, guard=g)
    sp.push("Bonjour Karim. Le gigot est à douze euros le kilo. Je vous en mets dix kilos ? ")
    await asyncio.wait_for(sp.halt.wait(), 2)
    assert sp.blocked is not None and "douze" in sp.blocked.sentence
    assert sp.said == ["Bonjour Karim."]  # the blocked sentence and the one after it are never heard
    text = "Bonjour Karim. Le gigot est à douze euros le kilo. "
    assert text[sp.blocked.start : sp.blocked.end] == "Le gigot est à douze euros le kilo."  # offsets in the agent's text
    at = sp.resume()
    sp.push("Pardon, le gigot est à treize euros soixante le kilo. ")
    await sp.finish()
    assert sp.said == ["Bonjour Karim.", "Pardon, le gigot est à treize euros soixante le kilo."]
    assert at == len("Bonjour Karim. Le gigot est à douze euros le kilo. Je vous en mets dix kilos ? ")


async def test_settle_waits_for_a_late_verdict(monkeypatch):
    """The LLM often finishes before Jev answers: settle() must still see the block (then the turn corrects itself),
    and must not wait on the previous block once the correction started."""
    fake_jev(monkeypatch, wrong_price)
    real = guard_mod.jev.decide

    async def slow(*a, **k):
        await asyncio.sleep(0.2)
        return await real(*a, **k)

    monkeypatch.setattr(guard_mod.jev, "decide", slow)

    async def nop(_):
        pass

    g = Guard(mode="block")
    g._today_set = True
    sp = Speaker(MockTTS(), nop, nop, guard=g)
    sp.push("Bonjour Karim. Le gigot est à douze euros le kilo")  # stream over, last sentence without a final space
    assert sp.blocked is None
    await sp.settle()
    assert sp.blocked is not None and "douze" in sp.blocked.sentence
    sp.resume()
    sp.push("Pardon, il est à treize euros soixante. ")
    await asyncio.wait_for(sp.settle(), 2)  # no stale wait on the first block
    assert sp.blocked is None
    await sp.finish()
    assert sp.said == ["Bonjour Karim.", "Pardon, il est à treize euros soixante."]


async def test_until_stops_the_stream_like_a_barge_in():
    stop = asyncio.Event()
    closed = []

    async def events():
        try:
            for i in range(100):
                yield i
                await asyncio.sleep(0.01)
        finally:
            closed.append(True)

    got = []
    gen = events()
    async for x in until(gen, stop):
        got.append(x)
        if x == 3:
            stop.set()
    await gen.aclose()
    assert got == [0, 1, 2, 3] and closed
