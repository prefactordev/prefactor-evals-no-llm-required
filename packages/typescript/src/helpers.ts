/**
 * Shared deterministic primitives.
 *
 * Everything here is a pure function. No clock reads, no randomness, no
 * network. Both language implementations must agree byte for byte, so changing
 * one of them is a conformance affecting change.
 *
 * Where JavaScript and Python would naturally disagree, the difference is
 * handled here on purpose. The three that bite:
 *
 *  1. Key ordering. Python sorts by code point; JS string comparison sorts by
 *     UTF-16 code unit. They differ once an astral character (emoji) meets a
 *     high BMP one, so the sort below compares code points explicitly.
 *  2. `undefined`. JSON.stringify silently drops object keys whose value is
 *     undefined; Python writes `null`. Normalizing undefined to null keeps a
 *     key present in both.
 *  3. Integral floats. Python folds 1.0 to 1 so it hashes the same as an int.
 *     JS already renders 1.0 as 1, so the two agree once undefined is handled.
 */

import { createHash } from 'node:crypto';
import type { EvalSpan } from './schema.js';
import { sortSpans } from './schema.js';

// ---------------------------------------------------------------------------
// Field paths
// ---------------------------------------------------------------------------
// Syntax, shared by every check that reads a user configured location:
//   "a.b"   object key
//   "a[0]"  array index
//   "a[]"   every element of an array, flattened
// A path that does not resolve yields no values, which is distinct from
// resolving to null.

const SEGMENT = /([^.[\]]+)|\[(\d+)\]|(\[\])/g;

type Segment =
  | { kind: 'key'; key: string }
  | { kind: 'index'; index: number }
  | { kind: 'spread' };

function segments(path: string): Segment[] {
  const out: Segment[] = [];
  SEGMENT.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SEGMENT.exec(path)) !== null) {
    if (match[1] !== undefined) out.push({ kind: 'key', key: match[1] });
    else if (match[2] !== undefined) out.push({ kind: 'index', index: Number(match[2]) });
    else out.push({ kind: 'spread' });
  }
  return out;
}

/** Resolve a field path, returning every matching value. Returns an array
 * because `[]` spreads; callers wanting one value use readPathOne. */
export function readPath(root: unknown, path: string): unknown[] {
  if (!path) return [];
  let current: unknown[] = [root];
  for (const seg of segments(path)) {
    const next: unknown[] = [];
    for (const node of current) {
      if (seg.kind === 'key') {
        if (node !== null && typeof node === 'object' && !Array.isArray(node)
            && seg.key in (node as Record<string, unknown>)) {
          next.push((node as Record<string, unknown>)[seg.key]);
        }
      } else if (seg.kind === 'index') {
        if (Array.isArray(node)) {
          const i = seg.index < 0 ? node.length + seg.index : seg.index;
          if (i >= 0 && i < node.length) next.push(node[i]);
        }
      } else if (Array.isArray(node)) {
        next.push(...node);
      }
    }
    current = next;
    if (current.length === 0) return [];
  }
  return current;
}

export function readPathOne(root: unknown, path: string): unknown {
  const values = readPath(root, path);
  return values.length ? values[0] : null;
}

export function pathExists(root: unknown, path: string): boolean {
  return readPath(root, path).length > 0;
}

// ---------------------------------------------------------------------------
// Canonical serialization
// ---------------------------------------------------------------------------

/** Compare two strings by Unicode code point, matching Python's string
 * ordering. Plain `<` in JS compares UTF-16 code units, which orders an astral
 * character before U+E000..U+FFFF and would sort keys differently. */
function codePointCompare(a: string, b: string): number {
  const ca = Array.from(a);
  const cb = Array.from(b);
  const len = Math.min(ca.length, cb.length);
  for (let i = 0; i < len; i += 1) {
    const pa = ca[i]!.codePointAt(0)!;
    const pb = cb[i]!.codePointAt(0)!;
    if (pa !== pb) return pa < pb ? -1 : 1;
  }
  return ca.length === cb.length ? 0 : ca.length < cb.length ? -1 : 1;
}

function serialize(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  const t = typeof value;
  if (t === 'boolean') return value ? 'true' : 'false';
  if (t === 'number') {
    const n = value as number;
    // Python writes NaN/Infinity as bare tokens; JSON.stringify writes null.
    // Neither is valid JSON and neither appears in trace payloads, so null is
    // chosen here and the conformance suite would catch it if one ever did.
    if (!Number.isFinite(n)) return 'null';
    return JSON.stringify(n);
  }
  if (t === 'string') return JSON.stringify(value);
  if (value instanceof Date) return JSON.stringify(value.toISOString());
  if (Array.isArray(value)) return `[${value.map(serialize).join(',')}]`;
  if (t === 'object') {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort(codePointCompare);
    const parts = keys.map((k) => `${JSON.stringify(k)}:${serialize(obj[k])}`);
    return `{${parts.join(',')}}`;
  }
  return JSON.stringify(String(value));
}

/**
 * Stable text for any JSON-ish value. Object keys sorted recursively, array
 * order preserved. Two structurally identical inputs always produce one string,
 * which is what makes signature and hash based checks reproducible.
 */
export function canonical(value: unknown): string {
  return serialize(value);
}

/** Short content hash. Used instead of raw arguments in evidence, because
 * arguments can be large and can contain sensitive values. */
export function digest(value: unknown): string {
  const hash = createHash('sha256').update(canonical(value), 'utf8').digest('hex');
  return `sha256:${hash.slice(0, 16)}`;
}

export function stripKeys(value: unknown, keys: Iterable<string>): unknown {
  const drop = new Set(keys ?? []);
  if (drop.size === 0 || value === null || typeof value !== 'object' || Array.isArray(value)) {
    return value;
  }
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (!drop.has(k)) out[k] = v;
  }
  return out;
}

