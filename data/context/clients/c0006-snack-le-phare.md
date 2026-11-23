# Snack Le Phare (C0006, `cli_0006`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0006"`).

| | |
|---|---|
| Type | Restaurant (Kebabs, snacks et tacos halal) — 3 quai Brescon, 13500 Martigues |
| Zone | Étang de Berre, Aix et Vitrolles — livré tue, thu |
| Statut | **blocked** — client régulier |
| Commercial | Youssef Amrani |
| Client depuis | 12/04/2021 (5 ans) |
| Paiement | 15 jours — mauvais payeur, compte à risque |
| Encours | 11 344,53 € TTC (plafond 7 000 €) — **18 facture échues : 10 438,57 €** |
| Tempérament | désorganisé, impulsif — qualité ★★★, prix ★★☆, risque de départ faible |

## Qui ils sont

Snack de bord de mer à Martigues, très bon l'été, presque vide l'hiver. Paie de plus en plus tard depuis septembre ; 2 300 € d'échu.

## Les personnes

- **Nassim Belaïd** — Gérant · 07 61 30 48 92
  - Comment il/elle parle : Jeune patron, débordé, s'excuse beaucoup.
  - Tics : « Frère, la saison a été mauvaise, je règle tout en décembre. »

## Ce qu'ils achètent et comment

Commande deux fois par semaine, en général la veille entre 20h et 2h, surtout par WhatsApp et téléphone. Produits habituels : broche kebab veau-dinde 10 kg, frites surgelées 9 mm (carton 4 × 2,5 kg), pains kebab ronds précuits surgelés (carton 100), sauce blanche kebab (seau 4,5 kg), haut de cuisse de poulet désossé, tortillas 30 cm pour tacos (carton 144), broche kebab veau-dinde 20 kg.

Sur les 90 derniers jours (17 commandes livrées) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Broche kebab veau-dinde 10 kg (`prd_0043`) | 65,00 pièce | 12 | 61,28 € |
| Broche kebab veau-dinde 20 kg (`prd_0044`) | 21,00 pièce | 6 | 118,18 € |
| Haut de cuisse de poulet désossé (`prd_0026`) | 123,89 kg | 7 | 6,37 € |
| Pains kebab ronds précuits surgelés (carton 100) (`prd_0050`) | 28,00 colis | 9 | 26,29 € |
| Tortillas 30 cm pour tacos (carton 144) (`prd_0051`) | 19,00 colis | 11 | 34,92 € |
| Frites surgelées 9 mm (carton 4 × 2,5 kg) (`prd_0049`) | 44,00 colis | 7 | 13,63 € |
| Sauce blanche kebab (seau 4,5 kg) (`prd_0052`) | 23,00 pièce | 6 | 14,83 € |
| Leben (lait fermenté, bouteille 1 L) (`prd_0063`) | 8,00 pièce | 1 | 1,65 € |

Fiche CRM : _Broches de 10 kg, frites, pains, sauces ; beaucoup plus l'été._

## Ce qui les agace

- Les appels de relance pendant le service

## Historique marquant

- **24/11/2025** · paiement · Relance : FAC-2025-005717 échue
- **09/01/2026** · prix · Tarifs 2026
- **14/01/2026** · paiement · Relance : FAC-2025-006653 échue
- **04/03/2026** · paiement · Relance : FAC-2026-000661 échue
- **18/04/2026** · paiement · Relance : FAC-2026-001547 échue
- **20/06/2026** · paiement · Relance : FAC-2026-002654 échue
- **04/08/2026** · paiement · Relance : FAC-2026-003560 échue
- **08/09/2026** · carte · Carte d'automne
- **28/09/2026** · paiement · Relance : FAC-2026-004670 échue
- **16/11/2026** · paiement · Compte bloqué pour impayés
- **19/11/2026** · paiement · Relance : FAC-2026-005618 échue
- **20/11/2026** · échéancier · Proposition d'échéancier

Historique complet : 52 événements (22 paiement, 7 visite, 6 appel, 5 qualité, 2 demande spéciale, 2 prix, 2 carte, 1 ouverture, 1 compliment, 1 retour, 1 livraison, 1 avoir, 1 échéancier).

## Dossiers ouverts aujourd'hui

- **28/09/2026 — Relance : FAC-2026-004670 échue** (`inv_019493`)
  Facture FAC-2026-004670 (462,54 € TTC) échue le 18/09/2026. Relance par mail puis téléphone. Nassim : « Le comptable s'en occupe. »
- **16/11/2026 — Compte bloqué pour impayés** (`inv_019188`)
  18 factures échues non réglées pour 10 438,57 € TTC. Livraisons suspendues jusqu'à règlement d'au moins 50 % de l'échu et un échéancier validé.
- **19/11/2026 — Relance : FAC-2026-005618 échue** (`inv_020441`)
  Facture FAC-2026-005618 (790,90 € TTC) échue le 06/11/2026. Relance par mail puis téléphone. Nassim : « C'est parti cette semaine. »
- **20/11/2026 — Proposition d'échéancier**
  Nassim propose de payer en 4 fois et demande la reprise des livraisons. En attente de validation par la direction.

## Comment leur parler

- Compte bloqué : renvoyer vers Martine, ne pas accepter de commande « juste une broche ».

## Prix & groupe

Groupes : **Snacks et kebabs** (`snacking`), **À surveiller** (`a-surveiller`).
Pas de prix négocié.

Règles de prix qui peuvent s'appliquer :
- `prl_0002` Snacks : −3 % sur les surgelés snacking
- `prl_0004` Volume : broche 10 kg −5 % dès 10 broches
- `prl_0005` Volume : poulet PAC −4 % dès 100 kg

Dernières notes CRM :

- 08/05/2026, Youssef (commercial) : Bloqué le 16/11 : 2 300 € d'échu à plus de 45 jours. Martine propose un échéancier en trois fois.
- 16/11/2026, Martine (comptabilité) : Compte BLOQUÉ pour impayés. 18 factures échues non réglées, la plus ancienne FAC-2026-004365 du 18/08/2026. Encours 11 344,53 € TTC pour un plafond de 7 000,00 €. Reprise des livraisons seulement après un règlement d'au moins 50 % de l'échu et un échéancier validé par la direction.
- 20/11/2026, Youssef (commercial) : Nassim a rappelé pour reprendre les livraisons et promet un virement. Ne rien livrer sans l'accord de la comptabilité.
