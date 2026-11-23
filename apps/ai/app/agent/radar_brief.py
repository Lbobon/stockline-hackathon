"""The hidden first turn of a radar call (Radar de demande): the agent calls a client before they order.

`brief(row, date)`, where row = one client of GET /api/radar/forecast, is all the campaign engine needs
(app/campaign/brief.py imports it). The agent speaks first from it, like agent/outbound.py.

What the brief makes the agent do, from the sim and the judge's notes on outbound calls:
- say who it is (AI disclosure), then the reason for calling: the client's rhythm and the event, in plain words;
- say 2 or 3 products with spoken quantities, not the whole basket, then ONE closed question;
- write through the order tools (draft → recap → finalize_order; for an add-on, add_order_line on the existing order);
- a no is a no: thank, no second pitch; a callback request is noted, with no time promised.
"""

from __future__ import annotations

import re

# Units said aloud
_UNIT = {"kg": "kilos", "l": "litres", "piece": "pièces", "pièce": "pièces", "carton": "cartons", "colis": "colis", "barquette": "barquettes", "box": "cartons"}


def _say_qty(qty: float, unit: str) -> str:
    """12.0 kg → « 12 kilos », 0.5 kg → « un demi-kilo », 2 piece → « 2 pièces »."""
    words = _UNIT.get(unit, unit)
    if unit in ("kg", "l") and qty == 0.5:
        return "un demi-kilo" if unit == "kg" else "un demi-litre"
    n = f"{qty:g}".replace(".", ",")
    if n.endswith(",5") and unit in ("kg", "l"):
        n = n[:-2] + " et demi"
    return f"{n} {words}"


def _short_name(name: str) -> str:
    """« Broche kebab veau-dinde 10 kg » stays, but drop calibre brackets: « Épaule d'agneau (5 kg) » → « Épaule d'agneau »."""
    return re.sub(r"\s*\([^)]*\)", "", name).strip()


def _lower_first(name: str) -> str:
    return name[:1].lower() + name[1:]


def _lines_for_tools(lines: list[dict]) -> str:
    return "; ".join(
        f"{l['productId']} {l['productName']} : {l['qty']:g} {l['unit']}"
        + (f" (événement {l['eventId']})" if l.get("eventId") else "")
        + (f" — note : {l['note']}" if l.get("note") else "")
        for l in lines
    )


def _headline(lines: list[dict], n: int = 3) -> list[dict]:
    """What to say aloud: event-boosted lines first, then the biggest by value."""
    return sorted(lines, key=lambda l: (not l.get("eventId"), -(l["qty"] * (l.get("unitPrice") or 0))))[:n]


def _spoken_line(l: dict) -> str:
    """A carcass sold by the kilo is said in animals: « 2 agneaux entiers, environ 36 kilos »."""
    name = _lower_first(_short_name(l["productName"]))
    if l["unit"] == "kg" and name.startswith("carcasse"):
        m = re.search(r"\((\d+)-(\d+) kg\)", l["productName"])
        each = (int(m[1]) + int(m[2])) / 2 if m else 18
        n = max(1, round(l["qty"] / each))
        return f"{n} {'bête entière' if n == 1 else 'bêtes entières'} ({name.replace('carcasse ', '')}, environ {_say_qty(l['qty'], 'kg')})"
    return f"{_say_qty(l['qty'], l['unit'])} de {name}"


def _spoken(lines: list[dict]) -> str:
    return ", ".join(_spoken_line(l) for l in lines)


def _events(reason: str) -> list[str]:
    """The event pitches from the forecast reason (« … ; OM – PSG au Vélodrome : avec le match… ; … »)."""
    return [part.split(" : ", 1)[1].strip() for part in reason.split(" ; ")[1:] if " : " in part]


def _rhythm(reason: str) -> str | None:
    m = re.match(r"Commande (\d+) (\w+) sur (\d+)", reason)
    if not m:
        return None
    hits, day, of = int(m[1]), m[2], int(m[3])
    if hits >= of - 1:
        return f"vous commandez presque tous les {day}"
    return f"vous commandez souvent le {day[:-1]}"


