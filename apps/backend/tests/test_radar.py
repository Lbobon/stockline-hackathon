"""Self-check for /api/radar on the Médina Viandes seed (fresh temp DB).

    cd apps/backend && DATA_DIR=data uv run python tests/test_radar.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "erp.db")
os.environ["FEEDS"] = "off"  # the live sources (weather, matches) have their own test: tests/test_feeds.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

with TestClient(app) as c:
    f = c.get("/api/radar/forecast", params={"date": "2026-11-25"}).json()
    by = {r["clientId"]: r for r in f["clients"]}
    s = f["summary"]
    assert s["predicted"] >= 10 and s["upsell"] >= 3 and s["upliftHt"] > 0, s
    assert {e["id"] for e in f["events"]} >= {"mariages", "marche-noel-2026"}
    ev = {e["id"]: e for e in f["events"]}
    assert ev["mariages"]["measured"] is None and ev["mariages"]["basis"].startswith("estimation")  # no history: labelled
    # measured on its 2 past occurrences, and too weak to pass the noise test (demand._significant): said as such
    assert ev["marche-noel-2026"]["measured"]["snacking"]["occurrences"] == 2
    assert ev["marche-noel-2026"]["basis"] == "pas d'effet mesurable sur l'historique", ev["marche-noel-2026"]["basis"]
    assert f["action"]["calls"] == s["toCall"] and f["action"]["expectedHt"] > 0 and f["action"]["pitch"].startswith(f"Appeler {s['toCall']} clients")

    # The calendar is an ERP table: ramadan and the aïds measured from history, per segment.
    cal = c.get("/api/radar/calendar", params={"type": "ramadan"}).json()
    assert [e["id"] for e in cal] == ["ramadan-2024", "ramadan-2025", "ramadan-2026", "ramadan-2027"]
    up = c.get("/api/radar/uplift").json()
    assert up["ramadan"]["boucheries"]["factor"] > 2 and up["ramadan"]["boucheries"]["occurrences"] == 3, up["ramadan"]
    assert up["school_holidays"]["collectivites"]["factor"] < 0.6  # canteens close in the holidays
    # Ramadan 2027 (8 Feb): butchers get more on each usual line, from the measured factor
    rf = c.get("/api/radar/forecast", params={"date": "2027-02-10"}).json()
    assert any(l["eventId"] == "ramadan-2027" and l["qty"] > l["baseQty"] for r in rf["clients"] for l in r.get("lines", []))
    # Adding an event goes through the API, with an actor, and shows in the forecast's events
    new = c.post("/api/radar/calendar", headers={"x-actor": "user"}, json={
        "type": "om_home", "kind": "sport", "title": "OM – Lyon au Vélodrome", "date": "2026-12-06", "from": "2026-12-06", "to": "2026-12-06",
        "segments": ["snacking"], "pitch": "avec le match OM–Lyon", "estimate": {"boosts": [{"productId": "prd_0043", "factor": 1.3}]}})
    assert new.status_code == 201, new.text
    assert any(e["id"] == new.json()["id"] for e in c.get("/api/radar/events", params={"from": "2026-12-06", "to": "2026-12-06"}).json())
    assert c.delete(f"/api/radar/calendar/{new.json()['id']}", headers={"x-actor": "user"}).status_code == 204

    for r in f["clients"]:
        assert r["lat"] and r["lng"] and r["reason"]
        if r["status"] == "predicted":
            assert r["lines"] and 0 < r["probability"] <= 1 and "existingOrderId" not in r
        if r["status"] == "upsell":
            assert r["existingOrderId"] and r["addLines"] and all(l["eventId"] for l in r["addLines"])

    # An estimated event raises a line, never above what the estimate allows.
    est = {b["productId"]: b["factor"] for b in ev["mariages"]["boosts"]}
    for r in f["clients"]:
        for l in [*r.get("lines", []), *r.get("addLines", [])]:
            if l["eventId"] == "mariages" and l["baseQty"]:
                assert l["qty"] <= l["baseQty"] * est.get(l["productId"], 4) + 1, l

    # Weddings: caterers who already ordered get an add-on call for whole lambs.
    assert any(r["status"] == "upsell" and any(l["productId"] == "prd_0001" for l in r["addLines"]) for r in f["clients"])

    # Nothing out of stock is proposed: the 20 kg spit (at zero, nothing incoming) never appears.
    assert not any(l["productId"] == "prd_0044" for r in f["clients"] if r["status"] == "predicted" for l in r["lines"])

    # Already-ordered clients carry their order id; lost clients are left out.
    assert all(r.get("existingOrderId") for r in f["clients"] if r["status"] in ("ordered", "upsell"))
    assert "cli_0044" not in by  # Couscous Marion, tagged "perdus"

    # Call order: loyal customers first, then by expected € net; a delivery that isn't worth it is not called.
    rows = [r for r in f["clients"] if r["status"] in ("predicted", "upsell")]
    for r in rows:
        assert r["loyalty"]["tier"] in ("fidele", "regulier", "occasionnel") and 0 <= r["loyalty"]["score"] <= 1
        assert r["loyalty"]["weeksActive"] <= 13 and r["routeFit"]["costEur"] >= 0 and isinstance(r["call"], bool)
        if r["status"] == "upsell":
            assert r["call"]  # an add-on rides on a delivery that happens anyway
        if not r["call"]:
            fit = r["routeFit"]
            assert r["skipReason"] and "priority" not in r
            assert r["totalHt"] < 120 or fit["costEur"] > 0.5 * r["marginHt"], r
    called = [r for r in f["clients"] if r.get("call")]
    assert [r["priority"] for r in called] == list(range(1, len(called) + 1))
    assert f["clients"][: len(called)] == called  # the forecast lists them in call order
    keys = [(r["wave"], -r["expectedNetEur"]) for r in called]
    assert keys == sorted(keys), keys
    assert called[0]["loyalty"]["tier"] == "fidele"
    assert s["toCall"] == len(called) and s["skipped"] == len(rows) - len(called) and s["skipped"] >= 1


    # One client, for the agent during an inbound call: a butcher during ramadan 2027 gets its boosted lines
    butcher = next(r for r in rf["clients"] if r["status"] == "predicted" and any(l["eventId"] == "ramadan-2027" for l in r["lines"]))
    sg = c.get(f"/api/radar/clients/{butcher['clientId']}/suggestion", params={"date": "2027-02-10"}).json()
    assert sg["status"] == "predicted" and any(l["eventId"] == "ramadan-2027" for l in sg["lines"]) and sg["events"], sg
    assert c.get("/api/radar/clients/cli_0044/suggestion").json()["status"] == "unlikely"  # lost client
    assert c.get("/api/radar/clients/cli_9999/suggestion").status_code == 404

print("radar OK:", s)
