/**
 * core.cost_budget
 *
 * Spec: spec/evals/core/cost_budget.md
 */

import type { Config } from '../config.js';
import { pyF } from '../fmt.js';
import { readPathOne } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.cost_budget';

/** Python's float(): identity on a number, True/False fold to 1.0/0.0, a numeric
 * string is parsed. Anything else is a ValueError, left to propagate exactly as
 * Python's does, because config coercion here is read raw via config.get. */
function pyFloat(value: unknown): number {
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number') return value;
  if (typeof value === 'string') {
    const text = value.trim();
    const lower = text.toLowerCase();
    if (lower === 'inf' || lower === '+inf' || lower === 'infinity' || lower === '+infinity') return Infinity;
    if (lower === '-inf' || lower === '-infinity') return -Infinity;
    if (lower === 'nan' || lower === '+nan' || lower === '-nan') return NaN;
    const n = Number(text);
    if (text !== '' && Number.isFinite(n)) return n;
    throw new Error(`could not convert string to float: '${value}'`);
  }
  throw new Error('float() argument must be a string or a number');
}

export function run(
  instance: EvalInstance,
  config: Config,
  _context: EvalContext,
): EvalResult | null {
  // Read raw via config.get, not cfg_float: Python reads it raw and coerces
  // with float(), so the same path is taken here to keep behaviour identical.
  const rawMaxCost = config['max_cost'];
  if (rawMaxCost === null || rawMaxCost === undefined) {
    return skipped(
      EVAL_ID, instance.id,
      'No cost budget configured, so there is no ceiling to judge against.',
      'Set max_cost for this eval in the pack file. Cost is in '
      + 'whatever currency the account reports and the tolerable '
      + 'spend for one run is a business decision.',
    );
  }
  const maxCost = pyFloat(rawMaxCost);

  // Explicit null check, never a falsiness check: a cost of exactly 0 is a
  // real value, and null means not requested, never free.
  if (instance.cost === null) {
    return skipped(
      EVAL_ID, instance.id,
      'Instance cost was not fetched, so cost is null and cannot be judged.',
      'Fetch instances with cost data so EvalInstance.cost is '
      + 'populated. Treating a null cost as zero would pass every '
      + 'instance the seam fetched cheaply.',
      { max_cost: maxCost },
    );
  }

  const cost = pyFloat(instance.cost);
  const values = {
    cost: instance.cost,
    max_cost: maxCost,
    overage: cost > maxCost ? cost - maxCost : 0,
    cost_breakdown: readPathOne(instance.metadata, 'cost_breakdown'),
    span_count: instance.spans.length,
  };

  if (cost <= maxCost) {
    return passed(
      EVAL_ID, instance.id,
      `Instance cost ${pyF(cost, 4)} is within budget of ${pyF(maxCost, 4)}.`,
      undefined,
      values,
    );
  }

  // No span_ids ever: cost is not attributable to any span, and naming spans
  // here would imply an attribution the data does not support.
  return failed(
    EVAL_ID, instance.id,
    `Instance cost ${pyF(cost, 4)} exceeds budget of ${pyF(maxCost, 4)}.`,
    undefined,
    values,
  );
}
