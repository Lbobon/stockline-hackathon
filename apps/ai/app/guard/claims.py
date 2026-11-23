"""Does a sentence state something the ERP can contradict? A price, a quantity, a date, a stock level, a discount.

« Bonjour, ici l'assistant vocal. » or « Je regarde. » state nothing checkable: they are spoken at once, no Jev call.
Only the sentences with a claim go to Jev, so the guard costs nothing on small talk.
"""

import re

from .numbers import UNITS_WORDS, numbers_in

_DAYS = r"lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|demain|après-demain|aujourd'hui|ce soir|ce matin|cet après-midi|la semaine prochaine"
_MONTHS = r"janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre"
KINDS: dict[str, re.Pattern] = {
    "remise": re.compile(r"remise|réduction|ristourne|promo|geste commercial|gratuit|offert|rabais|\d+ ?%|pour ?cent|prix spécial|prix d'ami", re.I),
    "prix": re.compile(r"€|\beuros?\b|centimes?|\bprix\b|tarif|\btotal\b|\bHT\b|TTC|le kilo\b|du kilo\b|la pièce\b", re.I),
    "stock": re.compile(r"en stock|disponibles?|\bdispo\b|rupture|il (?:nous |vous |en )?reste|arrivage|réappro|on (?:en )?a (?:encore|plus|assez)|on n'en a plus|manque", re.I),
    "date": re.compile(rf"\b(?:{_DAYS})\b|\b(?:{_MONTHS})\b|\b\d{{1,2}} ?h\b|\b(?:huit|neuf|dix|onze|midi|quatorze|quinze|seize|dix-sept|dix-huit) heures\b|\blivr", re.I),
    "commande": re.compile(r"\b(?:votre|la|sa|ta) commande (?:est|a été|sera)|c'est (?:validé|confirmé|enregistré)|commande (?:validée|confirmée|enregistrée)", re.I),
}
_QTY = re.compile(r"\b(?:" + "|".join(sorted(UNITS_WORDS, key=len, reverse=True)) + r")s?\b", re.I)


def claim_kinds(sentence: str) -> list[str]:
    """What the sentence affirms that the ERP knows: prix, remise, stock, date, commande, quantité. [] = nothing to check."""
    kinds = [k for k, rx in KINDS.items() if rx.search(sentence)]
    if numbers_in(sentence) and _QTY.search(sentence):
        kinds.append("quantité")
    elif numbers_in(sentence) and not kinds:
        kinds.append("quantité")  # a bare number (« il en faut quarante ») still asks for a source
    return kinds


KIND_LABEL = {"prix": "prix", "remise": "remise", "stock": "stock", "date": "date", "commande": "commande", "quantité": "quantité"}
