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

1. **Prévision** (`apps/backend/app/services/radar.py`, `GET /api/radar/forecast`). Pour chaque client livré demain, l'ERP calcule :
   - son rythme sur ce jour de la semaine (combien de mercredis sur les 12 derniers) ;
   - son panier de base (les produits présents dans au moins la moitié de ses commandes récentes) ;
   - ses prix négociés.
   Un produit en rupture n'est jamais proposé : on prend son remplaçant habituel, sauf si le client refuse les substitutions.
2. **Événements et commandes spéciales** (`apps/backend/app/radar_events.json`, données ajoutées). Chacun touche un segment de clients :
   - match OM–PSG mercredi soir : les snacks prennent +40 % de broches, +30 % de frites et de pains ;
   - mariages du week-end : les traiteurs reçoivent deux agneaux entiers (commande spéciale, à passer la veille avant 14 h) et plus de gigots ;
   - vague de froid : souris et collier pour les tajines ;
   - marché de Noël du Vieux-Port : merguez et brochettes.
   Les clients qui ont **déjà** commandé, mais qu'un événement concerne, reçoivent un **appel d'ajout** (« pour vos mariages de samedi, je vous ajoute deux agneaux ? »).
3. **Priorisation** (PR #49). Les clients **fidèles** passent d'abord (régularité, fréquence sur 90 jours, ancienneté), puis par **€ net attendu** = probabilité × marge − coût du détour du camion. Un client qui coûte plus à livrer qu'il ne rapporte n'est pas appelé, et on lui propose l'enlèvement au quai. Demain : 24 appels (20 fidèles), 2 clients écartés.
4. **Appels en parallèle** (`apps/ai/app/campaign/`, `POST /ai/campaigns` et un flux SSE). Pour chaque appel :
   - le vrai agent vocal appelle et parle en premier, à partir d'une consigne cachée (`agent/radar_brief.py`) ;
   - Jev classe chaque réponse du client en environ 300 ms : oui, modifie, non, rappeler ;
   - chaque « oui » crée la commande via les garde-fous (`finalize_order`) ;
   - les tournées de demain sont ré-optimisées et le tracé se met à jour en direct (`services/optimize.py`).
5. **Tournées sur la vraie géographie** (`services/geo.py`, `services/optimize.py`). Avant, l'ERP rangeait les arrêts sans géographie (« départ + 12 min par arrêt »). Maintenant :
   - les arrêts sont ordonnés par proximité, sur les coordonnées tirées du code postal ;
   - on échange des arrêts entre camions ;
   - capacité et créneau sont respectés.
   Sur les tournées du jour : **559 km → 352 km**, soit environ 4 h 20 et 240 € économisés. On calcule aussi le coût de livraison de chaque client et on signale les petites commandes lointaines.
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

Plus les tests du backend (`apps/backend/tests/` : `test_smart`, `test_shortages`, `test_radar`, `test_radar_backtest`, `test_dispatch`), 125 tests du service IA, et un test sur une **installation propre** (clone neuf + `make setup` + une campagne complète dans l'interface).

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
| Rétro-test sur les 8 derniers mercredis (`GET /api/radar/backtest`) | **95 % des commandes vues venir**, 88 % des prévisions justes, quantités à ±24 %, chiffre d'affaires prévu à **8 % près** |
| Campagne complète sur les données du jour (26 appels, 8 en parallèle, avant la priorisation) | 84 s, 22 commandes, 3 refus, 1 rappel ; 12,1 k€ HT, dont 4,6 k€ grâce aux événements ; tournées de 13 à 27 arrêts |
| **3 campagnes avec priorisation** (`python -m sim.radar_stats --runs 3`, 24 appels chacune) | **≈ 84 s**, **19 « oui »** en moyenne ; **12,5 k€ HT** (11,0–14,6) ; marge 2,6 k€ ; tournées −136 € ; temps commercial 40 € ; **≈ 2 800 € gagnés par campagne** (2 424 – 3 200 €) |
| Même campagne sur une **installation propre**, dans l'interface `/radar` | 26 appels, 19 → 32 commandes, 12 244 € HT, tournées −175 km / −198 € |
| Scénarios d'appel radar (match, ajout mariages, refus) | 6/6 sur Haiku, juge 3,8 / 4,2 |
| Jev sur les réponses (oui / modifie / non / rappeler) | 5/5 sur un contrôle manuel, ~300 ms |

Sur ce dernier run, les deux échecs sont : une coupure réseau de l'API (corrigée ensuite par une nouvelle tentative automatique, PR #35) et une remarque du juge sur un prospect. Le coût double avec l'orchestre (agents secondaires), mais reste à 1 centime par appel.

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
