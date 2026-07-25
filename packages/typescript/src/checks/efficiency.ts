/**
 * core.efficiency
 *
 * How much work a run took to reach completion, measured against the agent's
 * own median. A run that took far more steps than this agent normally needs is
 * either thrashing or stuck. Self calibrating, so it needs no configuration and
 * no industry benchmark that would be arguable for any given agent.
 *
 * Spec: spec/evals/core/efficiency.md
 */

import { DEFAULT_FLOOR, DEFAULT_TOLERANCE, buildBaseline } from '../baseline.js';
import type { Config } from '../config.js';
import { cfgFloat, cfgInt } from '../config.js';
import { pyD, pyF, pyG } from '../fmt.js';
import { spanIds } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance } from '../schema.js';
import { sortSpans } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.efficiency';

function spanCount(instance: EvalInstance): number {
  return instance.spans.length;
}

export function run(
  instance: EvalInstance,
  config: Config,
  context: EvalContext,
): EvalResult | null {
  const tolerance = cfgFloat(config, 'tolerance', DEFAULT_TOLERANCE)!;
  const floor = cfgInt(config, 'floor', DEFAULT_FLOOR)!;

  const baseline = buildBaseline(context.instances, spanCount, tolerance, floor);

  if (!baseline.ready) {
    return skipped(
      EVAL_ID, instance.id,
      `Only ${pyD(baseline.sampleSize)} completed runs in this batch, too few to calibrate `
      + "against the agent's own normal.",
      'Evaluate more runs at once. This check compares each run '
      + 'against the median of the others, so it needs a handful to '
      + 'work from.',
      { sample_size: baseline.sampleSize },
    );
  }

  const count = spanCount(instance);
  const values = {
    span_count: count,
    median: baseline.median,
    ceiling: baseline.ceiling,
    tolerance,
    sample_size: baseline.sampleSize,
  };

  if (!baseline.exceeds(count)) {
    return passed(
      EVAL_ID, instance.id,
      `Used ${pyD(count)} steps, the agent's median is ${pyG(baseline.median!)}.`,
      undefined,
      values,
    );
  }

  return failed(
    EVAL_ID, instance.id,
    `Used ${pyD(count)} steps to finish, more than ${pyF(tolerance, 1)} times the agent's `
    + `median of ${pyG(baseline.median!)}. `
    + 'The run took far more work than this agent normally needs.',
    spanIds(sortSpans(instance.spans)),
    values,
  );
}
