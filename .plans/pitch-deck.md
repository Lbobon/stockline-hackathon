# Pitch deck — Médina Viandes (team 18) · 5 min, 7 slides

Every slide ends on money. The numbers come from real runs on the seed data; see "Sources" at the bottom. Refresh the campaign figures with `python -m sim.radar_stats --runs 3` before the final cut.

---

## 1 · Le grossiste qui ne décroche jamais
**Médina Viandes** — viande halal, MIN des Arnavaux, 95 clients (boucheries, snacks, traiteurs, cantines).
- 91 % des commandes passent par **téléphone et WhatsApp**.
- Les camions partent à **6 h**. Personne ne décroche la nuit. Tout est retapé à la main.

> **Visual:** night photo of the market + « 46 % téléphone · 45 % WhatsApp »

## 2 · Ce qu'on a construit
**Un agent vocal qui travaille dans l'ERP, et qui appelle tout seul.**
1. **La nuit** : il prend les commandes, les confirme lui-même quand les règles le permettent, sinon les laisse à Leïla avec la raison.
2. **Le matin** : il appelle les clients touchés par une rupture, avant le départ du camion.
3. **L'après-midi** : le **Radar de demande** appelle tous les clients qui vont commander demain.

> **Visual:** 3 icons (moon · sunrise · radar)

## 3 · Radar de demande — la commande qui s'écrit toute seule
- L'ERP **prévoit la commande de demain** de chaque client : son rythme d'achat, son panier habituel, ses prix.
- Les **événements de la ville** gonflent la prévision : le match OM–PSG (+40 % de broches pour les snacks), les mariages du week-end (des agneaux entiers pour les traiteurs), une vague de froid (des souris pour les tajines).
- **Preuve** : sur les 8 derniers mercredis, le radar a vu venir **95 % des commandes**, avec un CA prévu **à 8 % près**.

> **Visual:** the /radar map, ghost pins + event chips

## 4 · LIVE — « Lancer les appels »
**26 appels en même temps, en ~90 s.** Chaque « oui » devient une commande (via nos garde-fous) et la tournée se redessine.

| Avant | Après 26 appels |
|---|---|
| 19 commandes pour mercredi | **32 commandes** |
| — | **+12,2 k€ HT** de commandes prises |
| tournées habituelles | **−175 km, −198 €** sur la journée |

> **Visual:** the live demo (backup: the video)

## 5 · L'argent
| Source | Par jour de livraison |
|---|---|
| Commandes prises par le Radar | ≈ 12 k€ HT de CA (dont ≈ 1,5 à 4,6 k€ grâce aux événements) |
| Tournées optimisées | 559 → 352 km : **≈ 240 €/jour**, 4 h de chauffeur |
| Temps commercial | 26 appels × 4 min = **1 h 45** rendues à l'équipe |
| Coût de l'agent | **≈ 1 centime** par appel |

> **Visual:** one big number, « € gagnés / jour », then the 3 lines

## 6 · Pourquoi on peut lui faire confiance
- **Les règles sont dans l'ERP, pas dans le prompt** : compte bloqué, plafond d'encours, heure limite, quantité inhabituelle, minimum de commande. L'agent ne peut pas les contourner.
- **Le Garde-fou** : chaque chiffre que l'agent prononce est vérifié contre l'ERP (✓ avec sa source).
- **Mesuré** :
  - 39/40 appels simulés réussis, plus 30 appels générés au hasard (client × situation) ;
  - premier son en **0,7 s** ;
  - **1 centime** par appel.
- **Ce qui est simulé, on le dit** : les clients qui répondent pendant la campagne (joués par un modèle à partir de leur fiche) et le calendrier des événements.

## 7 · Et demain
- Des appels aux **fournisseurs** en parallèle, pour négocier les prix de la nuit.
- Le **copilote** de l'ADV (déjà construit) : l'agent écoute Leïla et remplit l'ERP pendant qu'elle parle.
- Brancher un **vrai numéro** : l'agent est prêt, il ne manque que la ligne.

> **Close:** « Médina décroche, même quand personne n'est là. Et maintenant, c'est Médina qui appelle. »

---

## Sources (real runs, seed data)
- **Campaign through /radar on a clean install** (fresh clone + `make setup`): 26 calls, 19 → 32 orders, 12 244 € HT, +1 442 € from events, 181 km on 4 trucks, −175 km / −198 € vs the usual routes.
- **Engine runs**: 26 calls in 84 s, 22 orders, 12.1k € HT, +4.6k € from events.
- **Backtest**: `GET /api/radar/backtest?weeks=8` → 95 % of orders foreseen, 88 % of predictions right, revenue within 8 %.
- **Routes**: `GET /api/routing/optimize?date=2026-11-24` → 559 → 352 km, ≈ 243 € and 4.3 h saved.
- **Inbound sim on main** (Haiku + orchestra): 39/40, judge 4.0 / 3.9, first sound 0.70 s, $0.011 per call.
