"""
Éclaireur (scout): works WHILE the caller is still speaking.

Every few new words of the live transcript it asks a small fast model what the caller is talking about (client, products,
order number, language, tu / vous), then fetches that from the ERP in parallel: client card + context + upcoming orders
(with their alerts: credit, too late, stock…), product stock and the client's price. The results become `facts` that
the main agent receives with the caller's words, so it can answer without spending tool rounds (≈1–2 s each).
"""

import asyncio
import json
import re
import unicodedata

from app.agent.prompts import get_meta
from app.tools import Tool

from .models import ask_json
from .state import CallState, run_tool

EXTRACT = """Tu écoutes en direct un appel téléphonique entrant chez un grossiste en viande (Médina Viandes, Marseille).
On te donne la transcription PARTIELLE de ce que dit l'appelant (elle peut être coupée au milieu d'un mot).
Extrais ce qui permet de préparer la réponse. Réponds UNIQUEMENT par un objet JSON :
{"client": "nom de commerce ou de client tel que dit, sinon null",
 "products": ["produits cités, tels que dits (ex. « merguez », « broche kebab 10 kg »)"],
 "order_ref": "numéro de commande cité, sinon null",
 "intent": "commande | modification | suivi | reclamation | prix | prospect | autre",
 "language": "code ISO de la langue parlée par l'appelant (fr, en, ar, es, it, tr…)",
 "register": "tu si l'appelant tutoie, vous s'il vouvoie, ? sinon"}
N'invente rien : un nom de personne seul (« c'est Karim ») n'est pas un client ; « l'habituel » n'est pas un produit."""

OPEN_ORDER = ("draft", "confirmed")
LANG_NAMES = {"fr": "français", "en": "anglais", "ar": "arabe", "es": "espagnol", "it": "italien", "tr": "turc", "de": "allemand", "pt": "portugais"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in s if not unicodedata.combining(c))).strip()


def dump(v) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


