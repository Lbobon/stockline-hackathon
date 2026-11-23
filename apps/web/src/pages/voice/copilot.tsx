/**
 * Voix · Copilote: Leïla takes the call herself; the agents listen to both voices and help her, silently
 * (WS /ai/voice in copilot mode, app/orchestra/copilot.py).
 *  - Left: the call, two voices (Client / Leïla), push-to-talk with the voice picked in the switch, or typed.
 *  - Right: the Souffleur's whispers (ERP gates, shortages, the client's habits and price…) and the order being
 *    dictated, kept in the ERP live, with « Valider la commande » (finalize_order: the same gates as the voice agent).
 */
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { AlertTriangle, Check, Info, Lightbulb, Mic, MicOff, PhoneOff, RotateCcw, Send, ShieldAlert, Smile, X, type LucideIcon } from "lucide-react";
import type { CopilotSpeaker, WhisperLevel } from "@kit/shared";
import { cn } from "@/lib/cn";
import { Badge, Button, DateLabel, Dot, EmptyState, IconButton, Input, Kbd, Money, Page, PageHeader, Qty, SECTION_LABEL, Segmented, Spinner } from "@/ui";
import { useCopilotSession, type CopilotGate, type CopilotLine, type Whisper } from "@/features/assistant/useCopilotSession";
import { COPILOT_TEAM } from "@/features/calls/agents";
import { OrchestraStrip, ReportBanner } from "./orchestra";
import { LevelBars } from "./timeline";

const isTypingTarget = (t: EventTarget | null) => {
  const el = t as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
};

const SPEAKERS = [{ id: "client", label: "Client" }, { id: "leila", label: "Leïla" }] as const;

const LEVEL: Record<WhisperLevel, { icon: LucideIcon; label: string; text: string; bar: string; soft: string }> = {
  danger: { icon: ShieldAlert, label: "À régler avant de valider", text: "text-destructive", bar: "bg-destructive", soft: "bg-destructive/[.06]" },
  warn: { icon: AlertTriangle, label: "À vérifier", text: "text-warning", bar: "bg-warning", soft: "bg-warning/[.06]" },
  tip: { icon: Lightbulb, label: "Opportunité", text: "text-success", bar: "bg-success", soft: "bg-success/[.05]" },
  info: { icon: Info, label: "Bon à savoir", text: "text-info", bar: "bg-info", soft: "" },
};
const ORDER_LEVEL: Record<WhisperLevel, number> = { danger: 0, warn: 1, tip: 2, info: 3 };

