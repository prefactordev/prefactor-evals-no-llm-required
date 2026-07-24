/**
 * core.conversation_length
 *
 * How many back and forth turns a run took to reach completion, against the
 * agent's own median. A conversational agent that needs far more turns than
 * usual to finish is going in circles or failing to understand. Self
 * calibrating.
 *
 * A turn is counted from the messages back to the person. Which spans those are
 * varies by instrumentation, so the check looks for spans typed `output`, then
 * falls back to the `handoff` and `output` family, and skips honestly when it
 * cannot tell what a turn is rather than guessing.
 *
 * Spec: spec/evals/core/conversation_length.md
 */

import { DEFAULT_TOLERANCE, buildBaseline } from '../baseline.js';
import type { Config } from '../config.js';
import { cfgFloat, cfgInt, cfgStrSet } from '../config.js';
import { pyD, pyF, pyG } from '../fmt.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance, EvalSpan } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.conversation_length';

/** A conversation long enough to be a concern in any domain, so a batch of
 * short chats does not make a slightly longer one look pathological. */
export const TURN_FLOOR = 10;

function turnSpans(instance: EvalInstance, turnNames: Set<string>): EvalSpan[] {
  if (turnNames.size > 0) {
    return instance.spans.filter((s) => turnNames.has(s.name) || turnNames.has(s.schemaName));
  }
  // No names configured: output spans are messages back to the person.
  return instance.spans.filter((s) => s.type === 'output');
}

export function run(
  instance: EvalInstance,
  config: Config,
  context: EvalContext,
): EvalResult | null {
  const tolerance = cfgFloat(config, 'tolerance', DEFAULT_TOLERANCE)!;
  const floor = cfgInt(config, 'floor', TURN_FLOOR)!;
  const turnNames = cfgStrSet(config, 'turn_span_names');

  const turns = (inst: EvalInstance): number => turnSpans(inst, turnNames).length;

  // If nothing in this run looks like a turn, the metric does not apply to this
  // agent. Skip rather than reporting zero turns as a clean pass.
  if (turns(instance) === 0 && turnNames.size === 0) {
    return skipped(
      EVAL_ID, instance.id,
      'No output spans in this run, so it does not look like a '
      + 'conversation and turns cannot be counted.',
      'If this agent is conversational, set turn_span_names to the '
      + 'spans that carry a message back to the person. Otherwise '
      + 'this check does not apply and can be left off.',
    );
  }

  const baseline = buildBaseline(context.instances, turns, tolerance, floor);
  if (!baseline.ready) {
    return skipped(
      EVAL_ID, instance.id,
      `Only ${pyD(baseline.sampleSize)} completed runs in this batch, too few to calibrate turn `
      + "length against the agent's own normal.",
      'Evaluate more runs at once so the median has something to stand on.',
      { sample_size: baseline.sampleSize },
    );
  }

  const count = turns(instance);
  const values = {
    turns: count,
    median: baseline.median,
    ceiling: baseline.ceiling,
    tolerance,
    sample_size: baseline.sampleSize,
  };

  if (!baseline.exceeds(count)) {
    return passed(
      EVAL_ID, instance.id,
      `Took ${pyD(count)} turns, the agent's median is ${pyG(baseline.median!)}.`,
      undefined,
      values,
    );
  }

  return failed(
    EVAL_ID, instance.id,
    `Took ${pyD(count)} turns to finish, more than ${pyF(tolerance, 1)} times the agent's `
    + `median of ${pyG(baseline.median!)}.`,
    undefined,
    values,
  );
}
