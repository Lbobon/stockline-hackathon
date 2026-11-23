# Demo script — Médina Viandes, team 18

Organizer feedback (Moncef): the proactive part is the most interesting, especially at scale, and it demos well if presented well. **The extra money is what people will watch most.** So every scene ends on a € figure.

The figures below come from the seed data (Tuesday 24/11, 05:30, deliveries on Wednesday 25/11). Rerun before recording, because they move a little from run to run. Run `make reset` before each take.

## The video (2 min max, one real flow end to end)

| Time | Screen | Voice-over (FR) | € on screen |
|---|---|---|---|
| 0:00–0:12 | Médina Viandes, MIN des Arnavaux; the order book | « Un grossiste halal à Marseille : 95 clients, des commandes au téléphone jour et nuit. Aujourd'hui, tout est retapé à la main. » | — |
| 0:12–0:30 | `/radar` before the campaign: the map, 19 solid pins, 26 to call, event chips (⚽ OM–PSG, 💍 mariages, ❄️ froid) | « L'ERP sait déjà ce que vos clients vont commander demain : leur rythme, leur panier, et ce qui se passe en ville. Le match de mercredi soir ? Les snacks vont manquer de broches. » | Expected revenue + « +4,6 k€ grâce aux événements » |
| 0:30–1:05 | « Lancer les appels »: 26 pins ring, call cards scroll with live transcripts, Jev pills (oui 0,96), the route redraws on each yes | « 26 appels en même temps. Chaque oui crée la commande, avec nos garde-fous, et la tournée se recalcule en direct. » | « € gagnés » climbing |
| 1:05–1:20 | Zoom on one call card: Réceptions Agnès, add-on call: « pour vos mariages de samedi, je vous ajoute 39 kg de gigot ? » « oui » | « Même les clients qui ont déjà commandé : l'agent leur propose ce que l'événement va leur faire manquer. » | that call's margin |
| 1:20–1:40 | End of campaign. Big number, then the before/after routes toggle | « 85 secondes. 22 commandes. Et des tournées qui font 200 km de moins. » | **€ gagnés = margin + routes + staff time** |
| 1:40–2:00 | Backtest line + the inbound agent taking a call in 1 s | « Ce n'est pas un pari : sur les 8 derniers mercredis, le radar a vu venir 95 % des commandes, CA à 8 % près. Et la nuit, le même agent prend les appels entrants. » | — |

## The 5-min pitch (slides + live)

1. **Le problème (30 s):** everything happens by phone; nobody at night; trucks leave at 6 am; orders are retyped. Cost: lost night orders, missed upsell, inefficient routes.
2. **La démo live (2 min 30):**
   - Radar campaign, as in the video (it runs in about 85 s).
   - Then a jury member calls the agent (inbound), playing any client.
3. **Les chiffres (45 s), one slide, money first:**
   - € gagnés per campaign (margin + routes + staff time);
   - +4.6k € revenue from the events;
   - routes 559 → 352 km/day (≈ 240 €/day);
   - backtest 95 %;
   - inbound: first sound 0.7 s, about 1 ct per call, 25–26/28 simulated calls passed.
4. **Pourquoi on peut nous faire confiance (45 s):** the rules live in the ERP, not the prompt (blocked account, credit limit, cut-off, unusual quantity). The Garde-fou checks every figure the agent says. Draft orders go to Leïla.
5. **Ce qui est simulé (15 s, say it):** in the campaign, the clients who answer are played by a model from their persona cards, and the events calendar is added data. The forecast, the agent, the ERP writes and the routes are real.

## Safety nets for the live demo

- The `/radar` page has an offline mode (bundled sample + local replay) if the AI server is down.
- Do `make reset` before going on stage, and run one rehearsal campaign 10 minutes before.
- Keep GUARD_MODE on shadow (never block) during the demo.
- Backup: the recorded video.

## Inbound lines the jury might try (all tested in the sim)

« comme d'habitude », a blocked client, an order past 3 am, « vous me faites un geste ? », a complaint about weight, « il est où le camion ? », five clients called El Baraka, an English-speaking chef, a client who changes his mind.
