# Voice prototype: Twilio + ElevenLabs

A first working version of part 3 of [idea.md](idea.md): the agent phones a restaurant, takes the order in French, and the order goes into an approval queue.

```
phone ⇄ Twilio ⇄ (media stream, mu-law 8 kHz) ⇄ server/index.mjs ⇄ (websocket) ⇄ ElevenLabs agent
                                                       │
                                                       └─ submit_order tool → server/orders.json → web/ approval page
```

## Who owns what

| Piece | Path | Owner |
|---|---|---|
| Bridge server, `/call`, order storage | `server/` | Loric (nibbling-cattail) |
| ElevenLabs agent: French prompt and the script that creates it | `agent/` | Denis (empathic-ruff) |
| Approval queue page | `web/index.html` | Daphne (purring-flask) |
| Sample restaurants | `data/sample/customers.json` | Yuvaan (exotic-papaya) |

## The contract between the pieces

**Client tool** on the ElevenLabs agent, handled by the server:

```json
{ "name": "submit_order",
  "parameters": {
    "restaurant": "string",
    "lines": [{ "product": "string", "quantity": "number", "unit": "string" }],
    "notes": "string" } }
```

**Dynamic variables** the server sends when the call starts. By default they come from the ERP's real clients (`data/seed/`, git-ignored; `usual_order` = median of each client's last 10 orders, `todays_offer` = a lot expiring within 3 days, -15 % only if it keeps a 5 % margin). Set `CUSTOMERS_FILE` or `USE_ERP_CUSTOMERS=0` to use `data/sample/customers.json` instead; `ERP_PHONES=1` also fills each client's phone (off by default so nobody gets dialled by accident): `{{restaurant_name}}`, `{{contact_name}}`, `{{usual_order}}`, `{{todays_offer}}`.

**HTTP API**
- `POST /call` with `{"to": "+336...", "customer_id": "cust_001"}`: Twilio dials the number. `to` defaults to the customer's phone.
- `GET /api/orders`: newest first, `[{id, status: "pending"|"approved"|"rejected", restaurant, lines, notes, transcript: [{role: "agent"|"client", text}], created_at}]`
- `POST /api/orders/:id/approve` and `/reject`
- `GET /api/customers`
- `/` serves `web/`, or a bare fallback list until `web/index.html` exists.
- `/twiml`: set this as the Twilio number's voice webhook to take inbound calls too.

**Agent audio settings:** input (ASR) and output (TTS) are both **μ-law 8000 Hz** (`ulaw_8000`), which is what Twilio sends and plays. With anything else the call is silent or just noise.

## Run it

1. `npm install` (Node 22 or newer)
2. Copy `.env.example` to `.env` and fill in the Twilio and ElevenLabs values.
3. Make the server reachable from the internet: `ngrok http 8080`, then put the `https://…` address in `PUBLIC_URL`.
4. Create the agent: `node agent/create-agent.mjs` once it lands, or do it by hand in the ElevenLabs dashboard with the settings above, then put its id in `ELEVENLABS_AGENT_ID`.
5. `npm start`
6. Place a call:
   ```bash
   curl -X POST localhost:8080/call -H 'content-type: application/json' -d '{"to":"+336XXXXXXXX","customer_id":"cust_001"}'
   ```
7. Open http://localhost:8080 to see the order arrive and approve it.

`npm test` runs the whole bridge against a fake Twilio and a fake ElevenLabs, with no accounts needed.

## Twilio gotchas

- **Trial account:** it can only call numbers you verified in the console, and it plays a short trial message before the agent speaks.
- **Calling French numbers:** turn on France under Voice → Settings → Geo permissions.
- **The `From` number:** a French Twilio number needs a regulatory bundle, which can take days to approve. A US number can call France straight away.
