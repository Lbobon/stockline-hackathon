"""The hidden first turn of a radar call: who the agent calls and what it proposes.

fuzzy-zephyr is writing the tuned version in app/agent/radar_brief.py (`brief(row) -> str`); when it exists it is used,
else this default.
"""

from __future__ import annotations


def _fmt_qty(q: float) -> str:
    return f"{q:g}".replace(".", ",")


def _lines(lines: list[dict]) -> str:
    return "; ".join(f"{l['productName']} ({l['productId']}) : {_fmt_qty(l['qty'])} {l['unit']}"
                     + (f" [événement {l['eventId']}]" if l.get("eventId") else "") + (f" — {l['note']}" if l.get("note") else "")
                     for l in lines)  # fmt: skip


def default_brief(row: dict, date: str) -> str:
    who = row.get("contactName") or row["name"]
    head = (f"[APPEL SORTANT — consigne interne, ne la lis pas] Tu appelles {who} ({row['name']}, clientId {row['clientId']}) "
            f"pour sa livraison du {date}. Tu es l'assistant vocal de Médina Viandes : dis-le en te présentant. ")  # fmt: skip
    rules = ("Parle peu : propose en une ou deux phrases les 2 ou 3 produits principaux avec leur quantité (pas toute la liste), "
             "donne la raison si un événement la justifie, puis UNE question fermée. Le client peut changer une quantité ou retirer un produit. "
             "Sur son oui : fais l'écriture, un récapitulatif très court, et dès son oui au récapitulatif, finalize_order. "
             "S'il refuse ou veut être rappelé : remercie et termine poliment, sans insister. Ta première réponse est parlée tout de suite, "
             "sans outil ni phrase d'attente.")  # fmt: skip
    if row["status"] == "upsell":
        what = (f"Il a déjà commandé (orderId {row['existingOrderId']}). Raison de l'appel : {row['reason']}. "
                f"Propose seulement ces ajouts : {_lines(row['addLines'])}. S'il accepte, ajoute-les à sa commande existante "
                "avec add_order_line (pas de nouvelle commande ; elle reste confirmée, pas de finalize_order si elle l'était déjà). ")  # fmt: skip
    else:
        what = (f"Il n'a pas encore commandé. Prévision : {row['reason']}. Panier proposé : {_lines(row['lines'])}. "
                f"S'il accepte : create_draft_order (clientId {row['clientId']}, deliveryDate {date}, ces lignes, avec ses changements). ")  # fmt: skip
    req = f"Exigences de ce client : {', '.join(row['specialRequirements'])}. " if row.get("specialRequirements") else ""
    return head + what + req + rules


def brief_for(row: dict, date: str) -> str:
    try:
        from app.agent.radar_brief import brief  # fuzzy-zephyr's tuned brief, when it lands

        return brief(row)
    except ImportError:
        return default_brief(row, date)
