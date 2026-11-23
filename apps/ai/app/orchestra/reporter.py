"""
Rapporteur (reporter): at hang-up, turns the call into a report for the team — what happened, what Leïla (ADV, 5 h–13 h)
must do next, risks and opportunities, how well the agents did — next to the hard numbers measured during the call
(latency per turn, which turns were prefetched or answered by the fast path, what every agent did and cost).
Saved by store.py, served by GET /calls/{id}, drawn by the web page /calls/:id.
"""

import json
import statistics

from .models import ask_json, side_llm
from .scout import LANG_NAMES
from .state import AGENTS, CallState, cost

SYSTEM = """Tu es le rapporteur d'un grossiste en viande halal (Médina Viandes, Marseille). Un agent vocal IA vient de traiter un appel
téléphonique, aidé d'agents en parallèle. Écris le compte rendu pour l'équipe, en français, précis et utile, sans inventer :
uniquement ce que montrent la transcription et les appels d'outils. Leïla (ADV, au standard de 5 h à 13 h) le lira en premier.
Réponds UNIQUEMENT par un objet JSON :
{"headline": "titre en une ligne, factuel et parlant (ex. « Istanbul Grill : broches remplacées, commande de 412 € confirmée »)",
 "summary": "3 à 4 phrases : qui, quoi, résultat",
 "outcome": "order_confirmed | order_draft | order_changed | info_given | complaint_logged | escalated | no_action",
 "sentiment": {"start": "calme | pressé | agacé | mécontent | content", "end": "…", "comment": "une phrase"},
 "for_leila": [{"priority": "haute | moyenne | basse", "action": "action concrète", "why": "pourquoi, en une phrase"}],
 "risks": ["risque concret (encours, client qui menace de partir, rupture…)"],
 "opportunities": ["opportunité concrète (volume, nouveau produit, fidélisation…)"],
 "agent_review": {"score": 1-5, "good": ["ce que l'agent a bien fait"], "improve": ["ce qu'il aurait dû faire autrement"]},
 "moments": [{"turn": n, "label": "moment clé, 6 mots max"}],
 "tags": ["3 à 6 mots-clés"]}
Listes vides si rien. for_leila : seulement ce qui demande une action humaine (pas « rien à faire »)."""


def pct(values: list[int], p: float) -> int | None:
    v = sorted(x for x in values if x is not None)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def transcript(st: CallState) -> str:
    out = []
    for t in st.turns:
        out.append(f"[{t['n']}] Client : {t.get('caller', '')}")
        if t.get("tools"):
            out.append(f"    (outils de l'agent : {', '.join(t['tools'])})")
        out.append(f"[{t['n']}] Agent{' (réflexe)' if t.get('path') == 'fast' else ''} : {t.get('agent', '')}")
    return "\n".join(out)


def compact_calls(st: CallState) -> str:
    rows = []
    for c in st.tool_calls:
        r = json.dumps(c["result"], ensure_ascii=False, default=str)
        rows.append(f"[tour {c['turn']}] {AGENTS.get(c['agent'], c['agent'])} · {c['name']}({json.dumps(c['args'], ensure_ascii=False)}) → {'ok' if c['ok'] else 'ERREUR'} {r[:400]}")
    return "\n".join(rows[-40:])


async def build_report(session) -> dict:
    st: CallState = session.state
    narrative = None
    try:
        async with st.span("reporter", "compte rendu"):
            narrative = await ask_json(
                st, "reporter", "report", SYSTEM,
                f"Client : {st.client or 'non identifié'}\nLangue : {st.language}, {st.register}\n\nTranscription :\n{transcript(st)}\n\nAppels d'outils (tous agents) :\n{compact_calls(st)}",
                8000,  # thinking + JSON: long calls need room
            )  # fmt: skip
    except Exception as e:
        print(f"[reporter] {type(e).__name__}: {e}", flush=True)

    turns = st.turns
    llm_turns = [t for t in turns if t.get("path") == "llm"]
    first_sound = [t.get("firstSoundMs") for t in turns if t.get("firstSoundMs") is not None]
    first_word = [t.get("firstWordMs") for t in turns if t.get("firstWordMs") is not None]
    usage = {a: {**u, "label": AGENTS.get(a, a), "cost": round(cost(u, u["model"]), 5)} for a, u in st.usage.items()}
    orders = {}
    for c in st.tool_calls:
        r = c.get("result")
        if c["ok"] and isinstance(r, dict) and str(r.get("id", "")).startswith("ord_") and r.get("lines") is not None:
            orders[r["id"]] = r
    writes = [c for c in st.tool_calls if c["ok"] and c["name"] in ("log_client_event", "save_context", "create_draft_order", "confirm_order", "update_order_line", "add_order_line", "remove_order_line", "update_order")]
    scout_calls = [c for c in st.tool_calls if c["agent"] == "scout"]
    voice_calls = [c for c in st.tool_calls if c["agent"] == "voice"]
    spans = [
        {"agent": s.agent, "label": AGENTS.get(s.agent, s.agent), "task": s.task, "detail": s.detail, "turn": s.turn, "startMs": s.start_ms, "endMs": s.end_ms, "ok": s.ok}
        for s in st.spans
    ]
    duration = max([s["endMs"] or 0 for s in spans] + [t.get("speechEndMs", 0) + (t.get("doneMs") or 0) for t in turns] + [0])

    return {
        "id": st.id,
        "startedAt": st.started_at,
        "durationMs": duration,
        "actor": st.actor_name,
        "client": st.client,
        "language": st.language,
        "languageName": LANG_NAMES.get(st.language, st.language),
        "register": st.register,
        "turns": turns,
        "spans": spans,
        "toolCalls": [{**c, "result": c["result"] if c["name"] != "get_context" else {"about": (c["result"] or {}).get("about")}} for c in st.tool_calls],
        "orders": list(orders.values()),
        "writes": [{"agent": c["agent"], "name": c["name"], "args": c["args"], "result": c["result"]} for c in writes],
        "usage": usage,
        "metrics": {
            "turns": len(turns),
            "fastTurns": len(turns) - len(llm_turns),
            "prefetchedFacts": sum(len(t.get("prefetched") or []) for t in turns),
            "scoutToolCalls": len(scout_calls),
            "voiceToolCalls": len(voice_calls),
            "firstSoundP50": int(statistics.median(first_sound)) if first_sound else None,
            "firstSoundP90": pct(first_sound, 0.9),
            "firstWordP50": int(statistics.median(first_word)) if first_word else None,
            "firstWordP90": pct(first_word, 0.9),
            "cost": round(sum(u["cost"] for u in usage.values()), 4),
        },
        "ai": narrative,
        "reportModel": side_llm("report").model,
    }
