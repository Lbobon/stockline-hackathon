# DECISIONS — Team 18 · Médina Viandes

Le jury lit ce fichier. On le remplit au fil de l'eau : quoi, pourquoi, ce qu'on a essayé et abandonné, avec les mesures.
Spec complète : [.plans/night-line-shortage-triage-spec.md](.plans/night-line-shortage-triage-spec.md).

## 1. Le problème choisi

**Médina Viandes n'a pas de standard de nuit, et personne n'a le temps d'appeler avant le départ des camions.**

- **Le soir (20 h – 3 h)** : les snacks et restaurants commandent après le coup de feu, par WhatsApp ou message vocal. Rien n'est traité avant l'ouverture du standard par Leïla à 5 h. Or une commande pour le matin même doit arriver **avant 3 h** (`data/context/02-journee-type.md`). Aujourd'hui, ces commandes sont retapées à la main, en retard, ou perdues.
- **À 5 h 30** (le « maintenant » du jeu de données), deux produits sont à zéro : l'escalope de veau (audit sanitaire chez le fournisseur) et la broche kebab de 20 kg (panne de la ligne de montage). Le playbook dit d'appeler les clients qui refusent les substitutions **avant le départ du camion à 6 h**. Trois commandes contiennent ces produits : l'une est déjà remplacée avec l'accord du client, les deux autres sont à traiter (un client refuse toute substitution, l'autre a un remplaçant lui-même en rupture). Personne n'est disponible pour appeler.

**Ce que l'agent change :**
1. **Standard de nuit (entrant)** : il répond, identifie le client, propose « comme d'habitude », vérifie stock et prix négociés, relit la commande et la **termine lui-même**. Il la confirme seulement si les règles de l'entreprise le permettent ; sinon il la laisse en brouillon pour Leïla, avec la raison (§3).
2. **Tri des ruptures (sortant)** : à 5 h 30, il liste les commandes touchées et décide par client (appeler, ou substituer si la fiche l'accepte). Il **appelle lui-même** les clients concernés et parle en premier. Si le client refuse le remplaçant, il propose un geste calculé sur l'historique du client.
3. **Page « Ce matin »** pour Leïla, qui n'est pas à l'aise avec l'informatique : en une minute, ce qu'il faut vérifier, rappeler et valider, puis un bouton « Lancer les appels ».

**Pourquoi la voix :** les clients du soir (snacks, boucheries) commandent déjà au téléphone et par message vocal ; le tri des ruptures est une suite d'appels courts juste avant 6 h. Les deux sont des moments où il n'y a personne au bureau.

## 2. Scénarios de test

13 appels simulés (`apps/ai/sim`, lancés avec `cd apps/ai && uv run python -m sim`). Un modèle joue le client à partir de sa fiche persona. Notre agent répond comme sur la page Voix, contre le vrai ERP. Chaque appel est noté par des vérifications automatiques sur les outils appelés et par un juge.

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

Plus les vérifications du backend (`apps/backend/tests/test_smart.py`, `test_shortages.py`) et 72 tests du service IA.

## 3. Choix techniques

**Les règles métier sont du code, pas du prompt.** Le modèle formule ; le backend décide.
- **Garde-fous de commande** (`apps/backend/app/services/gates.py`, outil `finalize_order`). L'agent ne confirme pas lui-même : l'ERP confirme seulement si aucune règle ne bloque. Sinon il garde un brouillon avec une note « À vérifier : … » visible par Leïla. Les règles :
  - compte bloqué ;
  - plafond d'encours dépassé ;
  - trop tard pour aujourd'hui ;
  - récapitulatif pas clairement validé ;
  - quantité plus de 3× au-dessus de l'habitude du client ;
  - produit jamais acheté par le client ;
  - carcasse commandée après 14 h la veille.

  Les règles de compte bloqué et d'encours réutilisent les alertes déjà dans l'ERP : une seule source de vérité.
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

## 4. Mesures

Simulateur, 13 appels, agent Haiku 5.5. Latence mesurée dans le processus (texte de l'appelant → premier mot de l'agent), sans STT, TTS ni réseau. Coût = tokens LLM de l'agent.

| Configuration | Réussite | Juge smart / naturel (sur 5) | 1er mot, médiane / p90 | Coût / appel |
|---|---|---|---|---|
| Sonnet 5.5 (2 × 13 appels, mesure de fuzzy-zephyr) | 19/26 | — | 3,8 s / 6,0 s | ≈ 10× Haiku |
| Haiku 5.5 + outils composites (2 × 13) | 22/26 | — | 2,3 s / 4,0 s | ~0,005 $ |
| Haiku 5.5, main au 23/11 avant correctifs | 10/13 | 3,5 / 3,8 | 2,3 s / 4,9 s | 0,005 $ |
| **Haiku 5.5, main après #29** | **13/13** | **3,8 / 3,8** | **2,5 s / 4,7 s** | **0,005 $** |

Dans l'application réelle (page Voix, Soniox + ElevenLabs), un appel sortant mesuré avant le correctif qui a retiré Istanbul Grill du tri (sa broche était déjà remplacée) :

| LLM 1er token | LLM total | TTS 1er octet | Total | **Perçu (premier son)** |
|---|---|---|---|---|
| 920 ms | 1,5 s | 537 ms | 1,6 s | **1,7 s** |

En cours, pas encore sur main : la PR #26 (« orchestre » d'agents autour de l'agent vocal : pré-chargement pendant que l'appelant parle, réflexe pour « oui c'est bon », phrase d'attente). Mesure de son auteur sur 13 appels : premier son médian **0,7 s** (contre 4,2 s), 11/13 réussis, 0,082 $ par appel (sur Sonnet).

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
- **Réglages de sortie ElevenLabs** (format, speaker boost, normalisation) et préchauffage de connexion : aucun gain mesurable (~155 ms au premier octet quoi qu'on fasse).

**Reste à corriger (vu par le juge)**
- L'agent **promet des transmissions qu'il n'enregistre pas** (« je préviens Samia », « je transmets à Martine ») : c'est le défaut le plus fréquent dans les notes du juge.
- Il annonce parfois la date de péremption la plus proche en stock comme si c'était celle du lot livré.
- Il salue « Bonsoir » à 5 h 30.
- Il lit des chiffres trop précis à l'oral (« 41,11 kilos »).

La PR #30 traite les deux premiers points.
