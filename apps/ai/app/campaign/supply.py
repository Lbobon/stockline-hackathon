"""Supplier campaign: the same parallel calls as the client radar, but Médina Viandes BUYS.

GET /api/radar/supply says what the depot will run short of over the next days (or, with `weeks`, /supply/ahead: the
demand spikes of the coming weeks, booked ahead from the week-level model) (the client forecast added up, events
included, against stock + incoming) and who sells it. Here the voice agent calls each of those suppliers as Médina's
buyer, against a simulated supplier played from its persona card (data/context/suppliers), and writes the purchase
order it agreed on (create_purchase_order → send_purchase_order). A sent order counts as incoming stock, so the
shortfall shrinks on the radar as the calls land. Same live stream as the client campaign (engine.emit).
"""

from __future__ import annotations

import asyncio
import random
import uuid

from app.config import settings
from app.schemas import ChatMessage, ToolResultEvent
from app.tools import ErpClient, buyer_tools

from . import engine
from .engine import END, MAX_ROUNDS, Call, Campaign, _complete, _line, _set, emit

PO_TOOLS = {"create_purchase_order", "send_purchase_order"}

SUPPLIER_DECISIONS = {
    "oui": "« C'est bon, je vous livre tout ça » : il accepte toute la commande",
    "modifie": "« Je n'ai pas tout » ou « ce sera un peu plus cher » : il accepte une partie, ou à d'autres conditions",
    "non": "« Je ne peux pas » : il refuse, il n'a rien pour vous",
    "rappeler": "« Rappelez-moi plus tard » : il n'est pas disponible",
}

DISPOSITIONS = [
    (0.50, "Tu as la marchandise : tu acceptes la commande telle quelle, tu confirmes le jour de livraison."),
    (0.30, "Avec les fêtes tu es tendu : pour la plus grosse ligne, tu ne peux fournir qu'environ les deux tiers de la quantité. "
           "Tu le dis, et tu acceptes tout le reste."),
    (0.15, "Tu acceptes tout, mais avec la période tu annonces un prix un peu plus haut (environ +4 %). Tu acceptes si l'acheteur négocie un peu."),
    (0.05, "Tu es en plein chargement des camions : tu demandes qu'on te rappelle dans une heure."),
]

CALLEE_RULES = """Tu joues un fournisseur de Médina Viandes (grossiste en viande halal, MIN des Arnavaux, Marseille). Nous \
sommes le mardi 24 novembre 2026, vers 15 h. L'acheteur de Médina T'APPELLE pour te passer une commande.

Ta fiche (qui tu es, comment tu parles, ce que tu vends) :
<fiche>
{persona}
</fiche>

Ton état d'esprit pour cet appel : {disposition}

Règles :
- Tu PARLES au téléphone : UNE seule réplique à la fois, une ou deux phrases courtes, langage oral, avec tes tics de langage.
- Tu ne connais aucun identifiant technique.
- Quand l'appel est fini (commande passée, refus, ou rappel convenu), dis au revoir puis écris {end} à la fin.
- Écris uniquement ce que tu dis, sans didascalies ni guillemets."""

BUYER_STYLE = """Pour cet appel, tu n'es PAS face à un client de Médina : tu es l'acheteur de Médina Viandes et tu appelles un \
FOURNISSEUR pour lui acheter de la marchandise. Tu ne vends rien. Les seuls outils de cet appel écrivent la commande \
fournisseur."""


def _fmt(q: float) -> str:
    return f"{q:g}".replace(".", ",")


