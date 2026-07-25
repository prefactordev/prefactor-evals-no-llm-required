/**
 * Per-span detectors and the evaluator that drives them.
 *
 * A detector sees one span at a time and keeps whatever running state it needs.
 * When a span pushes it past a threshold, it returns a Breach reason; otherwise
 * null. The evaluator holds one set of detectors per live run and routes each
 * span to them.
 *
 * Determinism: the same spans in the same order always yield the same breaches.
 * No clock, no randomness, no network. The one time-derived thing, a span's
 * duration, comes off the span itself, computed by the seam at fetch time.
 *
 * This is the line for line port of the Python live/detectors.py.
 */

import { cfgFloat, cfgInt, type Config } from '../config.js';
import { toolSignature } from '../helpers.js';
import type { EvalSpan } from '../schema.js';

// What happens when a check breaches, per check. Off does not even run the
// detector. Warn records the breach. Terminate additionally asks the caller to
// stop the run. Terminate is never the default: killing a good run on a false
// positive is the one costly mistake, so it is always opt in.
export const POLICY_OFF = 'off';
export const POLICY_WARN = 'warn';
export const POLICY_TERMINATE = 'terminate';
export const POLICIES = [POLICY_OFF, POLICY_WARN, POLICY_TERMINATE] as const;

export class Breach {
  readonly check: string;
  readonly reason: string;
  readonly spanId: string;
  readonly policy: string;

  constructor(check: string, reason: string, spanId: string, policy: string) {
    this.check = check;
    this.reason = reason;
    this.spanId = spanId;
    this.policy = policy;
  }

  get terminate(): boolean {
    return this.policy === POLICY_TERMINATE;
  }

  toDict(): Record<string, unknown> {
    return { check: this.check, reason: this.reason, span_id: this.spanId, policy: this.policy };
  }
}

// A detector is `(span) -> string | null`: the breach reason, or null.
export type Detector = (span: EvalSpan) => string | null;
export type DetectorFactory = (config: Config) => Detector;

// --- detectors -------------------------------------------------------------
// Each is a factory that returns a fresh per-run detector.

/** The same tool signature repeated past a limit, anywhere in the run. */
function loops(config: Config): Detector {
  const limit = cfgInt(config, 'max_occurrences', 3)!;
  const ignoreArgKeys = (config['ignore_arg_keys'] as Iterable<string>) || [];
  const counts = new Map<string, number>();

  return (span: EvalSpan): string | null => {
    if (span.type !== 'tool_call') return null;
    const key = toolSignature(span, ignoreArgKeys);
    const next = (counts.get(key) ?? 0) + 1;
    counts.set(key, next);
    if (next > limit) {
      return `tool "${span.name}" called ${next} times with identical arguments, limit is ${limit}`;
    }
    return null;
  };
}

/** The same tool signature repeated back to back with no progress. */
function redundant(config: Config): Detector {
  const limit = cfgInt(config, 'max_repeats', 2)!;
  const ignoreArgKeys = (config['ignore_arg_keys'] as Iterable<string>) || [];
  const state: { last: string | null; run: number } = { last: null, run: 0 };

  return (span: EvalSpan): string | null => {
    if (span.type !== 'tool_call') return null;
    const key = toolSignature(span, ignoreArgKeys);
    if (key === state.last) {
      state.run += 1;
    } else {
      state.last = key;
      state.run = 1;
    }
    if (state.run > limit) {
      return `tool "${span.name}" called ${state.run} times in a row with identical arguments, `
        + `limit is ${limit}`;
    }
    return null;
  };
}

/** A step failed. */
function errors(config: Config): Detector {
  const tolerance = cfgInt(config, 'max_failures', 0)!;
  const seen = { n: 0 };

  return (span: EvalSpan): string | null => {
    if (span.state !== 'failed') return null;
    seen.n += 1;
    if (seen.n > tolerance) {
      const detail = (span.error && span.error['message']) || span.name;
      return `step failed: ${detail}`;
    }
    return null;
  };
}

/** A single agent-side step ran longer than the ceiling. */
function latency(config: Config): Detector {
  const ceiling = cfgFloat(config, 'max_span_ms', 60000)!;
  const agentTypes = new Set((config['span_types'] as Iterable<string>) || ['llm_call', 'tool_call', 'retrieval']);

  return (span: EvalSpan): string | null => {
    if (!agentTypes.has(span.type) || span.durationMs === null) return null;
    if (span.durationMs > ceiling) {
      return `step "${span.name}" took ${Math.round(span.durationMs)} ms, ceiling is ${Math.round(ceiling)} ms`;
    }
    return null;
  };
}

/** The run has already used more steps than this agent's normal ceiling.
 *
 * The ceiling is passed in, computed once from the agent's recent history when
 * the watch starts, so no baseline means this detector is off. */
function overBaseline(config: Config): Detector {
  const raw = config['ceiling'];
  if (!raw) return () => null;
  const ceiling = Number(raw);
  const ceilInt = Math.trunc(ceiling);
  const count = { n: 0 };

  return (_span: EvalSpan): string | null => {
    count.n += 1;
    if (count.n === ceilInt + 1) {
      return `run has used ${count.n} steps, past the agent's normal ceiling of ${ceilInt}, `
        + 'and is still going';
    }
    return null;
  };
}

export const DETECTORS: Record<string, DetectorFactory> = {
  loops,
  redundant_calls: redundant,
  errors,
  latency,
  runaway: overBaseline,
};

/** Format a value the way Python's %r would, for the policy validation error. */
function repr(value: unknown): string {
  return typeof value === 'string' ? `'${value}'` : String(value);
}

/**
 * Per-span evaluation for one live run.
 *
 * Built from a policy mapping check name to off/warn/terminate. A check set to
 * off is never even instantiated. Feed spans in with `onSpan`; it returns the
 * breaches that span caused, each already carrying its policy.
 */
export class LiveEvaluator {
  readonly policy: Record<string, string>;
  private detectors: Map<string, Detector>;
  private fired: Set<string>;

  constructor(policy: Record<string, string>, config?: Record<string, Config>) {
    this.policy = { ...(policy || {}) };
    for (const [name, mode] of Object.entries(this.policy)) {
      if (!(POLICIES as readonly string[]).includes(mode)) {
        throw new Error(`policy for "${name}" must be one of ('off', 'warn', 'terminate'), not ${repr(mode)}`);
      }
    }
    const cfg = config || {};
    this.detectors = new Map();
    for (const [name, mode] of Object.entries(this.policy)) {
      if (mode === POLICY_OFF || !(name in DETECTORS)) continue;
      this.detectors.set(name, DETECTORS[name]!(cfg[name] || {}));
    }
    this.fired = new Set();
  }

  onSpan(span: EvalSpan): Breach[] {
    const breaches: Breach[] = [];
    for (const [name, detector] of this.detectors) {
      const reason = detector(span);
      if (reason === null) continue;
      // A given check fires once per run. A loop keeps tripping every call after
      // the limit; reporting it once is enough and one breach is enough to
      // terminate.
      if (this.fired.has(name)) continue;
      this.fired.add(name);
      breaches.push(new Breach(name, reason, span.id, this.policy[name]!));
    }
    return breaches;
  }

  get wantsTerminate(): boolean {
    return Object.values(this.policy).some((m) => m === POLICY_TERMINATE);
  }
}