RULES = """Déroulé de l'appel (règles de l'appel sortant, elles priment) :
- Ta première réponse est parlée tout de suite, SANS aucun outil (stocks et prix sont déjà vérifiés dans le panier ci-dessus) et sans phrase d'attente : salue, dis « ici l'assistant vocal de Médina Viandes » (tu es une IA, dis-le), la raison de l'appel en une phrase, ce que tu proposes (2 ou 3 produits avec leur quantité, dits naturellement), puis UNE question fermée. Deux ou trois phrases en tout, jamais la liste complète.
- S'il dit oui : fais l'écriture tout de suite (voir plus bas), avec ses changements s'il en a ; puis un récapitulatif très court (produits principaux, jour, total arrondi) et « je la valide ? ». Dès son oui au récapitulatif : finalize_order (recapConfirmed true) dans le même tour, et dis-le simplement.
- Après l'écriture, si l'ERP renvoie insufficient_stock sur des lignes : dis-le clairement pour ces produits, avant le récapitulatif (« le poulet entier est juste en stock, un arrivage est prévu, Leïla vous confirme la quantité ») et demande s'il les garde ; ne dis pas « c'est bon » sur ces lignes.
- S'il change une quantité ou retire un produit : applique-le, sans discuter. S'il demande un prix : le prix de la ligne (c'est son prix client). S'il demande ce qu'il y a d'autre dans le panier : cite les autres produits, brièvement.
- Non, c'est non : remercie, bonne journée, rien d'autre, aucune écriture. « Rappelez-moi plus tard » : note la demande (log_client_event, type call, si tu as l'outil), sans promettre d'heure, et termine.
- Une exigence du client (certificat, pas de substitution) : respecte-la ; ne propose jamais un autre produit que ceux de la liste.
- Pas d'invention : ni date, ni heure de passage, ni remise, ni promotion. Pas d'id, pas de numéro de commande lu à voix haute."""


def brief(row: dict, date: str | None = None) -> str:
    """Instruction for calling one forecast client (status predicted or upsell)."""
    day = date or row.get("date")
    when = f"pour la livraison du {day} (deliveryDate {day})" if day else "pour sa livraison de demain (deliveryDate = demain)"
    who = row.get("contactName") or row["name"]
    reason = row.get("reason") or ""
    events = _events(reason)
    why = "; ".join(x for x in [_rhythm(reason), *events] if x)
    reqs = f"Exigences du client : {', '.join(row['specialRequirements'])}. " if row.get("specialRequirements") else ""
    head = (f"[APPEL SORTANT RADAR — consigne interne, ne la lis jamais] Tu appelles {who} ({row['name']}, clientId {row['clientId']}) "
            f"{when}. Pourquoi : {why or 'son rythme de commande habituel'}. ")  # fmt: skip

    if row.get("status") == "upsell":
        add = row.get("addLines") or []
        what = (f"Le client a DÉJÀ commandé pour cette livraison (orderId {row['existingOrderId']}, livrée {('le ' + day) if day else 'demain'}) : "
                "ne lui repropose pas sa commande. Ne confonds pas les dates : sa commande est livrée ce jour-là ; l'événement peut être "
                "plus tard (ex. des mariages samedi), c'est pour lui qu'il en prend plus dès cette livraison. "
                f"Tu appelles seulement pour un ajout lié à l'événement. À dire : {_spoken(_headline(add))}, avec la raison (« {events[0] if events else 'pour ce jour-là'} »). "
                f"Lignes à ajouter s'il accepte : {_lines_for_tools(add)}. Écriture : add_order_line sur l'orderId {row['existingOrderId']} pour chaque ajout accepté "
                "(jamais de nouvelle commande). Si la commande était confirmée, elle le reste : pas de finalize_order ; "
                "si elle était en attente de validation, finalize_order après son oui au récapitulatif. ")  # fmt: skip
    else:
        lines = row.get("lines") or []
        top = _headline(lines)
        rest = len(lines) - len(top)
        what = (f"Le client n'a pas encore commandé. À dire : son panier habituel, avec {_spoken(top)}"
                + (f" et {rest} autre{'s' if rest > 1 else ''} produit{'s' if rest > 1 else ''} comme d'habitude" if rest > 0 else "")
                + (f" ; les quantités sont un peu plus fortes à cause de l'événement" if any(l.get("eventId") for l in lines) else "")
                + f". Lignes du panier : {_lines_for_tools(lines)}. Écriture s'il accepte : create_draft_order (clientId {row['clientId']}, "
                "la deliveryDate ci-dessus, ces lignes avec ses changements). ")  # fmt: skip
    return head + what + reqs + "\n" + RULES
