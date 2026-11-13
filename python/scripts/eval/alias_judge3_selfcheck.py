"""Alias study, judge validation 2: the ``alias-judge/3`` self-check cases (JUDGE-3-PROMPT.md §4;
JUDGE-VALIDATION-2-PLAN.md §3.2, §4, §11 step 3).

Synthetic cases, **not** the prompt's six examples, at least one per rule, in the shape of
``VERIFY_CASES`` (``tests/eval/test_glossbench_alias.py``). All text is invented; no passage comes from
a paper. One dict per case:

* ``name`` -- a unique id; the committed list order is the canary selection's tie order (plan §4), and
  the labels are interleaved so that the order favours no label;
* ``title``, ``term``, ``question``, ``defs`` (a list of ``[alias, primary]``, only aliases occurring in
  the passage, sorted as by ``definitions_for``), ``passage`` -- the fields every judge sees
  (:func:`glossbench_alias_judge.judge_prompt_v3`);
* ``rules`` -- the rules of ``JUDGE_SYSTEM_V3`` the case exercises; ``rationale`` -- why;
* ``suggested`` -- the drafting agent's proposal, **non-authoritative metadata**: no code and no test
  reads it as the expected label;
* ``expected_label`` -- ``full`` / ``partial`` / ``none``, **set by the owner**. It is empty until then,
  and :func:`require_owner_labels` refuses to run a self-check, a canary selection or a canary while any
  case's label is empty.

The 23 cases below are the 2026-11-12 draft. The owner set every ``expected_label`` on 2026-11-13 (UTC).
Case names record the drafting intent, not the label: ``partial_vague_magnitude_for_fold`` is labelled
``none`` (the passage gives only a qualitative statement, no number). The owner marked three cases as
borderline: ``none_condition_contradicted_by_title`` (a transfer of the property is suggested by the
relationship, enough for a hypothesis), ``none_result_when_purpose_asked`` (a purpose can be inferred
from the result, but not that it was the original one) and ``partial_vague_magnitude_for_fold``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

GRADES = ("full", "partial", "none")
CASE_FIELDS = ("name", "title", "term", "question", "defs", "passage", "rules", "rationale", "suggested",
               "expected_label")
#: fields a case may omit (metadata only)
OPTIONAL_FIELDS = ("rules", "rationale", "suggested")

#: the committed self-check cases (draft of 2026-11-12; expected labels owner-set, empty until then)
SELF_CHECK_CASES_V3: list[dict[str, Any]] = [{'name': 'full_extra_detail',
  'title': 'Selection markers for engineered Quintara fibroblast lines',
  'term': 'brellomycin',
  'question': 'What concentration of brellomycin is used to select transformed Quintara fibroblasts?',
  'defs': [],
  'passage': 'Transformed Quintara fibroblasts were selected in 40 µg/mL brellomycin for ten days; untransformed '
             'controls died within four days, and surviving clones were expanded in brellomycin-free medium.',
  'rules': ['Deciding 4', 'Labels/full: extra information does not matter'],
  'rationale': 'The one part (the concentration) is stated exactly, and the condition (transformed Quintara '
               'fibroblasts) is met. The duration, the control deaths and the expansion are extra information.',
  'suggested': 'full',
  'expected_label': 'full'},
 {'name': 'none_topic_named_with_pointer',
  'title': 'Cytokine release by Hessel-line macrophages',
  'term': 'Brask-Ulm assay',
  'question': 'What does the Brask-Ulm assay measure?',
  'defs': [],
  'passage': 'Culture supernatants from all wells were analysed in triplicate with the Brask-Ulm assay, as '
             'described previously (ref. 14).',
  'rules': ['Deciding 2: naming what the question is about is never a part',
            'Parts: a pointer elsewhere states nothing',
            'Labels/none: names the method but not what is asked'],
  'rationale': 'The assay is named and used, but the passage never says what it measures. The reference to '
               'earlier work is a pointer and states nothing, so no part is STATED or WORKABLE.',
  'suggested': 'none',
  'expected_label': 'none'},
 {'name': 'partial_one_of_two_parts',
  'title': 'Histochemistry of Sarrow hepatocytes',
  'term': 'Velkin stain',
  'question': 'What does the Velkin stain label in Sarrow hepatocytes, and for how long is it applied?',
  'defs': [],
  'passage': 'In Sarrow hepatocytes, the Velkin stain labels lipid droplets bright orange.',
  'rules': ['Deciding 1: several parts',
            'Deciding 5: reason names what is missing',
            'Labels/partial: some parts stated, others absent'],
  'rationale': 'Two parts. What the stain labels (lipid droplets) is stated, and the application time is absent. '
               'The reason should name the missing duration.',
  'suggested': 'partial',
  'expected_label': 'partial'},
 {'name': 'full_condition_met_by_title',
  'title': 'Kestramide treatment lowers plasma glarin in Velloran mice',
  'term': 'kestramide',
  'question': 'In Velloran mice, by what percentage does kestramide lower plasma glarin?',
  'defs': [],
  'passage': 'After 14 days, kestramide lowered plasma glarin by 38% relative to vehicle.',
  'rules': ['Parts and conditions: condition MET via the paper title', 'Deciding 3', 'Deciding 4'],
  'rationale': 'The passage does not mention the organism, but the title states Velloran mice, so the condition '
               'is MET. The amount (38%) is stated as precisely as asked.',
  'suggested': 'full',
  'expected_label': 'full'},
 {'name': 'none_condition_contradicted_in_passage',
  'title': 'Anticonvulsant screening of selavane in Corbin rats',
  'term': 'selavane',
  'question': 'At what dose does selavane reduce seizure frequency in juvenile Corbin rats?',
  'defs': [],
  'passage': 'In adult Corbin rats, selavane at 15 mg/kg halved seizure frequency.',
  'rules': ['Deciding 3: one contradicted condition makes the label none',
            'Parts and conditions: condition CONTRADICTED'],
  'rationale': 'A dose is stated, but for adult rats. The passage states a different population from the '
               'juvenile one asked about, so the condition is CONTRADICTED and the label is none even though a '
               'value is present.',
  'suggested': 'none',
  'expected_label': 'none'},
 {'name': 'partial_condition_unstated',
  'title': 'Antimicrobial spectrum of skellomycin',
  'term': 'skellomycin',
  'question': 'What is the minimum inhibitory concentration of skellomycin against Brenlia spissa at pH 5?',
  'defs': [],
  'passage': 'Skellomycin inhibited growth of Brenlia spissa at concentrations of 8 µg/mL and above.',
  'rules': ['Parts and conditions: condition UNSTATED', 'Labels/partial: a condition is unstated'],
  'rationale': 'The MIC is stated in other words (8 µg/mL and above). The pH 5 condition appears in neither the '
               'passage, the title nor the definitions, so it is UNSTATED, not contradicted.',
  'suggested': 'partial',
  'expected_label': 'partial'},
 {'name': 'full_short_form_defined_as_term',
  'title': 'Solute transport in Narvosa hydrogels',
  'term': 'trellin diffusion coefficient',
  'question': 'How does the trellin diffusion coefficient in Narvosa gel change when the gel is cooled from 37 '
              '°C to 4 °C?',
  'defs': [['Dt', 'trellin diffusion coefficient']],
  'passage': 'Cooling the Narvosa gel from 37 °C to 4 °C halved Dt, consistent with its higher viscosity.',
  'rules': ['Rules bullets 2-3: a short form the paper defines as the TERM is the TERM', 'Labels/full'],
  'rationale': "The paper defines the symbol Dt as the TERM, so Dt counts as the TERM. 'Halved' states both the "
               'direction and the amount of the change under the stated conditions (Narvosa gel, 37 to 4 °C).',
  'suggested': 'full',
  'expected_label': 'full'},
 {'name': 'none_shared_short_form_other_concept',
  'title': 'Phosphorylation of the Hessel scaffold protein',
  'term': 'quarnel receptor kinase',
  'question': 'Which residue of quarnel receptor kinase is phosphorylated first after stimulation?',
  'defs': [['QRK', 'quarnel repeat knot']],
  'passage': 'Thr212 within the QRK of the scaffold protein was phosphorylated first after stimulation.',
  'rules': ['Rules bullet 2: a short form that stands for something else is NOT the TERM',
            'Labels/none: a different concept sharing a short form'],
  'rationale': "QRK looks like an abbreviation of the TERM, but this paper defines it as 'quarnel repeat knot', "
               'a different concept. The residue is stated about that concept, not about the TERM.',
  'suggested': 'none',
  'expected_label': 'none'},
 {'name': 'partial_fewer_items_than_asked',
  'title': 'Larval host range of Pallidora moths',
  'term': 'Pallidora ventrix',
  'question': 'Name the four host plants recorded for the moth Pallidora ventrix.',
  'defs': [],
  'passage': 'Larvae of Pallidora ventrix were collected from Quercitra alba and Brannoria minor, among other '
             'hosts.',
  'rules': ['Labels/full: all items needed when a number is asked',
            'Labels/partial: fewer items than the number asked',
            'Deciding 5'],
  'rationale': "The question asks for four items. Two are named, and 'among other hosts' does not name the rest. "
               'The reason should say that two of the four are missing.',
  'suggested': 'partial',
  'expected_label': 'partial'},
 {'name': 'full_all_items_listed',
  'title': 'Kinetic characterisation of drelmase variants',
  'term': 'drelmase',
  'question': 'At which three residues does the low-activity drelmase variant D3 differ from the wild type?',
  'defs': [],
  'passage': 'Drelmase variant D3 carries the substitutions S41A, H88Q and E130K and retains about a tenth of '
             'wild-type activity.',
  'rules': ['Labels/full: all items needed when a number is asked', 'Deciding 4'],
  'rationale': 'All three residues are stated (Ser41, His88, Glu130 in substitution notation). The residual '
               'activity is extra information.',
  'suggested': 'full',
  'expected_label': 'full'},
 {'name': 'partial_general_class_for_specific',
  'title': 'Extraction of jorvin from Tervanix cultures',
  'term': 'jorvin',
  'question': 'Which lysis buffer is used to release jorvin from Tervanix cells?',
  'defs': [],
  'passage': 'Tervanix cells were disrupted in a detergent-based lysis buffer, and jorvin was recovered from the '
             'cleared lysate.',
  'rules': ['Labels/partial: a general class where a specific item is asked', 'Deciding 4'],
  'rationale': 'The question asks which buffer. The passage gives only its class (detergent-based), not the '
               'specific buffer, so the part is stated less precisely than asked.',
  'suggested': 'partial',
  'expected_label': 'partial'},
 {'name': 'none_condition_contradicted_by_title',
  'title': 'Kelvane synthase of the soil fungus Krellomyces rubens',
  'term': 'kelvane synthase',
  'question': 'What is the pH optimum of kelvane synthase from Krellomyces fulvus?',
  'defs': [],
  'passage': 'Purified kelvane synthase was most active at pH 6.5 and lost half its activity at pH 8.',
  'rules': ['Parts and conditions: condition CONTRADICTED via the paper title', 'Deciding 3'],
  'rationale': 'The passage states a pH optimum, but the title places the enzyme in K. rubens, a different '
               'organism from the K. fulvus in the question. A contradicted condition makes the label none.',
  'suggested': 'none',
  'expected_label': 'none'},
 {'name': 'full_one_of_several_valid_answers',
  'title': 'Staining artefacts in fixed Ostrid leaf sections',
  'term': 'Halbeck wash',
  'question': 'Why is the Halbeck wash applied to tissue sections before staining?',
  'defs': [],
  'passage': 'Sections were given the Halbeck wash before staining, because residual fixative otherwise quenches '
             'the dye.',
  'rules': ['Labels/full: when a part can have several valid answers, one stated answer is enough'],
  'rationale': 'The question asks for a reason without asking for all reasons or a number of them. One reason is '
               'stated explicitly (removing fixative that quenches the dye), which is enough for full.',
  'suggested': 'full',
  'expected_label': 'full'},
 {'name': 'partial_condition_narrower_group',
  'title': 'Varnel-2 allele frequency in the Marrowfen cohort',
  'term': 'varnel-2 allele',
  'question': 'What proportion of adults in the Marrowfen cohort carry the varnel-2 allele?',
  'defs': [],
  'passage': 'Among women in the Marrowfen cohort, 22% carried the varnel-2 allele.',
  'rules': ['Labels/partial: a condition met only for a narrower group', 'Deciding 3', 'Deciding 4'],
  'rationale': 'A proportion is stated, but only for women, a subgroup of the adults asked about. The condition '
               'is narrowed, not contradicted. Hard boundary case on the partial/none line.',
  'suggested': 'partial',
  'expected_label': 'partial'},
 {'name': 'full_other_paper_own_threshold',
  'title': 'A revised contamination screen for Selbrin cell cultures',
  'term': 'Siltane index',
  'question': 'What Siltane index threshold is used to flag contamination in Selbrin cell cultures?',
  'defs': [],
  'passage': 'Unlike the original screen, we flagged a Selbrin culture as contaminated when its Siltane index '
             'exceeded 0.7.',
  'rules': ['Rules bullet 1: the passage may come from any paper',
            "Labels/full: another paper's answer counts even if it differs"],
  'rationale': 'The passage is from a different paper and openly departs from the original screen. It still '
               "states a threshold (0.7) under the question's condition, and an answer that differs from the "
               "question's own paper still counts.",
  'suggested': 'full',
  'expected_label': 'full'},
 {'name': 'none_result_when_purpose_asked',
  'title': 'Growth supplements for Pemmel algae cultures',
  'term': 'tolvaric acid',
  'question': 'What is the purpose of adding tolvaric acid to the Pemmel algae culture medium?',
  'defs': [],
  'passage': 'Adding tolvaric acid to the medium raised the final biomass of Pemmel algae cultures by 30%.',
  'rules': ['Labels/none: other facts about the term (its result when its purpose is asked)', 'Deciding 2'],
  'rationale': 'The passage gives the effect of tolvaric acid, not why it is added. The rubric names this exact '
               'pattern as none. Hard boundary case: a reader might infer a purpose from the effect, which the '
               'rubric treats as guessing.',
  'suggested': 'none',
  'expected_label': 'none'},
 {'name': 'partial_workable_by_calculation',
  'title': 'Rellow virus shedding in Kesmani ferrets',
  'term': 'Rellow virus',
  'question': 'How many days after infection do Rellow virus titres peak in Kesmani ferrets?',
  'defs': [],
  'passage': 'Kesmani ferrets were inoculated with Rellow virus on study day 3, and nasal titres peaked on study '
             'day 9.',
  'rules': ['Parts: WORKABLE',
            'Deciding 4: full needs the answer without working anything out',
            'Labels/partial: a part is only workable'],
  'rationale': 'The answer (6 days) follows from subtracting the two study days but is not stated. Under step 4 '
               'a workable-only part is partial. Hard boundary case: the arithmetic is trivial, so judges may '
               'say full.',
  'suggested': 'partial',
  'expected_label': 'partial'},
 {'name': 'full_condition_met_by_definitions',
  'title': 'Brainstem reflexes in captive primates',
  'term': 'thessel reflex latency',
  'question': 'What is the average thessel reflex latency in Selcan valley macaques?',
  'defs': [['SVQ', 'Selcan valley macaques']],
  'passage': 'In SVQ, the thessel reflex latency averaged 210 ms (n = 14).',
  'rules': ['Parts and conditions: condition MET via the definitions', 'Deciding 3', 'Deciding 4'],
  'rationale': "The population appears only as the short form SVQ. The paper's definitions say it stands for "
               'Selcan valley macaques, so the condition is MET. The average is stated.',
  'suggested': 'full',
  'expected_label': 'full'},
 {'name': 'partial_vague_magnitude_for_fold',
  'title': 'Longevity genes in Orvel worms',
  'term': 'vrel-4',
  'question': 'By how many fold does knockdown of vrel-4 extend lifespan in Orvel worms?',
  'defs': [],
  'passage': 'Knockdown of vrel-4 markedly extended the lifespan of Orvel worms.',
  'rules': ['Labels/partial: a direction where an amount is asked', 'Deciding 4', 'Deciding 5'],
  'rationale': "The direction is stated, and 'markedly' is an intensifier, not an amount. The fold change asked "
               'for is absent. The reason should name the missing amount.',
  'suggested': 'partial',
  'expected_label': 'none'},
 {'name': 'full_two_parts_paraphrased',
  'title': 'Brindol metabolism in Sallow yeast',
  'term': 'brindol',
  'question': 'Which enzyme converts brindol to oxobrindol in Sallow yeast, and in which cell compartment is it '
              'found?',
  'defs': [],
  'passage': 'In Sallow yeast, the mitochondrial oxidase Brx-2 turns brindol into oxobrindol.',
  'rules': ['Deciding 1: several parts', 'Parts: STATED in any words', 'Deciding 4'],
  'rationale': 'Two parts, both stated. The enzyme is Brx-2, and the compartment is given by the adjective '
               "'mitochondrial'. Hard boundary case: a part stated only in other words is still STATED.",
  'suggested': 'full',
  'expected_label': 'full'},
 {'name': 'partial_condition_broader_group',
  'title': 'Survival outcomes in the Tellan mouse lorvic tumour model',
  'term': 'lorvic tumours',
  'question': 'What is the median survival of Tellan mice with grade III lorvic tumours?',
  'defs': [],
  'passage': 'Tellan mice bearing lorvic tumours, pooled across grades I to III, survived a median of 41 days.',
  'rules': ['Labels/partial: a condition met only for a broader group', 'Deciding 3'],
  'rationale': 'A median survival is stated, but for all grades pooled, a broader group than the grade III mice '
               'asked about. The condition is not contradicted.',
  'suggested': 'partial',
  'expected_label': 'partial'},
 {'name': 'partial_approximate_value',
  'title': 'Reporter constructs for orvin regulation in Varrow seedlings',
  'term': 'orvin promoter',
  'question': 'How many base pairs long is the cloned orvin promoter fragment?',
  'defs': [],
  'passage': 'A promoter fragment of roughly 2 kb upstream of the orvin start codon was cloned into the reporter '
             'vector.',
  'rules': ['Labels/full: as precisely as the question asks',
            'Labels/partial: a part stated less precisely than asked',
            'Deciding 4: when unsure, partial'],
  'rationale': 'The question asks for a count in base pairs. The passage gives only an approximate length in '
               'kilobases, so the value is less precise than asked. Hard boundary case on the full/partial line.',
  'suggested': 'partial',
  'expected_label': 'partial'},
 {'name': 'full_boundary_overnight',
  'title': 'Feeding behaviour of Pellor beetle larvae',
  'term': 'Pellor larvae',
  'question': 'For how long are Pellor larvae starved before the feeding assay?',
  'defs': [],
  'passage': 'Before the feeding assay, Pellor larvae were starved overnight on moist filter paper.',
  'rules': ['Labels/full: as precisely as the question asks', 'Deciding 4: when unsure, partial'],
  'rationale': "'Overnight' answers 'for how long', and the question asks for no unit or precision. Hard "
               "boundary case: a judge that reads 'overnight' as imprecise and applies the unsure rule would say "
               'partial. The owner decides which reading the rubric intends.',
  'suggested': 'full',
  'expected_label': 'full'}]


def validate_cases(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shape check (every required field present, names unique, ``defs`` a list of pairs, a set label
    one of :data:`GRADES`); an EMPTY ``expected_label`` passes here (it is the owner's to set)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, c in enumerate(cases):
        missing = [f for f in CASE_FIELDS if f not in c and f not in OPTIONAL_FIELDS]
        if missing:
            raise ValueError(f"self-check case {i}: missing {missing}")
        extra = sorted(set(c) - set(CASE_FIELDS))
        if extra:
            raise ValueError(f"self-check case {i}: unknown keys {extra}")
        name = str(c["name"])
        if not name or name in seen:
            raise ValueError(f"self-check case {i}: name {name!r} empty or repeated")
        seen.add(name)
        defs = c["defs"]
        if not isinstance(defs, list) or any(not isinstance(d, list | tuple) or len(d) != 2 for d in defs):
            raise ValueError(f"self-check case {name}: defs must be a list of [alias, primary]")
        lab = str(c["expected_label"] or "")
        if lab and lab not in GRADES:
            raise ValueError(f"self-check case {name}: expected_label {lab!r} not one of {GRADES}")
        out.append({**dict(c), "defs": [(str(a), str(p)) for a, p in defs], "expected_label": lab})
    return out


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    """The cases from ``path`` (a JSON list) or, without one, :data:`SELF_CHECK_CASES_V3`."""
    raw = json.loads(Path(path).read_text(encoding="utf-8")) if path is not None else SELF_CHECK_CASES_V3
    if not isinstance(raw, list):
        raise ValueError("self-check cases: a JSON list is required")
    return validate_cases(raw)


def require_owner_labels(cases: Sequence[Mapping[str, Any]]) -> None:
    """Refuse (``SystemExit``) while the list is empty or any ``expected_label`` is empty: the
    self-check and the canaries are scored against owner-set labels only (``suggested`` is ignored)."""
    if not cases:
        raise SystemExit("self-check: no cases")
    empty = [str(c.get("name")) for c in cases if not str(c.get("expected_label") or "")]
    if empty:
        raise SystemExit(f"self-check: {len(empty)} case(s) without an owner-set expected_label: {empty[:5]}")
