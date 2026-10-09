"""spaCy-based NLP: sentence splitting, lexical text (stopwords removed + lemmatised), skills, PII masking.

* Lexical text = lowercase → stopword removal (spaCy list + custom list) → lemmas, with every matched skill
  kept as one protected token (``skill:machine_learning``). Used for skill matching, keyword density and
  stuffing checks.
* SBERT input = normalized, PII-masked *natural* sentence (stopwords kept), because SBERT is trained on
  natural language. ``SBERT_INPUT=stopword_removed`` switches to the lexical form for comparison.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from app.services.extraction.contact import EMAIL_RE, PHONE_RE, URL_RE
from app.services.preprocessing.lexicons import extra_stopwords
from app.services.scoring.skills import SkillCatalog, SkillMatcher

_NUMERIC = re.compile(r"^[\d.,%+\-/:]+$")


@dataclass
class TextAnalysis:
    lexical: list[str] = field(default_factory=list)
    skills: dict[str, str] = field(default_factory=dict)  # canonical skill → surface form
    has_verb: bool = False
    masked: str = ""
    content_tokens: int = 0  # non-stopword, non-punct tokens (incl. skills)
    lexical_pos: list[str] = field(default_factory=list)  # POS of each lexical token (SKILL for skills)


class NlpEngine:
    def __init__(self, model: str = "en_core_web_sm", catalog: SkillCatalog | None = None):
        import spacy

        self.nlp = spacy.load(model, exclude=["parser"])
        if "senter" in self.nlp.disabled:
            self.nlp.enable_pipe("senter")
        self.catalog = catalog or SkillCatalog()
        self.matcher = SkillMatcher(self.nlp, self.catalog)
        self.stopwords = frozenset(w.lower() for w in self.nlp.Defaults.stop_words) | extra_stopwords()
        self._skill_terms = {f.lower() for s in self.catalog for f in (s.name, s.display, *s.aliases)}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ sentences
    def sentences(self, text: str) -> list[str]:
        with self._lock:
            doc = self.nlp(text, disable=["ner", "lemmatizer", "tagger", "attribute_ruler"])
        return [s.text.strip() for s in doc.sents if s.text.strip()]

    # ------------------------------------------------------------------ analysis
    def analyze(self, texts: list[str], *, mask_names: tuple[str, ...] = ()) -> list[TextAnalysis]:
        name_re = self._name_regex(mask_names)
        with self._lock:
            docs = list(self.nlp.pipe(texts, batch_size=64))
        return [self._analyze_doc(doc, name_re) for doc in docs]

    def _analyze_doc(self, doc, name_re: re.Pattern[str] | None) -> TextAnalysis:
        out = TextAnalysis()
        skill_spans = self.matcher.find(doc)
        in_skill: dict[int, str] = {}
        for span in skill_spans:
            out.skills.setdefault(span.label_, span.text)
            for i in range(span.start, span.end):
                in_skill[i] = span.label_
        emitted: set[int] = set()
        for tok in doc:
            if tok.pos_ == "VERB":
                out.has_verb = True
            if tok.i in in_skill:
                start = next(s for s in skill_spans if s.start <= tok.i < s.end)
                if start.start not in emitted:
                    emitted.add(start.start)
                    out.lexical.append(self.catalog[in_skill[tok.i]].token)
                    out.lexical_pos.append("SKILL")
                    out.content_tokens += 1
                continue
            if tok.is_punct or tok.is_space or not tok.text.strip():
                continue
            lower = tok.lower_
            if lower in self.stopwords or tok.is_stop or _NUMERIC.match(lower) or len(lower) < 2:
                continue
            lemma = tok.lemma_.lower().strip()
            if lemma and lemma not in self.stopwords:
                out.lexical.append(lemma)
                out.lexical_pos.append(tok.pos_)
                out.content_tokens += 1
        out.masked = self._mask(doc, skill_spans, name_re)
        return out

    # ------------------------------------------------------------------ PII
    def _name_regex(self, names: tuple[str, ...]) -> re.Pattern[str] | None:
        parts = sorted({p for n in names if n for p in n.split() if len(p) >= 3}, key=len, reverse=True)
        return re.compile(r"\b(" + "|".join(map(re.escape, parts)) + r")\b", re.I) if parts else None

    def _mask(self, doc, skill_spans, name_re: re.Pattern[str] | None) -> str:
        text = doc.text
        spans: list[tuple[int, int, str]] = []
        for rx, tag in ((EMAIL_RE, "[EMAIL]"), (URL_RE, "[URL]")):
            spans += [(m.start(), m.end(), tag) for m in rx.finditer(text)]
        for m in PHONE_RE.finditer(text):
            if len(re.sub(r"\D", "", m.group(0))) >= 10:
                spans.append((m.start(), m.end(), "[PHONE]"))
        skill_chars = {(s.start_char, s.end_char) for s in skill_spans}
        for ent in doc.ents:
            if ent.label_ != "PERSON":
                continue
            overlaps_skill = any(a < ent.end_char and ent.start_char < b for a, b in skill_chars)
            if overlaps_skill or ent.text.lower() in self._skill_terms:
                continue  # spaCy often tags Django / Kafka as PERSON — never mask a skill
            spans.append((ent.start_char, ent.end_char, "[NAME]"))
        if name_re:
            spans += [(m.start(), m.end(), "[NAME]") for m in name_re.finditer(text)]
        if not spans:
            return text
        spans.sort(key=lambda sp: (sp[0], -sp[1]))  # longest span wins (an email beats a name inside it)
        out, pos = [], 0
        for start, end, tag in spans:
            if start < pos:
                continue
            out.append(text[pos:start])
            out.append(tag)
            pos = end
        out.append(text[pos:])
        return re.sub(r"(\[(?:NAME|EMAIL|PHONE|URL)\])(\s*\1)+", r"\1", "".join(out))
