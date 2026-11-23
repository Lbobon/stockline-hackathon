# Le Riad de Saint-Barnabé (C0077, `cli_0077`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0077"`).

| | |
|---|---|
| Type | Restaurant (Restaurants orientaux, libanais et marocains) — 98 rue Victor-Hugo, 13500 Martigues |
| Zone | Étang de Berre, Aix et Vitrolles — livré mon, tue, thu |
| Statut | **active** — nouveau client |
| Commercial | Julien Ferrara |
| Client depuis | 07/02/2024 (2 ans) |
| Paiement | 30 jours — paie en retard (3 à 6 semaines), mais finit par payer |
| Encours | 15 211,37 € TTC (plafond 20 000 €) — **3 facture échues : 6 826,58 €** |
| Tempérament | exigeant — qualité ★★★, prix ★☆☆, risque de départ faible |

## Qui ils sont

Restaurant marocain de Martigues, 60 couverts : couscous du vendredi, tajines, méchoui sur commande. Rachid Khelifi choisit ses souris d'agneau une par une.

## Les personnes

- **Rachid Khelifi** — Chef-propriétaire · 06 99 85 51 40
  - Comment il/elle parle : Ton sec, va droit au but, vérifie chaque quantité.
  - Tics : « Vous me relisez la commande ? » · « Je veux la même qualité que la dernière fois, pas moins. »
- **Awa Roche** — Gérante · 06 39 67 96 70
  - Comment il/elle parle : Parle vite et précis, vouvoie, ne supporte pas l'approximation.
  - Tics : « C'est noté où, ça ? » · « Je veux la même qualité que la dernière fois, pas moins. »

## Ce qu'ils achètent et comment

Commande trois fois par semaine, en général la veille entre 16h et 23h, surtout par téléphone et WhatsApp. Produits habituels : épaule d'agneau, collier d'agneau (pour tajine), souris d'agneau, poulet entier PAC halal (1,3 kg), merguez bœuf-agneau maison, semoule de blé dur moyenne (sac 10 kg), kefta bœuf persil-oignon (boulettes), émincé de poulet mariné chawarma.

Sur les 90 derniers jours (37 commandes livrées) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Épaule d'agneau (`prd_0005`) | 431,54 kg | 19 | 11,03 € |
| Collier d'agneau (pour tajine) (`prd_0008`) | 355,89 kg | 15 | 9,20 € |
| Poulet entier PAC halal (1,3 kg) (`prd_0023`) | 729,82 kg | 23 | 3,95 € |
| Kefta bœuf persil-oignon (boulettes) (`prd_0032`) | 232,31 kg | 14 | 11,60 € |
| Merguez bœuf-agneau maison (`prd_0030`) | 254,68 kg | 22 | 10,19 € |
| Émincé de poulet mariné chawarma (`prd_0035`) | 260,52 kg | 13 | 9,05 € |
| Escalope de dinde (`prd_0028`) | 178,68 kg | 18 | 9,40 € |
| Souris d'agneau (`prd_0007`) | 106,85 kg | 14 | 13,71 € |

Fiche CRM : _Merguez et collier pour le couscous du vendredi. Souris d'agneau régulières pour le tajine. Livré lun. mar. jeu. Refuse les substitutions : appeler avant toute modification._

## Ce qui les agace

- Souris de calibres différents
- Collier trop gras
- Livraison après 10h le vendredi

## Historique marquant

- **06/01/2026** · réclamation · Rupture : épaule d'agneau non livré
- **20/01/2026** · prix · Tarifs 2026
- **28/02/2026** · paiement · Relance : FAC-2026-000318 échue
- **27/03/2026** · carte · Nouvelle carte de printemps
- **27/04/2026** · paiement · Relance : FAC-2026-001404 échue
- **09/06/2026** · livraison · Livraison laissée devant la porte
- **16/06/2026** · paiement · Relance : FAC-2026-002333 échue
- **02/08/2026** · paiement · Relance : FAC-2026-003272 échue
- **01/09/2026** · carte · Carte d'automne
- **28/09/2026** · paiement · Relance : FAC-2026-004354 échue
- **15/11/2026** · paiement · Relance : FAC-2026-005296 échue
- **19/11/2026** · demande spéciale · Fêtes 2026 : réservation de volumes

Historique complet : 45 événements (18 paiement, 6 appel, 4 visite, 3 réclamation, 3 demande spéciale, 2 qualité, 2 prix, 2 compliment, 2 carte, 1 ouverture, 1 contact, 1 livraison).

## Dossiers ouverts aujourd'hui

- **15/11/2026 — Relance : FAC-2026-005296 échue** (`inv_020119`)
  Facture FAC-2026-005296 (2 047,47 € TTC) échue le 04/11/2026. Relance par téléphone. Rachid : « Le comptable s'en occupe. »
- **19/11/2026 — Fêtes 2026 : réservation de volumes**
  Rachid veut réserver pour les fêtes : merguez bœuf-agneau maison et émincé de poulet mariné chawarma en volume pour les 23-24 et 30-31/12. Rachid : « Vous me relisez la commande ? »

## Comment leur parler

- Confirmer les méchouis trois jours avant.
- Parler recettes et découpes, il aime.

## Prix & groupe

Groupes : **Restaurants orientaux** (`restaurants`), **À surveiller** (`a-surveiller`).
Prix négociés (`clientPrices`, toujours prioritaires) : souris d'agneau 13,89 € (liste 14,60) ; épaule d'agneau 11,22 € (liste 11,80).

Règles de prix qui peuvent s'appliquer :
- `prl_0004` Volume : broche 10 kg −5 % dès 10 broches
- `prl_0005` Volume : poulet PAC −4 % dès 100 kg

Dernières notes CRM :

- 28/10/2026, Martine (comptabilité) : Paie systématiquement en retard (3 à 5 semaines). Relancer par téléphone, Rachid ne lit pas les mails de relance.
