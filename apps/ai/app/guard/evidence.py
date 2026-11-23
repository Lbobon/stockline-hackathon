"""What the ERP said during the call: the facts a sentence of the agent may repeat.

Every tool result of the call (voice agent and side agents), every fact the Éclaireur prefetched, the hidden brief of an
outbound call and the business date become short, labelled items. Jev reads them as `faits_erp`; the UI shows the one
that backs a sentence (« Prix client · Gigot d'agneau → prix 14,20 € »).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .numbers import fmt_number

LABEL = {
    "get_price": "Prix client", "check_product": "Produit (stock et prix)", "get_product_stock": "Stock",
    "find_alternatives": "Remplaçants", "get_order": "Commande", "create_draft_order": "Commande (brouillon)",
    "add_order_line": "Commande (ligne ajoutée)", "update_order_line": "Commande (ligne modifiée)",
    "remove_order_line": "Commande (ligne retirée)", "update_order": "Commande (modifiée)", "finalize_order": "Commande (validation)",
    "confirm_order": "Commande (confirmée)", "get_client": "Fiche client", "find_client": "Fiche client", "get_context": "Contexte client",
    "get_client_history": "Historique client", "get_delivery": "Livraison", "search": "Catalogue", "search_context": "Contexte",
    "retention_offer": "Offre de fidélisation", "apply_substitution": "Substitution", "shortage_triage": "Ruptures",
}  # fmt: skip
SCOUT_LABEL = {"client": "Fiche client", "context": "Contexte client", "order": "Commande", "stock": "Stock",
               "alternatives": "Remplaçants", "price": "Prix client"}  # fmt: skip
SKIP_TOOLS = {"log_client_event", "save_context", "log_supplier_event"}  # writes of notes: nothing to repeat to the client
NOISE = {"lineId", "createdAt", "updatedAt", "lotId", "explanation", "candidates", "howToTalk", "prospect",
         "onHand", "reserved"}  # physical stock: the client is only ever told what is available (Jev took 382 on hand for « 300 dispo »)
STATUS_FR = {"draft": "brouillon", "confirmed": "confirmée", "prepared": "préparée", "delivered": "livrée", "invoiced": "facturée",
             "cancelled": "annulée"}  # fmt: skip
DAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def french_day(iso: str) -> str:
    """2026-11-25 → « mercredi 25 novembre »."""
    from datetime import date

    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{DAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]}"


# What Jev reads: ERP keys renamed so that a catalogue price is never taken for the client's price, nor the physical stock
# for what can still be sold (the two misses of the first eval).
KEY_FR = {"price": "prixCatalogueHT", "listPrice": "prixCatalogueHT", "unitPrice": "prixUnitaireHT", "available": "disponibleALaVente",
"incoming": "arrivagesAttendus",
          "totalHt": "totalHT", "deliveryDate": "dateLivraison", "qty": "quantite", "earliestExpiryInStock": "dlcLaPlusProche",
          "nextArrival": "prochainArrivage", "eta": "heureEstimeeDePassage", "departure": "departCamion"}  # fmt: skip
_DATE_KEYS = {"deliveryDate", "date", "nextArrival", "earliestExpiryInStock", "expectedDate"}


def _clean(x: Any, depth: int = 0) -> Any:
    if isinstance(x, dict):
        out: dict[str, Any] = {}
        for k, v in x.items():
            if k in NOISE or v in (None, "", [], {}):
                continue
            if k == "clientPrice" and isinstance(v, dict):  # check_product: the client's own price
                out["prixClientHT"] = v.get("price")
                continue
            if k in _DATE_KEYS and isinstance(v, str) and len(v) >= 10:
                v = f"{v[:10]} ({french_day(v)})"
            out[KEY_FR.get(k, k)] = _clean(v, depth + 1)
        return out
    if isinstance(x, list):
        return [_clean(v, depth + 1) for v in x[:12]]
    return x


def compact(x: Any, budget: int = 1100) -> str:
    s = json.dumps(_clean(x), ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= budget else s[: budget - 1] + "…"


@dataclass
class Item:
    key: str
    label: str  # « Prix client · Gigot d'agneau avec os »
    tool: str  # get_price, scout:stock, brief, meta
    text: str  # what Jev reads
    data: Any = None  # the raw result, for the highlights shown in the UI
    seq: int = 0  # newer = higher


def _subject(result: Any, args: dict, names: dict[str, str]) -> str:
    if isinstance(result, dict):
        if isinstance(result.get("order"), dict):
            result = result["order"]
        if isinstance(result.get("client"), dict):
            return result["client"].get("name") or ""
        if isinstance(result.get("products"), list) and result["products"]:
            return ", ".join(p.get("name", "") for p in result["products"][:2] if isinstance(p, dict))
        if result.get("number") or result.get("client"):
            return " ".join(str(v) for v in (result.get("number"), result.get("client")) if v)
        if result.get("name"):
            return str(result["name"])
    pid = args.get("productId") or args.get("product_id")
    return names.get(pid, "") if pid else ""


def _walk_names(x: Any, names: dict[str, str]) -> None:
    if isinstance(x, dict):
        if isinstance(x.get("id"), str) and x["id"].startswith("prd_") and x.get("name"):
            names[x["id"]] = x["name"]
        if isinstance(x.get("productId"), str) and x.get("productName"):
            names[x["productId"]] = x["productName"]
        for v in x.values():
            _walk_names(v, names)
    elif isinstance(x, list):
        for v in x:
            _walk_names(v, names)


@dataclass
class EvidenceBook:
    items: dict[str, Item] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)  # prd_… → product name
    _seq: int = 0
    _seen_calls: int = 0

    def _put(self, item: Item) -> Item:
        self._seq += 1
        item.seq = self._seq
        self.items.pop(item.key, None)
        self.items[item.key] = item
        return item

    def add_fact(self, key: str, label: str, text: str, tool: str = "fact", data: Any = None) -> Item:
        return self._put(Item(key, label, tool, text, data))

    def add_tool(self, name: str, args: dict, result: Any, ok: bool = True) -> Item | None:
        if not ok or name in SKIP_TOOLS or result is None:
            return None
        _walk_names(result, self.names)
        subject = _subject(result, args or {}, self.names)
        label = LABEL.get(name, name) + (f" · {subject}" if subject else "")
        pid = (args or {}).get("productId")
        about = f" (produit {self.names.get(pid, pid)})" if pid and name == "get_price" else ""
        key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        shown = result
        if name == "get_price" and isinstance(result, dict) and "price" in result:
            shown = {"prixClientHT": result["price"], **{k: v for k, v in result.items() if k != "price"}}
        return self._put(Item(key, label, name, f"{label}{about} → {compact(shown)}", result))

    def sync(self, state: Any) -> None:
        """Pull what a CallState knows: the Éclaireur's facts and every tool call since the last sync."""
        for k, text in list(getattr(state, "facts", {}).items()):
            kind = k.split(":", 1)[0]
            if kind == "candidates" or f"scout:{k}" in self.items:
                continue
            data = None
            if "→ " in text:
                try:
                    data = json.loads(text.rsplit("→ ", 1)[1])
                    _walk_names(data, self.names)
                except ValueError:
                    pass
            subject = _subject(data, {}, self.names) if data is not None else ""
            label = SCOUT_LABEL.get(kind, kind) + (f" · {subject}" if subject else "")
            self._put(Item(f"scout:{k}", label, f"scout:{kind}", text if len(text) < 1400 else text[:1399] + "…", data))
        calls = getattr(state, "tool_calls", [])
        for c in calls[self._seen_calls :]:
            self.add_tool(c["name"], c.get("args") or {}, c.get("result"), c.get("ok", True))
        self._seen_calls = len(calls)

    def view(self, budget: int = 7000) -> list[Item]:
        """The newest items that fit in `budget` characters (the date and the brief always first)."""
        pinned = [i for i in self.items.values() if i.tool in ("meta", "brief")]
        rest = sorted((i for i in self.items.values() if i.tool not in ("meta", "brief")), key=lambda i: -i.seq)
        out, used = list(pinned), sum(len(i.text) for i in pinned)
        for i in rest:
            if used + len(i.text) > budget:
                continue
            out.append(i)
            used += len(i.text)
        return out


