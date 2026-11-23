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


def spoken_price(price: float) -> str:
    """9.4 → « 9 euros 40 », 64 → « 64 euros »."""
    euros, cents = int(price), round((price - int(price)) * 100)
    return f"{euros} euros {cents:02d}" if cents else f"{euros} euros"


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
            try:  # the client's own price (negotiated > rule > list), as get_price would say it
                price = (await erp.get("/api/prices/resolve", {"clientId": row["clientId"], "productId": sub["productId"],
                                                                "qty": line["missingQty"]})).get("price", sub["price"])  # fmt: skip
            except Exception:
                price = sub["price"]
            offer_line = f"{sub['name'].lower()} à {spoken_price(price)} le {UNITS.get(line['unit'], line['unit']).rstrip('s')}"
            subs.append(f"{line['name']} → {offer_line}")
            lines.append(f"il manque {missing} de {line['name']} ; remplaçant en stock : {offer_line} (son prix à lui)")
        elif usual.get(line["productId"]):
            lines.append(f"il manque {missing} de {line['name']} ; son remplaçant habituel ({usual[line['productId']]}) n'est pas disponible non plus")
        else:
            lines.append(f"il manque {missing} de {line['name']} ; aucun remplaçant en stock")
    shortage = " ; ".join(lines)
    first_short = f"l'{row['lines'][0]['name'].lower()}" if row["lines"][0]["name"][:1].lower() in "aeiouyéè" else f"la {row['lines'][0]['name'].lower()}"

    # Other lines of the same order that are short today but covered by an incoming delivery (not a shortage: the
    # triage leaves them out on purpose). Worth one sentence for a client who must hear about any change.
    try:
        order = await erp.get(f"/api/orders/{order_id}")
    except Exception:
        order = {}
    short = {line["productId"] for line in row["lines"]}
    tight = []  # (name, incoming?) for the other short lines, from the ERP: never claim an arrival that isn't there
    for w in order.get("warnings") or []:
        if w.get("code") == "insufficient_stock" and w.get("productId") and w["productId"] not in short:
            try:
                stock = (await erp.get(f"/api/products/{w['productId']}")).get("stock") or {}
            except Exception:
                stock = {}
            tight.append((w["message"].split(" : ")[0].replace("Stock insuffisant pour ", ""), bool(stock.get("incoming"))))
    pending = order.get("status") == "draft"

    offer = (
        f"Propose ce remplaçant (et lui seul) : {'; '.join(subs)}. S'il accepte : apply_substitution, puis confirme en "
        "une phrase ; aucune autre écriture (ni note de ligne, ni autre modification)."
        if subs
        else "Il n'y a pas de remplaçant en stock : dis-le simplement (le remplaçant habituel manque aussi, mais ne le "
        "nomme que si on te pose la question), ne propose aucun autre produit de toi-même et ne lui demande pas d'en trouver un."
    )
    question = (
        f"est-ce qu'il accepte {subs[0].split(' → ')[1]} à la place (dis le prix dans la même phrase)"
        if subs
        else "est-ce qu'on retire ce produit de la commande pour cette livraison"
        + ("" if tight else ", le reste partant normalement")  # only when the rest of the order is really in stock
    )
    impossible = (
        f"\n- Aucun arrivage confirmé pour {', '.join(nothing_coming)} : une livraison plus tard ou une quantité partielle "
        "ne peuvent PAS être promises. Si retention_offer les propose, dis-le honnêtement en une phrase (« je n'ai rien de "
        "confirmé, je note la demande pour votre commercial ») puis passe à l'offre suivante, sans la reprendre ensuite."
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
        "- S'il dit qu'il n'a pas entendu ou demande qui appelle : réponds en DEUX phrases très courtes, sur ce modèle : "
        f"« Ici l'assistant vocal de Médina Viandes. Il nous manque {first_short} pour {day} : {question} ? » "
        "Rien d'autre (ni la cause, ni le détail), et pas ta première réplique.\n"
        "- Ne change rien d'autre dans la commande sans son accord explicite."
        + (("\n- " + ("Ce client veut être prévenu de tout changement : après le point principal, dis-lui en une phrase que le "
                     "stock est insuffisant ce matin aussi sur " if row.get("preference") == "refuses" else
                     "Si le client demande pour le reste de la commande : stock insuffisant ce matin aussi sur ")
            + ", ".join(f"{n} ({'un arrivage est prévu' if inc else 'aucun arrivage prévu'})" for n, inc in tight)
            + ". Dis-le tel quel, sans en rajouter (ni heure, ni qui vérifie) ; ne propose rien pour ces lignes.")
           if tight else "")
        + ("\n- La commande attend encore la validation de l'ADV : ne dis pas qu'elle est partie ni qu'elle est annulée, "
           "et ne parle pas d'encours ni de plafond (ce n'est pas l'objet de l'appel)." if pending else "")
    )
