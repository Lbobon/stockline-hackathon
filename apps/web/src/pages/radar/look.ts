/** Radar look: one identity per event kind (icon + chart token) and one colour per truck. */
import { CalendarDays, Heart, Moon, Snowflake, TreePine, Trophy, type LucideIcon } from "lucide-react";
import type { EventKind } from "@/features/radar/types";

export const EVENT_STYLE: Record<EventKind, { icon: LucideIcon; label: string; hsl: string; text: string; soft: string }> = {
  sport: { icon: Trophy, label: "Match", hsl: "hsl(var(--chart-4))", text: "text-chart-4", soft: "bg-chart-4/10" },
  weather: { icon: Snowflake, label: "Météo", hsl: "hsl(var(--chart-2))", text: "text-chart-2", soft: "bg-chart-2/10" },
  wedding: { icon: Heart, label: "Mariages", hsl: "hsl(var(--chart-5))", text: "text-chart-5", soft: "bg-chart-5/10" },
  local: { icon: TreePine, label: "Quartier", hsl: "hsl(var(--chart-3))", text: "text-chart-3", soft: "bg-chart-3/10" },
  religious: { icon: Moon, label: "Fête religieuse", hsl: "hsl(var(--chart-1))", text: "text-chart-1", soft: "bg-chart-1/10" },
  holiday: { icon: CalendarDays, label: "Vacances", hsl: "hsl(var(--chart-2))", text: "text-chart-2", soft: "bg-chart-2/10" },
};

/** Trucks: blue, rose, teal, amber (the chart tokens not used by the pins' green). */
export const TRUCK_COLORS = ["hsl(var(--chart-4))", "hsl(var(--chart-5))", "hsl(var(--chart-2))", "hsl(var(--chart-3))"];