export function CopilotMode({ modeSwitch }: { modeSwitch: ReactNode }) {
  const c = useCopilotSession();
  const cRef = useRef(c);
  cRef.current = c;
  const [speaker, setSpeaker] = useState<CopilotSpeaker>("client");
  const speakerRef = useRef(speaker);
  speakerRef.current = speaker;
  const [text, setText] = useState("");
  const spaceDown = useRef(false);
  const ready = c.conn === "ready";

  // Space = push-to-talk as the voice picked in the switch; Tab-free toggle: « ² »-less, so Alt+C / Alt+L pick the voice.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === "c" || e.key === "l")) { e.preventDefault(); setSpeaker(e.key === "c" ? "client" : "leila"); return; }
      if (e.code !== "Space" || isTypingTarget(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      e.preventDefault();
      if (e.repeat || spaceDown.current) return;
      spaceDown.current = true;
      void cRef.current.startTalking(speakerRef.current);
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || !spaceDown.current) return;
      e.preventDefault();
      spaceDown.current = false;
      cRef.current.stopTalking();
    };
    const blur = () => { if (spaceDown.current) { spaceDown.current = false; cRef.current.stopTalking(); } };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    c.sendText(text, speaker);
    setText("");
  };

  return (
    <Page>
      <PageHeader
        title="Voix"
        meta={<ConnState conn={c.conn} />}
        tabs={modeSwitch}
        actions={
          <>
            <Button size="sm" icon={PhoneOff} onClick={c.hangup} disabled={!ready || !c.lines.length}>Raccrocher</Button>
            <Button size="sm" variant="ghost" icon={RotateCcw} onClick={c.reset} disabled={!ready}>Nouvel appel</Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col">
          <ReportBanner report={c.report} onClose={c.dismissReport} />
          <OrchestraStrip agents={c.agents} lanes={c.lanes} profile={c.profile} notes={c.notes} team={COPILOT_TEAM} subtitle="Leïla parle, 4 agents écoutent et soufflent" />
          <Transcript lines={c.lines} mood={c.mood} />

          {c.mic === "denied" || c.mic === "unsupported" ? (
            <div className="flex shrink-0 items-center gap-2 border-t border-border bg-warning/[.08] px-5 py-2 text-[12px]">
              <MicOff size={14} strokeWidth={1.75} className="shrink-0 text-warning" />
              <span>{c.mic === "denied" ? "Micro refusé : autorisez-le dans les réglages du site. En attendant, tapez les phrases." : "Micro indisponible ici (il faut localhost ou https) : tapez les phrases."}</span>
            </div>
          ) : null}
          {c.lastError ? (
            <div className="flex shrink-0 items-center gap-2 border-t border-border bg-destructive/[.06] px-5 py-1.5 text-[12px] text-destructive">{c.lastError}</div>
          ) : null}

          <div className="flex shrink-0 items-center gap-4 border-t border-border px-5 py-3">
            <TalkButton speaker={speaker} recording={c.recording} disabled={!ready} onDown={() => void c.startTalking(speaker)} onUp={c.stopTalking} />
            <div className="flex shrink-0 flex-col gap-1.5">
              <Segmented<CopilotSpeaker> value={speaker} options={SPEAKERS} onChange={setSpeaker} />
              {c.recording ? (
                <LevelBars level={c.level} />
              ) : (
                <span className="hidden items-center gap-1 text-[11.5px] text-muted-foreground xl:flex"><Kbd>Espace</Kbd> parler · <Kbd>Alt C</Kbd>/<Kbd>Alt L</Kbd> voix</span>
              )}
            </div>
            <form onSubmit={submit} className="flex min-w-0 flex-1 items-center gap-2">
              <Input
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Escape") (e.target as HTMLInputElement).blur(); }}
                placeholder={speaker === "client" ? "Ou tapez la phrase du client…" : "Ou tapez la phrase de Leïla…"}
                disabled={!ready}
                className="min-w-0 flex-1"
              />
              <IconButton type="submit" icon={Send} label="Envoyer" variant="default" disabled={!ready || !text.trim()} />
            </form>
          </div>
        </section>
        <aside className="flex w-[320px] shrink-0 flex-col border-l border-border bg-card xl:w-[380px] 2xl:w-[420px]">
          <Whispers whispers={c.whispers} onDismiss={c.dismissWhisper} />
          <OrderCard order={c.order} gate={c.gate} final={c.final} confirming={c.confirming} onConfirm={c.confirm} ready={ready} />
        </aside>
      </div>
    </Page>
  );
}

function ConnState({ conn }: { conn: string }) {
  return (
    <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
      <Dot tone={conn === "ready" ? "success" : conn === "connecting" ? "warning" : "destructive"} pulse={conn === "connecting"} />
      {conn === "ready" ? "Copilote prêt" : conn === "connecting" ? "Connexion…" : "Service IA injoignable · nouvel essai…"}
    </span>
  );
}

// ── The call, two voices ──

