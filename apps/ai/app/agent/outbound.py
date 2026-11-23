"""Outbound calls: the agent calls a client and speaks first (shortage triage, spec §4).

The brief is a hidden first "user" turn that tells the agent who it is calling and why; the agent's answer is
its opening line. shortcut: the brief sits in the history as a user message, upgrade to a system note if the
model starts quoting it.
"""

from app.tools.erp import ErpClient


async def shortage_brief(order_id: str) -> str:
    """Instruction for calling the client of `order_id` about today's shortage."""
    triage = await ErpClient(actor="voice_agent").get("/api/shortages/today")
    row = next((o for o in triage["orders"] if o["orderId"] == order_id), None)
    if not row:
        raise ValueError(f"Aucune rupture en cours sur la commande {order_id}")
    lines = "; ".join(
        f"{l['name']} : {l['missingQty']:g} {l['unit']} manquant(s)"
        + (f", remplaçant possible {l['substitute']['name']} ({l['substitute']['price']:g} €)" if l.get("substitute") else ", pas de remplaçant en stock")
        for l in row["lines"]
    )
    who = row.get("contactName") or row["clientName"]
    return (
        f"[APPEL SORTANT — consigne interne, ne la lis pas] Tu appelles {who} ({row['clientName']}, clientId {row['clientId']}) "
        f"au sujet de la commande {row['orderNumber']} (orderId {row['orderId']}) livrée le {row['deliveryDate']}. "
        f"Rupture : {lines}. Fiche client : {row['reason']} "
        "Tu as déjà tout ce qu'il faut : ta première réponse est parlée tout de suite, SANS appeler d'outil et sans "
        "phrase d'attente. Commence l'appel maintenant : salue, présente-toi (Médina Viandes), explique la rupture en une phrase, "
        "propose le remplaçant s'il y en a un et demande son accord. S'il accepte, applique la substitution "
        "avec apply_substitution (si l'outil refuse, enregistre l'accord avec log_client_event et dis que Leïla ajuste "
        "la commande au chargement) ; s'il refuse, suis les garde-fous (retention_offer)."
    )
