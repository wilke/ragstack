"""Chunk-level boilerplate detection (ragstack.ingestion.boilerplate).

The negatives matter more than the positives here: the module's stated priority
is that dropping real content is far worse than keeping a boilerplate chunk, so
every realistic body paragraph below — including heavily-cited review prose,
which is the shape that most resembles a bibliography — must come back ``body``.
The prose samples are paraphrases of text from the open-access ``g1-corpus``
PDFs the defaults were calibrated on.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.boilerplate import (
    ACKNOWLEDGEMENTS,
    BODY,
    BOILERPLATE_KEY,
    LICENSE,
    REFERENCES,
    SECTION_KEY,
    BoilerplateConfig,
    BoilerplateFilter,
    classify_chunk,
    config_from_json,
    filter_from_mode,
    function_word_ratio,
    is_boilerplate,
    reference_signal_density,
)
from ragstack.models import Chunk

# --- the four chunks that were actually returned for "What is the role of bees?"
CC_LICENCE_FOOTER = (
    "The images or other third party material in this article are included in the "
    "article's Creative Commons licence, unless indicated otherwise in a credit line "
    "to the material. If material is not included in the article's Creative Commons "
    "licence and your intended use is not permitted by statutory regulation or exceeds "
    "the permitted use, you will need to obtain permission directly from the copyright "
    "holder. To view a copy of this licence, visit http://creativecommons.org/licenses/by/4.0/."
)
COPYRIGHT_LINE = (
    "creativecommons.org/licenses/by-nc-nd/4.0/ © The Author(s) 2026 Article "
    "https://doi.org/10.1038/s41586-026-01234-5"
)
REFERENCE_BLOCK = (
    "020.06.35.2.119\n"
    "2. Aizen MA, Aguiar S, Biesmeijer JC, Garibaldi LA, Roubik DW, Harder LD. Global "
    "agricultural productivity is threatened by increasing pollinator dependence without "
    "a parallel increase in crop diversification. Glob Chang Biol. 2019;25(10):3516-3527. "
    "doi:10.1111/gcb.14736\n"
    "3. Klein AM, Vaissiere BE, Cane JH, Steffan-Dewenter I, Cunningham SA, Kremen C. "
    "Importance of pollinators in changing landscapes for world crops. Proc R Soc B. "
    "2007;274:303-313. doi:10.1098/rspb.2006.3721\n"
    "4. Potts SG, Biesmeijer JC, Kremen C, Neumann P, Schweiger O, Kunin WE. Global "
    "pollinator declines: trends, impacts and drivers. Trends Ecol Evol. 2010;25:345-353.\n"
)
ACKNOWLEDGEMENTS_BLOCK = (
    "Acknowledgements\n"
    "Sequence data generated in this study have been deposited in the NCBI Sequence Read "
    "Archive under BioProject PRJNA123456. The authors thank the sequencing core facility "
    "and two anonymous reviewers for their helpful comments on an earlier draft."
)

# --- realistic negatives (paraphrased g1-corpus body text) -------------------
METHODS_PROSE = (
    "Colonies were maintained in standard Langstroth hives at the apiary from May to "
    "September. Foraging activity was recorded at the hive entrance for 10 min per hour "
    "between 09:00 and 17:00 on days without rain. Pollen loads were collected from "
    "returning foragers using entrance traps and identified to plant genus under a light "
    "microscope. We fitted a generalised linear mixed model with colony as a random "
    "effect to test whether foraging rate differed between the two treatments."
)
RESULTS_PROSE = (
    "Bees visited flowers of Brassica napus significantly more often than those of "
    "Trifolium pratense (mean 4.2 versus 1.1 visits per plant per hour; P < 0.001). "
    "Pollination service, measured as seed set, increased with visitation rate up to an "
    "asymptote at roughly six visits per flower. The role of bees in this system "
    "therefore appears to saturate rather than to increase linearly with abundance."
)
REVIEW_PROSE_HEAVILY_CITED = (
    "Diagnosis and treatment of non-carbapenemase CRE. To obtain the best outcome from "
    "the limited treatment options that are effective against CRE, a personalised "
    "approach to antibiotic dosing has been urged (Doi, 2019; Doi and Paterson, 2015; "
    "Reyes et al., 2019). Several authors have argued that combination therapy is "
    "superior to monotherapy (Tamma et al., 2017a; Falagas et al., 2014), although the "
    "evidence base for that claim remains contested."
)
INTRO_PROSE_CITED = (
    "Klebsiella pneumoniae is one of the leading causes of hospital-acquired infections "
    "globally (Pitout et al., 2015; David et al., 2019). Carbapenems are widely used to "
    "treat the serious infections that are caused by these organisms, and so resistance "
    "to them is of major clinical concern for the years ahead."
)
DISCUSSION_MENTIONING_FUNDING = (
    "A further limitation is that the sampling effort was not evenly distributed across "
    "sites, because funding for the third field season was reduced and two of the "
    "northern transects had to be dropped. We therefore interpret the between-region "
    "comparison with caution, and note that the acknowledgement of this imbalance does "
    "not change the direction of the effect we report for the remaining sites."
)


# --- positives --------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (CC_LICENCE_FOOTER, LICENSE),
        (COPYRIGHT_LINE, LICENSE),
        ("© 2024 The Authors. All rights reserved.", LICENSE),
        (REFERENCE_BLOCK, REFERENCES),
        (ACKNOWLEDGEMENTS_BLOCK, ACKNOWLEDGEMENTS),
        ("Competing interests: The authors declare no competing interests.", ACKNOWLEDGEMENTS),
        ("Author contributions\nA.B. designed the study; C.D. analysed the data.",
         ACKNOWLEDGEMENTS),
        ("Data availability statement\nAll sequences are available under PRJNA1.",
         ACKNOWLEDGEMENTS),
    ],
)
def test_boilerplate_is_flagged(text: str, expected: str) -> None:
    verdict = classify_chunk(text)
    assert verdict.section == expected
    assert verdict.is_boilerplate
    # Every positive carries a human-readable reason — that is what makes an
    # ingest-time drop auditable rather than silent.
    assert verdict.reason


def test_references_reason_names_the_evidence() -> None:
    verdict = classify_chunk(REFERENCE_BLOCK)
    assert "density" in verdict.reason
    assert verdict.density > 12.0


# --- negatives (the ones that matter) ---------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        METHODS_PROSE,
        RESULTS_PROSE,
        REVIEW_PROSE_HEAVILY_CITED,
        INTRO_PROSE_CITED,
        DISCUSSION_MENTIONING_FUNDING,
    ],
)
def test_real_prose_is_not_flagged(text: str) -> None:
    verdict = classify_chunk(text)
    assert verdict.section == BODY, verdict.reason
    assert not verdict.is_boilerplate


def test_single_incidental_licence_mention_in_long_prose_is_not_flagged() -> None:
    """One marker is not evidence in a chunk with room for real content."""
    text = (
        "All sequence data are released under a Creative Commons Attribution licence so "
        "that other groups can reuse them. " + RESULTS_PROSE
    )
    assert classify_chunk(text).section == BODY


def test_empty_text_is_body() -> None:
    assert classify_chunk("").section == BODY
    assert classify_chunk("   \n  ").section == BODY


def test_short_fragment_is_not_reference_flagged_on_density_alone() -> None:
    """Below ``reference_min_words`` the per-100-word ratio is meaningless."""
    assert classify_chunk("Smith JA, 2019, 12:3-9.").section == BODY


# --- the positional guard (the biggest false-positive source) ---------------
def test_trailing_section_header_does_not_condemn_leading_content() -> None:
    """A chunk that is 90% results and ends at the 'Acknowledgements' header is
    content: dropping it would drop the results."""
    text = RESULTS_PROSE + "\n" + METHODS_PROSE + "\nAcknowledgements\nWe thank the staff."
    assert classify_chunk(text).section == BODY
    # ...whereas the same header at the top does condemn it.
    assert classify_chunk("Acknowledgements\nWe thank the staff. " + RESULTS_PROSE).section == (
        ACKNOWLEDGEMENTS
    )


def test_trailing_references_header_does_not_condemn_leading_content() -> None:
    text = METHODS_PROSE + "\n\nReferences\n1. Smith JA. A paper. J Biol. 2019;1:1-2."
    assert classify_chunk(text).section == BODY


# --- the measures themselves ------------------------------------------------
def test_reference_density_separates_bibliography_from_prose() -> None:
    assert reference_signal_density(REFERENCE_BLOCK) > 20.0
    assert reference_signal_density(METHODS_PROSE) < 5.0


def test_function_word_ratio_separates_bibliography_from_prose() -> None:
    assert function_word_ratio(REFERENCE_BLOCK) < 0.22
    assert function_word_ratio(REVIEW_PROSE_HEAVILY_CITED) > 0.22


# --- config -----------------------------------------------------------------
def test_config_from_json_overrides_and_survives_garbage() -> None:
    assert config_from_json('{"reference_density": 30}').reference_density == 30
    # Malformed / unknown / wrong-typed input must never fail an ingest.
    assert config_from_json("not json").reference_density == 12.0
    assert config_from_json("[1, 2]").reference_density == 12.0
    assert config_from_json('{"nope": 1}').reference_density == 12.0
    assert config_from_json('{"reference_density": "high"}').reference_density == 12.0
    assert config_from_json("").reference_density == 12.0


def test_config_can_narrow_which_sections_count_as_boilerplate() -> None:
    cfg = config_from_json('{"boilerplate_sections": ["references"]}')
    assert is_boilerplate(REFERENCE_BLOCK, cfg)
    # Still *labelled* acknowledgements, but no longer treated as droppable.
    assert classify_chunk(ACKNOWLEDGEMENTS_BLOCK, cfg).section == ACKNOWLEDGEMENTS
    assert not is_boilerplate(ACKNOWLEDGEMENTS_BLOCK, cfg)


def test_raising_the_density_threshold_spares_a_borderline_chunk() -> None:
    strict = BoilerplateConfig(reference_density=99.0, reference_density_relaxed=99.0)
    assert classify_chunk(REFERENCE_BLOCK, strict).section == BODY


# --- the filter -------------------------------------------------------------
def _chunks(*texts: str, doc_id: str = "d1") -> list[Chunk]:
    return [
        Chunk(id=f"{doc_id}-{i}", doc_id=doc_id, content=t) for i, t in enumerate(texts)
    ]


def test_filter_flag_mode_stamps_but_never_removes() -> None:
    chunks = _chunks(METHODS_PROSE, REFERENCE_BLOCK, CC_LICENCE_FOOTER, RESULTS_PROSE)
    result = BoilerplateFilter().apply(chunks)

    assert len(result.chunks) == 4
    assert result.dropped == 0
    assert result.flagged == {REFERENCES: 1, LICENSE: 1}
    # Body chunks keep an untouched payload — no new keys at all.
    assert chunks[0].metadata == {}
    assert chunks[3].metadata == {}
    assert chunks[1].metadata == {SECTION_KEY: REFERENCES, BOILERPLATE_KEY: True}
    assert chunks[2].metadata == {SECTION_KEY: LICENSE, BOILERPLATE_KEY: True}


def test_filter_drop_mode_removes_only_the_boilerplate() -> None:
    chunks = _chunks(METHODS_PROSE, REFERENCE_BLOCK, CC_LICENCE_FOOTER, RESULTS_PROSE)
    result = BoilerplateFilter(drop=True).apply(chunks)

    assert [c.content for c in result.chunks] == [METHODS_PROSE, RESULTS_PROSE]
    assert result.dropped == 2
    assert sum(result.flagged.values()) == 2
    assert "dropped 2" in result.summary()


def test_filter_never_empties_an_all_boilerplate_document() -> None:
    """A document reduced to zero chunks either vanishes from the corpus or fails
    the ingest via EmptyIngestError — both worse than keeping the boilerplate."""
    chunks = _chunks(REFERENCE_BLOCK, CC_LICENCE_FOOTER, doc_id="all-boiler")
    result = BoilerplateFilter(drop=True).apply(chunks)

    assert len(result.chunks) == 2
    assert result.dropped == 0
    assert result.rescued_docs == ("all-boiler",)
    # ...and the chunks are still *flagged*, so the query side can demote them.
    assert all(c.metadata[BOILERPLATE_KEY] for c in result.chunks)


def test_all_boilerplate_guard_is_per_document_not_per_batch() -> None:
    """One document's rescue must not spare another document's boilerplate."""
    chunks = _chunks(REFERENCE_BLOCK, doc_id="boiler-only") + _chunks(
        METHODS_PROSE, CC_LICENCE_FOOTER, doc_id="mixed"
    )
    result = BoilerplateFilter(drop=True).apply(chunks)

    kept = {(c.doc_id, c.content) for c in result.chunks}
    assert ("boiler-only", REFERENCE_BLOCK) in kept       # rescued
    assert ("mixed", METHODS_PROSE) in kept
    assert ("mixed", CC_LICENCE_FOOTER) not in kept       # dropped
    assert result.rescued_docs == ("boiler-only",)


def test_filter_from_mode() -> None:
    assert filter_from_mode("off") is None
    flagger = filter_from_mode("flag")
    assert flagger is not None and flagger.drop is False
    dropper = filter_from_mode("drop", '{"reference_density": 30}')
    assert dropper is not None and dropper.drop is True
    assert dropper.config.reference_density == 30
