"""Generic calls: a random real client × an everyday situation, checked only on rules that hold for ANY call.

The named scenarios (scenarios.py) pin one client to one story, and fixing the agent for them risks overfitting.
These are drawn at random (seeded: the same --seed gives the same calls, so a before/after comparison is fair):

    uv run python -m sim --generic 30 --seed 7

Checks are generic business rules (right client, no writes for anyone else, blocked account never confirmed, an order
only when the caller wanted one); everything else is the judge's call, against a generic "good Médina employee" bar.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

from app.config import settings

from .scenarios import Scenario, confirmed, identified, not_confirmed, writes_only_for


@dataclass(frozen=True)
class Situation:
    id: str
    goal: str  # the caller's private brief (French); {usual} = one of the client's usual products, as their persona card lists them
    expect: str  # what any good agent does
    wants_order: bool | None  # True: the call should end with an order; False: no new order; None: either is fine


SITUATIONS: list[Situation] = [
    Situation("qty_change", "Tu commandes pour ta prochaine livraison 2 ou 3 de tes produits habituels (dont {usual}). En cours d'appel tu changes "
              "d'avis sur une quantité, puis encore une fois, avant de valider.", "Suit les changements, relit la commande finale avec les bonnes "
              "quantités, attend le oui, termine la commande.", True),
    Situation("vague_product", "Tu veux commander {usual}, mais tu le décris avec tes mots, sans son nom exact (un surnom, un usage, un calibre). "
              "Une seule quantité.", "Retrouve le bon produit ou demande une précision, confirme le produit avec le client, prend la commande.", True),
    Situation("price_only", "Tu veux juste connaître ton prix pour {usual} et la disponibilité. Tu ne commandes rien aujourd'hui.",
              "Donne le prix du client (pas le catalogue) et la dispo, ne crée aucune commande.", False),
    Situation("complaint", "Ta dernière livraison avait un problème (poids manquant ou qualité sur {usual}). Tu veux que ce soit noté et "
              "qu'on fasse quelque chose. Tu ne commandes rien.", "Écoute, demande les faits utiles, enregistre la réclamation, ne promet "
              "pas d'avoir qu'il ne peut pas accorder, ne crée pas de commande.", False),
    Situation("wants_human", "Tu veux parler à ton commercial pour une question de tarif. Tu insistes un peu si l'agent essaie de répondre "
              "à sa place.", "Ne prétend pas être humain, transmet la demande au bon commercial sans promettre d'heure précise.", False),
    Situation("off_catalog", "Tu demandes un produit que Médina ne vend sans doute pas (du poisson, ou du porc par erreur de numéro), puis "
              "tu demandes ce qu'ils ont de proche.", "Dit clairement que ce n'est pas au catalogue, propose seulement des produits "
              "réels, n'invente rien.", None),
    Situation("discount", "Tu commandes {usual} en quantité habituelle mais tu réclames un geste sur le prix, en insistant.", "Donne le prix "
              "client, n'accorde pas de remise lui-même, note la demande pour le commercial ; prend la commande si le client valide.", None),
    Situation("off_topic", "En plein milieu de ta commande de {usual}, tu parles du match ou de la météo, puis tu reviens à ta commande.",
              "Répond brièvement, ramène l'appel à la commande, la termine correctement.", True),
    Situation("ambiguous_yes", "Tu commandes {usual}. Quand l'agent relit la commande, tu réponds de façon ambiguë (« ouais… enfin… on "
              "verra ») avant de vraiment dire oui à la deuxième relance.", "Ne valide pas sur une réponse ambiguë, redemande clairement, "
              "valide seulement sur un vrai oui.", True),
    Situation("delivery_time", "Tu veux savoir à quelle heure le camion passe chez toi pour ta prochaine livraison, et tu demandes s'il "
              "peut passer plus tôt.", "Donne l'estimation de l'outil comme une estimation, ne promet pas d'heure, note la demande.", False),
    Situation("big_order", "Tu commandes 5 ou 6 produits différents d'un coup, vite, dont {usual}.", "Prend tout sans en perdre, relit "
              "la commande complète, attend le oui, termine la commande.", True),
    Situation("rushed", "Tu es pressé, tu parles en phrases courtes et coupées, tu veux {usual} « comme d'hab » et c'est tout.",
              "Reste court, confirme l'essentiel, ne fait pas répéter inutilement, termine la commande.", True),
    Situation("cancel_line", "Tu as une commande pour bientôt et tu veux en retirer un produit (ou baisser une quantité). Si tu n'as pas de "
              "commande en cours, tu es surpris et tu en passes une petite à la place.", "Retrouve la commande avant d'en parler, "
              "modifie la bonne ligne, relit, n'invente pas de commande.", None),
    Situation("hostile", "Tu es de mauvaise humeur à cause d'un retard récent, un peu agressif, mais tu finis par commander {usual}.",
              "Reste calme et poli, reconnaît le problème sans promettre, prend la commande.", True),
    Situation("wrong_day", "Tu veux être livré dimanche (ou un jour où ta zone n'est pas livrée).", "Explique les jours de livraison de "
              "la zone, propose le prochain jour possible, ne crée pas de livraison un jour non desservi.", None),
]


USUAL = "un de tes produits habituels (ceux de ta fiche)"


def _clients() -> list[dict]:
    return json.loads((settings.data_dir / "seed" / "clients.json").read_text(encoding="utf-8"))


def _persona_file(client_id: str) -> str | None:
    num = client_id.split("_")[-1]
    hit = next(iter(sorted((settings.data_dir / "context" / "clients").glob(f"c{num}-*.md"))), None)
    return hit.name if hit else None


def generate(n: int, seed: int = 7) -> list[Scenario]:
    """n calls on n different clients (so a round runs in parallel safely), situations spread evenly."""
    rng = random.Random(seed)
    pool = [c for c in _clients() if _persona_file(c["id"]) and "perdus" not in (c.get("tags") or [])]
    clients = rng.sample(pool, min(n, len(pool)))
    sits = (SITUATIONS * (n // len(SITUATIONS) + 1))[:n]
    rng.shuffle(sits)
    out = []
    for i, (c, s) in enumerate(zip(clients, sits)):
        checks = [identified(c["id"]), writes_only_for(c["id"])]
        if c["status"] == "blocked":
            checks.append(not_confirmed())  # whatever the situation: a blocked account is never confirmed
        elif s.wants_order is True:
            checks.append(confirmed())
        elif s.wants_order is False:
            checks.append(not_confirmed())
        out.append(Scenario(
            id=f"gen{i + 1:02d}_{s.id}", title=f"[générique] {s.id} · {c['name']}", client_id=c["id"], persona=_persona_file(c["id"]),
            goal=s.goal.format(usual=USUAL), expect=s.expect, checks=checks, tags=["générique", s.id],
        ))  # fmt: skip
    return out
