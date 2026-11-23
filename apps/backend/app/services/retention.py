"""Retention offer when a client refuses a substitute (spec §5): what gesture, how big, and why.

Known client (≥ 5 past orders and ≥ 90 days of history): a % of the lost line value, from their record
    3 %  base
  + 1 %  per year as a client, up to +5 %
  + 2 %  if they buy ≥ 3 000 € HT / month over 12 months
  + 2 %  if every invoice of the last 12 months was paid on time
  + 2 %  if they have open complaints (at risk)
  capped at 15 %.
New client: a fixed ladder that protects the margin (3 % capped at 30 €).
Hard rules: overdue invoices → service offers only (no money); amount > 150 € HT → proposed, Rachid approves.
Below cost is allowed (user decision) but flagged in the explanation.
"""

from __future__ import annotations

from datetime import date

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, fmt_eur, round2
from app.services.clients import client_balances, is_sale

APPROVAL_THRESHOLD = 150.0
SERVICE_OFFERS = ["Livraison demain en priorité du produit manquant", "Quantité partielle ou autre calibre aujourd'hui"]


def _record(client_id: str) -> Doc:
    """12-month revenue, order count, tenure, on-time payment, open complaints."""
    t = today()
    since = add_days(t, -365)
    sales = [o for o in crud.orders.rows.values() if o["clientId"] == client_id and is_sale(o)]
    first = min((o["deliveryDate"] for o in sales), default=t)
    rev12 = sum(o["totalHt"] for o in sales if o["deliveryDate"] >= since)
    paid_at: dict[str, str] = {}
    for p in crud.payments.rows.values():
        if p["clientId"] != client_id:
            continue
        for a in p.get("allocations") or []:
            paid_at[a["invoiceId"]] = max(paid_at.get(a["invoiceId"], ""), p["receivedAt"][:10])
    invs = [i for i in crud.invoices.rows.values() if i["clientId"] == client_id and i["issuedAt"] >= since and i["status"] != "cancelled"]
    late = sum(1 for i in invs if paid_at.get(i["id"], t) > i["dueDate"] and (i["id"] in paid_at or i["dueDate"] < t))
    open_issues = sum(1 for e in crud.client_events.rows.values() if e["clientId"] == client_id and e.get("status") == "open")
    days = (date.fromisoformat(t) - date.fromisoformat(first[:10])).days
    return {"orders": len(sales), "years": round(days / 365, 1), "days": days, "monthlyRevenue": round2(rev12 / 12),
            "invoices12m": len(invs), "lateInvoices12m": late, "openIssues": open_issues,
            "overdue": client_balances(client_id)["overdue"]}  # fmt: skip


def offer(client_id: str, basis_ht: float, cost_ht: float | None = None) -> Doc:
    """GET /api/retention/{clientId}?basisHt=…&costHt=…: the gesture for losing a line worth `basis_ht` € HT.
    `cost_ht` (purchase cost of that line) only serves to flag a below-cost gesture."""
    client = crud.clients.require(client_id)
    r = _record(client_id)
    why: list[str] = []
    if r["overdue"] > 0:
        return {"clientId": client_id, "kind": "service", "status": "service_only", "pct": 0, "amountHt": 0,
                "offers": SERVICE_OFFERS, "factors": r,
                "explanation": f"Pas de geste financier : {fmt_eur(r['overdue'])} TTC en retard de paiement."}  # fmt: skip
    if r["orders"] < 5 or r["days"] < 90:
        pct, cap = 3.0, 30.0
        why.append("nouveau client : barème fixe 3 % plafonné à 30 €")
    else:
        cap = None
        pct = 3.0
        loyalty = min(5, int(r["years"]))
        pct += loyalty
        if loyalty:
            why.append(f"{loyalty} an(s) de fidélité (+{loyalty} %)")
        if r["monthlyRevenue"] >= 3000:
            pct += 2
            why.append(f"~{fmt_eur(r['monthlyRevenue'])} HT/mois (+2 %)")
        if r["invoices12m"] and r["lateInvoices12m"] == 0:
            pct += 2
            why.append("paie toujours à l'heure (+2 %)")
        if r["openIssues"]:
            pct += 2
            why.append(f"{r['openIssues']} réclamation(s) ouverte(s), client à risque (+2 %)")
        pct = min(pct, 15.0)
    amount = round2(basis_ht * pct / 100)
    if cap is not None:
        amount = min(amount, cap)
    status = "needs_approval" if amount > APPROVAL_THRESHOLD else "granted"
    explanation = f"{pct:g} % sur la prochaine commande ({fmt_eur(amount)} HT)" + (" : " + ", ".join(why) if why else "") + "."
    if cost_ht is not None and basis_ht - amount < cost_ht:
        explanation += f" Attention : sous le coût, perte de {fmt_eur(round2(cost_ht - (basis_ht - amount)))} sur cette ligne."
    if status == "needs_approval":
        explanation += " Au-delà de 150 € HT : à valider par Rachid."
    return {"clientId": client_id, "clientName": client["name"], "kind": "money", "status": status, "pct": pct,
            "amountHt": amount, "offers": [*SERVICE_OFFERS, f"Remise de {pct:g} % sur la prochaine commande"],
            "factors": r, "explanation": explanation}  # fmt: skip
