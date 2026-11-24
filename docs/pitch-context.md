# Pitch context — Team 18, Médina Viandes (Stockline voice hackathon)

One self-contained brief for whoever builds our slides. Every number comes from a real run on our code; "Sources" at the end says which. **The slides must be in French.**

## 1. The event and what is judged

- **Challenge (Stockline):** in 24 h, build the most useful **voice agent** on top of a food wholesaler's ERP. We choose the problem and justify it.
- **Judging axes:**
  - **Smart:** knows the company, the clients, products and history; doesn't make mistakes; acts correctly in the ERP.
  - **Fast:** time from the end of the caller's speech to the first sound, plus the cost per call.
  - **Natural:** you forget it's a machine.
- **The jury calls our agent live,** on scenarios we don't know in advance.
- **Deliverables:**
  - the repo, which runs with `make setup`;
  - `DECISIONS.md`;
  - **slides (5 min max)**;
  - a **video (2 min max)**, which we generate with Higgsfield;
  - the **live demo**.
- **Prizes:** a main prize (smart + fast + natural + relevance of the problem), plus fastest, cheapest, most natural and smartest.
- **Organizer feedback (Moncef):** the proactive part (the agent calling clients itself) is very interesting, especially at scale, and demos well if presented well. **The extra money earned is what people will look at most;** other metrics are a bonus.

## 2. Our company: Médina Viandes (fictional data given to us)

- A halal meat wholesaler at the MIN des Arnavaux, Marseille.
- **95 clients:** halal butchers, kebab shops and snacks, Moroccan and Lebanese restaurants, wedding caterers, canteens, small supermarkets.
- **91 % of orders** come by phone (46 %) and WhatsApp (45 %).
- **The day:**
  - evening orders after service (20:00–02:00);
  - 03:00 is the cut-off for a same-morning delivery;
  - nobody answers at night (the office opens at 05:00);
  - trucks leave at 06:00.
- **Pain points:**
  - night orders wait or get lost, and everything is retyped by hand;
  - nobody has time to call clients before a football match or weekend weddings, so sales are missed;
  - delivery routes are ordered with no geography ("departure + 12 min per stop").

## 3. What we built (all running, open source in the repo)

1. **Night phone line (inbound voice agent).** It identifies the caller, offers "comme d'habitude", checks stock and the client's negotiated prices, reads the order back, and **confirms it itself only if the ERP's rules allow**. Otherwise it leaves a draft for Leïla (the order clerk) with the reason.
2. **Business rules live in the ERP, not in the prompt,** so the agent can't bypass them:
   - blocked account, credit limit;
   - 3 am cut-off; carcasses ordered the day before before 2 pm; canteens order 3 days ahead;
   - 120 € HT delivery minimum;
   - stock including incoming supplier deliveries;
   - unusual quantity, never-bought product;
   - a confirmed order the agent edits is re-checked.
3. **Shortage calls at 5:30.** Two products are out of stock. The agent finds the affected orders, reads each client's substitution preference, calls those who must be called, and offers a commercial gesture computed from the client's history.
4. **"L'orchestre": helper agents around the voice agent.**
   - The **Éclaireur** preloads the client file while the caller is still talking.
   - The **Réflexe** answers a plain "oui" without the big model.
   - The **Greffier** keeps the client file up to date.
   - The **Linguiste** handles language and tu/vous. A greeting in Arabic doesn't switch the call language.
   - The **Rapporteur** writes a call report.
5. **The "Ce matin" page** for Leïla, readable in one minute (what to check, call back, validate), and a **copilot mode**: Leïla takes the call, and the agent listens, fills in the order and whispers alerts.
6. **Route optimisation on real geography.** Today's routes go from **559 km to 352 km** (≈ 240 € and 4 h of driver time saved per day).

## 4. Unique feature #1: Radar de demande ("la commande qui s'écrit toute seule")

End to end:
1. **Forecast.** For each client delivered tomorrow, the ERP predicts the order from:
   - their weekday rhythm (how many of the last 12 Wednesdays they ordered);
   - their core basket;
   - their negotiated prices.

   An out-of-stock product is never proposed.
2. **City events boost the forecast:**
   - OM–PSG Wednesday night: +40 % kebab spits for snacks;
   - weekend weddings: 2 whole lambs for caterers;
   - a cold snap: tajine cuts;
   - the Christmas market.

   Clients who already ordered get an **add-on call**: "pour vos mariages de samedi, je vous ajoute deux agneaux ?"
3. **Prioritise:**
   - loyal clients first, then by expected net €, which is margin × probability − the truck's detour cost;
   - a client who costs more to deliver than they bring isn't called, and dock pickup is suggested instead.

   Tomorrow: 24 calls (20 loyal), 2 skipped.
