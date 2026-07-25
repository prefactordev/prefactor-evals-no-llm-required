/**
 * The three quality-span primitives publish.ts needs from the HTML renderer.
 *
 * The Python build imports `_quality_spans`, `_score_pct`, and `_CALM_VALUES`
 * from html_report.py so there is one definition of "what a quality span is".
 * The 804 line HTML dashboard is not ported, so those three items live here,
 * ported faithfully, and publish.ts imports them from this small file instead.
 */

import type { EvalInstance, EvalSpan } from './schema.js';

function isDict(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** Normalize a 0..1 or 0..100 score to a percentage, or null. */
export function scorePct(value: unknown): number | null {
  // Booleans are not scores. In JS a boolean is not a number, so the Python
  // `not isinstance(value, bool)` guard is implicit here.
  if (typeof value !== 'number' || Number.isNaN(value)) return null;
  const number = value;
  if (number >= 0.0 && number <= 1.0) return number * 100.0;
  if (number >= 0.0 && number <= 100.0) return number;
  return null;
}

// A calm signal value: the run said nothing was wrong. Anything else is treated
// as a concern in the recorded-quality summary.
export const CALM_VALUES: ReadonlySet<string> = new Set([
  'none', 'ok', 'good', 'normal', 'healthy', 'pass', 'fine', 'nominal',
]);

/** True when a span's output looks like a judgement rather than a result.
 *
 * Detected by shape, not by name: a judgement pairs at least one rating (a 0..1
 * score, or a boolean flag) with at least one piece of text. */
function isQualityPayload(payload: Record<string, unknown>): boolean {
  let hasRating = false;
  let hasText = false;
  for (const value of Object.values(payload)) {
    if (typeof value === 'boolean') {
      hasRating = true;
    } else if (typeof value === 'number' && scorePct(value) !== null) {
      hasRating = true;
    } else if (isDict(value) && Object.keys(value).length > 0
        && Object.values(value).every((v) => scorePct(v) !== null)) {
      hasRating = true;
    } else if (typeof value === 'string' && value.trim()) {
      hasText = true;
    }
  }
  return hasRating && hasText;
}

export type QualitySpan = [EvalSpan, Record<string, unknown>];

/** The run's own quality spans, split into whole run judgements and per turn
 * signals. A judgement that carries a group of named scores is treated as a
 * whole run verdict; anything else is a point in time signal. */
export function qualitySpans(instance: EvalInstance): [QualitySpan[], QualitySpan[]] {
  const judgements: QualitySpan[] = [];
  const signals: QualitySpan[] = [];
  for (const span of instance.spans) {
    const payload = isDict(span.output) ? span.output : null;
    if (!payload || !isQualityPayload(payload)) continue;
    const grouped = Object.values(payload).some((v) => isDict(v)
      && Object.keys(v).length > 0
      && Object.values(v).every((x) => scorePct(x) !== null));
    if (grouped) judgements.push([span, payload]);
    else signals.push([span, payload]);
  }
  return [judgements, signals];
}
