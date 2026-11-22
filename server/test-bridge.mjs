// End-to-end test of the bridge with no real accounts: a fake ElevenLabs agent and a fake Twilio
// media stream. Run: npm test
import { spawn } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { WebSocketServer, WebSocket } from 'ws';

const EL_PORT = 18081, PORT = 18080;
const ordersFile = path.join(os.tmpdir(), `orders-test-${process.pid}.json`);
const customersFile = path.join(os.tmpdir(), `customers-test-${process.pid}.json`);
fs.writeFileSync(customersFile, JSON.stringify([
  { id: 'other', restaurant_name: 'Chez Autre', contact_name: 'Paul', phone: '', usual_order: 'x', todays_offer: 'y' },
  { id: 'demo', restaurant_name: 'Le Petit Bouchon', contact_name: 'Marc', phone: '', usual_order: '2 colis de tomates', todays_offer: 'lotte -15 %' },
]));
const seen = { init: null, audioIn: 0, toolResult: null, twilioMedia: 0, twilioClear: 0 };

// --- fake ElevenLabs agent ---
const el = new WebSocketServer({ port: EL_PORT });
el.on('connection', (ws) => {
  ws.on('message', (raw) => {
    const m = JSON.parse(raw);
    if (m.type === 'conversation_initiation_client_data') {
      seen.init = m;
      ws.send(JSON.stringify({ type: 'conversation_initiation_metadata', conversation_initiation_metadata_event: { conversation_id: 'c1', user_input_audio_format: 'ulaw_8000', agent_output_audio_format: 'ulaw_8000' } }));
      ws.send(JSON.stringify({ type: 'agent_response', agent_response_event: { agent_response: 'Bonjour Marc, je suis un assistant IA.' } }));
      ws.send(JSON.stringify({ type: 'audio', audio_event: { audio_base_64: 'AAAA', event_id: 1 } }));
      ws.send(JSON.stringify({ type: 'ping', ping_event: { event_id: 7 } }));
    } else if (m.user_audio_chunk) {
      if (++seen.audioIn === 3) {
        ws.send(JSON.stringify({ type: 'interruption', interruption_event: { event_id: 2 } }));
        ws.send(JSON.stringify({ type: 'user_transcript', user_transcription_event: { user_transcript: "Comme d'hab, et mettez-moi de la lotte." } }));
        ws.send(JSON.stringify({ type: 'client_tool_call', client_tool_call: { tool_name: 'submit_order', tool_call_id: 't1', parameters: { restaurant: 'Le Petit Bouchon', lines: [{ product: 'lotte', quantity: 3, unit: 'kg' }], notes: 'livrer avant 10h' } } }));
      }
    } else if (m.type === 'client_tool_result') {
      seen.toolResult = m;
    } else if (m.type === 'pong') {
      seen.pong = m;
    }
  });
});

// --- the server under test ---
const srv = spawn(process.execPath, [path.join(import.meta.dirname, 'index.mjs')], {
  env: { ...process.env, PORT: String(PORT), PUBLIC_URL: 'https://example.test', ELEVENLABS_WS_URL: `ws://localhost:${EL_PORT}`, ORDERS_FILE: ordersFile, CUSTOMERS_FILE: customersFile, TWILIO_ACCOUNT_SID: '', TWILIO_AUTH_TOKEN: '', TWILIO_PHONE_NUMBER: '' },
  stdio: ['ignore', 'pipe', 'inherit'],
});
srv.stdout.on('data', (d) => process.stdout.write('  | ' + d));
await new Promise((r) => srv.stdout.once('data', r));
const base = `http://localhost:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

try {
  // TwiML points Twilio's stream at the public URL and carries the customer id
  const twiml = await (await fetch(`${base}/twiml?customer_id=demo`, { method: 'POST' })).text();
  assert.match(twiml, /<Stream url="wss:\/\/example\.test\/media-stream">/);
  assert.match(twiml, /name="customer_id" value="demo"/);

  // /call without Twilio credentials fails clearly
  const call = await fetch(`${base}/call`, { method: 'POST', body: JSON.stringify({ to: '+33600000000' }) });
  assert.equal(call.status, 500);
  assert.match((await call.json()).error, /TWILIO_ACCOUNT_SID/);

  // --- fake Twilio media stream ---
  const tw = new WebSocket(`ws://localhost:${PORT}/media-stream`);
  tw.on('message', (raw) => {
    const m = JSON.parse(raw);
    if (m.event === 'media') { assert.equal(m.streamSid, 'MZ1'); seen.twilioMedia++; }
    if (m.event === 'clear') seen.twilioClear++;
  });
  await new Promise((r) => tw.on('open', r));
  tw.send(JSON.stringify({ event: 'connected' }));
  tw.send(JSON.stringify({ event: 'start', streamSid: 'MZ1', start: { streamSid: 'MZ1', callSid: 'CA1', customParameters: { customer_id: 'demo' } } }));
  for (let i = 0; i < 3; i++) { tw.send(JSON.stringify({ event: 'media', streamSid: 'MZ1', media: { payload: 'f39/' } })); await sleep(50); }
  await sleep(400);
  tw.send(JSON.stringify({ event: 'stop', streamSid: 'MZ1' }));
  await sleep(300);

  assert.equal(seen.init?.dynamic_variables?.restaurant_name, 'Le Petit Bouchon', 'dynamic variables sent');
  assert.ok(seen.init.dynamic_variables.usual_order, 'usual_order sent');
  assert.equal(seen.audioIn, 3, 'caller audio reached ElevenLabs');
  assert.equal(seen.twilioMedia, 1, 'agent audio reached Twilio');
  assert.equal(seen.twilioClear, 1, 'interruption cleared Twilio playback');
  assert.equal(seen.pong?.event_id, 7, 'ping answered');
  assert.equal(seen.toolResult?.tool_call_id, 't1');
  assert.equal(JSON.parse(seen.toolResult.result).ok, true);

  const orders = await (await fetch(`${base}/api/orders`)).json();
  assert.equal(orders.length, 1);
  const o = orders[0];
  assert.equal(o.status, 'pending');
  assert.deepEqual(o.lines, [{ product: 'lotte', quantity: 3, unit: 'kg' }]);
  assert.equal(o.transcript.length, 2, 'transcript saved');

  const ok = await (await fetch(`${base}/api/orders/${o.id}/approve`, { method: 'POST' })).json();
  assert.equal(ok.status, 'approved');
  assert.match(await (await fetch(base + '/')).text(), /Approval queue|<html|<!doctype/i);

  console.log('\nPASS: call audio both ways, interruption, ping, submit_order -> approval queue -> approve');
} finally {
  srv.kill();
  el.close();
  fs.rmSync(ordersFile, { force: true });
  fs.rmSync(customersFile, { force: true });
}
