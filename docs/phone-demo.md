# Phone demo: the agent calls a real phone

The PC plays the wholesaler (Médina Viandes), a real phone plays the restaurant. Press **Faire sonner** on the PC, the
phone rings, you pick up and order out loud; the agent answers in your ear and the PC shows the whole call live
(transcript, agents, Garde-fou). No Twilio: the "phone" is a web page opened on the phone through an HTTPS tunnel.

## 1. Start the kit

```bash
./scripts/dev.sh          # web :5173, api :4000, ai :4100
```

On Windows set `PYTHONUTF8=1` (Git Bash: `export PYTHONUTF8=1 PYTHONIOENCODING=utf-8`) before starting, or a log line
with a non-ASCII character can crash with a `charmap` error. The AI service needs its usual keys in `.env`.

## 2. Open a tunnel to the web app

The phone needs HTTPS for the microphone, so expose Vite with a free Cloudflare quick tunnel (no account needed):

```bash
# install once: winget install --id Cloudflare.cloudflared   (macOS: brew install cloudflared)
cloudflared tunnel --url http://localhost:5173
```

It prints a `https://<random-words>.trycloudflare.com` address. Vite already allows `*.trycloudflare.com`
(`apps/web/vite.config.ts`). If `cloudflared` is "not recognized" right after installing, open a new terminal or call it
by its full path (`C:\Program Files (x86)\cloudflared\cloudflared.exe`). The address changes on every start.

The tunnel makes the ERP reachable from the internet while it runs: stop it (Ctrl+C) after the demo.

## 3. On the phone

Open `https://<random-words>.trycloudflare.com/phone`, tap **Activer la ligne** and allow the microphone. Keep the page
open with the screen on. It shows "En attente d'appel" with a green dot once the line is up.

## 4. On the PC

Open `http://localhost:5173` → **Voix** → **Téléphone**. The header shows "1 téléphone en attente". Pick the client the
phone plays (clients flagged by the Radar come first: the agent then opens with the Radar's suggestion), press
**Faire sonner**.

The phone rings → **Accepter**. The agent speaks first, then it's hands-free: talk, pause, it answers; you can cut it off.
**Raccrocher** ends the call and the PC shows the call report.

Tips: earphones or the phone at your ear work best (the loudspeaker can trigger the barge-in). The PC plays no audio:
it only mirrors the call.

## How it works

- `WS /ai/voice/phone`: the phone's line (receives `ring` / `cancel`, sends `answered` / `declined` / `hungup`).
- `WS /ai/voice/watch`: the PC (sends `ring` / `cancel`, receives every message of the call plus the line status).
- `WS /ai/voice?line=phone`: the call itself, a normal hands-free voice session whose messages are mirrored to the
  watchers. `start.outboundClientId` makes the agent call that client to take its order
  (`agent/outbound.py: order_call_brief`, using the Radar brief when the client is in today's forecast).
- In-memory pub/sub in `apps/ai/app/services/phone_line.py` (one AI process, demo only).
