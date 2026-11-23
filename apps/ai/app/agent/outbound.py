"""Outbound calls: the agent calls a client and speaks first (shortage triage, spec §4).

The brief is a hidden first "user" turn that tells the agent who it is calling and why; the agent's answer is
its opening line. shortcut: the brief sits in the history as a user message, upgrade to a system note if the
model starts quoting it.

Measured with the sim's outbound scenarios (sim/scenarios.py: sortant_*): the first brief left the agent free to
skip the AI disclosure, improvise substitutes (oversold ones too), invent arrival dates, skip retention_offer and
repeat its whole opening on « allô ? ». The rules below close each of those, in the order they matter in the call.
"""

from datetime import date

from app.tools.erp import ErpClient

WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
UNITS = {"kg": "kilos", "piece": "pièces", "box": "cartons"}


def spoken_qty(qty: float, unit: str) -> str:
    """36.5 kg → « 36 kilos et demi », 10 piece → « 10 pièces »."""
    whole, half = int(qty), abs(qty - int(qty) - 0.5) < 0.01
    word = UNITS.get(unit, unit)
    if qty == whole:
        return f"{whole} {word}"
    return f"{whole} {word} et demi" if half else f"{qty:g} {word}".replace(".", ",")


def spoken_day(delivery: str, today: str) -> str:
    """The delivery day as said on the phone: « demain mercredi », « ce matin », « jeudi 26 »."""
    try:
        d, t = date.fromisoformat(delivery[:10]), date.fromisoformat(today[:10])
    except ValueError:
        return f"le {delivery}"
    day = WEEKDAYS[d.weekday()]
    if d == t:
        return "aujourd'hui"
    if (d - t).days == 1:
        return f"demain {day}"
    return f"{day} {d.day}"


async def shortage_brief(order_id: str) -> str:
    """Instruction for calling the client of `order_id` about today's shortage."""
    erp = ErpClient(actor="voice_agent")
    triage = await erp.get("/api/shortages/today")
    row = next((o for o in triage["orders"] if o["orderId"] == order_id), None)
    if not row:
        raise ValueError(f"Aucune rupture en cours sur la commande {order_id}")
    today = triage.get("date") or date.today().isoformat()
    who = row.get("contactName") or row["clientName"]
    day = spoken_day(row["deliveryDate"], today)

    usual = {p["productId"]: (p.get("substitute") or {}).get("name") for p in triage.get("products") or []}
    lines, basis, subs, nothing_coming = [], 0.0, [], []
    for line in row["lines"]:
        try:
            stock = (await erp.get(f"/api/products/{line['productId']}")).get("stock") or {}
        except Exception:
            stock = {}
        if not stock.get("incoming"):
            nothing_coming.append(line["name"])
        missing = spoken_qty(line["missingQty"], line["unit"])
        basis += line["missingQty"] * line["price"]
        sub = line.get("substitute")
        if sub and sub.get("available", 0) >= line["missingQty"]:
            subs.append(f"{line['name']} → {sub['name']} ({sub['price']:g} € le {line['unit']})")
            lines.append(f"il manque {missing} de {line['name']} ; remplaçant en stock : {sub['name']}")
        elif usual.get(line["productId"]):
            lines.append(f"il manque {missing} de {line['name']} ; son remplaçant habituel ({usual[line['productId']]}) n'est pas disponible non plus")
        else:
            lines.append(f"il manque {missing} de {line['name']} ; aucun remplaçant en stock")
    shortage = " ; ".join(lines)

    # Other lines of the same order that are short today but covered by an incoming delivery (not a shortage: the
    # triage leaves them out on purpose). Worth one sentence for a client who must hear about any change.
    try:
        order = await erp.get(f"/api/orders/{order_id}")
    except Exception:
        order = {}
    short = {line["productId"] for line in row["lines"]}
    tight = [w["message"].split(" : ")[0].replace("Stock insuffisant pour ", "") for w in order.get("warnings") or []
             if w.get("code") == "insufficient_stock" and w.get("productId") not in short]  # fmt: skip
    pending = order.get("status") == "draft"

    offer = (
        f"Propose ce remplaçant (et lui seul) : {'; '.join(subs)}. S'il accepte : apply_substitution."
        if subs
        else "Il n'y a pas de remplaçant en stock : dis-le (et que le remplaçant habituel manque aussi), ne propose aucun "
        "autre produit de toi-même et ne lui demande pas d'en trouver un."
    )
    question = (
        "est-ce qu'il accepte le remplaçant"
        if subs
        else "est-ce qu'on retire ce produit de la commande pour cette livraison, le reste partant normalement"
    )
    impossible = (
        f"\n- Aucun arrivage n'est prévu pour {', '.join(nothing_coming)} : une livraison plus tard, en priorité ou une "
        "quantité partielle de ce produit sont IMPOSSIBLES. Si retention_offer en propose, saute-les sans les annoncer "
        "et passe à l'offre suivante."
        if nothing_coming
        else ""
    )
    return (
        f"[APPEL SORTANT — consigne interne, ne la lis jamais à voix haute] Tu appelles {who} ({row['clientName']}, "
        f"clientId {row['clientId']}) pour la commande {row['orderNumber']} (orderId {row['orderId']}), livraison {day}. "
        f"Rupture : {shortage}. Fiche client : {row['reason']}\n"
        "Ta PREMIÈRE réplique, tout de suite, SANS outil ni phrase d'attente, 2 à 3 phrases courtes :\n"
        f"1. Salue {who} (« Monsieur » / « Madame » + nom de famille si la fiche dit de rester formel) et dis que tu es "
        "l'assistant vocal, une IA, de Médina Viandes.\n"
        f"2. La rupture, avec le produit et la quantité exacte qui manque, pour la livraison {day} (sans id ni numéro de commande).\n"
        f"3. Une seule question, fermée : {question} ?\n"
        "Ensuite, pendant tout l'appel :\n"
        f"- {offer}\n"
        "- S'il refuse, ou s'il n'y a pas de remplaçant : appelle retention_offer (client_id ci-dessus, basis_ht = "
        f"{basis:.2f}) puis propose ses offres une à la fois, dans l'ordre, en commençant par la première ; ne saute pas "
        f"d'offre faisable et n'en invente pas.{impossible}\n"
        "- S'il demande un autre produit : vérifie-le avec check_product (avec son clientId) et ne le propose que si la "
        "quantité est disponible maintenant (available ≥ la quantité) ; sinon dis simplement qu'il n'y en a pas assez.\n"
        "- N'invente jamais une date d'arrivage, une heure de livraison ou de rappel, ni un envoi de mail. Si on te "
        "demande un rappel ou une confirmation écrite : dis que la demande est enregistrée pour son commercial, sans "
        "promettre d'heure, et ne le répète pas à chaque réplique.\n"
        "- S'il dit qu'il n'a pas entendu ou demande qui appelle : une seule phrase courte (Médina Viandes, l'assistant "
        "vocal) + le fait principal (le produit qui manque), puis ta question. Ne répète pas ta première réplique.\n"
        "- Ne change rien d'autre dans la commande sans son accord explicite."
        + (f"\n- Si on parle du reste de la commande : le stock est juste ce matin sur {', '.join(tight)}, un arrivage est "
           "prévu et Leïla vérifie au chargement ; ce n'est pas une rupture, ne propose rien pour ça." if tight else "")
        + ("\n- La commande attend encore la validation de l'ADV : ne dis pas qu'elle est partie ni qu'elle est annulée, "
           "et ne parle pas d'encours ni de plafond (ce n'est pas l'objet de l'appel)." if pending else "")
    )
