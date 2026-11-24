"""radar_suggestion: the radar's forecast for one client, trimmed for the model, against a fake ERP."""

import httpx

from app.tools import TOOLS, ErpClient, ToolContext

SUGGESTION = {
    "clientId": "cli_0001", "name": "Boucherie El Baraka Noailles", "date": "2027-02-10", "label": "mercredi 10 février",
    "status": "predicted", "probability": 1.0, "reason": "Commande 12 mercredis sur 12 ; Ramadan 2027 : avec le ramadan, les volumes doublent",
    "existingOrderId": None, "expectedHt": 2400.5, "upliftHt": 900.0,
    "lines": [{"productId": "prd_0018", "productName": "Minerai de bœuf", "unit": "kg", "qty": 60.0, "baseQty": 25.0,
               "unitPrice": 7.9, "eventId": "ramadan-2027", "frequency": 0.88}],
    "events": [{"id": "ramadan-2027", "title": "Ramadan 2027", "pitch": "avec le ramadan, les volumes doublent",
                "basis": "mesuré sur 3 occurrences passées : boucheries ×3,38"}],
}  # fmt: skip


async def test_radar_suggestion_returns_lines_and_measured_event():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=SUGGESTION)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = ToolContext(erp=ErpClient("voice_agent", base_url="http://erp", http=http), actor="voice_agent")
    r = await TOOLS["radar_suggestion"].run({"client_id": "cli_0001"}, ctx)
    assert seen[0].url.path == "/api/radar/clients/cli_0001/suggestion"
    assert r["status"] == "predicted" and r["lines"][0]["qty"] > r["lines"][0]["baseQty"]
    assert "unitPrice" not in r["lines"][0]  # prices come from check_product / get_price, never from the forecast
    assert r["events"][0]["basis"].startswith("mesuré")
