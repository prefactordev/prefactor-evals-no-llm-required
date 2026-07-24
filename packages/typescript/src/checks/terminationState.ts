/**
 * core.termination_state
 *
 * Spec: spec/evals/core/termination_state.md
 */

import type { Config } from '../config.js';
import { cfgList } from '../config.js';
import { pyD } from '../fmt.js';
import { readPathOne, spanIds } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.termination_state';

/** States that mean the run had not finished when it was fetched. Their elapsed
 * time is not computable without a clock read, which is forbidden. */
export const IN_FLIGHT_STATES = ['active', 'pending'];

export function run(
  instance: EvalInstance,
  config: Config,
  _context: EvalContext,
): EvalResult | null {
  const configured = cfgList(config, 'allow_states');
  const allowStates: unknown[] = configured.length > 0 ? configured : ['complete'];
  const maxDurationMs = config['max_duration_ms'] ?? null;
  const durationMs = instance.durationMs;
  const state = instance.state;

  const allowed = allowStates.includes(state);
  const inFlight = IN_FLIGHT_STATES.includes(state);

  if (!allowed && inFlight && durationMs === null) {
    return skipped(
      EVAL_ID, instance.id,
      `Instance is still in state "${state}" with no ended_at, so its elapsed `
      + 'time is not computable without reading a clock.',
      'Re-fetch this instance once it has finished, or widen '
      + 'allow_states in the pack file if this state is a clean '
      + 'finish for this agent.',
      { state, allow_states: allowStates },
    );
  }

  const failedSpans = instance.spans.filter((s) => s.state === 'failed');
  const values = {
    state,
    allow_states: allowStates,
    duration_ms: durationMs,
    max_duration_ms: maxDurationMs,
    termination_reason: readPathOne(instance.metadata, 'termination_reason'),
    failed_span_count: failedSpans.length,
  };

  const overCeiling = maxDurationMs !== null && durationMs !== null
    && durationMs > (maxDurationMs as number);

  // A terminal state that is not allowed is reported as a state failure even
  // when the run also blew its ceiling: the state is the stated reason.
  if (!allowed && !inFlight) {
    return failed(
      EVAL_ID, instance.id,
      `Instance ended in state "${state}", only `
      + `${allowStates.map((s) => `"${String(s)}"`).join(', ')} accepted.`,
      spanIds(failedSpans),
      values,
    );
  }

  if (overCeiling) {
    return failed(
      EVAL_ID, instance.id,
      `Instance completed in ${pyD(durationMs!)} ms, ceiling is `
      + `${pyD(maxDurationMs as number)} ms.`,
      undefined,
      values,
    );
  }

  if (!allowed) {
    return failed(
      EVAL_ID, instance.id,
      `Instance is in state "${state}" with both endpoints set, which is a stuck `
      + `record, only ${allowStates.map((s) => `"${String(s)}"`).join(', ')} accepted.`,
      spanIds(failedSpans),
      values,
    );
  }

  return passed(
    EVAL_ID, instance.id,
    `Instance ended in state "${state}".`,
    undefined,
    values,
  );
}
