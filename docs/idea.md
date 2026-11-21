# The idea: an AI phone sales rep

**One line:** an AI agent that calls restaurants in French, takes their order, sells the right products, and puts everything into Stockline. It does the job of the people wholesalers pay to phone customers every afternoon.

## Why this, why Stockline
- On Stockline's site, phone calls only come **in**: they turn a voicemail into an order. Nothing on the site says an agent **calls customers out**. *(Confirm with them at kickoff that they aren't already building it.)*
- It extends what they do instead of competing with it. Stockline captures incoming orders. We go and **get the orders that wouldn't have come in.**
- Phone ordering is what chefs actually do: they're in the kitchen and won't type. We go with the habit instead of fighting it.
- It reuses Stockline's own idea: the agent proposes, **a person approves**.

## The five parts

### 1. Call planner: who to call, and when
- Restaurants whose usual order hasn't arrived by the cutoff
- Accounts going quiet (churn risk)
- Each chef's best time to call (e.g. 15h–17h, between lunch and dinner service)
- Calls ranked by expected sales

### 2. Pre-call brief: what to say
For each customer, before dialling:
- The basket they'll probably order ("the usual")
- What's short today, and the substitute that fits this customer (a top restaurant won't accept farmed sea bass)
- What to push: short-dated stock, high-margin items, today's arrivals
- Price changes since their last order

### 3. The call
- A natural French voice. It copes with kitchen noise, interruptions and "call me back in 10 minutes"
- Understands trade jargon: 3/5, 30/40, colis, plateau, "comme d'hab" (the usual)
- Upsells naturally: *"J'ai de la lotte superbe arrivée ce matin, -15 % pour vous"*
- Reads the order back to confirm it before hanging up
- Says up front that it's an AI

### 4. Limits + handover to a human
- A maximum discount per customer and product, set from the margin floor. It never goes below it
- A complaint, an unhappy customer or a hard negotiation gets passed to a human salesperson with a summary

### 5. After the call
- The order lines go into the **approval queue** (nothing reaches the ERP without a person approving it)
- Call summary and transcript
- Signals: a competitor price mentioned ("Metro does it for €X"), dissatisfaction, churn risk
- Follow-ups: WhatsApp the price list, call back at 16:30

**On top:** a small results dashboard. Calls made, orders taken, extra sales from upselling, customers won back, which offers actually sell.

## Demo (3 minutes, live)
1. The dashboard shows today's call list and the brief for one restaurant.
2. The agent **calls a teammate's phone live**. The teammate plays the chef: orders "the usual", hesitates, takes the upsell.
3. A few seconds later the order shows up in the approval queue with the call summary. We approve it. Done.

## Scope for 24h
| Must have (P0) | Nice to have (P1) |
|---|---|
| Call list + pre-call brief from the data | Discount limits with the margin floor |
| One working live outbound call in French | Handover to a human |
| Order lines → approval queue | Results dashboard |
| Post-call summary | Competitor and churn signals |

## Risks
- **Voice delay and French voice quality.** Pick and test the voice stack on Monday night, not Tuesday.
- **Phone line setup** (number, credit, country restrictions). Get it working before anything else.
- **Live demo failing on stage.** Keep a backup: a call run from the browser, plus a recorded video.
- **Compliance:** the agent says it's an AI. I believe the EU AI Act requires this from August 2026; check before putting it on a slide. These are B2B calls to existing customers.

## Questions for Stockline at kickoff
- Are you already building outbound calls?
- How do your customers' salespeople phone restaurants today: who, when, how many calls?
- What real data do we get (customers, order history, stock, prices)?
