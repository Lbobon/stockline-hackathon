// Voice prototype: Twilio phone call <-> ElevenLabs conversational agent.
//
//   POST /call {to, customer_id}  -> Twilio dials `to`, the call's audio streams to /media-stream
//   POST /twiml                   -> TwiML for inbound calls (set as the Twilio number's voice webhook)
//   WS   /media-stream            -> bridges Twilio media (mu-law 8 kHz) to the ElevenLabs agent websocket
//   GET  /api/orders              -> approval queue; POST /api/orders/:id/approve | /reject
//   GET  /                        -> web/index.html (approval page), or a bare fallback list
//
// The ElevenLabs agent must use ulaw_8000 for both input and output audio, and have a client
// tool `submit_order` (see docs/prototype.md). Run: node --env-file=../.env server/index.mjs

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { WebSocketServer, WebSocket } from 'ws';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');

const env = process.env;
const PORT = Number(env.PORT || 8080);
const PUBLIC_URL = (env.PUBLIC_URL || `http://localhost:${PORT}`).replace(/\/$/, '');
const ORDERS_FILE = env.ORDERS_FILE || path.join(here, 'orders.json');
const CUSTOMERS_FILE = path.join(root, 'data', 'sample', 'customers.json');
const WEB_DIR = path.join(root, 'web');

// ---------- storage ----------

function loadOrders() {
  try { return JSON.parse(fs.readFileSync(ORDERS_FILE, 'utf8')); } catch { return []; }
}
function saveOrders(orders) {
  fs.writeFileSync(ORDERS_FILE, JSON.stringify(orders, null, 2));
}

const FALLBACK_CUSTOMERS = [{
  id: 'demo',
  restaurant_name: 'Le Petit Bouchon',
  contact_name: 'Marc',
  phone: '',
  usual_order: '2 colis de tomates grappe, 1 plateau de bar de ligne 3/5, 5 kg de beurre doux',
  todays_offer: 'lotte arrivée ce matin, -15 %',
}];

function loadCustomers() {
  try { return JSON.parse(fs.readFileSync(CUSTOMERS_FILE, 'utf8')); } catch { return FALLBACK_CUSTOMERS; }
}
function findCustomer(id) {
  const customers = loadCustomers();
  return customers.find((c) => c.id === id) || customers[0] || FALLBACK_CUSTOMERS[0];
}

// ---------- helpers ----------

const xmlEscape = (s) => String(s).replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' }[c]));

function streamTwiml(customerId) {
  const wsUrl = PUBLIC_URL.replace(/^http/, 'ws') + '/media-stream';
  return `<?xml version="1.0" encoding="UTF-8"?><Response><Connect><Stream url="${xmlEscape(wsUrl)}">` +
    `<Parameter name="customer_id" value="${xmlEscape(customerId || '')}"/></Stream></Connect></Response>`;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > 1e6) req.destroy(); });
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

function send(res, status, body, type = 'application/json') {
  res.writeHead(status, { 'Content-Type': type });
  res.end(type === 'application/json' ? JSON.stringify(body) : body);
}

