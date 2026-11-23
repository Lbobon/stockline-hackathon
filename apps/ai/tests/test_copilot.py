"""Copilote (orchestra/copilot.py): Leïla speaks, the Souffleur keeps the dictated order in sync with the ERP, the gates
become whispers (and go away when fixed), « Valider » goes through finalize_order. Jev helper: values, timeout → None."""

import asyncio

import pytest

from app.agent import jev
from app.orchestra import copilot as cp


class FakeErp:
    """A tiny order store behind fake tools: create / add / update / remove / update_order / finalize_order."""

    def __init__(self):
        self.order = None
        self.calls: list[tuple[str, dict]] = []
        self.gates: list[dict] = []
        self.n = 0

    def summary(self):
        return {**self.order, "lines": [dict(ln) for ln in self.order["lines"]], "warnings": []}

    def tools(self):
        erp = self

        class T:
            def __init__(self, name, fn):
                self.name, self.fn_ = name, fn

            async def run(self, args, ctx):
                erp.calls.append((self.name, args))
                return self.fn_(args)

        def line(pid, qty):
            erp.n += 1
            return {"lineId": f"l{erp.n}", "productId": pid, "qty": qty}

        def create(a):
            erp.order = {
                "id": "ord_9",
                "number": "CMD-9",
                "status": "draft",
                "deliveryDate": a.get("deliveryDate"),
                "lines": [line(x["productId"], x["qty"]) for x in a["lines"]],
            }
            return erp.summary()

        def add(a):
            erp.order["lines"].append(line(a["productId"], a["qty"]))
            return erp.summary()

        def update(a):
            next(ln for ln in erp.order["lines"] if ln["lineId"] == a["lineId"])["qty"] = a["qty"]
            return erp.summary()

        def remove(a):
            erp.order["lines"] = [ln for ln in erp.order["lines"] if ln["lineId"] != a["lineId"]]
            return erp.summary()

        def finalize(a):
            erp.order["status"] = "confirmed"
            return {"decision": "confirm", "reasons": [], "order": erp.summary()}

        fns = {"create_draft_order": create, "add_order_line": add, "update_order_line": update, "remove_order_line": remove,
               "update_order": lambda a: erp.summary(), "finalize_order": finalize}  # fmt: skip
        return [T(n, f) for n, f in fns.items()]


@pytest.fixture
def setup(monkeypatch):
    erp = FakeErp()
    answers: list[dict] = []

    async def ask_json(state, agent, kind, system, prompt, max_tokens=1200):
        return answers.pop(0) if answers else None

    class Erp:
        def __init__(self, *a):
            pass

        async def get(self, path, query=None):
            assert path == "/api/gates/orders/ord_9"
            return {"orderId": "ord_9", "decision": "draft" if erp.gates else "confirm", "reasons": erp.gates}

    async def meta():
        return {"today": "2026-11-23"}

    async def settle(self, text, cap_ms=450):
        return None

    async def no_jev(*a, **k):
        return None

    monkeypatch.setattr(cp, "ask_json", ask_json)
    monkeypatch.setattr(cp, "ErpClient", Erp)
    monkeypatch.setattr(cp, "get_meta", meta)
    monkeypatch.setattr(cp.Scout, "settle", settle)
    monkeypatch.setattr(cp.jev, "decide", no_jev)
    s = cp.CopilotSession(tools=erp.tools())
    s.state.client = {"id": "cli_0001", "name": "Istanbul Grill"}
    events: list[dict] = []

    async def listen(e):
        events.append(e)

    s.state.listeners.append(listen)
    return s, erp, answers, events


def say(s, speaker, text):
    async def go():
        await s.line(speaker, text)
        await s.task

    asyncio.run(go())


def test_dictation_is_kept_in_sync(setup):
    s, erp, answers, events = setup
    answers.append(
        {
            "order": {"deliveryDate": "2026-11-25", "lines": [{"productId": "prd_1", "qty": 10}, {"productId": "prd_2", "qty": 5}]},
            "whispers": [],
        }
    )
    say(s, "client", "Dix kilos de merguez et cinq de haché pour mercredi")
    assert [c[0] for c in erp.calls] == ["create_draft_order"]
    assert erp.calls[0][1]["deliveryDate"] == "2026-11-25" and len(erp.calls[0][1]["lines"]) == 2

    answers.append({"order": {"lines": [{"productId": "prd_1", "qty": 12}, {"productId": "prd_3", "qty": 2}]}, "whispers": []})
    say(s, "client", "Mets plutôt douze de merguez, pas de haché, et deux souris")
    names = [c[0] for c in erp.calls[1:]]
    assert sorted(names) == ["add_order_line", "remove_order_line", "update_order_line"]
    assert {ln["productId"]: ln["qty"] for ln in s.order["lines"]} == {"prd_1": 12, "prd_3": 2}
    assert events[-1]["type"] == "copilot_order" and events[-1]["order"]["id"] == "ord_9"


def test_empty_lines_never_wipe_the_order(setup):
    s, erp, answers, _ = setup
    answers.append({"order": {"lines": [{"productId": "prd_1", "qty": 10}]}})
    say(s, "client", "Dix kilos de merguez")
    answers.append({"order": {"lines": []}})
    say(s, "leila", "C'est noté")
    assert [c[0] for c in erp.calls] == ["create_draft_order"]


