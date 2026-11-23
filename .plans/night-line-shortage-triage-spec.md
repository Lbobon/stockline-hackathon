# Spec — Médina Viandes: night line + shortage triage

Team 18 · 4 devs · ~15 h total (pitch, video and slides included). Target: main prize (smart, fast and natural, balanced).

## 1. Problem and pitch

- Médina Viandes has **no night line**. Snacks and restaurants send orders after service (20:00–02:00) by WhatsApp or voice note. Nobody handles them before Leïla opens at 05:00, but same-day orders must arrive **before 03:00**.
- At 05:30 (the pack's "now"), two products are at zero: `prd_0020` veal escalope and `prd_0044` 20 kg kebab skewer. The playbook says to call clients who refuse substitutions **before the truck leaves at 06:00**. Nobody has time to do it.
- What the agent changes:
  1. **Night line (inbound, 20:00–05:00)**: answers calls, confirms orders itself when it is safe, and leaves everything else ready for the morning.
  2. **Shortage triage (proactive, 05:30)**: finds the affected orders, decides per client, calls the clients who must be called, and writes the result into the ERP.
  3. **"Ce matin" page**: Leïla sees in 10 seconds what happened in the night and what she must do.
- The jury calls the agent live with unknown scenarios. The inbound core is the priority. Shortage triage is the showcase for the video and the demo.

## 2. Scope

### In
- Inbound night line for **clients** (not suppliers, not staff).
- Shortage triage on today's real seed data.
- New "Ce matin" page and a live call view.
- Model routing (Haiku by default, Sonnet on escalation).
- History-based retention offers.
- Naturalness: register from the client card, STT hints, call memory, barge-in.

### Out
- Real telephony (we use the Voix page; for outbound calls the agent speaks first).
- Supplier calls, collections, seasonal campaign, lost-client backtest (add the backtest only if time is left over, as a slide).
- Redesigning the existing ERP pages.

## 3. Inbound night line

### 3.1 Call flow
1. **Identify the caller** by name, client code or contact name. Preload the client card, the last orders, the open complaints and the account status **in parallel at identification**, not turn by turn.
2. **Greet in the client's register**, read from the card (tu or vous, pace, small talk if the card says "prend des nouvelles"). Never use fake family terms such as "mon frère".
3. **Call memory**: if `save_context` holds a recent personal fact (a wedding, a closing day), mention it briefly.
4. Handle the request:
   - **Order**: offer "comme d'habitude" from the last orders → set quantities → check stock and today's shortages → apply the cut-off rules → read the order back (products, quantities **spoken aloud**, delivery day) → confirm or draft (see 3.2).
   - **Stock or price question**: client-specific price (`get_price`). For carcasses, quote a weight range ("16 to 20 kg, about X €, billed at the real weight").
   - **Complaint**: log a client event linked to the order. Never promise a credit note over 150 € HT ("I'll pass it on to Rachid").
   - **Anything else**: take a message, "Leïla will call you back at 5am".
5. End the call: short closing, then write a **call summary** (used by the "Ce matin" page) and save the memorable facts with `save_context`.

### 3.2 Confirm or draft (agent decides, deterministic rules first)
The agent **confirms** the order itself unless one of these applies. If one applies, it creates a **draft**, gives the reason in plain words, and puts the order on the morning list.

| Gate | Behaviour |
|---|---|
| Account blocked, or credit ceiling exceeded | No order confirmed. Take a message for Martine (accounting). Promise nothing. |
| Low confidence on a line (ambiguous product or quantity, recap not explicitly confirmed) | Draft. |
| Unusual order (quantity > 3× the client's average for that product, or a product the client never buys) | Draft. |
| After 03:00 → next-day delivery; carcasses and special cuts after 14:00 the day before → date moves | Move the date and **tell the client**. Confirm if no other gate applies. |

The gates are **code** (a backend service or a composite tool), not prompt text. Business rules live in `apps/backend/app/services/`.

### 3.3 Guardrail reasons
Every refusal or draft carries a `reason` code plus a plain-French sentence ("Compte bloqué par la compta", "Commande 4× plus grosse que d'habitude"). The live call view and the "Ce matin" page show it.

## 4. Shortage triage (showcase)

### 4.1 Data (seed, verified)
| Order | Client | Status / delivery | Shortage | Card preference | Decision |
|---|---|---|---|---|---|
| `ord_029604` | `cli_0002` Istanbul Grill (3 snacks) | prepared, **24/11 (truck at 06:00)** | 20 kg skewer | not stated ("si ton prix bouge je regarde ailleurs") | **Call first** (unknown = call) |
| `ord_029511` | `cli_0041` Centre social Louise Michel | draft, 25/11 | — check line | **Refuses substitutions** | **Call** |
| `ord_029586` | `cli_0066` Chez Karine | draft, 25/11 | — check line | Accepts equivalent substitutes if warned | **Auto-substitute + notify** |

Substitutes: veal escalope → turkey escalope. 20 kg skewer → 2 × 10 kg skewer. (Confirm the product ids with `find_alternatives`.)

### 4.2 Logic
1. Find the orders with a delivery date ≥ today that contain a product with zero available stock.
2. Per order, read the card preference: `refuses` → call · `accepts` → auto-substitute and notify · **unknown → call** (playbook: "jamais sans accord").
3. Sort the calls by urgency: today's truck first.
4. Each call: the agent speaks first, explains, and offers the substitute. If the client refuses, it goes down the **retention ladder** (section 5).
5. Write the result back: changed lines, an order note, and a client event "rupture — <outcome>".

### 4.3 UX
- A "Ruptures du jour" block on the "Ce matin" page: one row per affected order with the decision (call / auto / no substitute) and the reason from the card.
- One button, **"Lancer les appels"**: it runs the calls one after another on the Voix page. Each row updates live (to call → in call → solved, with the outcome).

## 5. Retention offers (when a substitute is refused)

Order of offers. The agent goes to the next one only if the previous one is refused:
1. **Delivery tomorrow** of the missing product (if a purchase order or the main supplier can cover it).
2. **Partial quantity or other caliber** (for example 2 × 10 kg instead of 1 × 20 kg).
3. **Commercial gesture**: a discount on the next order of that product, or a credit.

### 5.1 Value of the gesture
- **Known client**: computed from the history. Inputs: revenue over 12 months, order frequency, years as a client, payment record (overdue invoices, average delay), open complaints. Output: an amount in €, with an explanation ("12 %, because 3 years loyal, always pays on time, ~4 k€/month").
- **New client** (or too little history): a **fixed ladder** that protects the margin, for example a small % on the next order, then free delivery or priority.
- Selling **below cost is allowed** when the client value justifies it. The UI must show it explicitly ("perte de X € sur cette ligne").
- Hard rules:
  - **Over 150 € HT** → the agent *proposes* the gesture and escalates to Rachid. It does not grant it.
  - **Clients with overdue invoices** → no money offers, only service offers (tomorrow delivery, priority).
  - Every offer is **explainable** and logged as a client event with its reason.
- This calculation is backend code (a pure function plus a test), not an LLM decision. The LLM only phrases the offer.

## 6. Models, speed and cost

- **Routing**: Haiku 5.5 by default. Escalate to Sonnet 5.5 on hard turns: ambiguous product, complaint, blocked account, retention negotiation, a refused substitute. Use Jev for yes/no and for picking a product among candidates.
- **Latency target**: **< 1.2 s p50** from end of speech to first sound, measured per scenario.
- Techniques (in this order):
  1. Preload the client context when the caller is identified (in parallel).
  2. Run independent tools in parallel.
  3. Speak a short filler line while tools run ("je regarde ça…").
  4. Deterministic fast paths: yes/no, quantities, units, dates, spoken numbers.
  5. Composite tools (search + stock + client price in one call).
- Log latency, model calls and **cost per call** for every scenario in `DECISIONS.md`.

## 7. Naturalness

- Register taken from the "Comment il/elle parle" section and the CRM notes on the card.
- **STT hints**: add Arabic and Turkish words mixed into French ("wallah", "hamdoulah", "inchallah", "abi", product nicknames such as "la grosse broche" or "broche de 20") to the Soniox vocabulary (`services/vocabulary.py`). Test with mixed speech.
- **Call memory**: `save_context` at the end of the call; read it back at the start of the next call.
- **Barge-in**: already in the kit (`voice_session.py` `interrupt`). Check that it works with the chosen STT and TTS, and that the order state stays consistent after an interruption.
- Short sentences, quantities and prices spoken aloud ("douze kilos", "dix-huit euros cinquante"), a read-back before any confirmation.

## 8. UI (for a 50-year-old who is not fluent in IT)

General rules: large text, plain French with no ERP jargon, **one verb per action** ("Rappeler", "Valider", "Vérifier"), color shows urgency only (red = before the truck leaves, orange = this morning, grey = info). Use only components from `apps/web/src/ui/`; add new simple components there if needed.

### 8.1 "Ce matin" page (new)
- At the top: 3 to 5 figures ("12 appels cette nuit · 8 commandes confirmées · 3 à vérifier · 2 ruptures").
- **"À faire"**: one card per action, most urgent first: the drafts to check (with the reason), the callbacks to make, the complaints, the gestures waiting for Rachid.
- **"Ruptures du jour"** (section 4.3).
- **"Cette nuit"**: one line per call (time, client, one-sentence summary, result).
- **Spoken briefing**: in command mode, "qu'est-ce qui s'est passé cette nuit ?" → the agent reads the same summary aloud.

### 8.2 Live call view (cut first if behind)
- During a call: who is calling (name and type), what the agent is doing now in plain words ("vérifie le stock d'agneau"), the order being built, and any guardrail with its reason.

## 9. Team split and file ownership

| Dev | Track | Main files |
|---|---|---|
| 1 — Smart | Prompts, client-card register, confirm gates, composite tools, call memory, retention calculation | `apps/ai/app/agent/prompts.py`, `apps/ai/app/tools/*`, `apps/backend/app/services/` (gates, retention) |
| 2 — Fast | Model routing, preload, parallel tools, fillers, fast paths, STT hints, latency and cost measurement | `apps/ai/app/agent/loop.py`, `apps/ai/app/clients/*`, `apps/ai/app/services/voice_session.py`, `vocabulary.py` |
| 3 — Showcase + UI | Shortage triage (backend + outbound flow), "Ce matin" page, live call view | new backend route + service, `apps/web/src/pages/` (new page), `packages/shared/src/` |
| 4 — Tests + pitch | Scenarios, test script, DECISIONS.md, slides, video, plays the jury all night | `apps/ai/tests/`, `DECISIONS.md`, slides |

Interfaces to agree on in the first hour: the call summary format (written by Dev 1/2, read by Dev 3), the guardrail `reason` codes, and the retention function signature.

## 10. Tests

- **8 to 10 scripted text scenarios**, run through the agent loop (pytest or `make chat`). Each one asserts the ERP end state and logs latency and cost. Minimum set:
  1. "Comme d'habitude" from Istanbul Grill at 23:00 → confirmed, right quantities.
  2. Ambiguous product ("des escalopes") → clarification, then the right product.
  3. A product in shortage, from a client who refuses substitutions → no silent substitution.
  4. A blocked client orders → nothing confirmed, message for Martine.
  5. An order at 03:30 → delivery moves to tomorrow, and the client is told.
  6. An unusual quantity (5× the average) → draft with a reason.
  7. A complaint with a credit request over 150 € → logged and escalated, nothing promised.
  8. The client changes their mind mid-call → the order state is right.
  9. Shortage triage end to end → 2 calls + 1 auto-substitution, outcomes written.
  10. A refused substitute → the retention ladder, an offer within the rules, logged.
- **Live voice spot check** of every scenario before each merge to `main`. `make reset` before each run.

## 11. Plan (~15 h)

| Hours | Milestone |
|---|---|
| H0–H1 | Interfaces agreed, scenarios written, `DECISIONS.md` §1 filled in |
| H1–H5 | Inbound order path end to end with gates (ugly but complete); triage backend; "Ce matin" page skeleton; routing + measurement |
| H5–H9 | Retention offers; outbound triage calls; summary + briefing; latency work |
| H9–H12 | Naturalness, live call view, bug fixes from the scenario runs |
| H12–H15 | Code freeze, video, slides, demo rehearsal |

**Cut order if behind at H10**: live call view → history-based retention (replace it with the fixed ladder for everyone) → Haiku routing (Sonnet only). Never cut: the inbound order path with its gates, shortage triage, the "Ce matin" page.

## 12. Open points and risks

- Check the exact shortage lines in `ord_029511` and `ord_029586`, and whether the turkey escalope and the 10 kg skewer are in stock.
- "Confirms orders itself" plus Haiku is a risk: a wrong confirmed order is worse than a draft. The gates must be code, and the scenarios must cover them.
- Below-cost gestures are allowed: the demo must show the explanation, or the jury will read it as a bug.
- The jury tests at demo time: whatever routing is active then is what they get. Make sure escalation to Sonnet actually triggers on the hard cases.
- The seed "now" is 05:30, so the inbound night line runs in the demo after the 03:00 cut-off. Decide whether the demo uses a simulated clock (for example `meta.today` with a 23:00 time) so that same-day ordering can be shown.
