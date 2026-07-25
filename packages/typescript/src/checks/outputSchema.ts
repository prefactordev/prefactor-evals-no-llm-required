/**
 * core.output_schema
 *
 * Spec: spec/evals/core/output_schema.md
 *
 * Validation is done by the bundled draft 2020-12 subset (src/validate.ts, the
 * line for line port of Python's _schema_min), so this produces a real verdict
 * the conformance suite proves identical to Python's, with no runtime dependency
 * on either side. Schemas outside the subset are refused by schemaProblem before
 * the output is inspected.
 */

import type { Config } from '../config.js';
import { schemaProblem } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance } from '../schema.js';
import { sortSpans } from '../schema.js';
import { pointerOf, shortMessage, sortedErrors } from '../validate.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.output_schema';

// A wholly wrong output can otherwise produce hundreds of entries.
const MAX_VIOLATIONS = 5;

/** Python's truth test, needed where config is read raw via config.get. */
function truthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return false;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') return value.length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as object).length > 0;
  return true;
}

export function run(
  instance: EvalInstance,
  config: Config,
  _context: EvalContext,
): EvalResult | null {
  const schema = config['schema'];
  if (!truthy(schema)) {
    return skipped(
      EVAL_ID, instance.id,
      'No output schema configured, so nothing can be validated.',
      'Set schema for this eval in the pack file. There is no '
      + 'generic shape a correct agent output takes.',
    );
  }

  const problem = schemaProblem(schema);
  if (problem !== null) {
    return skipped(
      EVAL_ID, instance.id,
      `The configured output schema ${problem}.`,
      'Fix the schema entry for this eval in the pack file. It is '
      + 'not validated under the wrong draft and remote references '
      + 'are never fetched.',
    );
  }

  // The output is derived from the root span, so an instance whose spans were
  // sampled, truncated, or paged can lose its root. Never reconstructed from
  // the last span or any other heuristic.
  const roots = instance.spans.filter((s) => s.parentId === null);
  if (roots.length === 0) {
    return skipped(
      EVAL_ID, instance.id,
      'No span has a null parent_id, so there is no root span and no '
      + 'instance output to validate.',
      'Nothing to change in config. Fetch the instance without '
      + 'sampling or a page limit so the root span is present.',
      { root_span_count: 0 },
    );
  }
  if (roots.length > 1) {
    return skipped(
      EVAL_ID, instance.id,
      `This instance has ${roots.length} root spans, so the instance output is `
      + 'ambiguous and is not guessed.',
      'Nothing to change in config. An instance assembled from '
      + 'parallel sub-agents legitimately has several roots and has '
      + 'no single output.',
      { root_span_count: roots.length },
    );
  }

  const root = sortSpans(roots)[0]!;
  const output = instance.output !== null && instance.output !== undefined
    ? instance.output : root.output;

  if (output === null || output === undefined) {
    const nullOutput = 'null_output' in config ? config['null_output'] : 'skip';
    if (nullOutput === 'fail') {
      return failed(
        EVAL_ID, instance.id,
        'Instance output is null, and null_output is set to fail.',
        [root.id],
        {
          root_span_id: root.id,
          root_span_name: root.name,
          violations: [{ pointer: '', keyword: 'null_output', message: 'output is null' }],
          violation_count: 1,
        },
      );
    }
    return skipped(
      EVAL_ID, instance.id,
      'The root span output is null, so there is no output to validate.',
      'Set null_output to "fail" for this eval if this agent is '
      + 'required to produce structured output on every run.',
      { root_span_id: root.id, root_span_name: root.name },
    );
  }

  const errors = sortedErrors(schema, output);

  const values = {
    root_span_id: root.id,
    root_span_name: root.name,
    // Locations and keywords only, never the offending values, which can be
    // sensitive.
    violations: errors.slice(0, MAX_VIOLATIONS).map((e) => ({
      pointer: pointerOf(e.path),
      keyword: e.keyword,
      message: shortMessage(e),
    })),
    violation_count: errors.length,
  };

  if (errors.length === 0) {
    return passed(
      EVAL_ID, instance.id,
      'Instance output validated against the configured schema.',
      [root.id], values,
    );
  }

  return failed(
    EVAL_ID, instance.id,
    `Instance output failed schema validation: ${values.violations[0]!.message}.`,
    [root.id], values,
  );
}