async function startOutboundCall(to, customerId) {
  const sid = env.TWILIO_ACCOUNT_SID, token = env.TWILIO_AUTH_TOKEN, from = env.TWILIO_PHONE_NUMBER;
  if (!sid || !token || !from) throw new Error('TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER must be set');
  const form = new URLSearchParams({ To: to, From: from, Twiml: streamTwiml(customerId) });
  const r = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${sid}/Calls.json`, {
    method: 'POST',
    headers: { Authorization: 'Basic ' + Buffer.from(`${sid}:${token}`).toString('base64') },
    body: form,
  });
  const json = await r.json();
  if (!r.ok) throw new Error(`Twilio ${r.status}: ${json.message || JSON.stringify(json)}`);
  return json.sid;
}

async function elevenLabsUrl() {
  if (env.ELEVENLABS_WS_URL) return env.ELEVENLABS_WS_URL; // public agent, or a local fake for tests
  const agent = env.ELEVENLABS_AGENT_ID, key = env.ELEVENLABS_API_KEY;
  if (!agent || !key) throw new Error('ELEVENLABS_AGENT_ID and ELEVENLABS_API_KEY must be set');
  const r = await fetch(`https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=${encodeURIComponent(agent)}`, {
    headers: { 'xi-api-key': key },
  });
  if (!r.ok) throw new Error(`ElevenLabs signed URL ${r.status}: ${await r.text()}`);
  return (await r.json()).signed_url;
}

// ---------- HTTP ----------

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  try {
    if (req.method === 'POST' && url.pathname === '/call') {
      const body = JSON.parse((await readBody(req)) || '{}');
      const customer = findCustomer(body.customer_id);
      const to = body.to || customer.phone;
      if (!to) return send(res, 400, { error: 'give "to" (E.164, e.g. +336...) or a customer with a phone' });
      const callSid = await startOutboundCall(to, customer.id);
      console.log(`[call] dialling ${to} for ${customer.restaurant_name} (${callSid})`);
      return send(res, 200, { call_sid: callSid, customer_id: customer.id });
    }

    if (url.pathname === '/twiml') {
      return send(res, 200, streamTwiml(url.searchParams.get('customer_id') || ''), 'text/xml');
    }

    if (req.method === 'GET' && url.pathname === '/api/orders') {
      return send(res, 200, loadOrders().slice().reverse());
    }

    const m = url.pathname.match(/^\/api\/orders\/([\w-]+)\/(approve|reject)$/);
    if (req.method === 'POST' && m) {
      const orders = loadOrders();
      const order = orders.find((o) => o.id === m[1]);
      if (!order) return send(res, 404, { error: 'no such order' });
      order.status = m[2] === 'approve' ? 'approved' : 'rejected';
      order.decided_at = new Date().toISOString();
      saveOrders(orders);
      return send(res, 200, order);
    }

    if (req.method === 'GET' && url.pathname === '/api/customers') {
      return send(res, 200, loadCustomers());
    }

    if (req.method === 'GET') {
      const file = path.join(WEB_DIR, url.pathname === '/' ? 'index.html' : url.pathname);
      if (file.startsWith(WEB_DIR) && fs.existsSync(file) && fs.statSync(file).isFile()) {
        const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png' };
        return send(res, 200, fs.readFileSync(file), types[path.extname(file)] || 'application/octet-stream');
      }
      if (url.pathname === '/') return send(res, 200, FALLBACK_PAGE, 'text/html');
    }

    send(res, 404, { error: 'not found' });
  } catch (err) {
    console.error('[http]', err.message);
    send(res, 500, { error: err.message });
  }
});

// ---------- the bridge ----------

const wss = new WebSocketServer({ server, path: '/media-stream' });

wss.on('connection', (twilio) => {
  let streamSid = null, callSid = null, customer = null;
  let el = null;
  const pending = []; // caller audio that arrives before ElevenLabs is open
  const transcript = [];
  const log = (...a) => console.log(`[${callSid || 'stream'}]`, ...a);

  const toTwilio = (msg) => { if (twilio.readyState === WebSocket.OPEN) twilio.send(JSON.stringify(msg)); };
  const toEl = (msg) => {
    if (el && el.readyState === WebSocket.OPEN) el.send(JSON.stringify(msg));
    else if (msg.user_audio_chunk) pending.push(msg);
  };

  async function openElevenLabs() {
    el = new WebSocket(await elevenLabsUrl());
    el.on('open', () => {
      log('ElevenLabs connected');
      el.send(JSON.stringify({
        type: 'conversation_initiation_client_data',
        dynamic_variables: {
          restaurant_name: customer.restaurant_name,
          contact_name: customer.contact_name,
          usual_order: customer.usual_order,
          todays_offer: customer.todays_offer,
        },
      }));
      for (const m of pending.splice(0)) el.send(JSON.stringify(m));
    });
    el.on('message', (raw) => {
      let msg; try { msg = JSON.parse(raw); } catch { return; }
      switch (msg.type) {
        case 'audio':
          if (msg.audio_event?.audio_base_64) toTwilio({ event: 'media', streamSid, media: { payload: msg.audio_event.audio_base_64 } });
          break;
        case 'interruption':
          toTwilio({ event: 'clear', streamSid });
          break;
        case 'ping':
          toEl({ type: 'pong', event_id: msg.ping_event?.event_id });
          break;
        case 'user_transcript': {
          const text = msg.user_transcription_event?.user_transcript;
          if (text) { transcript.push({ role: 'client', text }); log('chef:', text); }
          break;
        }
        case 'agent_response': {
          const text = msg.agent_response_event?.agent_response;
          if (text) { transcript.push({ role: 'agent', text }); log('agent:', text); }
          break;
        }
        case 'client_tool_call':
          handleTool(msg.client_tool_call);
          break;
        case 'conversation_initiation_metadata':
          log('conversation', msg.conversation_initiation_metadata_event?.conversation_id,
            'formats', msg.conversation_initiation_metadata_event?.user_input_audio_format, '/',
            msg.conversation_initiation_metadata_event?.agent_output_audio_format);
          break;
      }
    });
    el.on('close', (code) => { log('ElevenLabs closed', code); if (twilio.readyState === WebSocket.OPEN) twilio.close(); });
    el.on('error', (e) => log('ElevenLabs error', e.message));
  }

  function handleTool(call) {
    if (!call) return;
    const reply = (result, is_error = false) =>
      toEl({ type: 'client_tool_result', tool_call_id: call.tool_call_id, result: JSON.stringify(result), is_error });
    if (call.tool_name !== 'submit_order') return reply({ error: `unknown tool ${call.tool_name}` }, true);
    const p = call.parameters || {};
    const order = {
      id: crypto.randomUUID().slice(0, 8),
      status: 'pending',
      customer_id: customer?.id,
      restaurant: p.restaurant || customer?.restaurant_name,
      lines: Array.isArray(p.lines) ? p.lines : [],
      notes: p.notes || '',
      call_sid: callSid,
      transcript, // same array: keeps filling until the call ends (saved again on stop)
      created_at: new Date().toISOString(),
    };
    const orders = loadOrders();
    orders.push(order);
    saveOrders(orders);
    log('order saved', order.id, JSON.stringify(order.lines));
    reply({ ok: true, order_id: order.id, message: 'Commande enregistrée, en attente de validation.' });
  }

  twilio.on('message', async (raw) => {
    let msg; try { msg = JSON.parse(raw); } catch { return; }
    if (msg.event === 'start') {
      streamSid = msg.start.streamSid;
      callSid = msg.start.callSid;
      customer = findCustomer(msg.start.customParameters?.customer_id);
      log('call started for', customer.restaurant_name);
      try { await openElevenLabs(); } catch (e) { log(e.message); twilio.close(); }
    } else if (msg.event === 'media') {
      toEl({ user_audio_chunk: msg.media.payload });
    } else if (msg.event === 'stop') {
      log('call ended');
      // the order (if any) holds `transcript` by reference: save once more with the full transcript
      const orders = loadOrders();
      const o = orders.find((x) => x.call_sid === callSid);
      if (o) { o.transcript = transcript; saveOrders(orders); }
      if (el && el.readyState === WebSocket.OPEN) el.close();
    }
  });
  twilio.on('close', () => { if (el && el.readyState === WebSocket.OPEN) el.close(); });
});

const FALLBACK_PAGE = `<!doctype html><meta charset="utf-8"><title>Orders</title>
<body style="font-family:system-ui;max-width:720px;margin:2rem auto">
<h1>Approval queue</h1><p>Bare fallback page: web/index.html replaces it.</p><div id="o"></div>
<script>
async function load(){const r=await fetch('/api/orders');const os=await r.json();
document.getElementById('o').innerHTML=os.map(o=>'<div style="border:1px solid #ccc;padding:.6rem;margin:.6rem 0"><b>'+o.restaurant+'</b> · '+o.status+
'<ul>'+o.lines.map(l=>'<li>'+l.quantity+' '+(l.unit||'')+' '+l.product+'</li>').join('')+'</ul>'+(o.notes?'<i>'+o.notes+'</i><br>':'')+
(o.status==='pending'?'<button onclick="act(\\''+o.id+'\\',\\'approve\\')">Approve</button> <button onclick="act(\\''+o.id+'\\',\\'reject\\')">Reject</button>':'')+'</div>').join('')||'No orders yet.';}
async function act(id,a){await fetch('/api/orders/'+id+'/'+a,{method:'POST'});load();}
load();setInterval(load,2000);
</script>`;

server.listen(PORT, () => {
  console.log(`voice prototype on http://localhost:${PORT}  (public: ${PUBLIC_URL})`);
});
