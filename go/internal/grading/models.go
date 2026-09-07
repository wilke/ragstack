// Package grading implements the `/v1/grading` resources — the study's
// two-independent-reader evidence read (SPEC-confirmation-run.md §6.6), moved
// off an honour-based artifact and into the API, where independence is
// enforced.
//
// `contracts/openapi.yaml` (tag Grading) and `contracts/schemas/grading_*.json`
// are authoritative for every shape here; where the contract is silent this
// package matches the merged Python implementation
// (`python/ragstack/grading/`, `python/ragstack/api/routers/grading.py`) so the
// two servers cannot disagree about a read.
//
// This file holds the STORED records and the vocabularies. The wire shapes live
// in internal/api/handler_grading.go, because two of the contract's rules are
// about a field's PRESENCE — `stratum` is absent when none was authored,
// `reader_verdicts`/`adjudication` are absent unless the caller is an admin on
// a batch that has left `open` — and presence is decided per caller, not per
// type.
package grading

import (
	"fmt"
	"time"
)

// Batch statuses. `closed` is reserved by the schema and produced by no v1
// operation; it is spelled here so a stored value round-trips.
const (
	StatusOpen         = "open"
	StatusAdjudicating = "adjudicating"
	StatusClosed       = "closed"
)

// MaxReaders is `GradingBatch.readers`' maxItems — one per label letter.
const MaxReaders = 26

const labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

// Verdicts is SPEC-confirmation-run.md §6.6.2's vocabulary, byte-for-byte what
// `s0_rdev_score.py` accepts. Order is the schema's.
var Verdicts = []string{
	"correct", "wrong-location", "non-minimal", "missed-evidence",
	"correctly-none", "ambiguous",
}

// Judgements is `GradingSpanJudgement.judgement`'s vocabulary.
var Judgements = []string{"located", "wrong", "non-minimal"}

// Kinds is the protocol a batch's tasks follow.
var Kinds = []string{"evidence-read", "pointed-read", "citation-feedback"}

// AnswerTypes is `GradingExtraQuestion.answer_type`'s vocabulary.
var AnswerTypes = []string{"yes-no", "text"}

// InVocabulary reports whether v is one of the allowed values.
func InVocabulary(v string, allowed []string) bool {
	for _, a := range allowed {
		if v == a {
			return true
		}
	}
	return false
}

// LabelFor is `A` for reader 0, `B` for 1, … — what the export's CSV filenames
// carry and what `s0_rdev_score.py --a/--b` means.
func LabelFor(index int) string {
	if index < 0 || index >= len(labels) {
		return fmt.Sprintf("?%d", index)
	}
	return labels[index : index+1]
}

// NowISO is the timestamp every record is stamped with: sortable ISO-8601 UTC,
// second resolution — the same string Python's
// `datetime.now(UTC).isoformat(timespec="seconds")` produces, offset included,
// so records written by either implementation sort together.
func NowISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05+00:00")
}

// --------------------------------------------------------------------------- //
// The authored triple: question, document, claims
// --------------------------------------------------------------------------- //

// Sentence is `grading_document.json#/$defs/GradingSentence`.
type Sentence struct {
	I    int    `json:"i"`
	Text string `json:"text"`
}

// Unit is `grading_document.json#/$defs/GradingUnit`.
type Unit struct {
	Index     int        `json:"index"`
	Title     string     `json:"title"`
	Sentences []Sentence `json:"sentences"`
}

// Document is `grading_document.json` — stored DENORMALISED on every task, so a
// read is reproducible against exactly what the reader saw.
type Document struct {
	DocID string `json:"doc_id"`
	Title string `json:"title"`
	Units []Unit `json:"units"`
}

// Span is `grading_evidence_set.json#/$defs/GradingSpan`.
type Span struct {
	Unit          int    `json:"unit"`
	FirstSentence int    `json:"first_sentence"`
	LastSentence  int    `json:"last_sentence"`
	Text          string `json:"text"`
}

// EvidenceSet is `grading_evidence_set.json`.
type EvidenceSet struct {
	SetIndex int      `json:"set_index"`
	Spans    []Span   `json:"spans"`
	Sources  []string `json:"sources"`
}

// Question is `grading_question.json`. `id` is optional in the schema and is
// emitted whenever the authored task supplied one — presence, not value, is
// what the contract specifies.
type Question struct {
	ID          string `json:"id,omitempty"`
	Type        string `json:"type"`
	Summary     string `json:"summary"`
	Description string `json:"description"`
}

// ExtraQuestion is `grading_extra_question.json` — r3 §11 guard 2's extra
// per-task question.
type ExtraQuestion struct {
	ID         string `json:"id"`
	Text       string `json:"text"`
	AnswerType string `json:"answer_type"`
}

// --------------------------------------------------------------------------- //
// Answers
// --------------------------------------------------------------------------- //

// SpanJudgement is `grading_verdict.json#/$defs/GradingSpanJudgement`: the
// pilot sheet's `<set>.<span>` key split in two.
type SpanJudgement struct {
	Set       int    `json:"set"`
	Span      int    `json:"span"`
	Judgement string `json:"judgement"`
}

// ExtraAnswer is `grading_verdict.json#/$defs/GradingExtraAnswer`.
type ExtraAnswer struct {
	ID     string `json:"id"`
	Answer string `json:"answer"`
}

// --------------------------------------------------------------------------- //
// Stored records
// --------------------------------------------------------------------------- //

// Batch is a read: the unit that fixes the rubric, the readers, the per-reader
// order seed and the status. Authored once; only `Status`/`AdjudicatingAt` ever
// change, and only through BeginAdjudication.
type Batch struct {
	ID             string
	Name           string
	Kind           string
	Status         string
	RubricSHA256   string
	OrderSeed      int64
	Readers        []string
	TaskCount      int
	CreatedAt      string
	CreatedBy      string
	AdjudicatingAt string
}

// Task is one thing to grade, as stored. `Position` is its index in BATCH
// order — the draw's order, which is the order the export's CSV rows use; a
// reader's order is derived from it by ReaderOrder and is never stored.
type Task struct {
	ID             string
	BatchID        string
	Kind           string
	Position       int
	PairID         string
	Stratum        string
	Question       Question
	Document       Document
	Claims         []EvidenceSet
	ExtraQuestions []ExtraQuestion
	Readers        []string
	CreatedAt      string
	CreatedBy      string
}

// Verdict is one reader's answer to one task — exactly one CURRENT row per
// (task, reader), with previous versions retained for audit.
type Verdict struct {
	TaskID         string
	BatchID        string
	Reader         string
	Verdict        string
	SpanJudgements []SpanJudgement
	ExtraAnswers   []ExtraAnswer
	Notes          string
	Version        int
	SavedAt        string
}

// Adjudication is the joint-read verdict for one task — the verdict the study
// USES (SPEC §6.6.3). It never modifies a reader's row: the pre-adjudication κ
// is the one reported, and it needs the originals.
type Adjudication struct {
	TaskID         string
	BatchID        string
	Verdict        string
	SpanJudgements []SpanJudgement
	Notes          string
	AdjudicatedBy  string
	Version        int
	SavedAt        string
}
