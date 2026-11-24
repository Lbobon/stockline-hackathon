# Spec — Radar de demande: the order that writes itself

Owners:
- **merciful-anchor (Denis):** backend + AI (prediction, events, parallel calls, live route).
- **nibbling-cattail (Loric):** the whole UI.

The two build in parallel against the contract below.

## Pitch (one line)

« On sait ce que vos clients vont commander demain avant qu'ils le sachent : l'ERP prédit chaque commande (rythme d'achat + événements : match de l'OM, mariages, froid), des agents appellent tous les clients en parallèle, et la tournée se construit en direct à chaque « oui ». »

## Story the demo tells (≈ 90 s)

1. **Radar** (Tuesday 24/11, 05:30, for Wednesday 25/11): a map of the clients. The 19 who have already ordered are solid pins. About 24 predicted clients are "ghost" pins, each with its predicted basket and one reason line: « OM–PSG demain soir : +40 % broches », « mariage samedi : 2 agneaux entiers à commander avant 14 h ». An events strip sits on top.
2. **« Lancer les appels »**: about 24 agents call at the same time. Each ghost pin rings, and a small call card shows the live transcript (agent / client).
3. **Each answer**: Jev classifies it in about 300 ms (oui / modifie / non / rappeler).
   - A "oui" turns the ghost into a solid pin, the order is created through the order checks (finalize_order), and **the route redraws immediately** (re-optimized with the new stop).
   - A "non" fades the pin.
4. **End**: the final route per truck. Totals: calls, orders, € of revenue, extra € from the events (uplift), km.
5. **Morning (optional)**: drivers confirm deliveries ("livré"), and the stops tick off one by one. No live truck tracking.

## What is real and what is simulated

- **Real:**
  - the prediction: the client's rhythm on that weekday over the last 12 weeks, plus their usual basket;
  - the event boosts, applied by client segment;
  - the order checks, order creation and route optimization (`services/optimize.py`);
  - the agent's side of each call (`run_agent`, voice rules).
- **Simulated:**
  - the clients answering, played by a model from each persona card in `data/context/clients/*.md`, as in the sim;
  - the events calendar (added data);
  - driver delivery confirmations (a button, or an auto-replay).

## Events (« commandes spéciales »)

New data in `apps/backend/app/radar_events.json`, committed (the data is fictional). Since replaced by the ERP calendar table (`calendar_seed.json`, `services/demand.py`: uplift measured from history):

| id | date | title | targets | boost |
|---|---|---|---|---|
| `om-psg` | 25/11 21:00 | OM – PSG au Vélodrome | snacking, kebabs | broches ×1.4, frites ×1.3, pains ×1.3 |
| `froid` | 25–27/11 | Vague de froid (4 °C) | restaurants | collier, souris d'agneau (tajine, couscous) ×1.25 |
| `mariages` | 28/11 | Mariages du week-end | traiteurs | épaules, gigots ×1.5; + whole lambs (special order, carcasses the day before before 14:00) |
| `marche-noel` | 27/11–24/12 | Marché de Noël du Vieux-Port | centre (13001-13002) | merguez, brochettes ×1.3 |

Each event has a `pitch` sentence that the agent can say (« avec le match demain soir, je vous mets 2 broches de plus ? »).

## API contract (camelCase JSON)

### Backend (`/api`, port 4000)

`GET /api/radar/events?from=YYYY-MM-DD&to=YYYY-MM-DD`
```json
[{ "id": "om-psg", "date": "2026-11-25", "time": "21:00", "title": "OM – PSG au Vélodrome",
   "kind": "sport|weather|wedding|local", "segments": ["snacking"], "pitch": "…",
   "boosts": [{ "productId": "prd_0043", "productName": "Broche kebab 10 kg", "factor": 1.4 }] }]
```

`GET /api/radar/forecast?date=2026-11-25` (default: business tomorrow)
```json
{ "date": "2026-11-25", "depot": { "lat": 43.333, "lng": 5.387, "name": "MIN des Arnavaux" },
  "events": [ "…events above…" ],
  "clients": [{
    "clientId": "cli_0002", "name": "Istanbul Grill (3 snacks)", "type": "restaurant", "city": "Marseille 3e",
    "lat": 43.31, "lng": 5.38, "contactName": "Murat Demir",
    "status": "ordered | upsell | predicted",
    "addLines": [ "…upsell only: what the events add to the existing order (same line shape)…" ],
    "probability": 0.83, "existingOrderId": "ord_… (when ordered)",
    "lines": [{ "productId": "prd_0043", "productName": "Broche kebab veau-dinde 10 kg", "unit": "piece",
                "qty": 14, "baseQty": 10, "eventId": "om-psg" }],
    "totalHt": 1240.5, "upliftHt": 310.2,
    "reason": "Commande 9 mercredis sur 12 ; OM–PSG demain soir : +40 % broches",
    "specialRequirements": ["Certificat halal du lot sur le bon"]
  }],
  "summary": { "ordered": 19, "predicted": 17, "upsell": 9, "expectedHt": 30430.7, "upliftHt": 5469.6 } }
```