function Transcript({ lines, mood }: { lines: CopilotLine[]; mood?: { score: number; label: string } }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [lines]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-2 px-5">
        <span className={SECTION_LABEL}>Appel</span>
        {mood ? (
          <span
            className={cn("ml-auto inline-flex h-[20px] items-center gap-1 rounded-[3px] border px-1.5 text-[11px]",
              mood.score >= 1.5 ? "border-warning/40 text-warning" : "border-border text-secondary-foreground")}
            title={`Humeur du client lue par Jev (TypeSafe) sur sa dernière phrase : ${mood.score.toFixed(1)} sur une échelle content 0 → furieux 3`}
          >
            <Smile size={12} strokeWidth={1.75} />
            Client {mood.label}
            <span className="text-muted-foreground">· Jev</span>
          </span>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4">
        {lines.length === 0 ? (
          <EmptyState
            icon={Mic}
            title="Prenez l'appel, les agents écoutent"
            description="Choisissez qui parle (Client ou Leïla), maintenez Espace ou tapez la phrase. La commande se remplit toute seule à droite, avec les alertes de l'ERP."
          />
        ) : (
          <ol className="flex flex-col gap-2">
            {lines.map((l) => <Bubble key={l.id} l={l} />)}
          </ol>
        )}
        <div ref={end} />
      </div>
    </div>
  );
}

function Bubble({ l }: { l: CopilotLine }) {
  const leila = l.speaker === "leila";
  const pending = l.status !== "final";
  return (
    <li className={cn("flex animate-fade-in flex-col gap-0.5", leila ? "items-end" : "items-start")}>
      <span className="px-1 text-[10.5px] text-muted-foreground">{leila ? "Leïla" : "Client"}</span>
      <div className={cn("max-w-[78%] rounded-[6px] border px-3 py-1.5 text-[13px] leading-snug",
        leila ? "border-violet/25 bg-violet/[.07]" : "border-border bg-card")}>
        {l.text ? (
          pending && l.stable != null ? <><span>{l.stable}</span><span className="text-muted-foreground">{l.text.slice(l.stable.length)}</span></> : l.text
        ) : (
          <span className="flex items-center gap-1.5 text-muted-foreground">
            {l.status === "recording" ? <Dot tone="violet" pulse /> : <Spinner size={11} />}
            {l.status === "recording" ? "J'écoute…" : "Transcription…"}
          </span>
        )}
      </div>
    </li>
  );
}

function TalkButton({ speaker, recording, disabled, onDown, onUp }: { speaker: CopilotSpeaker; recording?: CopilotSpeaker; disabled: boolean; onDown: () => void; onUp: () => void }) {
  const rec = !!recording;
  const held = useRef(false);
  return (
    <button
      type="button"
      disabled={disabled}
      aria-label={`Maintenir pour parler (${speaker === "client" ? "client" : "Leïla"})`}
      aria-pressed={rec}
      onPointerDown={(e) => { if (e.button !== 0) return; e.currentTarget.setPointerCapture(e.pointerId); held.current = true; onDown(); }}
      onPointerUp={() => { if (held.current) { held.current = false; onUp(); } }}
      onPointerCancel={() => { if (held.current) { held.current = false; onUp(); } }}
      onContextMenu={(e) => e.preventDefault()}
      className={cn(
        "relative flex h-14 w-14 shrink-0 select-none items-center justify-center rounded-full border transition-[transform,background-color,border-color] duration-100",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet/70 disabled:opacity-40",
        rec ? "scale-95 border-violet bg-violet text-primary-foreground" : "border-violet/40 bg-violet/10 text-violet hover:border-violet hover:bg-violet/15",
      )}
      style={{ touchAction: "none" }}
    >
      {rec ? <span className="absolute inset-0 animate-ping rounded-full bg-violet/30" aria-hidden /> : null}
      <Mic size={22} strokeWidth={1.75} className="relative" />
    </button>
  );
}

// ── Whispers ──

function Whispers({ whispers, onDismiss }: { whispers: Whisper[]; onDismiss: (key: string) => void }) {
  const sorted = [...whispers].sort((a, b) => ORDER_LEVEL[a.level] - ORDER_LEVEL[b.level] || b.at - a.at);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-4">
        <span className={SECTION_LABEL}>Souffles</span>
        {whispers.length ? <span className="num text-[11.5px] text-muted-foreground">{whispers.length}</span> : null}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {sorted.length === 0 ? (
          <EmptyState compact icon={Lightbulb} title="Rien à signaler" description="Alertes ERP, ruptures, habitudes et prix du client apparaissent ici pendant l'appel." />
        ) : (
          <ol>
            {sorted.map((w) => {
              const m = LEVEL[w.level];
              const Icon = m.icon;
              return (
                <li key={w.key} className={cn("group relative flex animate-fade-in gap-2.5 border-b border-border py-2.5 pl-4 pr-3", m.soft)} title={m.label}>
                  <span className={cn("absolute inset-y-0 left-0 w-[3px]", m.bar)} />
                  <Icon size={15} strokeWidth={1.9} className={cn("mt-[1px] shrink-0", m.text)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="text-[12.5px] font-medium">{w.title}</span>
                      <span className="ml-auto shrink-0 text-[10.5px] text-muted-foreground">{w.source}</span>
                    </div>
                    <p className="text-[12px] leading-snug text-secondary-foreground">{w.body}</p>
                  </div>
                  <button type="button" aria-label="Masquer" onClick={() => onDismiss(w.key)}
                    className="absolute right-1.5 top-1.5 hidden rounded-[3px] p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground group-hover:block">
                    <X size={12} />
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}

// ── The order being dictated ──

function OrderCard({ order, gate, final, confirming, onConfirm, ready }: {
  order: ReturnType<typeof useCopilotSession>["order"]; gate: CopilotGate | null; final: boolean; confirming: boolean; onConfirm: () => void; ready: boolean;
}) {
  return (
    <div className="shrink-0 border-t border-border">
      <div className="flex h-10 items-center gap-2 border-b border-border px-4">
        <span className={SECTION_LABEL}>Commande en cours</span>
        {order?.number ? <span className="font-mono text-[11.5px] text-muted-foreground">{order.number}</span> : null}
        {order ? <span className="ml-auto"><Badge tone={order.status === "confirmed" ? "success" : "warning"}>{order.status === "confirmed" ? "Confirmée" : "Brouillon"}</Badge></span> : null}
      </div>
      {!order ? (
        <p className="px-4 py-4 text-[12px] text-muted-foreground">Elle se crée dans l'ERP dès que le client dicte une première ligne.</p>
      ) : (
        <div className="flex flex-col gap-2 px-4 py-3">
          <div className="flex items-baseline justify-between gap-2 text-[12.5px]">
            <span className="truncate font-medium">{order.client}</span>
            {order.deliveryDate ? <span className="shrink-0 text-muted-foreground">livraison <DateLabel date={order.deliveryDate} format="long" /></span> : null}
          </div>
          <ol className="flex flex-col">
            {order.lines.map((l) => (
              <li key={l.lineId} className="flex animate-fade-in items-baseline gap-2 border-b border-border/60 py-1 text-[12.5px] last:border-b-0">
                <span className="min-w-0 flex-1 truncate">{l.productName ?? l.productId}</span>
                <Qty value={l.qty} unit={l.unit} tone={l.available != null && l.available < l.qty ? "warning" : undefined} />
                <Money value={l.unitPrice != null ? l.unitPrice * l.qty : undefined} className="w-[72px] text-right" />
              </li>
            ))}
          </ol>
          <div className="flex items-baseline justify-between text-[12.5px]">
            <span className="text-muted-foreground">Total HT</span>
            <Money value={order.totalHt} className="font-medium" />
          </div>
          <GateLine gate={gate} final={final} />
          <Button variant="primary" icon={Check} loading={confirming} disabled={!ready || final || !order.lines.length} onClick={onConfirm}>
            {final ? (order.status === "confirmed" ? "Commande validée" : "Envoyée à la vérification") : "Valider la commande"}
          </Button>
        </div>
      )}
    </div>
  );
}

function GateLine({ gate, final }: { gate: CopilotGate | null; final: boolean }) {
  if (!gate) return null;
  if (gate.decision !== "draft") {
    return <p className="flex items-center gap-1.5 text-[12px] text-success"><Check size={13} />{final ? "Confirmée : stock réservé, tournée affectée." : "Aucun garde-fou : la validation la confirmera."}</p>;
  }
  return (
    <div className="rounded-[4px] border border-warning/30 bg-warning/[.06] px-2.5 py-1.5 text-[12px]">
      <p className="font-medium text-warning">{final ? "Gardée en brouillon pour vérification" : "La validation la gardera en brouillon"}</p>
      <ul className="mt-0.5 list-disc pl-4 text-secondary-foreground">{gate.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
    </div>
  );
}
