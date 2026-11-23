#!/usr/bin/env node
// Creates (or updates) the French phone sales agent on ElevenLabs, with the
// submit_order client tool that server/ handles. See docs/prototype.md for the contract.
//
// Run from the repo root:  node --env-file=.env agent/create-agent.mjs
//   --dry-run   print the request bodies and exit, without calling ElevenLabs
//
// Env:
//   ELEVENLABS_API_KEY    required (except --dry-run)
//   ELEVENLABS_AGENT_ID   if set, this agent is updated instead of a new one being created
//   ELEVENLABS_VOICE_ID   optional: a French voice from your ElevenLabs voice library
//   ELEVENLABS_LLM        optional: e.g. gemini-2.5-flash; defaults to ElevenLabs' default
//   ELEVENLABS_TTS_MODEL  optional: e.g. eleven_flash_v2_5; defaults to ElevenLabs' default

import { readFile } from 'node:fs/promises';

const API = 'https://api.elevenlabs.io';
const NAME = 'Stockline – commercial téléphone (FR)';
const TOOL_NAME = 'submit_order';
const dryRun = process.argv.includes('--dry-run');
const env = process.env;

const prompt = await readFile(new URL('./prompt.fr.md', import.meta.url), 'utf8');

// Matches the submit_order contract in docs/prototype.md.
const toolConfig = {
  type: 'client',
  name: TOOL_NAME,
  description:
    "Envoie la commande confirmée par le chef dans la file de validation. À appeler une seule fois, après la relecture et la confirmation du chef (ou avec lines vide s'il faut rappeler).",
  expects_response: true,
  response_timeout_secs: 20,
  parameters: {
    type: 'object',
    required: ['restaurant', 'lines', 'notes'],
    properties: {
      restaurant: { type: 'string', description: 'Nom du restaurant appelé.' },
      lines: {
        type: 'array',
        description: 'Une ligne par produit commandé. Vide si rien n’est commandé.',
        items: {
          type: 'object',
          description: 'Une ligne de commande.',
          required: ['product', 'quantity', 'unit'],
          properties: {
            product: { type: 'string', description: 'Le produit en clair, calibre compris (ex. « crevettes 30/40 », « lotte »).' },
            quantity: { type: 'number', description: 'La quantité, en nombre (ex. 3, 0.5).' },
            unit: { type: 'string', description: 'L’unité dite par le chef : kg, colis, plateau, caisse, pièce, botte, barquette…' },
          },
        },
      },
      notes: {
        type: 'string',
        description:
          'Tout le reste en une ou deux phrases : livraison, heure de rappel, prix concurrent cité, mécontentement, demande de rappel par un commercial. Vide si rien.',
      },
    },
  },
};

const agentBody = (toolId) => ({
  name: NAME,
  tags: ['stockline-hackathon'],
  conversation_config: {
    agent: {
      language: 'fr',
      first_message:
        "Bonjour {{contact_name}}, ici l'assistant IA de votre fournisseur, j'appelle pour votre commande de demain. Vous avez deux minutes ?",
      dynamic_variables: {
        // Defaults for testing in the ElevenLabs dashboard; server/ sends the real values per call.
        dynamic_variable_placeholders: {
          restaurant_name: 'Le Petit Bouchon',
          contact_name: 'Chef',
          usual_order: '3 kg de lotte, 2 colis de tomates grappe, 1 plateau de crevettes 30/40',
          todays_offer: 'du bar de ligne arrivé ce matin, à -15 % pour vous',
        },
      },
      prompt: {
        prompt,
        tool_ids: [toolId],
        // Lets the agent hang up; the bridge then closes the Twilio stream and the call ends.
        built_in_tools: {
          end_call: {
            type: 'system',
            name: 'end_call',
            description: "Raccroche une fois l'au revoir dit : après submit_order, ou si le chef demande à être rappelé.",
            params: { system_tool_type: 'end_call' },
          },
        },
        ...(env.ELEVENLABS_LLM && { llm: env.ELEVENLABS_LLM }),
      },
    },
    // Twilio sends and plays mu-law 8 kHz. Anything else gives silence or noise.
    asr: { user_input_audio_format: 'ulaw_8000' },
    tts: {
      agent_output_audio_format: 'ulaw_8000',
      ...(env.ELEVENLABS_VOICE_ID && { voice_id: env.ELEVENLABS_VOICE_ID }),
      ...(env.ELEVENLABS_TTS_MODEL && { model_id: env.ELEVENLABS_TTS_MODEL }),
    },
  },
});

if (dryRun) {
  console.log(JSON.stringify({ tool: { tool_config: toolConfig }, agent: agentBody('<tool id>') }, null, 2));
  process.exit(0);
}

const key = env.ELEVENLABS_API_KEY;
if (!key) {
  console.error('ELEVENLABS_API_KEY is not set. Run: node --env-file=.env agent/create-agent.mjs');
  process.exit(1);
}

async function api(method, path, body) {
  const res = await fetch(API + path, {
    method,
    headers: { 'xi-api-key': key, 'content-type': 'application/json' },
    body: body && JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}: ${text}`);
  return text ? JSON.parse(text) : {};
}

// Reuse the submit_order tool if this workspace already has one, so reruns don't pile up copies.
async function findTool() {
  let cursor;
  do {
    const page = await api('GET', `/v1/convai/tools?page_size=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
    const hit = (page.tools ?? []).find((t) => t.tool_config?.type === 'client' && t.tool_config?.name === TOOL_NAME);
    if (hit) return hit.id;
    cursor = page.has_more ? page.next_cursor : undefined;
  } while (cursor);
}

let toolId = await findTool();
if (toolId) {
  await api('PATCH', `/v1/convai/tools/${toolId}`, { tool_config: toolConfig });
  console.log(`Updated tool ${TOOL_NAME}: ${toolId}`);
} else {
  toolId = (await api('POST', '/v1/convai/tools', { tool_config: toolConfig })).id;
  console.log(`Created tool ${TOOL_NAME}: ${toolId}`);
}

if (env.ELEVENLABS_AGENT_ID) {
  await api('PATCH', `/v1/convai/agents/${env.ELEVENLABS_AGENT_ID}`, agentBody(toolId));
  console.log(`Updated agent ${env.ELEVENLABS_AGENT_ID}`);
} else {
  const { agent_id } = await api('POST', '/v1/convai/agents/create', agentBody(toolId));
  console.log(`Created agent ${agent_id}\n\nPut this in .env:\nELEVENLABS_AGENT_ID=${agent_id}`);
}
