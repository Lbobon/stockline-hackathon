# Boucherie Sidi Brahim (C0005, `cli_0005`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0005"`).

| | |
|---|---|
| Type | Commerce de détail (Boucheries halal) — 118 boulevard Henri-Barnier, 13016 Marseille |
| Zone | Marseille quartiers Nord (13e-16e) — livré tue, thu, sat |
| Statut | **blocked** — client régulier |
| Commercial | Samia Haddad |
| Client depuis | 20/03/2017 (9 ans) |
| Paiement | 15 jours — mauvais payeur, compte à risque |
| Encours | 48 125,03 € TTC (plafond 23 000 €) — **13 facture échues : 48 125,03 €** |
| Tempérament | désorganisé, bavard — qualité ★☆☆, prix ★★☆, risque de départ faible |

## Qui ils sont

Boucherie de l'Estaque, bon chiffre jusqu'en 2025, puis travaux de la rue et perte de clientèle. Deux chèques impayés et 4 200 € d'échu.

## Les personnes

- **Abdelkader Saïdani** — Gérant · 06 47 29 81 05
  - Comment il/elle parle : Sympathique, promet toujours, change de numéro de temps en temps.
  - Tics : « Samia, je te fais le virement lundi, wallah. » · « Livre-moi juste deux agneaux pour le week-end. »

## Ce qu'ils achètent et comment

Commande trois fois par semaine, en général la veille entre 15h et 21h, surtout par téléphone et WhatsApp. Produits habituels : carcasse d'agneau de France (16-20 kg), carcasse d'agneau d'Espagne (12-14 kg), minerai de bœuf à hacher (VBF), poulet entier PAC halal (1,3 kg), foie d'agneau, sauce samouraï (seau 4,5 kg), cuisse de poulet, rumsteck de bœuf.

Sur les 90 derniers jours (23 commandes livrées) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Carcasse d'agneau de France (16-20 kg) (`prd_0001`) | 883,88 kg | 13 | 9,84 € |
| Carcasse d'agneau d'Espagne (12-14 kg) (`prd_0002`) | 896,84 kg | 16 | 8,45 € |
| Minerai de bœuf à hacher (VBF) (`prd_0018`) | 500,80 kg | 11 | 8,27 € |
| Entrecôte de bœuf sous vide (`prd_0010`) | 111,04 kg | 10 | 24,61 € |
| Émincé de poulet mariné chawarma (`prd_0035`) | 228,90 kg | 9 | 9,28 € |
| Poulet entier PAC halal (1,3 kg) (`prd_0023`) | 479,42 kg | 10 | 3,97 € |
| Cuisse de poulet (`prd_0025`) | 442,79 kg | 10 | 3,76 € |
| Rumsteck de bœuf (`prd_0012`) | 82,55 kg | 7 | 18,58 € |

Fiche CRM : _Commandait trois fois par semaine des carcasses espagnoles, du minerai et de la volaille._

## Ce qui les agace

- Les relances au magasin devant ses clients

## Historique marquant

- **11/11/2025** · demande spéciale · Fêtes 2025 : réservation de volumes
- **09/01/2026** · prix · Tarifs 2026
- **14/01/2026** · paiement · Relance : FAC-2025-006611 échue
- **27/02/2026** · paiement · Relance : FAC-2026-000566 échue
- **21/04/2026** · paiement · Relance : FAC-2026-001507 échue
- **09/06/2026** · paiement · Relance : FAC-2026-002440 échue
- **25/07/2026** · paiement · Relance : FAC-2026-003375 échue
- **12/09/2026** · paiement · Relance : FAC-2026-004321 échue
- **30/10/2026** · paiement · Blocage du compte
- **30/10/2026** · paiement · Compte bloqué pour impayés
- **02/11/2026** · échéancier · Proposition d'échéancier
- **08/11/2026** · paiement · Relance : FAC-2026-005390 échue

Historique complet : 57 événements (22 paiement, 10 visite, 9 appel, 5 qualité, 3 demande spéciale, 3 prix, 1 ouverture, 1 avoir, 1 livraison, 1 compliment, 1 échéancier).

## Dossiers ouverts aujourd'hui

- **12/09/2026 — Relance : FAC-2026-004321 échue** (`inv_019144`)
  Facture FAC-2026-004321 (5 315,89 € TTC) échue le 01/09/2026. Relance par mail puis téléphone. Abdelkader : « J'ai eu un trou de trésorerie, je régularise. »
- **30/10/2026 — Compte bloqué pour impayés** (`inv_019008`)
  13 factures échues non réglées pour 48 125,03 € TTC. Livraisons suspendues jusqu'à règlement d'au moins 50 % de l'échu et un échéancier validé.
- **02/11/2026 — Proposition d'échéancier**
  Abdelkader propose de payer en 3 fois et demande la reprise des livraisons. En attente de validation par la direction.
- **08/11/2026 — Relance : FAC-2026-005390 échue** (`inv_020213`)
  Facture FAC-2026-005390 (4 650,39 € TTC) échue le 27/10/2026. Relance par mail puis téléphone. Abdelkader : « Le comptable s'en occupe. »

## Comment leur parler

- Courtois mais ferme : aucune livraison sans accord de Martine.
- Ne rien promettre.

## Prix & groupe

Groupes : **Boucheries halal** (`boucheries`), **À surveiller** (`a-surveiller`).
Prix négociés (`clientPrices`, toujours prioritaires) : rumsteck de bœuf 17,85 € (liste 18,90) ; sauce samouraï (seau 4,5 kg) 14,73 € (liste 15,60).

Règles de prix qui peuvent s'appliquer :
- `prl_0001` Boucheries : −4 % sur l'agneau en carcasse
- `prl_0004` Volume : broche 10 kg −5 % dès 10 broches
- `prl_0005` Volume : poulet PAC −4 % dès 100 kg

Dernières notes CRM :

- 27/06/2026, Samia (commerciale) : Compte bloqué : 4 200 € d'échu dont deux chèques rejetés. Déblocage seulement avec échéancier signé par Rachid.
- 30/10/2026, Martine (comptabilité) : Compte BLOQUÉ pour impayés. 13 factures échues non réglées, la plus ancienne FAC-2026-004185 du 10/08/2026. Encours 48 125,03 € TTC pour un plafond de 23 000,00 €. Reprise des livraisons seulement après un règlement d'au moins 50 % de l'échu et un échéancier validé par la direction.
- 23/11/2026, Samia (commerciale) : Abdelkader a rappelé pour reprendre les livraisons et promet un virement. Ne rien livrer sans l'accord de la comptabilité.