# ── what the UI shows next to a ✓ / ✗: the ERP values, in words ──

_PRICE_KEYS = ("price", "unitPrice", "clientPrice", "listPrice")


def highlights(data: Any, numbers: list[float] | None = None, limit: int = 3, tool: str = "") -> list[str]:
    """« prix 14,20 € », « disponible 87 kg », « livraison mercredi 25 novembre », … the values matching the sentence first."""
    found: list[tuple[bool, str]] = []

    def say(k: str, v: Any, unit: str) -> str | None:
        if k in _PRICE_KEYS and isinstance(v, (int, float)):
            what = "prix client" if k == "clientPrice" or tool == "get_price" and k == "price" else "prix" if k == "unitPrice" else "prix catalogue"
            return f"{what} {fmt_number(v)} €" + (f"/{unit}" if unit else "")
        if k == "available" and isinstance(v, (int, float)):
            return f"disponible {fmt_number(round(v, 1))} {unit}".strip()
        if k == "incoming" and isinstance(v, (int, float)) and v:
            return f"arrivage {fmt_number(round(v, 1))} {unit}".strip()
        if k == "qty" and isinstance(v, (int, float)):
            return f"quantité {fmt_number(v)} {unit}".strip()
        if k == "totalHt" and isinstance(v, (int, float)):
            return f"total {fmt_number(v)} € HT"
        if k == "deliveryDate" and isinstance(v, str):
            return f"livraison {french_day(v)}"
        if k == "status" and v in STATUS_FR:
            return f"commande {STATUS_FR[v]}"
        if k == "eta" and isinstance(v, str):
            return f"passage vers {v}"
        return None

    def walk(x: Any, unit: str = "", depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(x, dict):
            unit = x.get("unit") or unit
            name = x.get("productName") or (x.get("name") if isinstance(x.get("id"), str) and x["id"].startswith("prd_") else None)
            for k, v in x.items():
                if k == "clientPrice" and isinstance(v, dict):
                    v = v.get("price")
                if (s := say(k, v, unit)) is not None:
                    match = bool(numbers) and isinstance(v, (int, float)) and any(abs(float(v) - n) < 0.011 or round(float(v)) == n for n in numbers)
                    found.append((match, f"{name} : {s}" if name and k in ("qty", "unitPrice") else s))
                elif isinstance(v, (dict, list)):
                    walk(v, unit, depth + 1)
        elif isinstance(x, list):
            for v in x[:8]:
                walk(v, unit, depth + 1)

    walk(data.get("order") if isinstance(data, dict) and isinstance(data.get("order"), dict) else data)
    seen, out = set(), []
    for _, s in sorted(found, key=lambda f: not f[0]):
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:limit]
