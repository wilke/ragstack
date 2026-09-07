package api

import (
	"fmt"

	"github.com/google/uuid"

	"github.com/ragstack/ragstack/internal/grading"
)

// Create-time validation for `POST /v1/grading/batches`.
//
// Everything the JSON schemas state (enums, bounds, patterns, uniqueness) plus
// everything they CANNOT state — above all, that every span points INTO its
// task's document. A span outside its document is not a cosmetic error: it is a
// highlight the reader will never see, on a pair they will nonetheless grade.
// Caught at create, when the importer can be fixed, rather than at read time,
// when the read has already happened.
//
// Nothing here is a partial batch: the first failure returns 422 and the caller
// never reaches the store.

func validateBatchCreate(req *gradingBatchCreateRequest) error {
	if l := len([]rune(req.Name)); l < 1 || l > 200 {
		return fmt.Errorf("name must be 1-200 characters")
	}
	if !grading.InVocabulary(req.Kind, grading.Kinds) {
		return fmt.Errorf("kind %q is not one of %v", req.Kind, grading.Kinds)
	}
	if !rubricSHA256Re.MatchString(req.RubricSHA256) {
		return fmt.Errorf(
			"rubric_sha256 must be 64 lowercase hex characters; got %q", req.RubricSHA256)
	}
	if req.OrderSeed == nil {
		// Required and never defaulted: the study records its seeds before any
		// pair is read, so a server-chosen seed would be a read whose order
		// nobody can reproduce.
		return fmt.Errorf("order_seed is required and is never defaulted")
	}
	if len(req.Readers) < 1 || len(req.Readers) > grading.MaxReaders {
		return fmt.Errorf("readers must name 1-%d people, in label order (first = 'A')",
			grading.MaxReaders)
	}
	if len(req.Tasks) < 1 {
		return fmt.Errorf("tasks must not be empty")
	}
	seen := map[string]struct{}{}
	for i := range req.Tasks {
		t := &req.Tasks[i]
		where := fmt.Sprintf("tasks[%d] (%s)", i, t.PairID)
		if l := len([]rune(t.PairID)); l < 1 || l > 200 {
			return fmt.Errorf("tasks[%d]: pair_id must be 1-200 characters", i)
		}
		if _, dup := seen[t.PairID]; dup {
			return fmt.Errorf(
				"tasks[%d]: duplicate pair_id %q; it is the export's key column and must "+
					"be unique within the batch", i, t.PairID)
		}
		seen[t.PairID] = struct{}{}
		if err := validateTaskCreate(t, where); err != nil {
			return err
		}
	}
	return nil
}

