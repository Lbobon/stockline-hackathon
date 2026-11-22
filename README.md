# Stockline × X-Forum Hackathon

24h hackathon: **AI agents for the food supply chain**, built as an add-on to [Stockline](https://stockline.ai).

**What we're building: an AI phone sales rep.** It calls restaurants in French, takes their order, sells the right products and puts everything into Stockline for a person to approve. Full spec: [docs/idea.md](docs/idea.md)

- 📅 Mon 23/11 19:00 → Tue 24/11 19:00 at l'X. Finalists pitch at the X-Forum on Wed 25/11
- 📚 Market research: [docs/research.md](docs/research.md)
- 🗂️ Task board: the repo's **Projects** tab ("Stockline Hackathon")

## Team

| Name | GitHub | Role | Owns |
|---|---|---|---|
| Loric | @Lbobon | _TBD_ | |
| Yuvaan | @yuvaan-d-gupta | TBD | |
| _teammate 3_ | | _TBD_ | |
| _teammate 4_ | | _TBD_ | |

Proposed roles: **Agent/LLM lead** · **Data & integrations** · **Frontend & design** · **Product & pitch**. We confirm them at kickoff.

## Getting set up (everyone, before Monday)

1. Accept the GitHub invite (check your email or https://github.com/notifications).
2. Install [Git](https://git-scm.com), the [GitHub CLI](https://cli.github.com), then run `gh auth login`.
3. Clone **outside OneDrive/iCloud/Dropbox**, because sync tools lock files and break `npm install`:
   ```bash
   cd ~/dev            # or C:\dev on Windows
   gh repo clone Lbobon/stockline-hackathon
   ```
4. Copy `.env.example` to `.env`. Get the real keys from the team in person. **Never commit `.env`.**
5. Add your name, GitHub handle and role to the table above (your first PR, to test the flow).
6. **Connect your coding agent:** open Claude Code (or Codex, etc.) in your clone. It reads `CLAUDE.md`/`AGENTS.md` and offers to join [Murmur](https://murmur.afig.dev), which lets all our agents see and message each other through this repo. Say yes, then restart the agent so the tools load. Run `mur ui` to watch the agents and messages.

## How we work

**Branches and PRs**
- `main` must always run the demo. Never push broken code to it.
- One branch per task: `<name>/<short-task>`, e.g. `loric/order-parser`.
- Open small PRs early (a draft is fine). Merge within the hour; don't sit on branches.
- Each PR needs one quick review, or a thumbs-up in the group chat at night.
- Pull `main` often: `git pull --rebase origin main`.

**Tasks**
- Everything we do is an issue on the Projects board: **Todo → In progress → Done**.
- Assign yourself before starting. Label it with your area (`agent`, `data`, `frontend`, `pitch`) and a priority (`P0` = demo dies without it).
- If you're stuck for more than 20 minutes, add the `blocked` label and say so in the chat.

**Comms**
- WhatsApp group for quick talk. Decisions go in the relevant issue or PR, so nothing gets lost.
- Sync every 2h, 5 minutes, standing: what I did, what's next, what's blocking me.

**Secrets and data**
- API keys go in `.env` only, shared in person or via a one-time link (e.g. Bitwarden Send).
- Hackathon data provided by Stockline goes in `data/raw/`. It is git-ignored, so don't push their data.

## 24h plan

| When | What |
|---|---|
| 19:00–20:00 | Hear the challenge, ask Stockline the questions in [idea.md](docs/idea.md), adjust the 3-minute demo script |
| 20:00–21:00 | Agree on the stack, data schema and API contract, create the issues |
| 21:00–01:00 | Ugly but working from start to finish |
| 01:00–07:00 | Sleep in pairs (3h shifts) while the others keep building |
| 07:00–15:00 | Real data, polish, second feature |
| **15:00** | **Feature freeze** |
| 15:00–19:00 | Pitch rehearsals in French, record a backup demo video, submit |
