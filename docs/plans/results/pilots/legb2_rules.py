"""Leg B re-run — the three protocol fixes from RESULTS-legBC-pilots.md §2.6, as code.

Fix 1  specificity   : the query must name >=1 specific entity that also occurs in the
                       SOURCE SECTION and is rare in the corpus (high IDF). Machine-checked
                       two ways: an *intrinsic* check that trusts nothing (a high-IDF query
                       term present in the section) and an *anchor* check against the entity
                       the generator declares. The intrinsic one is the gate.
Fix 2  positive rule : the hand-written section-title blocklist is DELETED. A section is
                       eligible iff it is >= MIN_SEC_TOK tokens AND starts past absolute
                       token 1,024 AND carries >=1 numeric result or method noun.
Fix 3  shape         : <= MAX_QUERY_WORDS words AND no compound clause.

Every predicate here is evaluated on every item independently, so per-filter hit rates are
real rates and not the first-match-wins funnel the previous round reported.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Fix 2 — the positive section rule
# --------------------------------------------------------------------------- #
MIN_SEC_TOK = 250          # N
MAX_SEC_TOK = 2200         # unchanged upper bound: keeps the paraphrase input sane
MIN_START_TOK = 1024       # absolute, in the same SFR coordinate system as the grid

# A "numeric result": a digit that is doing measurement/statistics work, not a section
# number or a citation. Each alternative is anchored on a unit, a statistic, or a relation.
_NUMERIC_RESULT = re.compile(
    r"""(
        \bp\s*[<>=]\s*0?\.\d+                       # p < 0.05
      | \b\d+(?:\.\d+)?\s*%                          # 37.2 %
      | \bn\s*=\s*\d+                                # n = 42
      | \b\d+(?:\.\d+)?\s*(?:±|\+/-)\s*\d+      # 4.2 +/- 0.3
      | \b\d+(?:\.\d+)?[-\s]*fold\b                  # 3-fold
      | \b95\s*%?\s*(?:CI|confidence)                # 95% CI
      | \b\d+(?:\.\d+)?\s*(?:mM|uM|µM|nM|pM|mg|µg|ug|ng|pg|kg|mL|ml|µL|ul|
                             nm|µm|um|mm|cm|bp|kb|Mb|kDa|Da|kcal|Hz|mV|rpm|
                             °C|min|h|hr|hours?|days?|weeks?|months?|years?)\b
      | \b(?:mean|median|SD|SEM|IQR|odds\ ratio|hazard\ ratio|AUC|R\^?2|r\ =\ )\s*
        [^.]{0,20}\d
      | \bTable\s*\d|\bFig(?:ure)?\.?\s*\d           # a section that reports a figure
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# A "method noun": vocabulary that only appears where an experiment, a computation or a
# study design is being described. Substring match on the lowercased section text.
_METHOD_NOUNS = (
    "assay", "pcr", "sequencing", "rna-seq", "chip-seq", "western blot", "immunoblot",
    "immunohistochem", "immunofluoresc", "elisa", "flow cytometry", "cytometr",
    "microscop", "chromatograph", "spectrometr", "spectroscop", "electrophoresis",
    "crystallograph", "nmr ", "simulation", "simulated", "algorithm", "pipeline",
    "workflow", "software", "dataset", "data set", "cohort", "randomis", "randomiz",
    "knockout", "knock-out", "knockdown", "transfect", "transduc", "plasmid", "primer",
    "antibody", "antibodies", "cell line", "culture", "incubat", "centrifug", "purifi",
    "titrat", "calibrat", "regression", "anova", "t-test", "chi-square", "bootstrap",
    "cross-validation", "classifier", "neural network", "machine learning", "parameter",
    "statistical analys", "protocol", "reagent", "buffer", "strain", "mutant", "quantif",
    "normalis", "normaliz", "threshold", "benchmark", "measured", "was performed",
    "were performed", "we used", "in vitro", "in vivo", "mouse model", "murine model",
)


def section_signals(text: str) -> dict:
    """The two positive signals fix 2 asks for, reported separately so each can be rated."""
    low = text.lower()
    nums = _NUMERIC_RESULT.findall(text)
    hits = [m for m in _METHOD_NOUNS if m in low]
    return {
        "numeric_result": len(nums) > 0,
        "n_numeric": len(nums),
        "method_noun": len(hits) > 0,
        "method_nouns": hits[:8],
    }


def section_eligible(*, n_tok: int, start_tok: int, text: str, cls: str) -> dict:
    """Evaluate every clause of the positive rule independently (never first-match-wins)."""
    sig = section_signals(text)
    clauses = {
        "is_body_unit": cls != "abstract",
        "min_tokens": n_tok >= MIN_SEC_TOK,
        "max_tokens": n_tok <= MAX_SEC_TOK,
        "past_1024": start_tok > MIN_START_TOK,
        "has_result_or_method": sig["numeric_result"] or sig["method_noun"],
    }
    return {"clauses": clauses, "eligible": all(clauses.values()), **sig}


# The DELETED blocklist, kept only so the re-run can report the crosstab "what the positive
# rule catches that the blocklist missed, and vice versa". It is never used to filter.
LEGACY_BLOCKLIST = (
    "conclusion", "concluding", "summary", "abbreviation", "acknowledg",
    "competing interest", "author contribution", "authors' contribution",
    "authors’ contribution", "funding", "consent", "supplementary",
    "supporting information", "pre-publication", "availability of data", "ethics",
)


def legacy_blocked(title: str) -> bool:
    tl = (title or "").lower()
    return any(w in tl for w in LEGACY_BLOCKLIST)


# --------------------------------------------------------------------------- #
# Fix 3 — query shape
# --------------------------------------------------------------------------- #
MAX_QUERY_WORDS = 20

# A compound clause: a coordinator followed (within a short window) by something that can
# only start a second finite clause -- an auxiliary, an interrogative, or a subject pronoun.
_COMPOUND = re.compile(
    r"(?:,\s*(?:and|but|or|while|whereas)\b"
    r"|\b(?:and|or|but|while|whereas|as\s+well\s+as)\s+"
    r"(?:\w+\s+){0,2}?"
    r"(?:does|do|did|is|are|was|were|has|have|had|can|could|will|would|should|may|might|"
    r"how|what|why|whether|which|when|where|it|they|this|these|there)\b)",
    re.IGNORECASE,
)


def query_shape(q: str) -> dict:
    words = q.split()
    return {
        "n_words": len(words),
        "too_long_20": len(words) > MAX_QUERY_WORDS,
        "compound": bool(_COMPOUND.search(q)),
    }


# --------------------------------------------------------------------------- #
# Fix 1 — specificity
# --------------------------------------------------------------------------- #
# idf_table gives ln((N+1)/(df+1)) + 1 over N = 10,000 OA title+abstracts.
# df <= 1% of the corpus  ->  idf >= ln(10001/101) + 1 = 5.60.
IDF_SPECIFIC = 5.60


def specificity(query: str, section_text: str, idf: dict, tokenize, entity: str = "") -> dict:
    """Does the query name something specific that the source section also names?

    ``intrinsic`` trusts nothing: at least one of the query's own content terms must be
    rare in the corpus AND present in the source section. ``anchor`` additionally checks
    the entity string the generator declared, which is a cross-check on the prompt clause
    rather than a second gate.
    """
    q_terms = set(tokenize(query))
    s_terms = set(tokenize(section_text))
    shared = q_terms & s_terms
    rare_shared = sorted(t for t in shared if idf.get(t, 99.0) >= IDF_SPECIFIC)

    e_terms = set(tokenize(entity or ""))
    anchor_in_query = bool(e_terms) and e_terms <= q_terms
    anchor_in_section = bool(e_terms) and bool(e_terms & s_terms)
    anchor_rare = bool(e_terms) and any(idf.get(t, 99.0) >= IDF_SPECIFIC for t in e_terms)

    return {
        "n_rare_shared": len(rare_shared),
        "rare_shared": rare_shared[:6],
        "max_shared_idf": round(max((idf.get(t, 99.0) for t in shared), default=0.0), 3),
        "specific": len(rare_shared) >= 1,
        "anchor_ok": bool(anchor_in_query and anchor_in_section and anchor_rare),
        "anchor_in_query": anchor_in_query,
        "anchor_in_section": anchor_in_section,
        "anchor_rare": anchor_rare,
    }