func validateTaskCreate(t *gradingTaskCreateIn, where string) error {
	if t.Question.Type == "" {
		return fmt.Errorf("%s: question.type must not be empty", where)
	}
	if t.Document.DocID == "" {
		return fmt.Errorf("%s: document.doc_id must not be empty", where)
	}
	// `index`/`i` are the values a span refers to, and the schemas say each MUST
	// equal its position. Checked rather than assumed: a gapped numbering (the
	// segmenter produces them) silently slides every span in the unit onto text
	// the labeler never claimed.
	for i, u := range t.Document.Units {
		if u.Index != i {
			return fmt.Errorf(
				"%s: unit at position %d declares index %d; a unit's index MUST equal its "+
					"position in `units`", where, i, u.Index)
		}
		for k, s := range u.Sentences {
			if s.I != k {
				return fmt.Errorf(
					"%s: unit %d sentence at position %d declares i=%d; a sentence's `i` "+
						"MUST equal its position in `sentences`", where, i, k, s.I)
			}
		}
	}

	seenSets := map[int]struct{}{}
	for _, c := range t.Claims {
		if c.SetIndex < 1 {
			return fmt.Errorf("%s: set_index must be >= 1, got %d", where, c.SetIndex)
		}
		if _, dup := seenSets[c.SetIndex]; dup {
			return fmt.Errorf("%s: duplicate set_index %d", where, c.SetIndex)
		}
		seenSets[c.SetIndex] = struct{}{}
		if len(c.Spans) < 1 {
			return fmt.Errorf("%s: set %d has no spans", where, c.SetIndex)
		}
		if len(c.Sources) < 1 {
			return fmt.Errorf("%s: set %d names no sources", where, c.SetIndex)
		}
		seenSources := map[string]struct{}{}
		for _, src := range c.Sources {
			if src == "" {
				return fmt.Errorf("%s: set %d lists an empty source", where, c.SetIndex)
			}
			if _, dup := seenSources[src]; dup {
				return fmt.Errorf("%s: set %d lists a source twice: %v", where, c.SetIndex, c.Sources)
			}
			seenSources[src] = struct{}{}
		}
		for pos, sp := range c.Spans {
			n := pos + 1
			if sp.Unit < 0 || sp.Unit >= len(t.Document.Units) {
				return fmt.Errorf(
					"%s: set %d span %d names unit %d, but the document has %d unit(s)",
					where, c.SetIndex, n, sp.Unit, len(t.Document.Units))
			}
			if sp.FirstSentence < 0 || sp.LastSentence < 0 {
				return fmt.Errorf(
					"%s: set %d span %d has a negative sentence index", where, c.SetIndex, n)
			}
			if sp.FirstSentence > sp.LastSentence {
				return fmt.Errorf(
					"%s: set %d span %d has first_sentence %d > last_sentence %d",
					where, c.SetIndex, n, sp.FirstSentence, sp.LastSentence)
			}
			count := len(t.Document.Units[sp.Unit].Sentences)
			if sp.LastSentence >= count {
				return fmt.Errorf(
					"%s: set %d span %d names sentence %d of unit %d, which has %d sentence(s)",
					where, c.SetIndex, n, sp.LastSentence, sp.Unit, count)
			}
		}
	}

	seenQ := map[string]struct{}{}
	for _, q := range t.ExtraQuestions {
		if !extraQuestionIDRe.MatchString(q.ID) {
			return fmt.Errorf(
				"%s: extra_questions id %q must match ^[A-Za-z0-9_.-]{1,64}$", where, q.ID)
		}
		if _, dup := seenQ[q.ID]; dup {
			return fmt.Errorf("%s: duplicate extra_questions id %q", where, q.ID)
		}
		seenQ[q.ID] = struct{}{}
		if q.Text == "" {
			return fmt.Errorf("%s: extra_questions %q has empty text", where, q.ID)
		}
		if !grading.InVocabulary(q.AnswerType, grading.AnswerTypes) {
			return fmt.Errorf(
				"%s: extra_questions %q has answer_type %q, not one of %v",
				where, q.ID, q.AnswerType, grading.AnswerTypes)
		}
	}
	return nil
}

