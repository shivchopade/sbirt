from __future__ import annotations

import re
from collections import Counter, defaultdict

from app.domain.document import ParsedDocument, TextUnit
from app.domain.enums import NON_PAGE_SOURCES, Severity
from app.domain.flags import Evidence, Flag, snippet
from app.domain.job import JobContext
from app.services.integrity.base import first_location, location_of, regions_of, register
from app.services.preprocessing.sections import normalized_key

_LABELLED_LIST = re.compile(r"^[A-Za-z][A-Za-z &/+\-]{1,30}:\s*\S")


def _pretty(token: str) -> str:
    return token.removeprefix("skill:").replace("_", " ")


@register
class KeywordStuffingDetector:
    """Catches attempts to game keyword-counting ATSs: one JD keyword repeated far more than any natural resume
    would (> max(8, 3% of all tokens)), dense blocks of JD keywords with no verbs outside the Skills section,
    absurdly long skill lists (> 50 items) and the same line pasted three or more times. The score itself is
    immune (skills are a set, coverage uses max similarity) — this makes the attempt visible to the reviewer."""

    name = "keyword_stuffing"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        integ = ctx.settings.integrity
        units = [u for u in doc.units if u.source not in NON_PAGE_SOURCES and not u.is_heading]
        flags: list[Flag] = []

        counts = Counter(tok for u in units for tok in u.lexical)
        total = sum(counts.values())
        limit = max(integ.stuffing_min_repeats, integ.stuffing_token_ratio * total)
        stuffed = {k: c for k, c in counts.items() if k in ctx.parsed.keywords and c > limit}
        if stuffed:
            worst = max(stuffed.values())
            where = [u for u in units if set(u.lexical) & set(stuffed)]
            page, bbox = first_location(where)
            flags.append(
                Flag(
                    code="KEYWORD_STUFFING",
                    severity=Severity.HIGH if worst >= 2 * limit else Severity.MEDIUM,
                    detector=self.name,
                    message="JD keywords repeated unnaturally often: "
                    + ", ".join(
                        f"{_pretty(k)} ×{c}" for k, c in sorted(stuffed.items(), key=lambda kv: -kv[1])[:6]
                    ),
                    evidence=Evidence(
                        page=page,
                        bbox=bbox,
                        snippet=snippet(max(where, key=lambda u: len(u.lexical)).text) if where else None,
                        measured_value=float(worst),
                        threshold=round(limit, 2),
                        location=location_of(where) if where else None,
                        regions=regions_of([u for u in where if sum(t in stuffed for t in u.lexical) >= 3]),
                        details={
                            "counts": {_pretty(k): c for k, c in stuffed.items()},
                            "total_tokens": total,
                        },
                    ),
                )
            )

        dumps = [u for u in units if self._is_dump(u, ctx)]
        skill_items = sum(
            len([p for p in re.split(r"[,|;•·]", u.text.split(":", 1)[-1]) if p.strip()])
            for u in units
            if u.section == "skills"
        )
        if dumps or skill_items > integ.skills_list_max_items:
            ratio = max((self._keyword_ratio(u, ctx) for u in dumps), default=0.0)
            page, bbox = first_location(dumps) if dumps else (None, None)
            flags.append(
                Flag(
                    code="KEYWORD_DUMP",
                    severity=Severity.MEDIUM,
                    detector=self.name,
                    message=(
                        f"{len(dumps)} block(s) of bare JD keywords with no sentences outside the Skills section"
                        if dumps
                        else f"Skills list with {skill_items} items"
                    ),
                    evidence=Evidence(
                        page=page,
                        bbox=bbox,
                        snippet=snippet(dumps[0].text) if dumps else None,
                        measured_value=round(ratio, 3) if dumps else float(skill_items),
                        threshold=integ.dump_keyword_ratio if dumps else float(integ.skills_list_max_items),
                        location=location_of(dumps) if dumps else "visible",
                        regions=regions_of(dumps),
                        details={"min_tokens": integ.dump_min_tokens, "skills_list_items": skill_items},
                    ),
                )
            )

        repeated = self._repeated_lines(doc, integ.repeated_line_min_words, integ.repeated_line_min)
        if repeated:
            worst = max(repeated.values(), key=len)
            first = next((s for s in worst if s.bbox is not None), worst[0])
            flags.append(
                Flag(
                    code="REPEATED_CONTENT",
                    severity=Severity.LOW,
                    detector=self.name,
                    message=f"The same line appears {len(worst)} times",
                    evidence=Evidence(
                        page=first.page or None,
                        bbox=first.bbox,
                        snippet=snippet(doc.join_spans([first])),
                        measured_value=float(len(worst)),
                        threshold=float(integ.repeated_line_min),
                        location="visible" if all(s.visible for s in worst) else "mixed",
                        regions=[
                            {"page": s.page, "bbox": list(s.bbox)}
                            for ss in repeated.values()
                            for s in ss
                            if s.bbox is not None
                        ][:30],
                        details={"lines": {key: len(ss) for key, ss in repeated.items()}},
                    ),
                )
            )
        return flags

    def _repeated_lines(self, doc: ParsedDocument, min_words: int, min_count: int) -> dict[str, list]:
        """Visual lines (not units) — 'the same line ≥ 3 times'."""
        groups: dict[str, list] = defaultdict(list)
        for _page, _line, spans in doc.lines(visible=None):
            text = doc.join_spans(spans)
            if len(text.split()) >= min_words:
                first = next((s for s in spans if s.text.strip()), spans[0])
                groups[normalized_key(text)].append(first)
        return {k: v for k, v in groups.items() if len(v) >= min_count}

    def _keyword_ratio(self, unit: TextUnit, ctx: JobContext) -> float:
        return sum(t in ctx.parsed.keywords for t in unit.lexical) / max(1, len(unit.lexical))

    def _is_dump(self, unit: TextUnit, ctx: JobContext) -> bool:
        integ = ctx.settings.integrity
        if unit.section == "skills" or unit.has_verb or len(unit.lexical) < integ.dump_min_tokens:
            return False
        items = [p for p in re.split(r"[,|;•·]", unit.text.split(":", 1)[-1]) if p.strip()]
        if _LABELLED_LIST.match(unit.text) and len(items) < 25:
            return False  # "Tech stack: FastAPI, PostgreSQL, ..." under a project is a legit labelled list
        return self._keyword_ratio(unit, ctx) >= integ.dump_keyword_ratio
