"""
Réflexe (fast path): the turns that need no thinking, answered without the big model.

- « Oui, c'est bon » right after the agent read back a draft order → confirm_order + a spoken sentence built from the
  ERP's answer (≈0.1 s instead of 3–5 s). Only when it is safe: a plain yes, no number, no « mais / plutôt / ajoute »,
  a draft without blocking alerts, a French caller. If the confirmation comes back with an alert, the big model takes over.
- « Non merci, c'est tout, au revoir » once the order is confirmed → a goodbye.
Anything else → None: the main agent handles the turn.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.agent.prompts import MONTHS, WEEKDAYS
from app.tools import Tool

from .models import ask_json
from .state import CallState, run_tool

READ_ANSWER = """Au téléphone, un agent de Médina Viandes vient de relire une commande et de demander au client s'il confirme.
On te donne sa question et la réponse du client. Réponds UNIQUEMENT en JSON :
{"accepts": true si le client accepte la commande TELLE QU'ELLE A ÉTÉ RELUE,
 "changes": true s'il veut changer quoi que ce soit (quantité, produit, date, lot, prix) ou pose une condition avant de confirmer,
 "question": true s'il pose une question qui attend une réponse,
 "message": true s'il demande de transmettre un message à quelqu'un (sans attendre de réponse maintenant),
 "goodbye": true s'il prend congé}
Répéter les chiffres de la commande n'est pas un changement. En cas de doute sur accepts, false."""

YES = re.compile(
    r"^(?:(?:oui|ouais|ouai|yes|ok|okay|d'accord|daccord|parfait|impeccable|nickel|top|super|très bien|tres bien|c'est bon|c'est parfait|"
    r"ça marche|ca marche|vas-y|vas y|allez-y|allez y|allez|go|banco|confirmez|confirme|validez|valide|c'est ça|c'est ca|exactement|"
    r"tout à fait|bien sûr|merci|carrément|on y va|envoie|envoyez)[\s,.!…-]*)+$",
    re.I,
)
BYE = re.compile(r"(au revoir|bonne journée|bonne journee|c'est tout|ce sera tout|rien d'autre|non merci|ça ira|ca ira|à la prochaine|salut)", re.I)
NOT_PLAIN = re.compile(r"\d|\b(mais|plutôt|plutot|ajout|ajoute|change|enlève|enleve|retire|aussi|attend|attends|non|pas|sauf|moins|plus)\b", re.I)
ASKS_YES = re.compile(r"(confirm|valid|je lance|je passe|on part|ça vous va|ça te va|c'est bon pour|je l'enregistre|on y va|je la passe)", re.I)
HARMLESS = {"expiring_soon"}  # every other ERP alert must be said by the big model


@dataclass
class FastAnswer:
    text: str
    note: str  # what the main agent will read in the history (what really happened)
    kind: str  # confirm | goodbye


def spoken_day(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso[:10])
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def spoken_euros(x) -> str:
    try:
        v = round(float(x))
    except (TypeError, ValueError):
        return ""
    return f"{v} euros hors taxes"


def alerts(o: dict) -> list[dict]:
    return [w for w in o.get("warnings") or [] if w.get("code") not in HARMLESS]


def plain_yes(heard: str) -> bool:
    words = heard.strip()
    return len(words.split()) <= 12 and not NOT_PLAIN.search(words) and bool(YES.match(words.lower()))


async def read_answer(state: CallState, heard: str) -> dict | None:
    """Réflexe: is this a yes to the read-back order, maybe with thanks / a message to pass / a goodbye? (Haiku, ≈0.7 s)"""
    asked = state.turns[-2].get("agent", "") if len(state.turns) >= 2 else ""
    try:
        async with state.span("fast", "lecture du oui", f"« {heard[:60]} »"):
            return await ask_json(state, "fast", "fast", READ_ANSWER, f"Question de l'agent : « {asked[-400:]} »\nRéponse du client : « {heard} »", 120)
    except Exception as e:
        print(f"[fast] {type(e).__name__}: {e}", flush=True)
        return None


def note_agent_turn(state: CallState, spoken: str) -> None:
    """After a main-agent turn: did it just ask the caller to confirm a draft order?"""
    o = state.order
    ok = o and o.get("status") == "draft" and not alerts(o)
    state.awaiting_yes = o["id"] if ok and "?" in spoken and ASKS_YES.search(spoken) else None


async def try_fast(state: CallState, tools: dict[str, Tool], heard: str, actor_name: str | None) -> FastAnswer | None:
    if state.language != "fr":
        return None
    you = "toi" if state.register == "tu" else "vous"

    if state.awaiting_yes:
        plain = plain_yes(heard)
        verdict = {"accepts": True} if plain else await read_answer(state, heard)
        if not verdict or not verdict.get("accepts") or verdict.get("changes") or verdict.get("question"):
            return None
        order_id, state.awaiting_yes = state.awaiting_yes, None
        async with state.span("fast", "confirmation", f"« {heard} »"):
            o = await run_tool(state, "fast", tools, "confirm_order", {"orderId": order_id}, actor_name)
        if not o or o.get("status") != "confirmed" or alerts(o):
            # an alert or an error: the big model explains it, told what just happened
            state.facts[f"fast:{order_id}"] = (
                f"Sur ce oui, le système a appelé confirm_order({order_id}) → {o if o else 'erreur'}. Annonce le résultat et les alertes."
            )
            return None
        day, total = spoken_day(o.get("deliveryDate")), spoken_euros(o.get("totalHt"))
        text = f"C'est confirmé{f', livraison {day}' if day else ''}{f' pour {total}' if total else ''}."
        if verdict.get("message"):
            text += f" Et je transmets {'ton' if you == 'toi' else 'votre'} message à l'équipe."
        text += f" Merci à {you}, bonne journée !" if verdict.get("goodbye") else f" Je peux faire autre chose pour {you} ?"
        note = f"[Note interne : sur ce oui, la commande {o.get('number')} ({o['id']}) a été confirmée par le système (confirm_order ok, statut confirmé, livraison {o.get('deliveryDate')}, total {o.get('totalHt')} € HT). Ne la reconfirme pas.]"
        return FastAnswer(text, note, "confirm")

    confirmed = state.order and state.order.get("status") == "confirmed"
    if confirmed and BYE.search(heard) and not re.search(r"\d|\b(mais|aussi|ajout|encore|autre chose)\b", heard, re.I) and len(heard.split()) <= 12:
        async with state.span("fast", "au revoir"):
            pass
        return FastAnswer(f"Merci à {you}, bonne journée !", "[Note interne : le client a pris congé, l'appel se termine.]", "goodbye")
    return None
