"""find_client / check_product: one model step for the usual lookup chains, against a fake ERP."""

import httpx

from app.tools import TOOLS, ErpClient, ToolContext

CLIENT = {
    "id": "cli_0088", "name": "Couscous Brigitte", "type": "restaurant", "status": "active", "notes": [], "usual": [],
    "recentOrders": [], "contacts": [],
}  # fmt: skip
PRODUCTS = {
    "prd_0043": {"id": "prd_0043", "name": "Broche kebab veau-dinde 10 kg", "unit": "piece", "price": 64.0, "available": 808.0},
    "prd_0044": {"id": "prd_0044", "name": "Broche kebab veau-dinde 20 kg", "unit": "piece", "price": 122.0, "available": 0.0},
}


def fake_erp(search_results: list[dict]) -> tuple[ToolContext, list[str]]:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        path = req.url.path
        if path == "/api/search":
            return httpx.Response(200, json=search_results)
        if path.startswith("/api/clients/"):
            return httpx.Response(200, json=CLIENT)
        if path == "/api/context/for":
            return httpx.Response(200, json={"label": "Couscous Brigitte", "entries": [], "related": []})
        if path.startswith("/api/products/"):
            return httpx.Response(200, json=PRODUCTS[path.rsplit("/", 1)[1]])
        if path == "/api/prices/resolve":
            return httpx.Response(200, json={"price": 1.0, "listPrice": 1.0, "source": "list", "explanation": "x", "candidates": []})
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ToolContext(erp=ErpClient("voice_agent", base_url="http://erp", http=http), actor="voice_agent"), seen


async def test_find_client_one_match_returns_card_and_context():
    ctx, seen = fake_erp([{"type": "client", "id": "cli_0088", "label": "Couscous Brigitte", "sublabel": "Marseille 5e"}])
    r = await TOOLS["find_client"].run({"query": "Couscous Brigitte boulevard Gambetta"}, ctx)
    assert r["client"]["name"] == "Couscous Brigitte"
    assert r["context"]["about"] == "Couscous Brigitte"
    assert "/api/context/for" in seen


async def test_find_client_ambiguous_returns_candidates_only():
    labels = ["Boucherie El Baraka Noailles", "Boucherie El Baraka Marseille 3e"]
    ctx, seen = fake_erp([{"type": "client", "id": f"cli_{i}", "label": label} for i, label in enumerate(labels)])
    r = await TOOLS["find_client"].run({"query": "El Baraka"}, ctx)
    assert [c["label"] for c in r["candidates"]] == labels
    assert "client" not in r and seen == ["/api/search"]  # no card loaded for a guess


async def test_check_product_returns_every_close_match_with_price():
    ctx, _ = fake_erp([{"type": "product", "id": "prd_0043", "label": "Broche 10 kg"}, {"type": "product", "id": "prd_0044", "label": "Broche 20 kg"}])
    r = await TOOLS["check_product"].run({"query": "broche de 20", "clientId": "cli_0002"}, ctx)
    assert [p["id"] for p in r["products"]] == ["prd_0043", "prd_0044"]  # the model picks the 20 kg, out of stock
    assert r["products"][1]["available"] == 0.0 and r["products"][1]["clientPrice"]["price"] == 1.0
