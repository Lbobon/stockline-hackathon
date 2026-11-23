/**
 * The call orchestra's agents, one identity each: name, role, icon and color (tokens --agent-*, a categorical set
 * validated for colour-blind separation in light and dark). The client and the Rapporteur stay neutral.
 * Used by the live strip on Voix, the Gantt and the transcript of a call report.
 */
import { AudioLines, Ear, FileText, Languages, MessageSquareQuote, NotebookPen, ShieldCheck, User, Zap, type LucideIcon } from "lucide-react";

export type AgentKey = "caller" | "voice" | "fast" | "scout" | "scribe" | "linguist" | "guard" | "reporter" | "copilot";

export interface AgentMeta {
  key: AgentKey;
  label: string;
  role: string;
  icon: LucideIcon;
  bg: string; // solid fill (bars, dots)
  soft: string; // tinted background (icon tiles)
  text: string; // icon / accent text (never body text)
  border: string;
}

export const AGENT: Record<AgentKey, AgentMeta> = {
  caller: { key: "caller", label: "Client", role: "parle au téléphone", icon: User, bg: "bg-foreground/25", soft: "bg-foreground/[.06]", text: "text-muted-foreground", border: "border-foreground/20" },
  voice: { key: "voice", label: "Agent vocal", role: "parle au client, décide", icon: AudioLines, bg: "bg-agent-voice", soft: "bg-agent-voice/10", text: "text-agent-voice", border: "border-agent-voice/40" },
  fast: { key: "fast", label: "Réflexe", role: "répond aux « oui » sans le grand modèle", icon: Zap, bg: "bg-agent-fast", soft: "bg-agent-fast/10", text: "text-agent-fast", border: "border-agent-fast/40" },
  scout: { key: "scout", label: "Éclaireur", role: "écoute et précharge l'ERP pendant que le client parle", icon: Ear, bg: "bg-agent-scout", soft: "bg-agent-scout/10", text: "text-agent-scout", border: "border-agent-scout/40" },
  scribe: { key: "scribe", label: "Greffier", role: "tient la fiche client en arrière-plan", icon: NotebookPen, bg: "bg-agent-scribe", soft: "bg-agent-scribe/10", text: "text-agent-scribe", border: "border-agent-scribe/40" },
  linguist: { key: "linguist", label: "Linguiste", role: "langue et tutoiement de l'appelant", icon: Languages, bg: "bg-agent-linguist", soft: "bg-agent-linguist/10", text: "text-agent-linguist", border: "border-agent-linguist/40" },
  guard: { key: "guard", label: "Garde-fou", role: "vérifie chaque phrase contre l'ERP avant qu'elle soit dite (Jev)", icon: ShieldCheck, bg: "bg-agent-guard", soft: "bg-agent-guard/10", text: "text-agent-guard", border: "border-agent-guard/40" },
  // Copilot mode only (Leïla speaks, no voice agent): the Souffleur takes the voice agent's slot and color.
  copilot: { key: "copilot", label: "Souffleur", role: "tient la commande dictée et souffle à Leïla", icon: MessageSquareQuote, bg: "bg-agent-voice", soft: "bg-agent-voice/10", text: "text-agent-voice", border: "border-agent-voice/40" },
  reporter: { key: "reporter", label: "Rapporteur", role: "écrit le compte rendu", icon: FileText, bg: "bg-muted-foreground/45", soft: "bg-muted-foreground/10", text: "text-muted-foreground", border: "border-muted-foreground/30" },
};

/** The working agents, in a fixed order (also the order of their colors). */
export const TEAM: AgentKey[] = ["voice", "fast", "scout", "scribe", "linguist", "guard"];
/** Copilot mode: the agents around Leïla. */
export const COPILOT_TEAM: AgentKey[] = ["copilot", "scout", "scribe", "linguist"];

export const agentOf = (k: string): AgentMeta => AGENT[(k === "warmup" ? "voice" : k) as AgentKey] ?? AGENT.reporter;

export const LANGUAGE: Record<string, string> = {
  fr: "Français", en: "English", ar: "العربية", es: "Español", it: "Italiano", tr: "Türkçe", de: "Deutsch", pt: "Português",
};
