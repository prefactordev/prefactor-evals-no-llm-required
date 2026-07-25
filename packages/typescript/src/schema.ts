/**
 * Normalized eval schema, version v1.
 *
 * The shapes here are the only thing checks ever see. See spec/schema/v1/ for
 * the field by field definitions. Nothing in this module talks to Prefactor.
 *
 * This must agree with the Python implementation exactly. Where the two
 * languages could diverge, the divergence is called out in a comment rather
 * than left to chance.
 */

import { comparePython } from './fmt.js';

export const SCHEMA_VERSION = 'v1';

/** Instance lifecycle. `terminated` is server set: Prefactor stopped the run
 * externally. Deliberately distinct from `cancelled`. */
export const INSTANCE_STATES = [
  'pending', 'active', 'complete', 'failed', 'cancelled', 'terminated',
] as const;
export type InstanceState = (typeof INSTANCE_STATES)[number];

/** Spans have no terminated state. A span of a terminated instance very often
 * stays `active` forever, because nothing closed it. */
export const SPAN_STATES = [
  'pending', 'active', 'complete', 'failed', 'cancelled',
] as const;
export type SpanState = (typeof SPAN_STATES)[number];

export const SPAN_TYPES = [
  'tool_call', 'llm_call', 'retrieval', 'handoff', 'output', 'other',
] as const;
export type SpanType = (typeof SPAN_TYPES)[number];

/** Built in schema_name to type mapping, exact match.
 * See spec/schema/v1/span.md note 2. Verified against live traces. */
export const BUILTIN_TYPE_EXACT: Record<string, SpanType> = {
  'langchain:tool': 'tool_call',
  'langchain:llm': 'llm_call',
  'langchain:retriever': 'retrieval',
  'langchain:chain': 'other',
  'langchain:agent': 'other',
  'ai-sdk:llm': 'llm_call',
  'ai-sdk:agent': 'other',
};

/** Closed prefix list. Both the ai-sdk and langchain conventions encode the
 * tool name as a third segment (ai-sdk:tool:issue_refund,
 * langchain:tool:deposit_btc), so exact matching alone would type every named
 * tool span as `other` and silently disable the checks that key on tool
 * identity. Verified against two live agents, one of each. */
export const BUILTIN_TYPE_PREFIX: ReadonlyArray<readonly [string, SpanType]> = [
  ['ai-sdk:tool:', 'tool_call'],
  ['langchain:tool:', 'tool_call'],
];

export interface EvalSpan {
  id: string;
  parentId: string | null;
  instanceId: string;
  type: SpanType;
  name: string;
  schemaName: string;
  input: unknown;
  output: unknown;
  state: SpanState | string;
  startedAt: Date | null;
  endedAt: Date | null;
  durationMs: number | null;
  /** Always null against Prefactor: there is no per span cost field anywhere. */
  cost: number | null;
  tokens: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
}

export interface EvalInstance {
  id: string;
  agentId: string;
  agentVersion: string | null;
  environment: string | null;
  startedAt: Date | null;
  endedAt: Date | null;
  state: InstanceState | string;
  durationMs: number | null;
  spans: EvalSpan[];
  input: unknown;
  output: unknown;
  cost: number | null;
  metadata: Record<string, unknown>;
}

// ISO 8601 date or date-time, with optional fraction and offset. Anchored, so
// the Date constructor's lenient heuristics (which read "5" as April 2001 and
// "July 1 2026" as a date) never get a say. Python's fromisoformat rejects
// those too, and both languages must refuse the same inputs: a typo like
// `--since 5` must mean "no filter" in both, not a filter in one.
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})?)?$/;

/**
 * ISO 8601 string to a Date. Returns null on anything unusable.
 * Checks never call this; the seam parses once, at the boundary.
 */
export function parseTimestamp(value: unknown): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value !== 'string' || !value.trim()) return null;
  let text = value.trim();
  if (!ISO_TIMESTAMP.test(text)) return null;
  // A timestamp with no offset is UTC, matching Python, which attaches UTC to
  // a naive fromisoformat result. JavaScript's Date would read it as LOCAL
  // time, silently shifting every filter and duration by the machine's zone.
  text = text.replace(' ', 'T');
  if (!text.includes('T')) text += 'T00:00:00';
  text = text.replace(/([+-]\d{2})(\d{2})$/, '$1:$2');
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(text)) text += 'Z';
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** Milliseconds between two instants, or null if either is missing.
 * Null means unknown and must never be read as zero. */
export function durationMs(start: Date | null, end: Date | null): number | null {
  if (!start || !end) return null;
  return end.getTime() - start.getTime();
}

/**
 * schema_name to normalized type. Config wins, then exact, then prefix.
 *
 * There is deliberately no substring guessing: a rule like "contains tool"
 * would mistype `protocol:sync`, and a silently mistyped span produces a
 * silently wrong result, which is worse than no result.
 */
export function resolveSpanType(
  schemaName: string,
  spanTypeMap?: Record<string, string> | null,
): SpanType {
  if (spanTypeMap) {
    const mapped = spanTypeMap[schemaName];
    if (mapped && (SPAN_TYPES as readonly string[]).includes(mapped)) {
      return mapped as SpanType;
    }
  }
  const exact = BUILTIN_TYPE_EXACT[schemaName];
  if (exact) return exact;
  for (const [prefix, spanType] of BUILTIN_TYPE_PREFIX) {
    if (schemaName.startsWith(prefix)) return spanType;
  }
  return 'other';
}

/**
 * Tool identity first, then the display label, then the raw schema name.
 *
 * The prefix step comes first because `payload.name` may be shared across tools
 * or absent, while `ai-sdk:tool:issue_refund` is unambiguous.
 */
export function resolveSpanName(schemaName: string, payloadName?: string | null): string {
  for (const [prefix] of BUILTIN_TYPE_PREFIX) {
    if (schemaName.startsWith(prefix)) {
      const remainder = schemaName.slice(prefix.length);
      if (remainder) return remainder;
    }
  }
  if (payloadName) return payloadName;
  return schemaName;
}

/**
 * Start time ascending, then id as a stable tiebreak.
 *
 * Concurrent spans share a start time often enough that an unstable sort would
 * make order sensitive checks nondeterministic, which breaks the premise of the
 * library. Array.prototype.sort is stable in modern JS, but the id tiebreak is
 * explicit so the ordering does not depend on that guarantee, and so it matches
 * Python's sort key exactly.
 */
export function sortSpans(spans: readonly EvalSpan[]): EvalSpan[] {
  return [...spans].sort((a, b) => {
    const aMissing = a.startedAt === null;
    const bMissing = b.startedAt === null;
    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    if (!aMissing && !bMissing) {
      const diff = a.startedAt!.getTime() - b.startedAt!.getTime();
      if (diff !== 0) return diff;
    }
    // Code point comparison, not UTF-16: Python compares ids by code point,
    // and the tiebreak feeds directly into result ordering.
    return comparePython(a.id, b.id);
  });
}

export function spansOfType(instance: EvalInstance, ...types: SpanType[]): EvalSpan[] {
  const wanted = new Set<string>(types);
  return instance.spans.filter((s) => wanted.has(s.type));
}

export function spanFailed(span: EvalSpan): boolean {
  return span.state === 'failed';
}
