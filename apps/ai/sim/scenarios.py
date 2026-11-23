"""
The simulated calls. Each scenario = one Médina client (its persona file in data/context/clients/ is what the caller
model plays), a private goal for the caller, what a good agent does (for the judge), and checks run on the tool trace.

They follow the call types METIER.md lists ("Où la voix change la donne"), on today's real data (Tuesday 24/11/2026,
05:30): 20 kg spit out of stock, veal escalope out of stock, blocked accounts, clients near their credit limit…

Add a scenario: copy one, change the client and the goal, pick checks from the helpers below.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from .trace import Trace

Check = tuple[str, Callable[[Trace], bool]]


@dataclass
class Scenario:
    id: str
    title: str
    client_id: str  # the real caller (cli_…)
    persona: str  # file in data/context/clients/
    goal: str  # private brief for the caller model (French, like the persona)
    expect: str  # what a good agent does, for the judge
    checks: list[Check] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# ── check helpers ────────────────────────────────────────────────────────────────────────────────────────────


def called(tool: str) -> Check:
    return (f"appelle {tool}", lambda t: t.called(tool))


def not_called(tool: str) -> Check:
    return (f"n'appelle pas {tool}", lambda t: not t.called(tool))


def identified(client_id: str) -> Check:
    return (f"identifie {client_id}", lambda t: client_id in t.client_ids_looked_up())


def writes_only_for(client_id: str) -> Check:
    return (f"n'écrit que pour {client_id}", lambda t: t.write_client_ids() <= {client_id})


def confirmed() -> Check:
    """The order ended confirmed, by confirm_order or by finalize_order (the ERP's confirm-or-draft gates)."""
    ended = lambda t: (t.called("confirm_order") or t.called("finalize_order")) and any(o.get("status") != "draft" for o in t.orders().values())  # noqa: E731
    return ("commande confirmée", ended)


def not_confirmed() -> Check:
    """Nothing got confirmed: no confirm_order, and finalize_order (if called) left every order as a draft."""
    return ("ne confirme pas", lambda t: not t.called("confirm_order") and all(o.get("status") == "draft" for o in t.orders().values()))


def order_qty(product_id: str, lo: float, hi: float) -> Check:
    return (f"{product_id} entre {lo:g} et {hi:g}", lambda t: lo <= t.qty(product_id) <= hi)


def order_lacks(product_id: str) -> Check:
    return (f"pas de {product_id} dans la commande", lambda t: t.qty(product_id) == 0)


def order_qty_on(order_id: str, product_id: str, lo: float, hi: float) -> Check:
    return (f"{order_id} : {product_id} entre {lo:g} et {hi:g}", lambda t: lo <= t.qty(product_id, order_id) <= hi)


def no_delivery_on(date: str) -> Check:
    return (f"aucune commande pour le {date}", lambda t: all(o.get("deliveryDate") != date for o in t.orders(written_only=True).values()))


def no_substitution() -> Check:
    return ("aucune substitution", lambda t: not any(c["args"].get("substituteFor") or any(line.get("substituteFor") for line in c["args"].get("lines") or []) for c in t.calls()))


def event_logged() -> Check:
    return ("événement noté (log_client_event)", lambda t: t.called("log_client_event"))


# ── scenarios ────────────────────────────────────────────────────────────────────────────────────────────────

SCENARIOS: list[Scenario] = [
    Scenario(
        id="habituel",
        title="« Comme d'habitude » d'une cliente bavarde",
        client_id="cli_0088",
        persona="c0088-couscous-brigitte.md",
        goal=(
            "Tu appelles pour ta commande de jeudi. Tu dis « comme d'habitude » sans détailler, tu digresses un peu "
            "(ta journée, ton fils, le marché). Quand l'agent te propose ton habituel, tu acceptes mais tu ajoutes "
            "5 kilos de merguez en plus cette semaine. Tu confirmes quand il te relit la commande."
        ),
        expect=(
            "Identifier Couscous Brigitte, proposer son panier habituel (get_client → usual, pas simplement la dernière "
            "commande), qui contient déjà de la merguez : en ajouter 5 kg de plus que d'habitude, "
            "ramener poliment la conversation au sujet, relire la commande et confirmer après un oui clair, "
            "livraison jeudi 26/11."
        ),
        checks=[identified("cli_0088"), writes_only_for("cli_0088"), confirmed(), order_qty("prd_0030", 5, 1000)],
        tags=["commande", "naturel"],
    ),
    Scenario(
        id="rupture_broche",
        title="Rupture : la broche de 20 kg",
        client_id="cli_0002",
        persona="c0002-istanbul-grill-3-snacks.md",
        goal=(
            "Tu veux 3 broches de 20 kilos pour jeudi. Si l'agent te dit qu'il n'y en a plus, tu râles un peu "
            "(c'est la deuxième fois), puis tu acceptes deux broches de 10 à la place de chaque broche de 20, "
            "comme d'habitude quand ça manque. Tu confirmes après la relecture."
        ),
        expect=(
            "Dire tout de suite que la broche 20 kg (prd_0044) est en rupture (ligne du fournisseur en panne), sans "
            "promettre de date. Proposer la broche 10 kg (prd_0043), 6 pièces, avec le prix (get_price). "
            "Commande pour jeudi 26/11, confirmée après oui. Noter l'agacement si utile."
        ),
        checks=[identified("cli_0002"), writes_only_for("cli_0002"), order_lacks("prd_0044"), order_qty("prd_0043", 6, 6), confirmed()],
        tags=["rupture", "commande"],
    ),
    Scenario(
        id="carcasses_kg",
        title="« Quatre agneaux » = carcasses au kilo",
        client_id="cli_0001",
        persona="c0001-boucherie-el-baraka-noailles.md",
        goal=(
            "Tu veux « quatre agneaux de France » pour demain mercredi. Tu parles en carcasses, jamais en kilos. "
            "Tu demandes d'où ils viennent et si le certificat halal est bon. Tu confirmes après la relecture."
        ),
        expect=(
            "Comprendre que 4 agneaux = 4 carcasses d'agneau de France (prd_0001, vendue au kg, 16-20 kg pièce) "
            "≈ 64-80 kg, le dire au client. Donner l'origine (Sisteron) et rassurer sur le certificat sans inventer. "
            "Commande pour mercredi 25/11, confirmée après oui."
        ),
        checks=[identified("cli_0001"), writes_only_for("cli_0001"), order_qty("prd_0001", 64, 80), confirmed()],
        tags=["unités", "commande"],
    ),
    Scenario(
        id="el_baraka_ambigu",
        title="Lequel des cinq El Baraka ?",
        client_id="cli_0083",
        persona="c0083-boucherie-el-baraka-marseille-13e.md",
        goal=(
            "Tu te présentes seulement comme « El Baraka » avec ton prénom. Tu ne donnes ton quartier (13e) que si "
            "l'agent te le demande. Tu veux 20 kilos de merguez bœuf-agneau pour jeudi. Tu confirmes après la relecture."
        ),
        expect=(
            "Voir qu'il y a cinq Boucherie El Baraka, demander lequel (quartier ou prénom) avant d'agir, retrouver "
            "El Baraka Marseille 13e (cli_0083). 20 kg de merguez bœuf-agneau (prd_0030) pour jeudi 26/11, confirmée."
        ),
        checks=[identified("cli_0083"), writes_only_for("cli_0083"), order_qty("prd_0030", 20, 20), confirmed()],
        tags=["identification", "commande"],
    ),
    Scenario(
        id="client_bloque",
        title="Client bloqué : « juste deux agneaux »",
        client_id="cli_0005",
        persona="c0005-boucherie-sidi-brahim.md",
        goal=(
            "Tu veux « juste deux agneaux » pour jeudi. Si on te parle du compte ou des impayés, tu minimises : "
            "« le virement part lundi, wallah ». Tu insistes une fois, puis tu acceptes ce que l'agent propose."
        ),
        expect=(
            "Voir que le compte est bloqué (status blocked), rester courtois, expliquer sans humilier, ne RIEN "
            "confirmer. Au plus un brouillon avec une note, transmis à la compta/au commercial. Pas de promesse de livraison."
        ),
        checks=[identified("cli_0005"), writes_only_for("cli_0005"), not_confirmed()],
        tags=["règles", "bloqué"],
    ),
    Scenario(
        id="trop_tard",
        title="Commande trop tardive pour ce matin",
        client_id="cli_0084",
        persona="c0084-le-comptoir-libanais-marseille-10e.md",
        goal=(
            "Il est 5 h 30. Tu veux 10 kilos de kefta pour ce midi, livrés ce matin avec la tournée. Si l'agent dit "
            "que c'est trop tard, tu demandes quand c'est possible, et tu acceptes la prochaine livraison. Tu es bavard."
        ),
        expect=(
            "Dire clairement que la commande de 3 h est passée et que la tournée du jour est bouclée (pas de "
            "livraison aujourd'hui). Proposer la prochaine livraison du client (jeudi 26/11). Ne pas créer de commande "
            "livrée le 24/11. Commande pour jeudi si le client accepte."
        ),
        checks=[identified("cli_0084"), writes_only_for("cli_0084"), no_delivery_on("2026-11-24")],
        tags=["règles", "horaires"],
    ),
    Scenario(
        id="modification",
        title="Modifier la commande de demain",
        client_id="cli_0057",
        persona="c0057-chicken-spot-marseille-1er.md",
        goal=(
            "Tu as une commande pour demain mercredi (CMD-2026-008733) avec 17 cartons de frites. Tu veux passer à 20 "
            "cartons. Tu hésites, tu dis 22, puis tu reviens à 20. Tu veux la confirmation de ce qui sera livré."
        ),
        expect=(
            "Retrouver la commande de demain (ord_029558, confirmée), monter les frites (prd_0049) de 17 à 20 cartons "
            "au total (ajout d'une ligne de 3 ou modification de ligne), gérer l'hésitation, relire ce qui sera livré."
        ),
        checks=[identified("cli_0057"), writes_only_for("cli_0057"), order_qty_on("ord_029558", "prd_0049", 20, 20)],
        tags=["modification"],
    ),
    Scenario(
        id="camion",
        title="« Il est où le camion ? »",
        client_id="cli_0004",
        persona="c0004-cuisine-centrale-associative-les-oliviers.md",
        goal=(
            "Tu es Djamel. Tu demandes à quelle heure le camion arrive ce matin et qui le conduit, parce que tu dois "
            "organiser la réception. Tu demandes aussi ce qu'il y a dans la livraison."
        ),
        expect=(
            "Donner l'heure estimée de l'arrêt (tournée Nord, départ 6 h, 1er arrêt ≈ 6 h 42), le chauffeur "
            "(Kamel Djebbar) et le contenu de la commande du jour. Si l'agent n'a pas l'information, il le dit "
            "honnêtement et propose de faire rappeler, sans inventer d'heure."
        ),
        checks=[identified("cli_0004"), not_called("create_draft_order")],
        tags=["info"],
    ),
    Scenario(
        id="reclamation",
        title="Réclamation : poids manquant",
        client_id="cli_0046",
        persona="c0046-boucherie-atlas.md",
        goal=(
            "Tu appelles pour te plaindre : sur la livraison d'hier, la carcasse d'agneau pesait 3 kilos de moins que "
            "la facture et elle était trop grasse. Tu veux un avoir. Tu es énervé mais pas insultant."
        ),
        expect=(
            "Écouter, poser les questions utiles (quel produit, combien, quelle livraison), noter précisément "
            "(log_client_event), ne promettre ni avoir ni montant (l'avoir > 150 € passe par Rachid), rassurer et "
            "faire remonter. Ne pas créer de commande."
        ),
        checks=[identified("cli_0046"), event_logged(), not_called("create_draft_order"), not_confirmed()],
        tags=["réclamation"],
    ),
    Scenario(
        id="prospect",
        title="Le prospect qui demande les prix",
        client_id="cli_0010",
        persona="c0010-restaurant-le-cedre-d-or.md",
        goal=(
            "Tu ouvres bientôt / tu envisages de changer de fournisseur. Tu demandes le prix de la merguez et de "
            "l'escalope de dinde, et les jours de livraison chez toi. Tu ne passes pas commande aujourd'hui."
        ),
        expect=(
            "Accueillir, reconnaître un prospect, donner les prix liste (get_price ou catalogue) et les jours de la "
            "tournée Toulon (mardi, jeudi, samedi), poser les bonnes questions (volumes, produits clés, fournisseur "
            "actuel), proposer un rappel du commercial. Ne rien confirmer."
        ),
        checks=[not_confirmed()],
        tags=["prospect", "info"],
    ),
    Scenario(
        id="geste_volume",
        title="« Vous me faites un geste ? » et « si j'en prends le double ? »",
        client_id="cli_0024",
        persona="c0024-halal-market-marseille-1er.md",
        goal=(
            "Tu veux 40 kilos de merguez bœuf-agneau pour jeudi. Tu demandes le prix, tu trouves ça cher, tu demandes "
            "un geste. Puis tu demandes « et si j'en prends 80 ? ». Tu finis par commander 40 kilos et tu confirmes."
        ),
        expect=(
            "Donner le prix avec get_price, ne JAMAIS accorder de remise (noter la demande : log_client_event "
            "price_negotiation, transmettre au commercial), utiliser get_price avec la quantité pour répondre sur 80 kg "
            "(prix volume s'il existe). Commande de 40 kg pour jeudi, confirmée après oui."
        ),
        checks=[identified("cli_0024"), called("get_price"), event_logged(), order_qty("prd_0030", 40, 40), confirmed()],
        tags=["prix", "règles"],
    ),
    Scenario(
        id="refus_substitution",
        title="Le veau manque, le client refuse les substitutions",
        client_id="cli_0041",
        persona="c0041-centre-social-louise-michel.md",
        goal=(
            "Tu appelles pour vérifier ta commande de demain : tu as bien tes 36,5 kilos d'escalope de veau ? "
            "Si on te propose autre chose, tu refuses : la diététicienne doit valider toute substitution par écrit."
        ),
        expect=(
            "Retrouver la commande (CMD-2026-008686), dire que l'escalope de veau est en rupture (fournisseur arrêté), "
            "ne PAS remplacer sans accord écrit (le client refuse les substitutions), noter et faire remonter au "
            "commercial pour un rappel. Formel."
        ),
        checks=[identified("cli_0041"), no_substitution(), not_called("add_order_line")],
        tags=["rupture", "règles"],
    ),
    Scenario(
        id="plafond_credit",
        title="Grosse commande de nuit au-dessus du plafond",
        client_id="cli_0022",
        persona="c0022-boucherie-de-l-estaque.md",
        goal=(
            "Tu prépares une grosse semaine : tu veux 15 agneaux de France et 60 kilos de merguez bœuf-agneau pour "
            "jeudi. Si l'agent parle de ton encours ou du plafond, tu es surpris et un peu vexé, puis tu demandes ce "
            "qu'il peut faire."
        ),
        expect=(
            "Convertir 15 agneaux ≈ 240-300 kg de carcasses France. Voir l'alerte credit_limit (encours proche du "
            "plafond de 18 000 €), le dire sans vexer, ne pas confirmer au-delà du plafond : brouillon + note, "
            "transmis au commercial/à la compta."
        ),
        checks=[identified("cli_0022"), writes_only_for("cli_0022"), not_confirmed()],
        tags=["règles", "unités"],
    ),
]
