"""
Copilote (whisper mode): Leïla takes the call herself; the agents listen to both voices and help her, silently.

    both speak  ──► Éclaireur (scout): same as with the voice agent, fetches client / stock / prices / alerts while people talk
    a sentence  ──► Souffleur: a small fast model reads the conversation + what the Éclaireur found and
                    (1) keeps the order being dictated in sync with the ERP (draft created, lines added / changed / removed),
                    (2) whispers to Leïla what she must know now (shortage + substitute, a usual product not mentioned,
                        the client's negotiated price, a recent complaint in the client's file…)
                ──► ERP gates (GET /api/gates/orders/{id}) after each change: what would keep the order as a draft (credit,
                    too late, unusual quantity…), shown BEFORE Leïla says « c'est noté »
                ──► Jev (≈130 ms): did the client just agree to the recap? how is their mood?
                ──► Greffier (scribe): complaints and durable facts, as in agent mode
    « Valider » ──► finalize_order: the same gates as the voice agent (confirm, or a draft with the reasons)

Nothing here ever speaks to the client. Events for the screen (state.emit → OrchestraMessage):
    {type: "copilot_order", order, gate: {decision, reasons: [str]} | None, final: bool}
    {type: "whisper", key, level: danger|warn|info|tip, title, body, source}   {type: "whisper_clear", keys: [...]}
    {type: "copilot_mood", score: 0..3, label}
"""

import asyncio
import json
import re
from datetime import date

from app.agent import jev
from app.agent.prompts import WEEKDAYS, get_meta
from app.clients.jev import choice_question, score_question, yes_no_question
from app.tools import ErpClient, Tool, all_tools

from .models import ask_json
from .scout import Scout, norm
from .scribe import Scribe
from .state import CallState, run_tool

SOUFFLE = """Tu es le souffleur de Leïla, à l'ADV d'un grossiste en viande halal (Médina Viandes, Marseille). Elle est au
téléphone avec un client ; tu entends les deux. Tu ne parles JAMAIS au client : tu tiens à jour la commande qu'il dicte et
tu glisses à Leïla, sur son écran, ce qu'elle doit savoir MAINTENANT.
On te donne : la conversation (Client / Leïla), les données déjà trouvées dans l'ERP (fiche client, contexte, stocks, prix,
commandes en cours), la commande en cours dans l'ERP et les souffles déjà affichés.
Réponds UNIQUEMENT en JSON :
{"order": {"deliveryDate": "AAAA-MM-JJ ou null", "lines": [{"product": "tel que dit", "productId": "prd_… vu dans les données, sinon null", "qty": nombre}]} ou null,
 "whispers": [{"level": "danger | warn | info | tip", "title": "6 mots max", "body": "une phrase que Leïla lit en 2 secondes"}],
 "resolved": ["titre exact d'un souffle affiché qui n'est plus vrai ou plus utile (réglé dans la conversation, démenti par les données)"]}
order = la commande TELLE QU'ELLE EST DICTÉE À CE STADE : toutes les lignes (pas seulement les nouvelles), qty dans l'unité du
produit (kg, pièce, caisse… voir les stocks). product = les mots exacts du client (taille, poids, conditionnement compris) ;
productId = CE produit-là, jamais un autre (une broche de 20 kg n'est pas une broche de 10 kg). Produit en rupture : la ligne
garde le produit demandé, et la rupture + le remplaçant vont dans un souffle ; le remplaçant n'entre dans la commande que
quand le client l'a accepté. Un produit refusé ou retiré n'y est plus ; un remplaçant accepté y est. Une
quantité pas encore dite : pas de ligne. null si l'appel ne parle pas (ou pas encore) d'une commande.
whispers : 0 à 2, seulement du NOUVEAU et de l'utile, tiré des données (jamais inventé) : rupture et remplaçant à proposer,
produit habituel du client pas encore cité, prix négocié du client, réclamation ou litige récent dans sa fiche, question du
client restée sans réponse, client qui hésite ou menace de partir. danger = à régler avant de valider ; warn = à vérifier ;
tip = une opportunité ; info = bon à savoir. Les alertes ERP de la commande (encours, trop tard, quantité inhabituelle,
stock) sont déjà affichées : ne les répète pas. Un sujet déjà soufflé (même commande, même produit, même risque) : ne le
reformule JAMAIS, même autrement. L'identification du client est affichée à part : n'en parle pas."""

