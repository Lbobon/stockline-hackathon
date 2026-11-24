# DECISIONS — Team 18 · Médina Viandes

Le jury lit ce fichier. On le remplit au fil de l'eau : quoi, pourquoi, ce qu'on a essayé et abandonné, avec les mesures.
Specs : [V1](.plans/night-line-shortage-triage-spec.md) · [V2 Radar](.plans/demand-radar-spec.md).

## 1. Le problème choisi

**Médina Viandes n'a pas de standard de nuit, et personne n'a le temps d'appeler avant le départ des camions.**

- **Le soir (20 h – 3 h)** : les snacks et restaurants commandent après le coup de feu, par WhatsApp ou message vocal. Rien n'est traité avant l'ouverture du standard par Leïla à 5 h. Or une commande pour le matin même doit arriver **avant 3 h** (`data/context/02-journee-type.md`). Aujourd'hui, ces commandes sont retapées à la main, en retard, ou perdues.
- **À 5 h 30** (le « maintenant » du jeu de données), deux produits sont à zéro : l'escalope de veau (audit sanitaire chez le fournisseur) et la broche kebab de 20 kg (panne de la ligne de montage). Le playbook dit d'appeler les clients qui refusent les substitutions **avant le départ du camion à 6 h**. Trois commandes contiennent ces produits : l'une est déjà remplacée avec l'accord du client, les deux autres sont à traiter (un client refuse toute substitution, l'autre a un remplaçant lui-même en rupture). Personne n'est disponible pour appeler.

**Ce que l'agent change :**
1. **Standard de nuit (entrant)** : il répond, identifie le client, propose « comme d'habitude », vérifie stock et prix négociés, relit la commande et la **termine lui-même**. Il la confirme seulement si les règles de l'entreprise le permettent ; sinon il la laisse en brouillon pour Leïla, avec la raison (§3).
2. **Tri des ruptures (sortant)** : à 5 h 30, il liste les commandes touchées et décide par client (appeler, ou substituer si la fiche l'accepte). Il **appelle lui-même** les clients concernés et parle en premier. Si le client refuse le remplaçant, il propose un geste calculé sur l'historique du client.
3. **Page « Ce matin »** pour Leïla, qui n'est pas à l'aise avec l'informatique : en une minute, ce qu'il faut vérifier, rappeler et valider, puis un bouton « Lancer les appels ».

**Pourquoi la voix :** les clients du soir (snacks, boucheries) commandent déjà au téléphone et par message vocal ; le tri des ruptures est une suite d'appels courts juste avant 6 h. Les deux sont des moments où il n'y a personne au bureau.

4. **Copilote de l'ADV** : Leïla prend l'appel elle-même. L'agent écoute, remplit la commande dans l'ERP pendant qu'elle parle et lui souffle à l'écran ce qu'elle ne doit pas rater (plafond dépassé, refuse les substitutions, produit en rupture). Elle valide en un clic, et les garde-fous passent quand même.

## 1 bis. V2 — « Radar de demande » : la commande qui s'écrit toute seule

**Le pari** : au lieu d'attendre que les clients appellent, l'ERP sait ce qu'ils vont commander demain, et des agents les appellent tous en même temps pour le leur proposer.

1. **Prévision** (`apps/backend/app/services/radar.py` et `demand.py`, `GET /api/radar/forecast`). Pour chaque client livré demain, l'ERP calcule :
   - son rythme sur ce jour de la semaine (combien de mercredis sur les 12 derniers) ;
   - sa **base** : la quantité médiane de chaque produit sur ses **8 derniers mêmes jours de semaine** (un produit compte s'il est sur au moins la moitié de ces commandes ; les jours de ramadan et d'Aïd sont sautés pour ne pas gonfler une semaine normale) ;
   - ses prix négociés.
   Un produit en rupture n'est jamais proposé : on prend son remplaçant habituel, sauf si le client refuse les substitutions.
2. **Calendrier des événements, dans l'ERP** (table `calendar`, `GET/POST/DELETE /api/radar/calendar`) : ramadan, Aïd el-Fitr, Aïd el-Kébir, vacances scolaires (zone B), fêtes de fin d'année, marché de Noël du Vieux-Port, mariages du week-end, plus trois **sources en direct** (`apps/backend/app/services/feeds.py`, `GET /api/radar/feeds`) :
   - **météo** Open-Meteo pour Marseille, relevés depuis octobre 2023 et prévision à 16 jours : froid (≤ 11 °C), chaleur (≥ 32 °C), pluie (≥ 10 mm), mistral (≥ 40 km/h), soit 166 épisodes ;
   - **matchs de l'OM au Vélodrome** (calendrier public de Ligue 1, openfootball) : 68 matchs à domicile depuis 2023, le vrai calendrier remplace le « OM–PSG » inventé (prochains : 22/11 contre Le Mans, 05/12 contre Nice) ;
   - **agendas ICS** (Le Dôme ou toute salle : variable `CALENDAR_ICS="concert|Le Dôme|https://…"`), rangés en `concert`.
   Les sources sont mises en cache dans SQLite (une panne réseau garde les dernières données, réessai toutes les 10 min ; `FEEDS=off` coupe le réseau) et survivent à un reset de l'ERP. Ce que fait chaque événement est **mesuré sur l'historique** (29 604 commandes) par type d'événement et par segment de clients (`GET /api/radar/uplift`) :
   - ramadan : boucheries ×3,4, supérettes ×3,8, traiteurs ×2,7, restaurants ×2,0 sur le chiffre d'affaires, mais les snacks ne bougent pas (×0,9) ;
   - Aïd el-Kébir (la semaine d'avant) : boucheries ×4 ;
   - vacances scolaires : cantines ×0,3 (elles ferment), le reste ≈ ×1,1 ;
   - sur une ligne, l'effet n'est pas le même que sur le chiffre d'affaires : au ramadan, un client commande aussi souvent, avec 35 % de lignes en plus et environ 2,5 fois plus par ligne. On mesure donc aussi un facteur par ligne (client par client : quantité médiane pendant l'événement ÷ quantité médiane des 8 semaines d'avant), par produit quand l'échantillon suffit, ramené vers le facteur du segment sinon.
   On ajoute aussi les produits qu'un client ne prend que pour l'événement (dattes, leben, smen au ramadan) : ce qu'il a pris à la dernière occurrence. Seuls les événements **déjà terminés** comptent : le rétro-test ne voit jamais l'événement qu'il note.
   Matchs, météo et concerts sont **mesurés** comme le reste, avec deux garde-fous : un jour de météo ou de match qui tombe pendant des vacances ou le ramadan n'entre pas dans sa mesure (sinon les cantines fermées l'été se lisent « chaleur ×0,6 »), et un effet qui ne dépasse pas trois écarts-types du bruit jour à jour compte pour ×1. Résultat honnête : presque rien ne bouge (froid : cantines ×1,5 ; chaleur : traiteurs ×1,3 par ligne ; match OM : boucheries ×1,13 par ligne ; pluie, mistral : rien de mesurable). Seuls les mariages et la soirée OM–PSG du 25/11 (match fictif, gardé pour la démo, entre les vrais matchs du 22/11 et du 05/12), sans historique, gardent une **estimation**, marquée comme telle partout (page, API, phrase de synthèse).
   Les clients qui ont **déjà** commandé, mais qu'un événement concerne, reçoivent un **appel d'ajout** (« pour vos mariages de samedi, je vous ajoute deux agneaux ? »).
   Synthèse calculée (`action` dans la prévision) : « Appeler 25 clients, 58 lignes proposées : 18 413 € HT de commandes attendues, dont 1 396 € de hausse mesurée sur l'historique et 4 802 € estimés (OM – PSG au Vélodrome, Mariages du week-end) ».
   **Modèle LightGBM** (`apps/backend/app/services/demand_gbm.py`, modèle par défaut, `RADAR_MODEL=radar` pour revenir à « médiane × hausse ») : qui commande reste le rythme sur 12 semaines ; pour chaque produit que le client a pris dans les 90 derniers jours ou à la même période l'an dernier, un classifieur donne la probabilité qu'il soit sur la commande et un régresseur (L1, une médiane) la quantité par rapport à sa base. Variables : historique du client (fréquences, médianes, dernière quantité, jours depuis), jour, mois, segment, produit, catégorie, événements mesurés du jour qui le concernent, météo. Entraîné sur 354 000 lignes (client × jour × produit) **avant le 1er du mois** du jour prévu, donc le rétro-test ne voit jamais ses propres jours ; 3 s de table + 2 s d'entraînement, fait au démarrage. Une ligne qu'il relève porte le nom de l'événement mesuré du jour, pour que l'agent puisse dire pourquoi.
   Pendant un appel entrant, l'agent appelle `radar_suggestion(client)` (`GET /api/radar/clients/{id}/suggestion`) et propose une fois le produit que l'événement relève.
3. **Priorisation** (PR #49). Les clients **fidèles** passent d'abord (régularité, fréquence sur 90 jours, ancienneté), puis par **€ net attendu** = probabilité × marge − coût du détour du camion. Un client qui coûte plus à livrer qu'il ne rapporte n'est pas appelé, et on lui propose l'enlèvement au quai. Demain : 24 appels (20 fidèles), 2 clients écartés.
4. **Appels en parallèle** (`apps/ai/app/campaign/`, `POST /ai/campaigns` et un flux SSE). Pour chaque appel :
   - le vrai agent vocal appelle et parle en premier, à partir d'une consigne cachée (`agent/radar_brief.py`) ;
   - Jev classe chaque réponse du client en environ 300 ms : oui, modifie, non, rappeler ;
   - chaque « oui » crée la commande via les garde-fous (`finalize_order`) ;
   - les tournées de demain sont ré-optimisées et le tracé se met à jour en direct (`services/optimize.py`).
5. **Tournées : vrai solveur, vrais temps de route** (`services/geo.py`, `services/optimize.py`). Avant, l'ERP rangeait les arrêts sans géographie (« départ + 12 min par arrêt »). Maintenant :
   - **temps et distances de route réels** : une matrice OSRM (le même routeur public que le tracé de la carte), demandée en un appel et gardée dans la base (table `road_legs`) : une paire de clients n'est demandée qu'une fois. Hors ligne, on revient à l'estimation (ligne droite × 1,35) ; `ROAD_TIMES=estimate` la force ;
   - **Google OR-Tools** (problème de tournées de véhicules) sur les camions du jour, en minimisant les € (km × 0,55 € + heures × 30 €) ;
   - **créneaux** : chaque client entre 6 h 30 et 11 h, ou plus tôt si sa fiche le dit (« livraison avant 10h ») ; un camion en avance attend ;
   - **capacité** de chaque camion en kg ;
   - **chaîne du froid** : au plus 5 h entre le départ du quai et la livraison (hypothèse, réglable : `COLD_CHAIN_MAX_MIN`) ;
   - un arrêt peut changer de camion, et un camion peut rester au dépôt si c'est moins cher. Si la journée ne peut pas tout respecter, les contraintes deviennent de fortes pénalités et les arrêts en retard sont signalés ;
   - **re-calcul à chaque « oui »** de la campagne : 0,5 à 0,7 s, mis en cache jusqu'à la prochaine écriture.
   Sur les tournées du jour (temps OSRM) : **601 km / 807 € → 378 km / 566 €**, 0 arrêt en retard, aucun arrêt plus de 4 h 33 dans le camion, 2 camions laissés au dépôt. L'ancienne heuristique (plus proche voisin + 2-opt) faisait 391 km / 589 €. On calcule aussi le coût de livraison de chaque client et on signale les petites commandes lointaines.
6. **Page `/radar`** : une carte, les clients qui sonnent, les appels en cours et le tracé qui se redessine à chaque « oui ».
7. **L'argent, en un chiffre** : chaque campagne calcule ses « € gagnés » = la marge des commandes prises (prix − coût d'achat ; un appel d'ajout ne compte que ce qu'il ajoute) + les € économisés sur les tournées + le temps commercial évité (4 min par appel à 25 €/h, hypothèses affichées).

**Ce qui est réel, ce qui est simulé** (on le dit dans la démo) :
- **Réel** : la prévision, les événements appliqués par segment, l'agent vocal et ses outils, les garde-fous, l'écriture des commandes et l'optimisation des tournées.
- **Simulé** : les clients qui répondent, joués par un modèle à partir de leur fiche persona, et le calendrier des événements. **Aucun vrai numéro n'est appelé.**

## 1 ter. « Le Garde-fou » : l'agent ne peut pas inventer un chiffre (PR #47)

Chaque phrase que l'agent va prononcer est vérifiée contre l'ERP **avant** d'être dite (`apps/ai/app/guard/`) :
1. Le texte de l'agent est coupé en phrases ; chacune est vérifiée dès qu'elle est complète.
2. On cherche ce qui est vérifiable : prix, remise, quantité, stock, date, état de commande (y compris les chiffres dits en toutes lettres). « Je regarde. » ne contient rien : elle est dite tout de suite, sans appel.
3. Les preuves : tout ce que l'ERP a dit pendant l'appel (résultats des outils, faits pré-chargés par l'Éclaireur, consigne d'un appel sortant, date du jour).
4. **Jev**, en une requête d'environ 150 ms, répond à cinq questions : la phrase est-elle appuyée par l'ERP ? la contredit-elle ? promet-elle ce que personne n'a accordé ? un chiffre diffère-t-il ? quelle est la source ? La vérification tourne pendant la synthèse vocale : une bonne phrase ne coûte rien.
5. Verdict : ✓ vérifié (avec sa source), ? non vérifié, ✗ contredit. Avec `GUARD_MODE=block`, un ✗ franc n'est **jamais envoyé à la voix** : l'agent se reprend à partir du fait de l'ERP. Si Jev tarde, la phrase passe (jamais de silence).

Mesures : 27/27 sur le jeu de test étiqueté, **0 blocage à tort** ; en simulation, 156 phrases vérifiées et aucune bloquée à tort ; premier son inchangé (0,70 s) avec le blocage actif. La démo utilise le bouton « Provoquer une erreur », qui force un mauvais chiffre (une hallucination provoquée, on le dit) : « l'épaule à 8,80 € » est arrêtée en 196 ms (prix client 11,80 €) et l'agent dit « 11 euros 80 ».

## 2. Scénarios de test

Le simulateur (`apps/ai/sim`, `cd apps/ai && uv run python -m sim`) : un modèle joue le client à partir de sa fiche persona, notre agent répond comme sur la page Voix contre le vrai ERP, et chaque appel est noté par des vérifications automatiques sur les outils appelés et par un juge. **20 scénarios nommés** : les 14 appels entrants ci-dessous (dont un chef anglophone), plus 3 appels sortants de rupture et 3 appels Radar.

| Scénario | Ce qui est testé |
|---|---|
| `habituel` | « Comme d'habitude » d'une cliente bavarde |
| `rupture_broche` | Broche de 20 kg en rupture : proposer la 10 kg sans forcer |
| `carcasses_kg` | « Quatre agneaux » = carcasses vendues au kilo, prix négocié |
| `el_baraka_ambigu` | Cinq clients s'appellent El Baraka : lever le doute |
| `client_bloque` | Compte bloqué : prendre le message, ne rien promettre |
| `trop_tard` | Commande après 3 h : livraison décalée, et le dire |
| `modification` | Modifier une commande existante, client qui hésite |
| `camion` | « Il est où le camion ? » |
| `reclamation` | Réclamation sur un poids manquant |
| `prospect` | Un prospect qui demande les prix |
| `geste_volume` | « Vous me faites un geste ? » |
| `refus_substitution` | Veau manquant, client qui refuse toute substitution |
| `plafond_credit` | Grosse commande de nuit au-dessus du plafond d'encours |

**Appels génériques** (`python -m sim --generic 30 --seed 7`) : pour ne pas sur-ajuster l'agent à nos scénarios, on tire au hasard un vrai client et une des 15 situations courantes (change deux fois une quantité, décrit un produit sans son nom, demande un prix sans commander, réclamation, veut parler à un humain, produit hors catalogue, demande une remise, parle d'autre chose, « oui » ambigu, heure du camion, grosse commande, client pressé, retirer une ligne, client agressif, jour non livré). On ne vérifie que des règles vraies pour tout appel (bon client, rien écrit pour un autre, compte bloqué jamais confirmé, commande seulement si voulue), le juge fait le reste. Même graine = mêmes appels, pour comparer avant / après.

Plus les tests du backend (`apps/backend/tests/` : `test_smart`, `test_shortages`, `test_radar`, `test_radar_backtest`, `test_feeds`, `test_dispatch`), 125 tests du service IA, et un test sur une **installation propre** (clone neuf + `make setup` + une campagne complète dans l'interface).

## 3. Choix techniques

**Les règles métier sont du code, pas du prompt.** Le modèle formule ; le backend décide.
- **Garde-fous de commande** (`apps/backend/app/services/gates.py`, outil `finalize_order`). L'agent ne confirme pas lui-même : l'ERP confirme seulement si aucune règle ne bloque. Sinon il garde un brouillon avec une note « À vérifier : … » visible par Leïla. Les règles :
  - compte bloqué ;
  - plafond d'encours dépassé ;
  - trop tard pour aujourd'hui ;
  - récapitulatif pas clairement validé ;
  - quantité plus de 3× au-dessus de l'habitude du client ;
  - produit jamais acheté par le client ;
  - carcasse commandée après 14 h la veille ;
  - **cantine** commandée à moins de J-3 (règle de la maison) ;
  - commande **sous le minimum de 120 € HT** (sinon enlèvement au quai) ;
  - **stock insuffisant même avec les arrivages** prévus.

  Les règles de compte bloqué et d'encours réutilisent les alertes déjà dans l'ERP : une seule source de vérité. **Une commande confirmée que l'agent modifie est revérifiée** : si la modification crée une infraction, elle repasse en brouillon pour Leïla avec la raison (les modifications faites par un humain ne sont pas revérifiées, comme le reste de l'ERP).
- **Dates de livraison calculées par l'ERP** : la fiche client donne ses 3 prochaines vraies dates (`nextDeliveries`), qui tiennent compte de ses jours, du camion de sa zone et de son délai. L'agent ne calcule jamais une date lui-même : il proposait « lundi 30 » quand jeudi 26 était un jour de livraison.
- **Tri des ruptures** (`services/shortages.py`). Le remplaçant habituel vient de la fiche de contexte « Substitutions habituelles ». La préférence du client vient de sa fiche CRM (refuse / accepte / inconnue). **Préférence inconnue ⇒ on appelle** (« jamais sans accord »). Les commandes du camion du jour passent en premier.
- **Geste commercial** (`services/retention.py`). C'est un pourcentage de la ligne perdue, plafonné à 15 % :
  - 3 % de base ;
  - +1 % par année de fidélité ;
  - +2 % si le client achète plus de 3 000 € HT par mois ;
  - +2 % s'il paie toujours à l'heure ;
  - +2 % s'il a une réclamation ouverte (client à risque).

  Un nouveau client a un barème fixe (3 %, au plus 30 €). Un client avec des factures en retard n'a **aucun geste financier**, seulement des offres de service. Au-delà de 150 € HT, le geste est **proposé et validé par Rachid** : il est enregistré comme événement ouvert et apparaît dans « Ce matin ». Chaque geste est expliqué en une phrase.
- **Appel sortant** (`agent/outbound.py`). Le message `start` du WebSocket voix accepte un `outboundOrderId`. L'agent reçoit une consigne cachée (qui appeler, quoi manque, le remplaçant, la préférence du client) et parle en premier, **sans appeler d'outil** : tout est déjà dans la consigne.
- **Modèle** : Claude **Haiku 5.5** (effort bas, réflexion par défaut) plutôt que Sonnet 5.5. Il est plus rapide, environ 10× moins cher et meilleur au simulateur (mesures §4).
- **Outils composites** (`tools/fast.py`) : `find_client` (recherche + fiche + contexte) et `check_product` (recherche + stock + prix client). Ils font un seul aller-retour modèle au lieu de trois.
- **Pas de date réelle** : tout est ancré sur l'horloge du jeu de données (24/11, 05 h 30), y compris l'heure limite des carcasses.
- **Mode mains libres** (page Voix, bouton « Mains libres » ; `services/handsfree.py`). Le micro reste ouvert tout l'appel, un seul flux Soniox. Comment ça marche :
  - **Fin de phrase.** Le jeton `<end>` de Soniox, réglé à 500 ms au plus après le dernier mot. Sans lui, 0,9 s sans mot nouveau.
  - **Phrase finie ?** Elle ne l'est pas si elle se termine par « de », « et », « pour », « euh »… Elle l'est si Soniox l'a ponctuée. Sinon Jev tranche en ≈ 140 ms.
  - **Départ anticipé.** Le tour démarre dès la pause, en brouillon. Il réfléchit, lit l'ERP et synthétise, mais rien n'est envoyé, dit ou écrit dans l'ERP tant que la phrase n'est pas jugée finie (`Gate` dans `tools/erp.py`).
  - **Brouillon jeté.** Si l'appelant reprend avant, le brouillon est jeté et l'historique remis en état. Sa phrase continue.
  - **Interruption.** Si l'appelant parle par-dessus l'agent, l'audio est coupé sur le navigateur (`agent_audio_cut`) et le tour annulé.
  - **Ce qui ne coupe pas.** Un « oui oui » ou l'écho de l'agent dans le micro ne coupent pas. L'écho, c'est une suite de mots de l'agent dans le même ordre ; l'annulation d'écho du navigateur reste activée.
  - **Voix mot à mot.** Les mots partent vers ElevenLabs au fil de l'écriture (WebSocket `stream-input`), avec un vidage à chaque fin de phrase. Mesuré : premier audio 155 ms après le premier mot, sans attendre la fin de la phrase. Désactivé en mode blocage du Garde-fou, qui doit entendre une phrase entière avant qu'elle soit dite, et avec `TTS_STREAM=0`.
  - **Phrase d'attente.** En mains libres, elle part 200 ms après la fin de phrase, même pendant que l'Éclaireur précharge.
- **Robustesse** : une étape du modèle que l'API coupe avant toute réponse est relancée une fois (PR #35). La transcription n'attend plus que 0,8 s la confirmation finale de Soniox ; au-delà, les mots déjà entendus en direct sont utilisés. En mode blocage du Garde-fou, une interruption du client (barge-in) s'arrêtait en erreur : corrigé.

## 4. Mesures

Simulateur, 13 appels, agent Haiku 5.5. Latence mesurée dans le processus (texte de l'appelant → premier mot de l'agent), sans STT, TTS ni réseau. Coût = tokens LLM de l'agent.

| Configuration | Réussite | Juge smart / naturel (sur 5) | 1er mot, médiane / p90 | Coût / appel |
|---|---|---|---|---|
| Sonnet 5.5 (2 × 13 appels, mesure de fuzzy-zephyr) | 19/26 | — | 3,8 s / 6,0 s | ≈ 10× Haiku |
| Haiku 5.5 + outils composites (2 × 13) | 22/26 | — | 2,3 s / 4,0 s | ~0,005 $ |
| Haiku 5.5, main au 23/11 avant correctifs | 10/13 | 3,5 / 3,8 | 2,3 s / 4,9 s | 0,005 $ |
| Haiku 5.5, main après #29 | 13/13 | 3,8 / 3,8 | 2,5 s / 4,7 s | 0,005 $ |
| Haiku 5.5 + correctifs du simulateur (#30, 2 × 13, mesure de fuzzy-zephyr) | 26/26 | 4,2 / 3,9 | 2,6 s / 5,0 s | 0,006 $ |
| Haiku 5.5 + orchestre (#26) + voix adaptée (#23) + prompt affiné, 2 × 14 appels | 26/28 | 3,9 / 3,9 | premier son 0,70 s ; fin de tour 3,3 s | 0,010 $ |
| **Tous les scénarios nommés (20 × 2), main du 24/11** | **39/40** | **4,0 / 3,9** | **premier son 0,70 s** | **0,011 $** |
| Appels génériques (30, graine 7), avant les correctifs des règles | 26/30 | 3,6 / 3,9 | 0,70 s | 0,015 $ |
| **Appels génériques (30, graine 7), après** (minimum 120 €, revérification, dates de l'ERP, cantines) | **27/30** | 3,7 / 3,9 | 0,70 s | 0,014 $ |

Les 3 échecs restants des appels génériques changent d'un tirage à l'autre (variance de ±1–2 appels) : ce sont des remarques du juge (une promesse de rappel, un passage du tu au vous, le minimum non dit dans le récapitulatif, corrigé depuis).

**Radar de demande** :

| Mesure | Résultat |
|---|---|
| Rétro-test sur les 8 derniers mercredis (`GET /api/radar/backtest`) | **95 % des commandes vues venir**, 87 % des prévisions justes ; voir le tableau radar contre moyenne ci-dessous |
| Campagne complète sur les données du jour (26 appels, 8 en parallèle, avant la priorisation) | 84 s, 22 commandes, 3 refus, 1 rappel ; 12,1 k€ HT, dont 4,6 k€ grâce aux événements ; tournées de 13 à 27 arrêts |
| **3 campagnes avec priorisation** (`python -m sim.radar_stats --runs 3`, 24 appels chacune) | **≈ 84 s**, **19 « oui »** en moyenne ; **12,5 k€ HT** (11,0–14,6) ; marge 2,6 k€ ; tournées −136 € ; temps commercial 40 € ; **≈ 2 800 € gagnés par campagne** (2 424 – 3 200 €) |
| Même campagne sur une **installation propre**, dans l'interface `/radar` | 26 appels, 19 → 32 commandes, 12 244 € HT, tournées −175 km / −198 € |
| Scénarios d'appel radar (match, ajout mariages, refus) | 6/6 sur Haiku, juge 3,8 / 4,2 |
| Jev sur les réponses (oui / modifie / non / rappeler) | 5/5 sur un contrôle manuel, ~300 ms |

**Trois modèles sur les mêmes jours** (rétro-test, chaque jour prévu la veille, `GET /api/radar/backtest` et `/backtest/events`, test `apps/backend/tests/test_radar_backtest.py`, ici avec les sources en direct). Même liste de clients pour les trois ; seules les lignes et les quantités changent. « Radar » = médiane des 8 mêmes jours × hausse mesurée ; « LightGBM » = le modèle ci-dessus ; « moyenne » = le panier moyen des 60 derniers jours sans événement. CA : Σ |prévu − livré| ÷ livré, jour par jour. Quantités : écart moyen sur les lignes proposées et commandées. Client : Σ |proposé − livré| ÷ livré, client par client.

| Jours testés | Nb | Erreur sur le CA du jour (LightGBM / radar / moyenne) | Quantités | Erreur client par client |
|---|---|---|---|---|
| 8 mercredis normaux | 8 | 19 % / 20 % / 18 % | **±19 %** / ±21 % / ±24 % | **0,74** / 0,92 / 0,78 |
| Ramadan 2026 (un jour par semaine) | 4 | **12 %** / 25 % / 67 % | ±23 % / ±24 % / ±42 % | 0,78 / 0,72 / 0,65 |
| Aïd el-Fitr 2026 | 3 | **17 %** / 30 % / 59 % | ±23 % / ±26 % / ±38 % | 0,59 / 0,59 / 0,66 |
| Aïd el-Kébir 2026 (semaine d'avant) | 6 | 38 % / **23 %** / 66 % | ±26 % / ±23 % / ±36 % | 0,72 / 0,74 / 0,81 |
| Vacances scolaires | 16 | **9 %** / 17 % / 27 % | ±18 % / ±22 % / ±30 % | 0,68 / 0,85 / 0,77 |
| Matchs de l'OM au Vélodrome | 22 | **15 %** / 23 % / 42 % | ±21 % / ±24 % / ±34 % | 0,85 / 0,95 / 0,76 |
| Jours de froid / chaleur / pluie / mistral | 69 | 12–19 % / 14–21 % / 13–40 % | ±17–23 % / ±20–28 % / ±23–42 % | 0,64–0,70 / 0,75–0,86 / 0,66–0,87 |
| Marché de Noël 2025 | 4 | 10 % / 10 % / 10 % | ±17 % / ±18 % / ±19 % | 0,73 / 0,85 / 0,77 |
| Fêtes de fin d'année 2025 | 2 | 3 % / 18 % / 3 % | ±17 % / ±20 % / ±18 % | 0,70 / 0,98 / 0,68 |
| **Tous les jours d'événement de l'année** (chaque jour une fois) | ≤ 126 | **17 %** / 19 % / 35 % | **±20 %** / ±22 % / ±30 % | **0,69** / 0,80 / 0,74 |

Lecture honnête : en semaine normale, personne ne devine le chiffre d'affaires du jour (≈ 19 % pour les trois : un mercredi varie de 26 k€ à 52 k€), mais LightGBM propose les bonnes lignes et les bonnes quantités plus souvent (erreur client par client 0,74 contre 0,92 pour le radar). Sur les jours d'événement, il fait mieux que le radar presque partout (ramadan : CA à 12 % contre 25 %), sauf la semaine de l'Aïd el-Kébir où la hausse mesurée du radar reste meilleure (23 % contre 38 % : une seule occurrence passée dans ses données d'entraînement). Il est devenu le modèle par défaut parce qu'il gagne sur l'ensemble, pas sur chaque ligne. La météo et les matchs n'expliquent presque rien de la demande d'un grossiste halal : les sources sont branchées et mesurées, et le disent.

Sur ce dernier run, les deux échecs sont : une coupure réseau de l'API (corrigée ensuite par une nouvelle tentative automatique, PR #35) et une remarque du juge sur un prospect. Le coût double avec l'orchestre (agents secondaires), mais reste à 1 centime par appel.

**Latence en mains libres** (bout en bout, vrais services : voix d'appelant ElevenLabs envoyée en temps réel à WS /voice, Soniox, Sonnet 5.5, ElevenLabs ; mesurée depuis le dernier échantillon parlé). Premier son de l'agent :

| Type de phrase | Premier son |
|---|---|
| Question (« Il vous reste de l'épaule d'agneau pour demain ? ») | **0,66 à 0,81 s** |
| Affirmation (« Bon, mettez-moi vingt kilos d'épaule pour jeudi. ») | **≈ 1,2 s** |
| Phrase avec une pause sur une virgule | **≈ 1,8 s** |
| Phrase coupée (« Et je voudrais aussi du ») | **≈ 2,3 s**, attendu (on lui laisse 1,2 s pour finir) |

Le plancher, c'est Soniox : le dernier mot apparaît ≈ 0,5 s après avoir été dit. La fin de phrase arrive ≈ 0,2 s plus tard. Le premier vrai mot de l'agent dépend toujours du modèle et des outils (2 à 6 s avec Sonnet). Le micro réel du navigateur n'a pas été testé dans cet environnement : le navigateur de test bloque le micro.

**Latence en appel réel** (page Voix, push-to-talk, Soniox + ElevenLabs). Un appel entrant le 24/11 au soir : premier son **1,9 s** (la phrase d'attente), dont 1,9 s de transcription après le relâchement, puis **5,4 s** avant le premier vrai mot de l'agent (premier tour : il a cherché le client, relu la commande, vérifié deux produits, avec la réflexion de Haiku à chaque étape). Reproduit dans le processus : 11 à 14 s au premier tour, 3,5 s sur un simple « ouais c'est bon ». Corrigé depuis : transcription plafonnée à 0,8 s. La réflexion du modèle est mesurée ci-dessous.

Un appel sortant mesuré plus tôt (avant le correctif qui a retiré Istanbul Grill du tri) :

| LLM 1er token | LLM total | TTS 1er octet | Total | **Perçu (premier son)** |
|---|---|---|---|---|
| 920 ms | 1,5 s | 537 ms | 1,6 s | **1,7 s** |

**L'orchestre (PR #26)** : des agents secondaires autour de l'agent vocal, qui ramènent le premier son de 4,2 s à **0,7 s**.
- L'**Éclaireur** pré-charge la fiche client, le stock et les prix pendant que l'appelant parle encore (40 % des tours).
- Le **Réflexe** répond à un simple « oui » au récapitulatif sans le grand modèle, en passant par `finalize_order`.
- Une phrase d'attente pré-synthétisée part à 0,7 s si rien n'a encore été dit.
- Le **Greffier** écrit l'historique client hors du chemin de la voix.
- Le **Linguiste** adapte la langue et le tutoiement. La langue suit la majorité de l'appel : un « salam aleykoum » dans une commande en français reste en français (PR #34).
- Le **Rapporteur** écrit un compte rendu d'appel à la fin (page « Appels »).

**Voix adaptée à l'appelant (PR #23)** : le registre de la fiche client (tu/vous) prime, et la stabilité de la voix ElevenLabs est réglée par client.

## 5. Ce qui a marché, ce qui n'a pas marché

**A marché**
- Mettre les règles dans le backend. Le simulateur a montré que le modèle seul confirmait des commandes au-dessus du plafond. Avec `finalize_order`, c'est impossible par construction.
- Le simulateur comme test de non-régression : chaque correctif se vérifie en une commande, avec un chiffre.
- Haiku plutôt que Sonnet : meilleur sur les deux axes, pas un compromis.
- Pour l'appel sortant, donner toute l'information dans la consigne. Le premier son est passé de 4,9 s (3 outils avant de parler, plus une phrase d'attente) à 1,8 s.

**N'a pas marché / abandonné**
- **Cacher la raison d'un blocage d'encours au client.** On l'avait interdit par prudence, mais le juge l'a pénalisé à chaque fois (`plafond_credit` échoue). L'agent dit maintenant, avec tact, l'encours, le plafond et ce qui débloque. Le compte bloqué, lui, reste non détaillé.
- **L'heure limite des carcasses sur l'horloge réelle de la machine.** Le même appel passait le matin et échouait le soir. Elle utilise maintenant l'horloge du jeu de données.
- **Haiku sans réflexion** (`LLM_THINKING=disabled`). Premier mot à 0,47 s, mais seulement 15/26 réussis, et une fois il a lu son raisonnement anglais au client. Abandonné.
  Re-mesuré le 24/11 sur les 30 appels génériques (même graine) : **22/30** et juge 3,2 / 3,1 sans réflexion, contre **27/30** et 3,7 / 3,9 avec. Les tours sont 1,7× plus rapides, mais l'agent invente (« oui, nous travaillons le poisson », du stock qui n'existe pas). On garde la réflexion ; le premier tour se raccourcit en réduisant les allers-retours outils, pas en coupant la réflexion.
- **Router chaque tour entre Haiku et Sonnet** (étudié, pas construit). Le préfixe du prompt fait 29 079 tokens et son cache est propre à chaque modèle : le premier tour Sonnet d'un appel coûte ≈ 0,06–0,07 $, soit environ 10× un appel Haiku entier. Sonnet est aussi plus lent (3,8 s contre 2,5 s au premier mot), et les blocs de réflexion ne se rejouent pas d'un modèle à l'autre. Les cas difficiles se corrigent dans le prompt et les outils, pour Haiku.
- **Réglages de sortie ElevenLabs** (format, speaker boost, normalisation) et préchauffage de connexion : aucun gain mesurable (~155 ms au premier octet quoi qu'on fasse).

- **Radar, premier essai** :
  - la prévision proposait tout le panier récent, d'où un CA prévu égal à 2× le réel. On garde maintenant les produits présents dans au moins la moitié des commandes, et le CA prévu tombe à 8 % près.
  - Jev lisait toute la proposition de l'agent avec la réponse et classait des « non merci » en « oui ». Il ne lit plus que la réponse du client.
  - certains clients disaient « oui » puis raccrochaient dans la même phrase, et la commande n'était jamais écrite. Le moteur donne maintenant un dernier tour à l'agent et finalise via les garde-fous.
  - les clients simulés râlaient d'être appelés à 5 h 30. Les appels de prévision se font l'après-midi, comme les commerciaux.
- **Panne de camion avec appels en parallèle** : construit et testé (`POST /api/routing/breakdown` : un autre camion récupère la marchandise et livre, sinon retrait au quai ou prochaine livraison), puis mis de côté pour la démo au profit du Radar, plus fort à pitcher.

- **Les appels génériques** ont trouvé des trous dans les règles que nos scénarios nommés ne voyaient pas : une commande confirmée modifiée n'était plus vérifiée (100 kg de poulet en rupture ajoutés), aucun minimum de commande, un jour de livraison calculé de tête. Tous corrigés dans le backend, pour tous les appels.

**Corrigé depuis les premières notes du juge** : les transmissions promises sont maintenant écrites (le Greffier enregistre chaque note, PR #40) ; la date de péremption n'est plus annoncée comme celle du lot livré ; les poids sont dits naturellement (« un peu plus de 101 kilos ») (PR #30).

**Reste à corriger**
- **Le premier tour d'un appel réel est lent** : plusieurs allers-retours outils, chacun avec la réflexion du modèle (voir §4).
- L'agent promet encore parfois un rappel « ce matin » : le Garde-fou le marque « ? » mais ne le bloque pas.
- Un passage du tu au vous dans un appel tendu.