4. **Call everyone in parallel.**
   - The real voice agent calls 24 clients at once and speaks first.
   - **Jev** (a fast decision model) classifies each answer in about 300 ms: yes / changes / no / call me back.
5. **Every "yes"** creates the order through the ERP's rules, re-optimises tomorrow's routes, and **the route redraws live on the map**.
6. **Proof:** on the last 8 Wednesdays, the radar foresaw **95 %** of orders, with revenue predicted **within 8 %**.

**Results** (3 full campaigns, 24 calls each):
- about **85 s** per campaign;
- **19 yes** on average;
- **≈ 12.5 k€ HT** of orders, of which **≈ 4.6 k€ thanks to events**;
- **≈ 2 800 € gained per campaign** (range 2 424–3 200 €), counting margin on the orders + route savings + sales-rep phone time saved (4 min per call at 25 €/h).

## 5. Unique feature #2: Le Garde-fou ("l'agent ne peut pas inventer un chiffre")

End to end:
1. The agent's text is cut into sentences. Each is checked as soon as it's complete, before it's spoken.
2. **Is there a fact to check?** A price, discount, quantity, stock, date or order status, including numbers said in words. Small talk ("Je regarde.") is spoken immediately.
3. **Evidence:** everything the ERP said during the call, namely tool results, facts the Éclaireur preloaded, the outbound call brief, and today's date.
4. **Jev decides in about 150 ms,** in one request with five questions:
   - is the sentence supported by the ERP?
   - does it contradict the ERP?
   - does it promise something nobody granted (a discount, free delivery, a guaranteed time)?
   - does a number differ from the ERP's?
   - which ERP fact is the source?

   This runs in parallel with speech synthesis, so a good sentence adds **0 ms**.
5. **Verdict:** ✓ verified, shown with its source; ? unverified; ✗ contradicted.
   - In block mode, a **strong ✗ is never spoken**: the agent re-says it from the ERP fact, as if it caught itself.
   - If Jev is slow, the sentence goes through, so there's never silence.

**Example (live, with the demo button "Provoquer une erreur", which forces one wrong figure, i.e. a *provoked* hallucination):**
- The agent was about to say « L'épaule d'agneau est à 8 euros 80 le kilo ».
- The ERP's client price is 11,80 €. Jev flagged it as contradicted in 196 ms.
- It was blocked and never heard. The agent said « … 11 euros 80 le kilo » instead.

**Numbers:**
- 27/27 correct on the labelled test set, **0 false blocks**;
- in simulation, 156 sentences checked, 0 wrongly blocked;
- first sound unchanged at 0.70 s with blocking on.

## 6. Performance and quality

- **Model:** Claude Haiku 5.5. Faster, about 10× cheaper and more accurate than Sonnet in our tests.
- **First sound:** **0.7 s** in simulation. Live, through speech-to-text, it's about 1.9 s (a short filler line first).
- **Cost:** ≈ **1 cent per call**.
- **Tests:**
  - a simulator plays each client with a model from their persona card, against the real agent and ERP, and a judge grades the call;
  - business scenarios: **39/40**;
  - randomly generated calls (random client × everyday situation, so we don't over-fit): **27/30**.
- **Clean-laptop test:** a fresh clone plus `make setup` works, including a full campaign.

## 7. What is real vs. simulated (we say it on stage)

- **Real:** the forecast, the prioritisation, the voice agent and its tools, the ERP rules, order writing, route optimisation, the Garde-fou.
- **Simulated:**
  - in Radar campaigns, the **clients who answer** are played by a model from their persona cards;
  - the events calendar;
  - **no real phone number is called.**
- Delivery confirmations by drivers are a replay.

## 8. Live demo plan (what the slides should set up)

1. `/radar`: click « Lancer les appels ». 24 pins ring, the feed shows each conversation, and the route grows to its final shape. End on the € figure.
2. `/voice`: a jury member calls the agent as any client (push-to-talk).
3. **Garde-fou:** « Provoquer une erreur », ask a price, and the wrong figure is caught and corrected.

## Sources

- **Campaign numbers:** `python -m sim.radar_stats --runs 3`.
- **Backtest:** `GET /api/radar/backtest?weeks=8`.
- **Routes:** `GET /api/routing/optimize?date=2026-11-24`.
- **Garde-fou:** `python -m app.guard.eval`, and the sim with `--guard`.
- **Tests:** `python -m sim --repeat 2` and `--generic 30 --seed 7`.

All the details are in `DECISIONS.md` and `.plans/`.