def brief(row: dict) -> str:
    who = row.get("contactName") or row["name"]
    lines = "; ".join(f"{l['productName']} ({l['productId']}) : {_fmt(l['qty'])} {l['unit']} à {_fmt(l['unitPrice'])} €"
                      + (f" [dont {_fmt(round(l['eventQty']))} {l['unit']} pour {', '.join(l['events'])}]" if l.get("eventQty") and l.get("events") else "")
                      for l in row["lines"])  # fmt: skip
    return (
        f"[APPEL SORTANT FOURNISSEUR — consigne interne, ne la lis pas] Tu appelles {who} ({row['name']}, supplierId "
        f"{row['supplierId']}) pour le compte de Médina Viandes : présente-toi comme l'assistant vocal des achats de Médina Viandes. "
        f"Pourquoi : {row['reason']}. À commander : {lines}. Livraison au dépôt au plus tard le {row['deliverBy']}. "
        "Parle peu : annonce en une ou deux phrases les 2 ou 3 produits principaux avec leur quantité et la raison (les événements), "
        "puis demande s'il peut livrer. S'il n'a qu'une partie, prends ce qu'il a. S'il demande plus cher, accepte jusqu'à +5 % en "
        "négociant un peu, au-delà dis que tu reviens vers lui. Dès son accord : create_purchase_order (supplierId "
        f"{row['supplierId']}, expectedDate {row['deliverBy']} ou avant, les quantités convenues, unitPrice seulement si négocié), "
        "puis send_purchase_order, puis un récapitulatif très court et au revoir. S'il refuse ou veut être rappelé : remercie et "
        "termine poliment. Ta première réponse est parlée tout de suite, sans outil ni phrase d'attente."
        + (" C'est une RÉSERVATION À L'AVANCE pour un pic de demande prévu par nos ventes : dis-le (« on réserve dès maintenant "
           "pour être sûrs d'être servis »), livraison le jour indiqué et pas avant (produit frais), expectedDate exactement "
           f"{row['deliverBy']}." if row.get("ahead") else "")
    )


def _persona(supplier_id: str) -> str:
    folder = settings.data_dir / "context" / "suppliers"
    num = supplier_id.split("_")[-1]
    hit = next(iter(sorted(folder.glob(f"s{num}-*.md"))), None)
    return hit.read_text(encoding="utf-8") if hit else f"Fournisseur {supplier_id}."


def _disposition(supplier_id: str) -> str:
    r = random.Random(f"supply-{supplier_id}").random()
    acc = 0.0
    for p, text in DISPOSITIONS:
        acc += p
        if r < acc:
            return text
    return DISPOSITIONS[0][1]


async def _agent_turn(history: list[ChatMessage]) -> tuple[str, list[ToolResultEvent], str | None]:
    from app.agent import run_agent
    from app.clients.factory import get_llm_client
    from app.schemas import ErrorEvent, TextEvent

    text, results, error = "", [], None
    async for ev in run_agent(history, llm=get_llm_client(), tools=buyer_tools(), mode="voice", actor_name="Agent vocal (achats)",
                              caller_style=BUYER_STYLE):  # fmt: skip
        if isinstance(ev, TextEvent):
            text += ev.delta
        elif isinstance(ev, ToolResultEvent):
            results.append(ev)
        elif isinstance(ev, ErrorEvent):
            error = ev.message
    return text.strip(), results, error


def _po_from(results: list[ToolResultEvent]) -> dict | None:
    for r in reversed(results):
        if r.name in PO_TOOLS and r.ok and isinstance(r.result, dict) and r.result.get("id"):
            return r.result
    return None


def _totals(c: Campaign, rows: list[dict]) -> None:
    t = c.totals
    t["called"] = sum(x.status not in ("queued", "ringing") for x in c.calls)
    t["confirmed"] = sum(x.status in ("confirmed", "modified") for x in c.calls)
    t["declined"] = sum(x.status == "declined" for x in c.calls)
    t["callback"] = sum(x.status == "callback" for x in c.calls)
    t["revenueHt"] = round(sum(x.totalHt for x in c.calls), 2)  # here: € bought
    t["neededHt"] = round(sum(r["totalHt"] for r in rows), 2)
    t["coverage"] = round(t["revenueHt"] / t["neededHt"], 3) if t["neededHt"] else 0.0
    answered = sum(x.status in ("confirmed", "modified", "declined", "callback") for x in c.calls)
    t["staffMinutesSaved"] = answered * engine.STAFF_MIN_PER_CALL
    emit(c, {"type": "totals", **t})