/**
 * Identity of a tool call for repetition detection.
 *
 * Null input is included as a distinct value rather than skipped: an agent
 * calling the same argument free tool twenty times is exactly the failure these
 * checks exist to catch.
 */
export function toolSignature(span: EvalSpan, ignoreArgKeys: Iterable<string> = []): string {
  return canonical([span.name, stripKeys(span.input, ignoreArgKeys)]);
}

/** Always sorted by start time then id, so results are byte identical across
 * runs. */
export function spanIds(spans: readonly EvalSpan[]): string[] {
  return sortSpans(spans).map((s) => s.id);
}

// ---------------------------------------------------------------------------
// Regex
// ---------------------------------------------------------------------------

/** Constructs one engine supports and the other does not, or handles
 * differently. A pattern using one of these cannot give identical results in
 * both implementations, which is the whole promise of this library. */
export const UNPORTABLE: ReadonlyArray<readonly [RegExp, string]> = [
  [/\(\?<[=!]/, 'lookbehind'],
  [/\(\?P?<[A-Za-z_]/, 'named group'],
  [/\(\?P=/, 'named backreference'],
  [/\(\?[aiLmsux]+[):]/, 'inline flag group'],
  [/\(\?>/, 'atomic group'],
  [/\(\?\(/, 'conditional'],
  [/\(\?R\)/, 'recursion'],
  [/\[\[:/, 'POSIX character class'],
  [/(?<!\\)\\[1-9]/, 'backreference'],
  [/(?<!\\)[*+?}]\+/, 'possessive quantifier'],
];

/** A quantified group that is itself quantified: the shape behind catastrophic
 * backtracking. (a+)+ against a long non-matching string explores exponentially
 * many paths and hangs the process, and neither engine offers a timeout, so the
 * only defence is refusing the pattern. */
const NESTED_QUANTIFIER = /\([^()]*[*+][^()]*\)\s*[*+{]/;
const QUANTIFIED_ALTERNATION = /\((?=[^()]*\|)[^()]*\)\s*[*+]/;

export const MAX_PATTERN_LENGTH = 1000;

export class UnsafePattern extends Error {
  constructor(public readonly pattern: string, public readonly reason: string) {
    super(`Pattern "${pattern.slice(0, 80)}" ${reason}.`);
    this.name = 'UnsafePattern';
  }
}

/** Reason a pattern must be refused, or null. Safety is always checked;
 * portability is opt in, because it is stricter than some checks want. */
export function patternProblem(pattern: unknown, portable = false): string | null {
  if (typeof pattern !== 'string') return 'is not text';
  if (pattern.length > MAX_PATTERN_LENGTH) {
    return `is longer than ${MAX_PATTERN_LENGTH} characters`;
  }
  if (NESTED_QUANTIFIER.test(pattern) || QUANTIFIED_ALTERNATION.test(pattern)) {
    return 'nests one quantifier inside another, which can take exponential '
      + 'time to fail and hang the run';
  }
  if (portable) {
    for (const [probe, label] of UNPORTABLE) {
      if (probe.test(pattern)) {
        return `uses ${label}, which is outside the portable regex subset this check accepts`;
      }
    }
  }
  return null;
}

export function compilePatterns(
  patterns: Iterable<string>,
  caseSensitive = true,
  portable = false,
): RegExp[] {
  const flags = caseSensitive ? '' : 'i';
  const out: RegExp[] = [];
  for (const pattern of patterns ?? []) {
    const problem = patternProblem(pattern, portable);
    if (problem) throw new UnsafePattern(String(pattern), problem);
    out.push(new RegExp(pattern, flags));
  }
  return out;
}

// ---------------------------------------------------------------------------
// JSON Schema safety
// ---------------------------------------------------------------------------

function collectRefs(node: unknown, out: string[]): void {
  if (node !== null && typeof node === 'object' && !Array.isArray(node)) {
    const ref = (node as Record<string, unknown>)['$ref'];
    if (typeof ref === 'string') out.push(ref);
    for (const v of Object.values(node as Record<string, unknown>)) collectRefs(v, out);
  } else if (Array.isArray(node)) {
    for (const v of node) collectRefs(v, out);
  }
}

/**
 * Reason a user supplied JSON Schema cannot be used, or null.
 *
 * A remote $ref is refused rather than resolved. Resolving one would make the
 * library fetch a URL chosen by whoever wrote the config, from inside CI, on
 * every run. That is a request forgery primitive, not a feature.
 */
export function schemaProblem(schema: unknown): string | null {
  if (schema === null || typeof schema !== 'object' || Array.isArray(schema)) {
    return 'is not an object';
  }
  const declared = (schema as Record<string, unknown>)['$schema'];
  if (typeof declared === 'string' && !declared.includes('2020-12')) {
    return `declares draft "${declared}", only draft 2020-12 is interpreted`;
  }
  const refs: string[] = [];
  collectRefs(schema, refs);
  for (const ref of refs) {
    if (!ref.startsWith('#')) {
      return `contains a remote $ref "${ref}", which would mean fetching a URL from config on every run`;
    }
  }
  return null;
}

/** ASCII case folding only. Python's str.lower and JavaScript's toLowerCase
 * disagree on some non-ASCII input, and identical results across languages are
 * non negotiable. */
export function asciiLower(text: string): string {
  let out = '';
  for (const ch of text) {
    out += ch >= 'A' && ch <= 'Z' ? String.fromCharCode(ch.charCodeAt(0) + 32) : ch;
  }
  return out;
}

export function textOf(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  return canonical(value);
}