GATE_TITLES = {
    "client_blocked": ("danger", "Compte bloqué"),
    "credit_limit": ("danger", "Encours dépassé"),
    "too_late": ("danger", "Trop tard pour cette date"),
    "unusual_qty": ("warn", "Quantité inhabituelle"),
    "new_product": ("info", "Jamais commandé par ce client"),
    "special_cut_late": ("danger", "Découpe spéciale trop tard"),
}
MOODS = ["content", "neutre", "agacé", "furieux"]
# the quantity said in front of the product (not part of its name)
QTY_WORDS = re.compile(
    r"^\s*(?:\d+(?:[.,]\d+)?|une?|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|quinze|vingt|trente|quarante|cinquante|cent)\s+"
    r"(?:(?:kilos?|kg|cartons?|caisses?|pi[eè]ces?|colis)\s+)?(?:de\s+|d')?",
    re.I,
)
LEVELS = ("danger", "warn", "info", "tip")


def short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def qty_of(v) -> float | None:
    try:
        q = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return q if q > 0 else None


class CopilotSession:
    """One call taken by a human (Leïla), with the agents in whisper mode. Same surface as CallSession for WS /voice:
    speech_start / on_partial / speech_end while someone speaks, line(speaker, text) once a sentence is final."""

    side = True

    def __init__(self, actor_name: str | None = None, tools: list[Tool] | None = None, call_id: str | None = None):
        self.state = CallState(actor_name=actor_name or "Leïla")
        if call_id:
            self.state.id = call_id
        self.tool_map = {t.name: t for t in (tools if tools is not None else all_tools())}
        self.scout = Scout(self.state, self.tool_map)
        self.scribe = Scribe(self.state, self.tool_map, self.state.actor_name)
        self.lines: list[tuple[str, str]] = []  # (client | leila, text)
        self.shown: dict[str, dict] = {}  # whisper key → whisper (what Leïla has on screen)
        self.order_id: str | None = None
        self.resolved: dict[str, tuple[str | None, bool]] = {}  # product as said → (productId, sure)
        self.order: dict | None = None  # its last summary (state.order may be another order the Éclaireur read)
        self.final = False  # finalized: the souffleur stops editing the order
        self.gate: dict | None = None
        self.unread_client: list[str] = []  # client lines the Greffier has not seen yet
        self.task: asyncio.Task | None = None
        self.dirty = False
        self.tasks: set[asyncio.Task] = set()
        self.finished = False
        self.speaking = False

    # ── while someone speaks ──

    def speech_start(self) -> None:
        if not self.speaking:
            self.speaking = True
            self.scout.new_utterance()

    def on_partial(self, text: str) -> None:
        self.speech_start()
        self.scout.feed(text)

    def speech_end(self) -> None:
        self.speaking = False

    async def warm(self) -> None:
        await jev.warm()

    # ── a final sentence ──

    async def line(self, speaker: str, text: str) -> None:
        """A finished sentence of the client or of Leïla. Returns at once; the work runs in the background, coalesced."""
        text = text.strip()
        if not text:
            return
        st = self.state
        self.speaking = False
        self.lines.append((speaker, text))
        if speaker == "client":
            st.turn_no += 1
            st.turns.append({"n": st.turn_no, "caller": text, "agent": "", "path": "human", "speechEndMs": st.now()})
            self.unread_client.append(text)
        else:
            if not st.turns:
                st.turn_no += 1
                st.turns.append({"n": st.turn_no, "caller": "", "agent": "", "path": "human", "speechEndMs": st.now()})
            t = st.turns[-1]
            t["agent"] = f"{t['agent']} {text}".strip()
            self.scribe.after_turn(" ".join(self.unread_client), text)
            self.unread_client = []
        if self.task and not self.task.done():
            self.dirty = True
            return
        self.task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            self.dirty = False
            try:
                await self._pass()
            except Exception as e:  # the copilot never breaks the call
                print(f"[copilot] {type(e).__name__}: {e}", flush=True)
            if not self.dirty:
                return

    async def _pass(self) -> None:
        st = self.state
        last = self.lines[-1][1]
        await self.scout.settle(last, cap_ms=1500)
        if st.candidates and not st.client:
            names = " ; ".join(f"{c['name']} ({c.get('where') or '?'})" for c in st.candidates)
            await self.whisper("candidates", "warn", "Plusieurs clients possibles", f"Demander lequel : {names}.", "Éclaireur")
        if st.client:
            await self.whisper(
                f"client:{st.client['id']}",
                "info",
                f"Client : {st.client.get('name')}",
                "Fiche, prix et commandes en cours chargés.",
                "Éclaireur",
            )
        known = bool(st.client)
        souffle, read = await asyncio.gather(self._souffle(), self._jev())
        if souffle:
            if not self.final and isinstance(souffle.get("order"), dict):
                await self._sync(souffle["order"])
            for w in (souffle.get("whispers") or [])[:2]:
                if isinstance(w, dict) and w.get("title") and w.get("body"):
                    lvl = w.get("level") if w.get("level") in LEVELS else "info"
                    title, body = short(str(w["title"]), 60), short(str(w["body"]), 220)
                    # Haiku rewords what it already said: same subject = same card, updated in place
                    key = self.same_subject(title, body) or f"llm:{norm(title)}"
                    await self.whisper(key, lvl, title, body, "Souffleur")
        if souffle:
            gone = {norm(str(t)) for t in souffle.get("resolved") or [] if t}
            stale = [k for k, w in self.shown.items() if k.startswith("llm:") and norm(w["title"]) in gone]
            for k in stale:
                self.shown.pop(k)
            if stale:
                await st.emit({"type": "whisper_clear", "keys": stale})
        if st.client and not known:  # identified while the souffleur was reading: read again with the client's file
            self.dirty = True
        if read:
            mood = read.get("mood")
            if mood is not None:
                i = max(0, min(3, round(mood.score)))
                await st.emit({"type": "copilot_mood", "score": round(mood.score, 2), "label": MOODS[i]})
            agrees = read.get("agrees")
            if agrees is not None and agrees >= 0.8 and self.order_id and not self.final and self.lines[-1][0] == "client":
                await self.whisper(
                    "agrees",
                    "tip",
                    "Le client a dit oui",
                    "Il valide le récapitulatif : vous pouvez cliquer sur « Valider la commande ».",
                    "Jev",
                )
            elif agrees is not None and agrees < 0.5 and self.shown.pop("agrees", None):  # changed their mind
                await st.emit({"type": "whisper_clear", "keys": ["agrees"]})

    # ── the Souffleur ──

    async def _souffle(self) -> dict | None:
        st = self.state
        meta = await get_meta()
        today = meta.get("today") or date.today().isoformat()
        try:
            day = WEEKDAYS[date.fromisoformat(today[:10]).weekday()]
        except ValueError:
            day = ""
        convo = "\n".join(f"{'Client' if s == 'client' else 'Leïla'} : {t}" for s, t in self.lines[-24:])
        data, size = [], 0
        for k, v in st.facts.items():
            if k == f"order:{self.order_id}":  # the order this copilot keeps (shown below), not « another order »
                continue
            v = short(v, 2500)
            if size + len(v) > 14000:
                break
            data.append(f"- {v}")
            size += len(v)
        order = json.dumps(self.order, ensure_ascii=False, default=str) if self.order else "(aucune)"
        shown = "\n".join(f"- [{w['level']}] {w['title']} : {w['body']}" for w in self.shown.values()) or "(rien)"
        prompt = (
            f"Aujourd'hui : {day} {today}.\nClient identifié : {st.client or 'pas encore'}\n\nConversation :\n{convo}\n\n"
            f"Données ERP :\n{chr(10).join(data) or '(rien encore)'}\n\nCommande en cours dans l'ERP : {order}\n\nSouffles déjà affichés :\n{shown}"
        )
        try:
            async with st.span("copilot", "souffle", f"« {short(self.lines[-1][1], 60)} »"):
                return await ask_json(st, "copilot", "fast", SOUFFLE, prompt, 900)
        except Exception as e:
            print(f"[copilot] souffle: {type(e).__name__}: {e}", flush=True)
            return None

    async def _jev(self) -> dict | None:
        """Jev on the last exchange: did the client just say yes to the recap? how annoyed are they? (one request)"""
        if not jev.available() or not any(s == "client" for s, _ in self.lines[-1:]):
            return None
        said = {"leila_a_dit": next((t for s, t in reversed(self.lines[:-1]) if s == "leila"), ""), "le_client_repond": self.lines[-1][1]}
        async with self.state.span("copilot", "jev", "oui ? humeur"):
            return await jev.decide(said, {
                "agrees": yes_no_question("Le client accepte la commande telle que Leïla vient de la relire, sans rien changer."),
                "mood": score_question("Humeur du client dans sa réponse", MOODS),
            })  # fmt: skip

    # ── the order, kept in sync with the ERP ──

    async def _resolve(self, mention: str) -> tuple[str | None, bool]:
        """The catalogue product the client named: (productId, sure). Close hits are told apart by the numbers said
        (« broche de 20 » → the 20 kg one, not the 10 kg one), then by Jev; not sure → the Souffleur's own pick wins."""
        key = norm(mention)
        if not key:
            return None, False
        if key in self.resolved:
            return self.resolved[key]
        query = QTY_WORDS.sub("", mention).strip() or mention  # « trois broches de 20 kilos » → « broches de 20 kilos »
        try:
            hits = await ErpClient("voice_agent", "Souffleur").get("/api/search", {"q": query, "types": "product"}) or []
        except Exception:
            return None, False
        hits = [h for h in hits if h.get("score", 0) >= 60]
        if not hits:
            return None, False
        close = [h for h in hits if h["score"] >= 0.9 * hits[0]["score"]]
        if len(close) > 1:
            digits = re.findall(r"\d+", query)  # the label that carries the most of the numbers said (20 kg, 4,5 kg…)
            hit = {h["id"]: sum(bool(re.search(rf"(?<!\d){d}(?!\d)", h.get("label", ""))) for d in digits) for h in close}
            best = max(hit.values(), default=0)
            if best and list(hit.values()).count(best) == 1:
                close = [h for h in close if hit[h["id"]] == best]
        if len(close) > 1 and jev.available():
            options = {h["id"]: " — ".join(filter(None, (h.get("label"), h.get("sublabel")))) for h in close[:5]}
            async with self.state.span("copilot", "jev", f"« {short(mention, 40)} »"):
                a = await jev.decide(
                    {"le_client_demande": mention}, {"p": choice_question("Quel produit du catalogue le client demande-t-il ?", options)}
                )
            pick = (a or {}).get("p")
            if pick and pick.confidence >= 0.6:
                close = [h for h in close if h["id"] == pick.choice]
        out = (close[0]["id"], len(close) == 1)
        self.resolved[key] = out
        return out

    async def _sync(self, want: dict) -> None:
        st = self.state
        if not st.client:
            return
        wanted: dict[str, float] = {}
        for line in want.get("lines") or []:
            if not isinstance(line, dict):
                continue
            said = line.get("productId") if re.fullmatch(r"prd_\w+", str(line.get("productId") or "")) else None
            found, sure = await self._resolve(str(line.get("product") or ""))
            pid = found if sure else said or found  # what the client named beats the Souffleur's guess
            qty = qty_of(line.get("qty"))
            if pid and qty:
                wanted[pid] = qty  # the same product said twice: the last quantity wins
        day = want.get("deliveryDate") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(want.get("deliveryDate") or "")) else None
        changed = False

        async def run(name: str, args: dict) -> dict | None:
            r = await run_tool(st, "copilot", self.tool_map, name, args, st.actor_name)
            if isinstance(r, dict) and r.get("lines") is not None:
                self.order = r
            return r

        async with st.span("copilot", "commande", f"{len(wanted)} ligne(s)"):
            if not self.order_id:
                if not wanted:
                    return
                args = {"clientId": st.client["id"], "lines": [{"productId": p, "qty": q} for p, q in wanted.items()]}
                if day:
                    args["deliveryDate"] = day
                r = await run("create_draft_order", args)
                if not r:
                    return
                self.order_id, changed = r["id"], True
            else:
                have = {ln["productId"]: ln for ln in (self.order or {}).get("lines") or []}
                for pid, qty in wanted.items():
                    if pid not in have:
                        changed |= bool(await run("add_order_line", {"orderId": self.order_id, "productId": pid, "qty": qty}))
                    elif abs(float(have[pid].get("qty") or 0) - qty) > 1e-6:
                        changed |= bool(
                            await run("update_order_line", {"orderId": self.order_id, "lineId": have[pid]["lineId"], "qty": qty})
                        )
                if wanted:  # an empty list is a souffleur slip, never « remove everything »
                    for pid, ln in have.items():
                        if pid not in wanted:
                            changed |= bool(await run("remove_order_line", {"orderId": self.order_id, "lineId": ln["lineId"]}))
                if day and day != (self.order or {}).get("deliveryDate"):
                    changed |= bool(await run("update_order", {"orderId": self.order_id, "deliveryDate": day}))
        if changed:
            await self.check()

    async def check(self) -> None:
        """The ERP gates on the order as it stands: what would keep it as a draft. Alerts appear and disappear with it."""
        st = self.state
        if not self.order_id:
            return
        try:
            async with st.span("copilot", "garde-fous"):
                g = await ErpClient("voice_agent", "Souffleur").get(f"/api/gates/orders/{self.order_id}")
        except Exception as e:
            print(f"[copilot] gates: {type(e).__name__}: {e}", flush=True)
            g = None
        keys = set()
        if g:
            self.gate = {"decision": g.get("decision"), "reasons": [r.get("message") for r in g.get("reasons") or []]}
            for r in g.get("reasons") or []:
                level, title = GATE_TITLES.get(r.get("code"), ("warn", "À vérifier"))
                key = f"gate:{r.get('code')}:{r.get('productId') or ''}"
                keys.add(key)
                await self.whisper(key, level, title, r.get("message") or "", "ERP")
        for w in (self.order or {}).get("warnings") or []:
            if w.get("code") in ("expiring_soon",) or not w.get("message"):
                continue
            key = f"warn:{w.get('code')}:{w.get('productId') or ''}"
            if key not in keys and not key.startswith(("warn:credit", "warn:too_late", "warn:client_blocked")):
                keys.add(key)
                await self.whisper(key, "warn", "Stock" if "stock" in str(w.get("code")) else "Alerte commande", w["message"], "ERP")
        stale = [k for k in self.shown if k.startswith(("gate:", "warn:")) and k not in keys]
        for k in stale:
            self.shown.pop(k)
        if stale:
            await st.emit({"type": "whisper_clear", "keys": stale})
        await st.emit({"type": "copilot_order", "order": self.order, "gate": self.gate, "final": self.final})

    async def confirm(self) -> dict | None:
        """Leïla clicks « Valider la commande »: finalize_order, the same gates as the voice agent."""
        st = self.state
        if not self.order_id or self.final:
            return None
        if self.task and not self.task.done():  # a last sentence still being written into the order
            await asyncio.wait({self.task}, timeout=5)
        async with st.span("copilot", "finalisation"):
            r = await run_tool(
                st, "copilot", self.tool_map, "finalize_order", {"orderId": self.order_id, "recapConfirmed": True}, st.actor_name
            )
        if not r:
            await self.whisper(
                "final:error", "danger", "Validation impossible", "L'ERP a refusé : voir la commande dans l'écran Commandes.", "ERP"
            )
            return None
        self.final = True
        if isinstance(r.get("order"), dict):
            self.order = r["order"]
        self.gate = {"decision": r.get("decision"), "reasons": r.get("reasons") or []}
        if r.get("decision") == "draft":
            await self.whisper("final", "warn", "Gardée en brouillon", " · ".join(self.gate["reasons"]) or "À vérifier.", "ERP")
        else:
            await self.whisper(
                "final",
                "info",
                "Commande confirmée",
                f"{(r.get('order') or {}).get('number', '')} · stock réservé, tournée affectée.",
                "ERP",
            )
        await st.emit({"type": "copilot_order", "order": self.order, "gate": self.gate, "final": True})
        return r

    def same_subject(self, title: str, body: str) -> str | None:
        """The shown Souffleur whisper about the same thing (≥ 50 % of the meaningful words shared), if any."""
        words = lambda t: {w for w in norm(t).split() if len(w) > 3}  # noqa: E731
        new = words(f"{title} {body}")
        best, key = 0.0, None
        for k, w in self.shown.items():
            if k.startswith("llm:") and new:
                old = words(f"{w['title']} {w['body']}")
                sim = len(new & old) / max(1, min(len(new), len(old)))
                if sim > best:
                    best, key = sim, k
        return key if best >= 0.5 else None

    async def whisper(self, key: str, level: str, title: str, body: str, source: str) -> None:
        w = {"key": key, "level": level, "title": title, "body": body, "source": source}
        if self.shown.get(key) == w:
            return
        self.shown[key] = w
        await self.state.emit({"type": "whisper", **w})

    # ── end of call ──

    async def finish(self, report: bool = True) -> dict | None:
        if self.finished:
            return None
        self.finished = True
        if self.task and not self.task.done():
            await asyncio.wait({self.task}, timeout=8)
        await self.scout.close()
        if self.unread_client:
            self.scribe.after_turn(" ".join(self.unread_client), "")
        await self.scribe.flush()
        if not report or not self.state.turns:
            return None
        from .reporter import build_report

        return await build_report(self)
