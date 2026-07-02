// Pure, React-free, HTML-free matched-span segmentation.
//
// Highlighting is done by SLICING the plain string into ordered segments that
// the component renders as separate React text/<mark> nodes (each auto-escaped)
// — never by injecting HTML. This is the load-bearing XSS guardrail: chunk
// content is untrusted ingested text.
//
// The offsets consumed here are CHUNK-RELATIVE match offsets (a future backend
// `match_start`/`match_end`). The API does NOT emit them today, and the model's
// `start_char`/`end_char` are DOCUMENT-absolute — slicing `content` on those
// would mark the wrong span or throw — so callers must pass chunk-relative
// offsets or nothing. Missing/out-of-range offsets degrade to a single unmarked
// segment (whole-passage framing), never an exception.

export interface Segment {
  text: string;
  marked: boolean;
}

export function segmentContent(
  content: string,
  start?: number,
  end?: number,
): Segment[] {
  const valid =
    typeof start === "number" &&
    typeof end === "number" &&
    Number.isInteger(start) &&
    Number.isInteger(end) &&
    start >= 0 &&
    end <= content.length &&
    start < end;

  if (!valid) return [{ text: content, marked: false }];

  const segments: Segment[] = [];
  if (start! > 0) segments.push({ text: content.slice(0, start), marked: false });
  segments.push({ text: content.slice(start, end), marked: true });
  if (end! < content.length) segments.push({ text: content.slice(end), marked: false });
  return segments;
}
