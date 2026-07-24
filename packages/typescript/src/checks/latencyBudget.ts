/**
 * core.latency_budget
 *
 * Spec: spec/evals/core/latency_budget.md
 */

import type { Config } from '../config.js';
import { cfgList } from '../config.js';
import { comparePython, pyD } from '../fmt.js';
import { spanIds } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance, EvalSpan } from '../schema.js';
import { sortSpans } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.latency_budget';

/** A pathological run must not produce a result object larger than the trace
 * summary, so the reported list is capped and the true total carried alongside. */
export const MAX_SLOWEST = 10;

/** A single agent step taking longer than this is a hang in almost any domain.
 * Generous on purpose, so zero config catches only clear stalls and never a
 * merely slow step. Tune it down for a latency sensitive agent like voice. */
export const DEFAULT_MAX_SPAN_MS = 60000;

/** By default, measure only the agent's own work. A message or output span
 * covers the time a person spent reading and replying, which can be minutes and
 * is not the agent being slow. Measuring it would blame the agent for human
 * think time. */
export const DEFAULT_SPAN_TYPES = ['llm_call', 'tool_call', 'retrieval'];

export function run(
  instance: EvalInstance,
  config: Config,
  _context: EvalContext,
): EvalResult | null {
  // Runs zero config with a generous default. max_instance_ms stays opt in,
  // because a sensible whole run ceiling is genuinely agent specific.
  const rawMaxSpanMs = 'max_span_ms' in config ? config['max_span_ms'] : DEFAULT_MAX_SPAN_MS;
  const maxSpanMs = rawMaxSpanMs === null || rawMaxSpanMs === undefined
    ? null : (rawMaxSpanMs as number);
  const maxInstanceMs = (config['max_instance_ms'] ?? null) as number | null;
  const configuredTypes = cfgList(config, 'span_types');
  const spanTypes: unknown[] = configuredTypes.length > 0 ? configuredTypes : [...DEFAULT_SPAN_TYPES];
  const ignoreSpans = cfgList(config, 'ignore_spans');

  const measured: EvalSpan[] = [];
  const unmeasured: EvalSpan[] = [];
  const over: EvalSpan[] = [];
  if (maxSpanMs !== null) {
    for (const span of sortSpans(instance.spans)) {
      if (spanTypes.length > 0 && !spanTypes.includes(span.type)) continue;
      if (ignoreSpans.includes(span.name)) continue;
      // Null duration means an open span. Inferring one would need a clock
      // read, so it is reported and never counted as a pass or a failure.
      if (span.durationMs === null) {
        unmeasured.push(span);
        continue;
      }
      measured.push(span);
      if (span.durationMs > maxSpanMs) over.push(span);
    }
  }

  const instanceMeasured = maxInstanceMs !== null && instance.durationMs !== null;
  const instanceOver = instanceMeasured && instance.durationMs! > maxInstanceMs!;

  const spanHalfRan = maxSpanMs !== null && measured.length > 0;
  if (!spanHalfRan && !instanceMeasured) {
    return skipped(
      EVAL_ID, instance.id,
      'No duration was measurable on this instance, so no latency verdict '
      + 'is possible.',
      'Nothing to change in config. Re-fetch once the run and its '
      + 'spans have closed, or check core.termination_state, which '
      + 'reports the termination that left them open.',
      {
        max_span_ms: maxSpanMs,
        max_instance_ms: maxInstanceMs,
        unmeasured_spans: unmeasured.length,
        unmeasured_span_ids: spanIds(unmeasured),
      },
    );
  }

  const slowest = [...over]
    .sort((a, b) => (b.durationMs! - a.durationMs!) || comparePython(a.id, b.id))
    .slice(0, MAX_SLOWEST);
  const values = {
    max_span_ms: maxSpanMs,
    max_instance_ms: maxInstanceMs,
    instance_duration_ms: instance.durationMs,
    instance_over: instanceOver,
    slowest_spans: slowest.map((s) => ({
      span_id: s.id,
      name: s.name,
      type: s.type,
      duration_ms: s.durationMs,
    })),
    over_span_count: over.length,
    measured_spans: measured.length,
    unmeasured_spans: unmeasured.length,
    unmeasured_span_ids: spanIds(unmeasured),
  };

  if (over.length > 0) {
    const worst = slowest[0]!;
    return failed(
      EVAL_ID, instance.id,
      `Span "${worst.name}" took ${pyD(worst.durationMs!)} ms, ceiling is `
      + `${pyD(maxSpanMs!)} ms.`,
      spanIds(over),
      values,
    );
  }

  if (instanceOver) {
    return failed(
      EVAL_ID, instance.id,
      `Instance took ${pyD(instance.durationMs!)} ms, ceiling is ${pyD(maxInstanceMs!)} ms.`,
      undefined,
      values,
    );
  }

  return passed(
    EVAL_ID, instance.id,
    'Every measured duration was within its ceiling.',
    undefined,
    values,
  );
}