def test_gates_become_whispers_and_clear(setup):
    s, erp, answers, events = setup
    erp.gates = [{"code": "credit_limit", "message": "Encours 4 200 € pour un plafond de 4 000 €"}]
    answers.append(
        {
            "order": {"lines": [{"productId": "prd_1", "qty": 300}]},
            "whispers": [{"level": "tip", "title": "Il prend du haché", "body": "Habituellement 5 kg de haché le mercredi."}],
        }
    )
    say(s, "client", "Trois cents kilos de merguez")
    whispers = {e["key"]: e for e in events if e["type"] == "whisper"}
    assert whispers["gate:credit_limit:"]["level"] == "danger" and "4 000" in whispers["gate:credit_limit:"]["body"]
    assert any(k.startswith("llm:") for k in whispers)

    erp.gates = []
    answers.append({"order": {"lines": [{"productId": "prd_1", "qty": 30}]}})
    say(s, "client", "Bon, trente kilos alors")
    assert {"type": "whisper_clear", "keys": ["gate:credit_limit:"]} in events


def test_souffleur_retracts_a_whisper(setup):
    s, _, answers, events = setup
    answers.append(
        {"order": None, "whispers": [{"level": "warn", "title": "Question sans réponse", "body": "Il demande le prix du haché."}]}
    )
    say(s, "client", "C'est combien le haché ?")
    answers.append({"order": None, "whispers": [], "resolved": ["Question sans réponse"]})
    say(s, "leila", "Huit euros quatre-vingt le kilo")
    assert {"type": "whisper_clear", "keys": ["llm:question sans reponse"]} in events and not s.shown.get("llm:question sans reponse")


def test_confirm_goes_through_finalize(setup):
    s, erp, answers, events = setup
    answers.append({"order": {"lines": [{"productId": "prd_1", "qty": 10}]}})
    say(s, "client", "Dix kilos de merguez")
    r = asyncio.run(s.confirm())
    assert r["decision"] == "confirm" and erp.calls[-1] == ("finalize_order", {"orderId": "ord_9", "recapConfirmed": True})
    assert events[-1] == {"type": "copilot_order", "order": s.order, "gate": {"decision": "confirm", "reasons": []}, "final": True}
    answers.append({"order": {"lines": [{"productId": "prd_1", "qty": 99}]}})
    say(s, "client", "Ah et mets-en 99")  # finalized: the souffleur no longer edits it
    assert erp.calls[-1][0] == "finalize_order"


def test_turns_for_the_report(setup):
    s, _, _, _ = setup
    say(s, "client", "Allô, c'est Istanbul Grill")
    say(s, "leila", "Bonjour, je vous écoute")
    assert s.state.turns[0]["caller"] == "Allô, c'est Istanbul Grill" and s.state.turns[0]["agent"] == "Bonjour, je vous écoute"
    assert s.state.turns[0]["path"] == "human"


# ── app/agent/jev.py ──


class FakeJev:
    def __init__(self, delay=0.0):
        self.delay = delay

    async def ask(self, state, questions):
        await asyncio.sleep(self.delay)
        return {
            "answers": {
                "yes": {"type": "noul", "noul": 0.93},
                "mood": {"type": "score", "score": 1.4, "confidence": 0.7, "probabilities": {"0": 0.1}},
            }
        }


def test_jev_decide_values(monkeypatch):
    monkeypatch.setattr(jev, "client", lambda: FakeJev())
    a = asyncio.run(jev.decide("oui", {"yes": {}, "mood": {}}))
    assert a["yes"] == 0.93 and a["mood"].score == 1.4


def test_jev_decide_timeout_and_no_key(monkeypatch):
    monkeypatch.setattr(jev, "client", lambda: FakeJev(delay=0.5))
    assert asyncio.run(jev.decide("oui", {"yes": {}}, timeout_ms=50)) is None
    monkeypatch.setattr(jev, "client", lambda: None)
    assert asyncio.run(jev.decide("oui", {"yes": {}})) is None


def test_resolve_tells_sizes_apart(setup, monkeypatch):
    """« trois broches de 20 kilos »: the search ties the 10 kg and the 20 kg cones; the number said picks the 20 kg."""
    s, _, _, _ = setup

    class Erp:
        def __init__(self, *a):
            pass

        async def get(self, path, query=None):
            return [
                {"id": "prd_0043", "label": "Broche kebab veau-dinde 10 kg", "score": 68},
                {"id": "prd_0044", "label": "Broche kebab veau-dinde 20 kg", "score": 68},
            ]

    monkeypatch.setattr(cp, "ErpClient", Erp)
    assert asyncio.run(s._resolve("trois broches kebab de 20 kilos")) == ("prd_0044", True)
    assert asyncio.run(s._resolve("des broches kebab"))[1] is False  # no number: not sure, the Souffleur's pick stands


def test_reworded_whisper_updates_the_same_card(setup):
    s, _, answers, events = setup
    w1 = {
        "level": "warn",
        "title": "Prix 10 kg : rappel Youssef",
        "body": "Murat conteste 62,08 € (il cite 61,28 €) et veut Youssef au téléphone ; il menace de partir si le prix bouge.",
    }
    w2 = {
        "level": "warn",
        "title": "Murat demande le rappel de Youssef",
        "body": "Il veut que Youssef le rappelle sur le prix (il cite 61,28 €, prix actuel 62,08 €), il menace de partir si le prix bouge.",
    }
    w3 = {"level": "tip", "title": "Il prend du haché", "body": "Habituellement 5 kg de haché le mercredi."}
    answers += [{"order": None, "whispers": [w1]}, {"order": None, "whispers": [w2, w3]}]
    say(s, "client", "Et dis à Youssef de m'appeler pour le prix")
    say(s, "leila", "Je lui transmets")
    keys = [k for k in s.shown if k.startswith("llm:")]
    assert len(keys) == 2 and s.shown[keys[0]]["title"] == w2["title"]  # reworded: same card, new text
