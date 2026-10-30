"""Step-by-step search creation dialogue as a pure state machine.

The aiogram handlers only persist ``state`` (a JSON-able dict in FSM storage), call
``on_text`` / ``on_callback`` and render the returned ``Reply``. This keeps the whole
dialogue unit-testable without Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import Plan
from app.domain.validation import InvalidInput, parse_name, parse_phrases, parse_place, parse_price_range
from app.i18n import esc, format_interval, t
from app.presentation import Button, describe_criteria
from app.services.searches import SearchDraft
from app.sources.registry import DISPLAY_NAMES, SourceRegistry

STEPS = ["name", "sources", "keywords", "alt_keywords", "excluded", "category", "price", "condition", "city",
         "delivery", "summary"]
TEXT_STEPS = {"name", "keywords", "alt_keywords", "excluded", "price", "city"}
SKIPPABLE = {"alt_keywords", "excluded", "price", "city"}
CATEGORIES = ["elektronika", "dom", "moda", "sport", "motoryzacja", "dzieci"]
# Reseller presets for OLX (app/sources/olx_categories.py); an OLX search must pick one.
OLX_CATEGORIES = ["iphone", "laptopy", "komputery", "konsole", "karty_graficzne"]
CONDITIONS = ["any", "new", "used"]
DELIVERIES = ["any", "shipping", "pickup"]

FILTER_CAPABILITY = {"price": "price", "category": "category", "condition": "condition", "city": "location",
                     "delivery": "delivery"}


@dataclass
class Reply:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)
    finished: bool = False  # user pressed "save" -> caller creates the search from state["draft"]
    cancelled: bool = False
    error: bool = False


def _cancel_row(lang, skip=False) -> list[Button]:
    row = [Button(t("btn.cancel", lang), callback="wz:cancel")]
    if skip:
        row.insert(0, Button(t("btn.skip", lang), callback="wz:skip"))
    return row


class SearchWizard:
    def __init__(self, registry: SourceRegistry, lang: str | None = None) -> None:
        self.registry = registry
        self.lang = lang

    # ----------------------------------------------------------------- api
    def start(self, plan: Plan | None) -> tuple[dict, Reply]:
        selectable = [s for s in self.registry.selectable_codes if plan is None or s in plan.allowed_sources]
        state = {
            "step": "name",
            "draft": SearchDraft().to_dict(),
            "selectable": selectable,
            "min_interval": plan.min_interval_seconds if plan else 900,
            "fuzzy_allowed": bool(plan and plan.fuzzy_matching),
        }
        if len(selectable) == 1:  # only one source: preselect it, step is still shown for transparency
            state["draft"]["sources"] = list(selectable)
        return state, self.render(state)

    def on_text(self, state: dict, text: str) -> Reply:
        step = state["step"]
        if step not in TEXT_STEPS:
            reply = self.render(state)
            reply.error = True
            return reply
        draft = state["draft"]
        try:
            if step == "name":
                draft["name"] = parse_name(text)
            elif step == "keywords":
                draft["keywords"] = parse_phrases(text, allow_empty=True)
            elif step == "alt_keywords":
                draft["alt_keywords"] = parse_phrases(text, allow_empty=True)
                if not draft["keywords"] and not draft["alt_keywords"]:
                    raise InvalidInput("empty")
            elif step == "excluded":
                draft["excluded_keywords"] = parse_phrases(text, allow_empty=True)
            elif step == "price":
                low, high = parse_price_range(text)
                draft["price_min"] = str(low) if low is not None else None
                draft["price_max"] = str(high) if high is not None else None
            elif step == "city":
                draft["city"] = parse_place(text)
        except InvalidInput as exc:
            reply = self.render(state)
            reply.text = t(f"invalid.{exc.code}", self.lang, **exc.params) + "\n\n" + reply.text
            reply.error = True
            return reply
        return self._advance(state)

    def on_callback(self, state: dict, data: str) -> Reply:
        lang = self.lang
        step = state["step"]
        draft = state["draft"]
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        value = parts[2] if len(parts) > 2 else ""
        if action == "cancel":
            return Reply(t("cancelled", lang), cancelled=True)
        if action == "skip" and step in SKIPPABLE:
            if step == "alt_keywords" and not draft["keywords"]:
                reply = self.render(state)
                reply.text = t("deny.no_keywords", lang) + "\n\n" + reply.text
                reply.error = True
                return reply
            return self._advance(state)
        if step == "sources" and action == "src" and value in state["selectable"]:
            chosen = draft["sources"]
            draft["sources"] = [s for s in chosen if s != value] if value in chosen else chosen + [value]
            return self.render(state)
        if step == "sources" and action == "src_done":
            if not draft["sources"]:
                reply = self.render(state)
                reply.text = t("wizard.sources_none", lang) + "\n\n" + reply.text
                reply.error = True
                return reply
            return self._advance(state)
        if step == "category" and action == "cat" and (value in self._category_options(state)
                                                       or (value == "any" and not self._olx_selected(state))):
            draft["category"] = None if value == "any" else value
            return self._advance(state)
        if step == "condition" and action == "cond" and value in CONDITIONS:
            draft["condition"] = value
            return self._advance(state)
        if step == "delivery" and action == "del" and value in DELIVERIES:
            draft["delivery"] = value
            return self._advance(state)
        if step == "summary":
            if action == "save":
                return Reply("", finished=True)
            if action == "unknown":
                draft["include_unknown"] = not draft["include_unknown"]
            elif action == "deal":
                options = [None, 10, 15, 20, 30]
                current = draft.get("deal_min_discount")
                draft["deal_min_discount"] = options[(options.index(current) + 1) % len(options)] \
                    if current in options else 10
            elif action == "desc":
                draft["match_description"] = not draft.get("match_description", False)
            elif action == "fuzzy" and state["fuzzy_allowed"]:
                draft["fuzzy"] = not draft["fuzzy"]
            elif action == "interval":
                options = self.interval_options(state["min_interval"])
                current = draft["interval_seconds"] or options[0]
                idx = options.index(current) if current in options else -1
                draft["interval_seconds"] = options[(idx + 1) % len(options)]
            return self.render(state)
        reply = self.render(state)  # stale button from an earlier step
        reply.error = True
        return reply

    def draft(self, state: dict) -> SearchDraft:
        return SearchDraft.from_dict(state["draft"])

    # ------------------------------------------------------------- helpers
    @staticmethod
    def interval_options(min_interval: int) -> list[int]:
        return sorted({min_interval, *(v for v in (900, 1800, 3600, 3 * 3600) if v > min_interval)})

    def _advance(self, state: dict) -> Reply:
        state["step"] = STEPS[STEPS.index(state["step"]) + 1]
        if state["step"] == "category" and not self._category_useful(state):
            state["draft"]["category"] = None
            state["step"] = STEPS[STEPS.index("category") + 1]
        return self.render(state)

    @staticmethod
    def _olx_selected(state: dict) -> bool:
        return "olx" in state["draft"]["sources"]

    def _category_options(self, state: dict) -> list[str]:
        return OLX_CATEGORIES if self._olx_selected(state) else CATEGORIES

    def _category_useful(self, state: dict) -> bool:
        """Skip the category step when no selected source provides listing categories (e.g. OLX)."""
        for code in state["draft"]["sources"]:
            adapter = self.registry.get(code)
            if adapter is not None and adapter.capabilities().listing_category:
                return True
        return False

    def render(self, state: dict) -> Reply:
        lang, step, draft = self.lang, state["step"], state["draft"]
        if step == "sources":
            rows = [[Button(("✅ " if c in draft["sources"] else "⬜ ") + DISPLAY_NAMES.get(c, c), callback=f"wz:src:{c}")]
                    for c in state["selectable"]]
            rows += [[Button(t("btn.done", lang), callback="wz:src_done")], _cancel_row(lang)]
            return Reply(t("wizard.sources", lang), rows)
        if step == "category":
            options = self._category_options(state)
            buttons = [Button(t(f"category.{c}", lang), callback=f"wz:cat:{c}") for c in options]
            rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
            if not self._olx_selected(state):
                rows.append([Button(t("category.any", lang), callback="wz:cat:any")])
            rows.append(_cancel_row(lang))
            key = "wizard.category_olx" if self._olx_selected(state) else "wizard.category"
            return Reply(t(key, lang), rows)
        if step == "condition":
            rows = [[Button(t(f"condition.{c}", lang), callback=f"wz:cond:{c}")] for c in CONDITIONS]
            return Reply(t("wizard.condition", lang), rows + [_cancel_row(lang)])
        if step == "delivery":
            rows = [[Button(t(f"delivery.{d}", lang), callback=f"wz:del:{d}")] for d in DELIVERIES]
            return Reply(t("wizard.delivery", lang), rows + [_cancel_row(lang)])
        if step == "summary":
            return self._summary(state)
        return Reply(t(f"wizard.{step}", lang), [_cancel_row(lang, skip=step in SKIPPABLE)])

    def _summary(self, state: dict) -> Reply:
        lang, draft = self.lang, state["draft"]
        d = self.draft(state)
        interval = d.interval_seconds or state["min_interval"]
        sources = ", ".join(DISPLAY_NAMES.get(s, s) for s in d.sources)
        lines = [f"📝 <b>{esc(d.name)}</b>", f"🌐 {esc(sources)}", describe_criteria(d, lang),
                 f"⏱ {format_interval(interval, lang)}"]
        if d.category:
            lines.append("🗂 " + t(f"category.{d.category}", lang))
        for note in self.unsupported_notes(d):
            lines.append(note)
        rows = [[Button(t("wizard.btn_interval", lang, interval=format_interval(interval, lang)), callback="wz:interval")],
                [Button(t("wizard.btn_unknown_on" if draft["include_unknown"] else "wizard.btn_unknown_off", lang),
                        callback="wz:unknown")]]
        rows.append([Button(t("wizard.btn_desc_on" if draft.get("match_description") else "wizard.btn_desc_off", lang),
                            callback="wz:desc")])
        deal = draft.get("deal_min_discount")
        rows.append([Button(t("wizard.btn_deal_on", lang, pct=deal) if deal else t("wizard.btn_deal_off", lang),
                            callback="wz:deal")])
        if state["fuzzy_allowed"]:
            rows.append([Button(t("wizard.btn_fuzzy_on" if draft["fuzzy"] else "wizard.btn_fuzzy_off", lang),
                                callback="wz:fuzzy")])
        rows.append([Button(t("btn.save", lang), callback="wz:save"), Button(t("btn.cancel", lang), callback="wz:cancel")])
        return Reply(t("wizard.summary", lang, summary="\n".join(lines)), rows)

    def unsupported_notes(self, d: SearchDraft) -> list[str]:
        used = {
            "price": d.price_min is not None or d.price_max is not None,
            "category": d.category is not None,
            "condition": d.condition != "any",
            "city": d.city is not None,
            "delivery": d.delivery != "any",
        }
        notes = []
        for code in d.sources:
            adapter = self.registry.get(code)
            if adapter is None:
                continue
            caps = adapter.capabilities()
            missing = [t(f"filter.{f}", self.lang) for f, on in used.items()
                       if on and not getattr(caps, FILTER_CAPABILITY[f])]
            if missing:
                notes.append(t("wizard.unsupported_filters", self.lang, source=esc(DISPLAY_NAMES.get(code, code)),
                               filters=", ".join(missing)))
        return notes