// toTaskRecord builds the stored task. `Position` is the task's index in BATCH
// order — the draw's order, which is what every export CSV is written in and
// what every reader's permutation is computed over.
func toTaskRecord(t *gradingTaskCreateIn, batch *grading.Batch, position int) *grading.Task {
	units := make([]grading.Unit, 0, len(t.Document.Units))
	for _, u := range t.Document.Units {
		sentences := make([]grading.Sentence, 0, len(u.Sentences))
		for _, s := range u.Sentences {
			sentences = append(sentences, grading.Sentence{I: s.I, Text: s.Text})
		}
		units = append(units, grading.Unit{Index: u.Index, Title: u.Title, Sentences: sentences})
	}
	claims := make([]grading.EvidenceSet, 0, len(t.Claims))
	for _, c := range t.Claims {
		spans := make([]grading.Span, 0, len(c.Spans))
		for _, sp := range c.Spans {
			spans = append(spans, grading.Span{
				Unit:          sp.Unit,
				FirstSentence: sp.FirstSentence,
				LastSentence:  sp.LastSentence,
				Text:          sp.Text,
			})
		}
		claims = append(claims, grading.EvidenceSet{
			SetIndex: c.SetIndex,
			Spans:    spans,
			Sources:  append([]string{}, c.Sources...),
		})
	}
	extras := make([]grading.ExtraQuestion, 0, len(t.ExtraQuestions))
	for _, q := range t.ExtraQuestions {
		extras = append(extras, grading.ExtraQuestion{
			ID: q.ID, Text: q.Text, AnswerType: q.AnswerType,
		})
	}
	return &grading.Task{
		ID:      uuid.New().String(),
		BatchID: batch.ID,
		Kind:    batch.Kind,
		// The batch's kind, denormalised: a task's protocol is the batch's, and
		// a task fetched by id must be readable without a second lookup.
		Position: position,
		PairID:   t.PairID,
		Stratum:  t.Stratum,
		Question: grading.Question{
			ID:          t.Question.ID,
			Type:        t.Question.Type,
			Summary:     t.Question.Summary,
			Description: t.Question.Description,
		},
		Document: grading.Document{
			DocID: t.Document.DocID,
			Title: t.Document.Title,
			Units: units,
		},
		Claims:         claims,
		ExtraQuestions: extras,
		Readers:        append([]string{}, batch.Readers...),
		CreatedAt:      batch.CreatedAt,
		CreatedBy:      batch.CreatedBy,
	}
}

// checkJudgements resolves a body's span judgements against the task.
//
// Every (set, span) must EXIST on the task — otherwise the row records a
// judgement of nothing, and the per-span κ silently counts it as a
// disagreement or an agreement about a span neither reader saw.
func checkJudgements(
	task *grading.Task, judgements []gradingSpanJudgementIn,
) ([]grading.SpanJudgement, error) {
	spans := map[[2]int]struct{}{}
	available := make([]string, 0)
	for _, c := range task.Claims {
		for pos := range c.Spans {
			spans[[2]int{c.SetIndex, pos + 1}] = struct{}{}
			available = append(available, fmt.Sprintf("(%d, %d)", c.SetIndex, pos+1))
		}
	}
	out := make([]grading.SpanJudgement, 0, len(judgements))
	for _, j := range judgements {
		if !grading.InVocabulary(j.Judgement, grading.Judgements) {
			return nil, fmt.Errorf("judgement %q is not one of %v", j.Judgement, grading.Judgements)
		}
		if _, ok := spans[[2]int{j.Set, j.Span}]; !ok {
			return nil, fmt.Errorf(
				"span judgement names set %d span %d, which this task does not have "+
					"(it has %v)", j.Set, j.Span, available)
		}
		out = append(out, grading.SpanJudgement{Set: j.Set, Span: j.Span, Judgement: j.Judgement})
	}
	return out, nil
}

// checkAnswers resolves a body's extra answers against the task's questions.
func checkAnswers(
	task *grading.Task, answers []gradingExtraAnswerIn,
) ([]grading.ExtraAnswer, error) {
	byID := map[string]grading.ExtraQuestion{}
	asked := make([]string, 0, len(task.ExtraQuestions))
	for _, q := range task.ExtraQuestions {
		byID[q.ID] = q
		asked = append(asked, q.ID)
	}
	out := make([]grading.ExtraAnswer, 0, len(answers))
	for _, a := range answers {
		q, ok := byID[a.ID]
		if !ok {
			return nil, fmt.Errorf(
				"extra answer names question %q, which this task does not ask (it asks %v)",
				a.ID, asked)
		}
		if q.AnswerType == "yes-no" && a.Answer != "yes" && a.Answer != "no" {
			return nil, fmt.Errorf(
				"question %q is yes-no; %q is neither 'yes' nor 'no'", a.ID, a.Answer)
		}
		out = append(out, grading.ExtraAnswer{ID: a.ID, Answer: a.Answer})
	}
	return out, nil
}