class Scout:
    def __init__(self, state: CallState, tools: dict[str, Tool]):
        self.state, self.tools = state, tools
        self.latest = ""
        self.last_words = 0
        self.task: asyncio.Task | None = None
        self.dirty = False
        self.tried: set[str] = set()  # mentions already resolved (or tried)
        self.products: dict[str, str] = {}  # mention → productId (best hit)

    # ── input ──

    def feed(self, text: str) -> None:
        """Live transcript so far (the whole utterance). Cheap: starts a run only every ~3 new words, one at a time."""
        self.latest = text
        words = len(text.split())
        if words < 2:
            return
        if self.task and not self.task.done():
            self.dirty = True
            return
        if self.last_words and words - self.last_words < 3:
            return
        self.task = asyncio.create_task(self._loop())

    def new_utterance(self) -> None:
        self.latest, self.last_words, self.dirty = "", 0, False

    async def settle(self, final_text: str, cap_ms: int = 450) -> None:
        """Turn start: give the scout up to `cap_ms` to finish what it is doing (or a last pass on the final words).
        Whatever finishes later still helps the next turn."""
        self.latest = final_text
        if not (self.task and not self.task.done()) and len(final_text.split()) - self.last_words >= 3:
            self.task = asyncio.create_task(self._loop())
        if self.task and not self.task.done():
            await asyncio.wait({self.task}, timeout=cap_ms / 1000)

    async def close(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()

    # ── work ──

    async def _loop(self) -> None:
        while True:
            text = self.latest
            self.last_words, self.dirty = len(text.split()), False
            try:
                await self._run(text)
            except Exception as e:  # a scout failure never breaks the call
                print(f"[scout] {type(e).__name__}: {e}", flush=True)
            if not self.dirty or len(self.latest.split()) - self.last_words < 3:
                return

    async def _run(self, text: str) -> None:
        st = self.state
        known = f"Client déjà identifié : {st.client['name']}." if st.client else "Client pas encore identifié."
        async with st.span("scout", "écoute", f"« {text[-80:]} »"):
            ex = await ask_json(st, "scout", "fast", EXTRACT, f"{known}\nTranscription partielle : « {text} »", 300)
        if not ex:
            return
        await self._linguist(ex)

        jobs = []
        client = (ex.get("client") or "").strip()
        if client and not st.client and norm(client) not in self.tried:
            self.tried.add(norm(client))
            jobs.append(self._client(client))
        for p in (ex.get("products") or [])[:4]:
            if isinstance(p, str) and p.strip() and norm(p) not in self.tried:
                self.tried.add(norm(p))
                jobs.append(self._product(p.strip()))
        ref = (ex.get("order_ref") or "").strip()
        if ref and norm(ref) not in self.tried:
            self.tried.add(norm(ref))
            jobs.append(self._order_ref(ref))
        if jobs:
            await asyncio.gather(*jobs)
        await self._prices()

    async def _linguist(self, ex: dict) -> None:
        """Linguiste: the caller's language and tu / vous, read from the same extraction; drives the prompt and the TTS."""
        st = self.state
        lang = str(ex.get("language") or "fr").lower()[:2]
        reg = ex.get("register") if ex.get("register") in ("tu", "vous") else st.register
        if lang == st.language and reg == st.register:
            return
        async with st.span("linguist", "adaptation", f"{LANG_NAMES.get(lang, lang)} · {'tutoiement' if reg == 'tu' else 'vouvoiement'}"):
            st.language, st.register = lang, reg
            await st.emit({"type": "call_profile", "language": lang, "register": reg})

    async def _search(self, query: str, kind: str) -> list[dict]:
        from app.tools import ErpClient

        try:
            return await ErpClient("voice_agent", "Éclaireur").get("/api/search", {"q": query, "types": kind}) or []
        except Exception:
            return []

    async def _client(self, mention: str) -> None:
        st = self.state
        async with st.span("scout", "client", f"« {mention} »"):
            hits = [h for h in await self._search(mention, "client") if h.get("score", 0) >= 40]
            if not hits:
                return
            top, second = hits[0], hits[1] if len(hits) > 1 else None
            if second and top["score"] < 1.5 * second["score"]:
                st.candidates = [{"id": h["id"], "name": h["label"], "where": h.get("sublabel")} for h in hits[:3]]
                names = " ; ".join(f"{h['label']} ({h.get('sublabel')}, {h['id']})" for h in hits[:3])
                st.facts[f"candidates:{norm(mention)}"] = f"« {mention} » peut être plusieurs clients : {names}. Demande lequel avant d'agir."
                return
            await self._load_client(top["id"])

    async def _load_client(self, client_id: str) -> None:
        st = self.state
        card, ctx = await asyncio.gather(
            run_tool(st, "scout", self.tools, "get_client", {"clientId": client_id}),
            run_tool(st, "scout", self.tools, "get_context", {"entityType": "client", "id": client_id}),
        )
        if card:
            st.facts[f"client:{client_id}"] = f"Client identifié : {card.get('name')} ({client_id}). get_client → {dump(card)}"
        if ctx:
            st.facts[f"context:{client_id}"] = f"get_context(client, {client_id}) → {dump(ctx)}"
        # Proactive: the client's upcoming orders and their alerts (too late, credit, stock…)
        today = (await get_meta()).get("today") or ""
        upcoming = [o for o in (card or {}).get("recentOrders") or [] if o.get("status") in OPEN_ORDER and (o.get("deliveryDate") or "") >= today]
        orders = await asyncio.gather(*(run_tool(st, "scout", self.tools, "get_order", {"orderId": o["id"]}) for o in upcoming[:2]))
        for o in orders:
            if o:
                st.facts[f"order:{o['id']}"] = f"Commande en cours du client : get_order({o['id']}) → {dump(o)}"

    async def _product(self, mention: str) -> None:
        st = self.state
        async with st.span("scout", "produit", f"« {mention} »"):
            hits = [h for h in await self._search(mention, "product") if h.get("score", 0) >= 60][:2]
            if len(hits) == 2 and hits[1]["score"] < 0.9 * hits[0]["score"]:
                hits = hits[:1]
            if not hits:
                return
            self.products[norm(mention)] = hits[0]["id"]
            stocks = await asyncio.gather(*(run_tool(st, "scout", self.tools, "get_product_stock", {"productId": h["id"]}) for h in hits))
            for h, s in zip(hits, stocks, strict=True):
                if s:
                    st.facts[f"stock:{h['id']}"] = f"« {mention} » → {h['label']} ({h['id']}) : get_product_stock → {dump(s)}"
            # Proactive: out of stock → the replacements, before the agent even knows it needs them
            short = next((s for s in stocks if s and (s.get("available") or 0) <= 0), None)
            if short:
                alts = await run_tool(st, "scout", self.tools, "find_alternatives", {"productId": short["id"]})
                if alts:
                    st.facts[f"alternatives:{short['id']}"] = f"{short['name']} en rupture : find_alternatives({short['id']}) → {dump(alts)}"
                    for a in alts[:2]:
                        self.products.setdefault(f"alt:{a['id']}", a["id"])

    async def _order_ref(self, ref: str) -> None:
        st = self.state
        async with st.span("scout", "commande", ref):
            hits = await self._search(ref, "order")
            if hits and hits[0].get("score", 0) >= 80:
                o = await run_tool(st, "scout", self.tools, "get_order", {"orderId": hits[0]["id"]})
                if o:
                    st.facts[f"order:{o['id']}"] = f"Commande citée « {ref} » : get_order({o['id']}) → {dump(o)}"

    async def _prices(self) -> None:
        """Once client and products are known: the client's own price for each (negotiated > rules > catalogue)."""
        st = self.state
        if not st.client:
            return
        cid = st.client["id"]
        todo = [p for p in dict.fromkeys(self.products.values()) if f"price:{cid}:{p}" not in st.facts]
        if not todo:
            return
        async with st.span("scout", "prix", f"{len(todo)} produit(s)"):
            prices = await asyncio.gather(*(run_tool(st, "scout", self.tools, "get_price", {"clientId": cid, "productId": p}) for p in todo))
        for p, r in zip(todo, prices, strict=True):
            if r:
                st.facts[f"price:{cid}:{p}"] = f"get_price({cid}, {p}) → {dump(r)}"
