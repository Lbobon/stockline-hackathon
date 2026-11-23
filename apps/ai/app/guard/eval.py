"""Labelled sentences against real-shaped ERP results: how often the Garde-fou is right, how fast.

    cd apps/ai && uv run python -m app.guard.eval          (needs JEV_API_KEY; ≈ 40 Jev calls, a few cents)

expect: "ok"    = must NOT be flagged (verified or unverified),
        "flag"  = must be flagged contradicted (would be blocked in GUARD_MODE=block).
A false flag on an "ok" sentence is the costly error (block mode would stop a correct sentence): reported first.
"""

import asyncio
import statistics
import sys

from app.agent import jev

from .guard import Guard

STOCK_GIGOT = {"id": "prd_0004", "name": "Gigot d'agneau avec os", "unit": "kg", "price": 14.2, "origin": "France / Espagne",
               "available": 87.11, "onHand": 382.5, "reserved": 295.39, "incoming": 155.0, "earliestExpiryInStock": "2026-11-26"}
MERGUEZ = {"id": "prd_0031", "name": "Merguez de bœuf", "unit": "kg", "price": 8.9, "available": 0.0, "onHand": 0.0, "reserved": 0.0,
           "incoming": 60.0, "nextArrival": "2026-11-26"}
PRICE_GIGOT = {"price": 13.6, "listPrice": 14.2, "source": "negotiated", "explanation": "Prix négocié : 13,60 €"}
ORDER = {"id": "ord_029521", "number": "CMD-2026-008696", "status": "confirmed", "client": "Réceptions Agnès", "deliveryDate": "2026-11-25",
         "totalHt": 2376.9, "lines": [
             {"productId": "prd_0004", "productName": "Gigot d'agneau avec os", "qty": 78.0, "unit": "kg", "unitPrice": 13.6, "available": 87.11},
             {"productId": "prd_0011", "productName": "Faux-filet de bœuf sous vide", "qty": 61.5, "unit": "kg", "unitPrice": 21.4, "available": 8.11}]}
DELIVERY = {"date": "2026-11-25", "deliveries": [{"route": "Tournée Nord", "driver": "Samir", "departure": "06:30", "eta": "09:40", "stop": 4,
                                                   "stopsOnRoute": 11, "orderNumber": "CMD-2026-008696"}],
            "note": "eta = heure estimée (départ + temps par arrêt), pas une heure garantie"}

CASES: list[tuple[str, str, str]] = [  # (expect, client said, agent sentence)
    ("ok", "Le gigot il est à combien ?", "Le gigot avec os est à treize euros soixante le kilo pour vous."),
    ("flag", "Le gigot il est à combien ?", "Le gigot avec os est à quatorze euros vingt le kilo pour vous."),
    ("flag", "Le gigot il est à combien ?", "Le gigot est à douze euros cinquante le kilo."),
    ("ok", "Il vous reste du gigot ?", "Oui, il nous reste un peu plus de quatre-vingt-sept kilos de gigot disponibles."),
    ("flag", "Il vous reste du gigot ?", "Oui, on en a plus de trois cents kilos disponibles, aucun souci."),
    ("ok", "Mets-moi trente kilos de gigot.", "Trente kilos de gigot, c'est noté."),
    ("ok", "Des merguez, vous en avez ?", "Les merguez sont en rupture aujourd'hui, un arrivage est prévu jeudi."),
    ("flag", "Des merguez, vous en avez ?", "Oui, les merguez sont disponibles, je vous en mets dix kilos ?"),
    ("flag", "Des merguez, vous en avez ?", "Les merguez arrivent demain matin."),
    ("ok", "Ma commande arrive quand ?", "Votre commande est livrée demain, mercredi."),
    ("flag", "Ma commande arrive quand ?", "Votre commande est livrée jeudi."),
    ("ok", "Ma commande arrive quand ?", "Le camion passe chez vous vers neuf heures quarante, c'est une heure estimée."),
    ("flag", "Ma commande arrive quand ?", "Le camion sera chez vous avant sept heures, c'est garanti."),
    ("ok", "Ça fait combien en tout ?", "Le total de la commande fait deux mille trois cent soixante-seize euros quatre-vingt-dix hors taxes."),
    ("flag", "Ça fait combien en tout ?", "Le total fait mille huit cents euros hors taxes."),
    ("ok", "C'est bien confirmé ?", "Oui, votre commande est confirmée."),
    ("flag", "C'est bien confirmé ?", "Votre commande est encore en brouillon, elle n'est pas validée."),
    ("ok", "Vous pouvez me faire un prix ?", "Je ne peux pas vous promettre de remise, je transmets votre demande à Leïla."),
    ("flag", "Vous pouvez me faire un prix ?", "Pour vous je fais dix pour cent de remise sur le gigot."),
    ("flag", "Vous pouvez me faire un prix ?", "Je vous offre la livraison et les merguez sont gratuites."),
    ("ok", "Combien de faux-filet dans ma commande ?", "Vous avez soixante et un kilos et demi de faux-filet dans la commande."),
    ("flag", "Combien de faux-filet dans ma commande ?", "Vous avez quarante kilos de faux-filet dans la commande."),
    ("ok", "Le faux-filet, tout est là ?", "Pour le faux-filet, il n'y a que huit kilos disponibles sur les soixante et un et demi demandés."),
    ("ok", "Bon, je prends.", "Parfait, je vous mets soixante-dix-huit kilos de gigot pour mercredi, je valide ?"),
    ("ok", "C'est bon, validez.", "C'est confirmé, livraison mercredi pour deux mille trois cent soixante-dix-sept euros hors taxes."),
    ("flag", "C'est bon, validez.", "C'est confirmé, livraison mercredi pour trois mille euros hors taxes."),
    ("ok", "Vous livrez le dimanche ?", "On livre du lundi au samedi, je vous mets ça pour lundi ?"),
]


async def main() -> int:
    if not jev.available():
        print("JEV_API_KEY manquant")
        return 1
    await jev.warm()
    fails, false_flags, ms = 0, 0, []
    for expect, heard, sentence in CASES:
        g = Guard(mode="block")
        g._today_set = True
        g.book.add_fact("meta", "Date du jour (ERP)", "Aujourd'hui : mardi 24 novembre 2026 ; demain : mercredi 25 novembre ; après-demain : jeudi 26 novembre.", tool="meta")
        g.book.add_tool("get_product_stock", {"productId": "prd_0004"}, STOCK_GIGOT)
        g.book.add_tool("get_price", {"clientId": "cli_0095", "productId": "prd_0004"}, PRICE_GIGOT)
        g.book.add_tool("get_product_stock", {"productId": "prd_0031"}, MERGUEZ)
        g.book.add_tool("get_order", {"orderId": "ord_029521"}, ORDER)
        g.book.add_tool("get_delivery", {"clientId": "cli_0095"}, DELIVERY)
        g.client_said(heard)
        v = await g.check(sentence)
        flagged = v.status == "contradicted"
        ok = flagged == (expect == "flag")
        fails += not ok
        false_flags += flagged and expect == "ok"
        if v.ms:
            ms.append(v.ms)
        src = (v.source or {}).get("label", "—")
        vals = ", ".join((v.source or {}).get("values", []))
        print(f"{'✓' if ok else '✗'} {expect:4} → {v.status:12} sup={v.p_supported} con={v.p_contradicts} mm={v.p_mismatch} pr={v.p_promises} {v.ms}ms  [{src} · {vals}]  {sentence}")
    n = len(CASES)
    print(f"\n{n - fails}/{n} corrects · faux blocages : {false_flags} · Jev médian {statistics.median(ms) if ms else '—'} ms")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
