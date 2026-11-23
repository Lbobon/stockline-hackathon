"""
Greffier (scribe): keeps the client's file up to date in the background, so the voice agent never spends a tool round
(≈1.5 s of silence on the phone) writing notes.

After each turn a small fast model reads the new exchange and decides what to write: a complaint / quality / delivery
issue as a client event (log_client_event), a durable fact (save_context: delivery constraint, contact, preference…).
It never writes twice the same thing, and waits for the client to be identified.
"""

import asyncio
import json

from app.tools import Tool

from .models import ask_json
from .state import CallState, run_tool

EVENT_TYPES = ["complaint", "quality_issue", "delivery_issue", "return", "dispute", "late_payment", "special_request",
               "price_negotiation", "menu_change", "contact_change", "compliment", "churn_risk", "note"]  # fmt: skip
KINDS = ["preference", "fact", "relationship", "pricing", "logistics", "risk"]

SYSTEM = f"""Tu es le greffier d'un grossiste en viande : tu tiens la fiche du client pendant un appel téléphonique.
On te donne les derniers échanges de l'appel et ce qui a déjà été enregistré. Décide ce qu'il faut ENREGISTRER, sans rien inventer :
- events : un incident ou une demande à traiter par l'équipe : réclamation, problème qualité / livraison, retour, litige,
  demande de geste commercial (price_negotiation), demande spéciale, client mécontent qui menace de partir (churn_risk).
  type parmi {EVENT_TYPES} ; title = ligne courte en français ; description = ce qui s'est passé (peut citer le client) ;
  severity low | medium | high.
- facts : un fait DURABLE utile aux prochains appels (contrainte de livraison, nouveau contact, préférence, habitude).
  kind parmi {KINDS} ; title court ; body 1 à 2 phrases.
Une prise de commande (même grosse, même avec une rupture ou un remplacement accepté), une question de stock ou de prix, une politesse :
rien à enregistrer — la commande est déjà dans l'ERP. special_request = une demande inhabituelle à traiter par un humain (découpe, horaire…).
À ENREGISTRER en revanche (l'agent vocal n'a pas d'outil pour le faire, c'est toi qui le fais) :
- un remplaçant refusé et l'issue du geste commercial proposé (retention_offer : accepté, refusé, « à valider par Rachid »)
  → price_negotiation (churn_risk si le client menace de partir) ;
- un remplaçant accepté que le système n'a pas pu appliquer (l'agent dit que Leïla ajuste la commande) → special_request.
Les « Outils » donnés sous un échange disent ce qui s'est vraiment passé.
Ne répète pas ce qui est déjà enregistré. Réponds UNIQUEMENT en JSON : {{"events": [...], "facts": [...]}} (listes vides si rien)."""
WATCHED = ("retention_offer", "apply_substitution", "finalize_order")  # outcomes the scribe must see, not only hear


class Scribe:
    def __init__(self, state: CallState, tools: dict[str, Tool], actor_name: str | None):
        self.state, self.tools, self.actor_name = state, tools, actor_name
        self.pending: list[tuple[str, str, str]] = []  # (caller, agent, outcomes of watched tools) not yet read
        self.written: list[str] = []
        self.tasks: set[asyncio.Task] = set()
        self.lock = asyncio.Lock()

    def outcomes(self) -> str:
        """What the watched tools returned this turn (retention offer, substitution, finalization), one line each."""
        rows = []
        for c in self.state.tool_calls:
            if c["turn"] == self.state.turn_no and c["name"] in WATCHED:
                r = c["result"] if isinstance(c["result"], dict) else {}
                keep = {k: r.get(k) for k in ("status", "decision", "reasons", "pct", "amountHt", "error") if r.get(k) is not None}
                rows.append(f"{c['name']} → {'ok' if c['ok'] else 'ÉCHEC'} {json.dumps(keep, ensure_ascii=False, default=str)}")
        return "; ".join(rows)

    def after_turn(self, caller: str, agent: str) -> None:
        self.pending.append((caller, agent, self.outcomes()))
        if self.state.client:
            t = asyncio.create_task(self._run())
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)

    async def flush(self) -> None:
        """End of call: wait for the pending writes (and read what is left)."""
        if self.pending and self.state.client:
            await self._run()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _run(self) -> None:
        async with self.lock:  # one at a time: each run sees what the previous one wrote
            if not self.pending or not self.state.client:
                return
            batch, self.pending = self.pending, []
            st, cid = self.state, self.state.client["id"]
            exchange = "\n".join(f"Client : {c}\nAgent : {a}" + (f"\nOutils : {o}" if o else "") for c, a, o in batch)
            done = "\n".join(f"- {w}" for w in self.written) or "(rien)"
            try:
                async with st.span("scribe", "lecture", f"{len(batch)} échange(s)"):
                    out = await ask_json(st, "scribe", "fast", SYSTEM, f"Client : {st.client.get('name')} ({cid})\nDéjà enregistré :\n{done}\n\nNouveaux échanges :\n{exchange}", 800)
            except Exception as e:
                print(f"[scribe] {type(e).__name__}: {e}", flush=True)
                return
            if not out:
                return
            jobs = []
            for e in (out.get("events") or [])[:3]:
                if isinstance(e, dict) and e.get("type") in EVENT_TYPES and e.get("title"):
                    args = {"clientId": cid, "type": e["type"], "title": str(e["title"])[:140], "description": e.get("description"),
                            "severity": e.get("severity") if e.get("severity") in ("low", "medium", "high") else None}  # fmt: skip
                    if st.order and e["type"] in ("complaint", "quality_issue", "delivery_issue", "return"):
                        args["orderId"] = st.order.get("id")
                    jobs.append(self._write("log_client_event", {k: v for k, v in args.items() if v}, f"événement {e['type']} : {e['title']}"))
            for f in (out.get("facts") or [])[:2]:
                if isinstance(f, dict) and f.get("kind") in KINDS and f.get("title") and f.get("body"):
                    args = {"entityType": "client", "entityId": cid, "kind": f["kind"], "title": str(f["title"])[:120], "body": f["body"], "tags": ["appel"]}
                    jobs.append(self._write("save_context", args, f"fiche {f['kind']} : {f['title']}"))
            if jobs:
                await asyncio.gather(*jobs)

    async def _write(self, tool: str, args: dict, label: str) -> None:
        async with self.state.span("scribe", "écriture", label):
            r = await run_tool(self.state, "scribe", self.tools, tool, args, self.actor_name)
        if r is not None:
            self.written.append(label)
            await self.state.emit({"type": "scribe_note", "label": label, "data": json.loads(json.dumps(r, default=str))})