async def run_call(c: Campaign, call: Call, row: dict, rows: list[dict]) -> None:
    _set(c, call, "ringing")
    await asyncio.sleep(random.uniform(0.6, 2.0))
    _set(c, call, "talking")
    system = CALLEE_RULES.format(persona=_persona(call.clientId), disposition=_disposition(call.clientId), end=END)
    history = [ChatMessage(role="user", content=brief(row))]
    callee_view: list[ChatMessage] = []
    po = None
    erp = ErpClient("voice_agent", "Agent vocal (achats)")
    try:
        for _ in range(MAX_ROUNDS):
            said, results, error = await _agent_turn(history)
            if error:
                raise RuntimeError(error)
            history.append(ChatMessage(role="assistant", content=said))
            _line(c, call, "agent", said)
            if p := _po_from(results):
                po = p
                call.orderId, call.totalHt, call.lines = p["id"], float(p.get("totalHt") or 0), p.get("lines") or []
                emit(c, {"type": "order", "callId": call.callId, "clientId": call.clientId, "orderId": p["id"],
                         "number": p.get("number"), "totalHt": call.totalHt, "lines": call.lines, "status": p.get("status")})  # fmt: skip
                _totals(c, rows)
            callee_view.append(ChatMessage(role="user", content=said or "(silence)"))
            reply = await _complete(system, callee_view)
            done = END in reply
            reply = reply.replace(END, "").strip().split("\n\n")[0].strip()
            if reply:
                callee_view.append(ChatMessage(role="assistant", content=reply))
                _line(c, call, "client", reply)
                if call.decision is None or call.decision["label"] != "oui" or not po:
                    call.decision = await engine.decide(said, reply, "L'acheteur d'un grossiste vient de passer commande à un fournisseur. Que répond le fournisseur ?",
                                                        SUPPLIER_DECISIONS)  # fmt: skip
                    emit(c, {"type": "decision", "callId": call.callId, **call.decision})
            history.append(ChatMessage(role="user", content=reply or "(silence)"))
            yes = (call.decision or {}).get("label") in ("oui", "modifie")
            if (done or not reply) and not (yes and not po):
                break
            if done and yes and not po:  # agreed and hung up in one breath: the agent still writes it
                said, results, _ = await _agent_turn(history)
                _line(c, call, "agent", said)
                po = _po_from(results) or po
                break
        label = (call.decision or {}).get("label")
        if po and po.get("status") == "draft" and label in ("oui", "modifie"):
            try:  # safety net: agreed on the phone but the agent left it as a draft
                po = await erp.post(f"/api/purchase-orders/{po['id']}/send", {})
            except Exception as e:
                print(f"[supply] send {po['id']}: {e}", flush=True)
        if po:
            call.orderId, call.totalHt = po["id"], float(po.get("totalHt") or 0)
            call.lines = [{k: l.get(k) for k in ("productId", "productName", "qty", "unit", "unitPrice")} for l in po.get("lines") or []]
            emit(c, {"type": "order", "callId": call.callId, "clientId": call.clientId, "orderId": po["id"],
                     "number": po.get("number"), "totalHt": call.totalHt, "lines": call.lines, "status": po.get("status")})  # fmt: skip
            partial = call.totalHt < 0.97 * float(row["totalHt"])  # they had only part of it: shown as « partiel »
            final = "modified" if label == "modifie" or partial else "confirmed"
        else:
            final = {"non": "declined", "rappeler": "callback"}.get(label or "", "declined")
        _set(c, call, final)
    except Exception as e:
        print(f"[supply] appel {call.clientName} : {type(e).__name__}: {e}", flush=True)
        _set(c, call, "failed")
    _totals(c, rows)


async def run(c: Campaign, rows: list[dict], concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(call: Call, row: dict) -> None:
        async with sem:
            await run_call(c, call, row, rows)

    await asyncio.gather(*(one(call, row) for call, row in zip(c.calls, rows)))
    c.status = "done"
    _totals(c, rows)
    emit(c, {"type": "done", "totals": c.totals})


async def start(date: str | None, supplier_ids: list[str] | None, concurrency: int = 6, weeks: int | None = None) -> Campaign:
    erp = ErpClient("voice_agent", "Radar")
    needs = await (erp.get("/api/radar/supply/ahead", {"weeks": weeks}) if weeks else erp.get("/api/radar/supply", {"date": date}))
    rows = [s for s in needs["suppliers"] if not supplier_ids or s["supplierId"] in supplier_ids]
    c = Campaign(id=f"sup_{uuid.uuid4().hex[:8]}", date=needs["from"], forecast=needs, kind="supply")
    c.totals.update({"neededHt": round(sum(r["totalHt"] for r in rows), 2), "coverage": 0.0})
    # a supplier call reuses Call: clientId/clientName hold the supplier (the stream and the page read them the same way)
    c.calls = [Call(callId=f"buy_{i + 1:02d}", clientId=r["supplierId"], clientName=r["name"], lat=0.0, lng=0.0, kind="supply", priority=i + 1)
               for i, r in enumerate(rows)]  # fmt: skip
    engine.CAMPAIGNS[c.id] = c
    asyncio.create_task(run(c, rows, max(1, min(concurrency, 10))))
    return c
