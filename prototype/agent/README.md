# agent/: the ElevenLabs phone agent

- `prompt.fr.md`: the agent's French system prompt. It says it's an AI, takes "comme d'hab", offers `{{todays_offer}}` once, reads the order back, then calls `submit_order`.
- `create-agent.mjs`: creates the agent on ElevenLabs with the `submit_order` client tool and μ-law 8 kHz audio in and out (what Twilio needs). No dependencies.

The tool and the dynamic variables follow the contract in [docs/prototype.md](../docs/prototype.md).

## Create the agent

From the repo root, with `ELEVENLABS_API_KEY` in `.env`:

```bash
node --env-file=.env agent/create-agent.mjs
```

It prints the new agent's id: put it in `.env` as `ELEVENLABS_AGENT_ID`. Run it again after editing the prompt: with `ELEVENLABS_AGENT_ID` set, it updates that agent instead of creating another one, and it reuses the existing `submit_order` tool.

`--dry-run` prints what it would send, without calling ElevenLabs.

Optional `.env` settings:
- `ELEVENLABS_VOICE_ID`: a French voice from the ElevenLabs voice library. Without it, the agent gets ElevenLabs' default voice. Pick one on Monday night and test it on a real call.
- `ELEVENLABS_LLM` (e.g. `gemini-2.5-flash`) and `ELEVENLABS_TTS_MODEL` (e.g. `eleven_flash_v2_5`): if the call feels slow, try faster ones.