`GET /api/routing/optimize?date=2026-11-25`: already built (branch `denis/dispatch`). This is the **RoutePlan** shape:
```json
{ "date": "…", "depot": {"lat","lng","name"},
  "optimized": [{ "runId": "run_…", "route": "Tournée Nord", "driver": "Kamel Djebbar", "departure": "06:00",
                  "km": 9.8, "hours": 1.2, "lastEta": "07:30", "loadKg": 357, "capacityKg": 1500,
                  "stops": [{ "orderId": "ord_…", "clientId": "cli_…", "lat": 0, "lng": 0, "eta": "06:42" }] }],
  "current": [ "…same shape, before optimization…" ],
  "totals": { "before": {"km","hours","costEur"}, "after": {"km","hours","costEur"}, "savedKm": 0, "savedEur": 0 },
  "costToServe": [{ "orderId","clientName","costEur","marginEur","flags":["below_minimum|costly"],"suggestion" }] }
```

### AI service (`/ai`, port 4100, through the Vite proxy)

`POST /ai/campaigns` with body `{ "date": "2026-11-25", "clientIds": ["…"] | null, "concurrency": 8 }`. `clientIds: null` means every `predicted` and `upsell` client. Returns `{ "id": "cmp_…" }`.

`GET /ai/campaigns/{id}`: a full snapshot. Use it on page load or after a reconnect.
```json
{ "id": "cmp_…", "date": "…", "status": "running | done",
  "calls": [{ "callId": "…", "clientId": "…", "clientName": "…", "lat": 0, "lng": 0,
              "status": "queued | ringing | talking | confirmed | modified | declined | callback | failed",
              "transcript": [{ "who": "agent | client", "text": "…" }],
              "decision": { "label": "oui | modifie | non | rappeler", "confidence": 0.92, "source": "jev | llm" },
              "orderId": "ord_…", "totalHt": 0, "lines": [] }],
  "route": "RoutePlan | null",
  "totals": { "called": 0, "confirmed": 0, "declined": 0, "callback": 0, "revenueHt": 0, "upliftHt": 0 } }
```

`GET /ai/campaigns/{id}/stream`: Server-Sent Events. Each `data:` line is one JSON event:
- `{ "type": "call_status", "callId", "clientId", "status" }`
- `{ "type": "line", "callId", "who": "agent|client", "text" }`: one spoken line, whole sentences
- `{ "type": "decision", "callId", "label", "confidence", "source" }`
- `{ "type": "order", "callId", "clientId", "orderId", "totalHt", "lines": [...] }`
- `{ "type": "route", "plan": RoutePlan }`: sent after every new order. Redraw the lines from `plan.optimized[].stops`.
- `{ "type": "totals", ... }`, then finally `{ "type": "done", "totals": {...} }`

`POST /ai/campaigns/{id}/deliveries`, with body `{ "speed": 10 }`: replays the drivers' "livré" confirmations along the final route (fake clock). Stream events: `{ "type": "delivered", "orderId", "clientId", "at": "06:42" }`.

## UI (nibbling-cattail): simple, no data swarm

One page, `/radar`:
- **Map (left):**
  - clients as pins (solid = ordered, ghost = predicted, faded = declined);
  - the depot;
  - route lines per truck, coloured by truck, which animate when a `route` event arrives.
- **Right panel:** a top strip of event chips. Under it, one call card per client while calling: name, status, the last 2 transcript lines, and the Jev decision as a pill. Then the final totals.
- **Header:** a « Lancer les appels » button and 4 KPIs (orders, €, extra € from the events, km).
- Ideas for the tools are in the message to nibbling-cattail.

## Done when

- `GET /api/radar/forecast` returns about 24 predicted clients for 25/11, each with a reason. At least the OM match and the weddings visibly change baskets. There's a backend test.
- A campaign of about 24 calls runs in parallel (concurrency 8) against simulated clients. Every call ends with a Jev decision. Every "oui" creates a finalized order and a `route` event with one more stop.
- The UI shows it live, from start to final route, in under 2 minutes.

## Status values (from the real data)

- `ordered`: already has an order for the date. Solid pin.
- `upsell`: has ordered, but an event calls for more: `addLines`. Examples: 2 whole lambs + legs for a caterer's weddings; extra fries and bread for a snack on match night. Solid pin with a "+" badge; it gets a call too.
- `predicted`: no order yet; `probability` = their weekday rhythm over 12 weeks. Ghost pin; it gets a call.
- An out-of-stock product is never proposed. Its usual substitute is used, with `note`, unless the client refuses substitutions.

A real sample (2026-11-25, seed data): `.plans/radar-forecast-sample.json`.
