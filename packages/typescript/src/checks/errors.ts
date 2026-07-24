/**
 * core.errors
 *
 * Did any step in the run fail. The most universal health signal there is: a
 * failed step means something the agent tried did not work, and it needs no
 * configuration on any agent, because "failed" is a state the platform records,
 * not a judgement the user has to define.
 *
 * Spec: spec/evals/core/errors.md
 */

import type { Config } from '../config.js';
import { cfgInt } from '../config.js';
import { spanIds } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed } from '../result.js';
import type { EvalInstance } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.errors';

/** Python's truth test, which differs from JavaScript's on empty containers. */
function truthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return false;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') return value.length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as object).length > 0;
  return true;
}

/** Python's str() for the values that reach a "%s" here. */
function pyStr(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return 'None';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (typeof value === 'number') return String(value);
  return pyRepr(value);
}

function pyRepr(value: unknown): string {
  if (typeof value === 'string') {
    const escaped = value.replace(/\\/g, '\\\\');
    // Python switches to a double quoted repr when the text contains an
    // apostrophe and no double quote, rather than escaping the apostrophe.
    if (escaped.includes("'") && !escaped.includes('"')) return `"${escaped}"`;
    return `'${escaped.replace(/'/g, "\\'")}'`;
  }
  if (Array.isArray(value)) return `[${value.map(pyRepr).join(', ')}]`;
  if (value !== null && typeof value === 'object') {
    const parts = Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${pyRepr(k)}: ${pyRepr(v)}`);
    return `{${parts.join(', ')}}`;
  }
  return pyStr(value);
}

export function run(
  instance: EvalInstance,
  config: Config,
  _context: EvalContext,
): EvalResult | null {
  // Zero config by default: any failed step is a concern. A tolerance is
  // allowed for agents where the odd retried failure is expected, but the
  // default is strict, because a silent tolerance would hide real breakage.
  const maxFailures = cfgInt(config, 'max_failures', 0)!;

  const failedSpans = instance.spans.filter((s) => s.state === 'failed');
  const values = {
    failed_spans: failedSpans.length,
    total_spans: instance.spans.length,
    max_failures: maxFailures,
  };

  if (failedSpans.length <= maxFailures) {
    return passed(
      EVAL_ID, instance.id,
      failedSpans.length === 0
        ? 'No steps failed.'
        : `${failedSpans.length} steps failed, within the tolerance of ${maxFailures}.`,
      undefined,
      values,
    );
  }

  const first = failedSpans[0]!;
  const error = first.error ?? {};
  const message = error['message'];
  const type = error['type'];
  const detail = truthy(message) ? pyStr(message)
    : truthy(type) ? pyStr(type)
      : 'no error detail recorded';
  return failed(
    EVAL_ID, instance.id,
    `${failedSpans.length} steps failed. First: "${first.name}" (${detail}).`,
    spanIds(failedSpans),
    values,
  );
}
