/**
 * core.tool_arg_schema
 *
 * Spec: spec/evals/core/tool_arg_schema.md
 *
 * Validation is done by the bundled draft 2020-12 subset (src/validate.ts, the
 * line for line port of Python's _schema_min), so this produces a real verdict
 * that the conformance suite proves identical to Python's, with no runtime
 * dependency on either side. Schemas outside the subset are refused by
 * schemaProblem before any data is inspected.
 */

import type { Config } from '../config.js';
import { cfgStr } from '../config.js';
import { comparePython, pyD } from '../fmt.js';
import { schemaProblem, spanIds } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance, EvalSpan } from '../schema.js';
import { sortSpans, spansOfType } from '../schema.js';
import { firstError, pointerOf, shortMessage } from '../validate.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.tool_arg_schema';

/** Python's truth test, needed where config is read raw via config.get. */
function truthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return false;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') return value.length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as object).length > 0;
  return true;
}

function hasKey(obj: unknown, key: string): boolean {
  return obj !== null && typeof obj === 'object'
    && Object.prototype.hasOwnProperty.call(obj, key);
}

/** Sorted, de-duplicated span names, matching Python's sorted({...}). */
function sortedNames(names: Iterable<string>): string[] {
  return [...new Set(names)].sort(comparePython);
}

export function run(
  instance: EvalInstance,
  config: Config,
  _context: EvalContext,
): EvalResult | null {
  // config.get("schemas") or {}: a falsy value skips exactly as Python does.
  const schemas = config['schemas'];
  if (!truthy(schemas)) {
    return skipped(
      EVAL_ID, instance.id,
      'No tool argument schemas configured, so nothing can be validated.',
      'Set schemas for this eval in the pack file, mapping each '
      + 'tool name to a JSON Schema for its input. There is no '
      + 'generic correct tool argument shape.',
    );
  }

  const toolCalls = sortSpans(spansOfType(instance, 'tool_call'));
  if (toolCalls.length === 0) {
    return skipped(
      EVAL_ID, instance.id,
      'No tool_call spans on this instance, nothing to check.',
      'If this agent does call tools, its span schema names are not '
      + 'recognised. Map them with span_type_map in the pack file.',
    );
  }

  const covered = toolCalls.filter((s) => hasKey(schemas, s.name));
  const uncovered = toolCalls.filter((s) => !hasKey(schemas, s.name));
  const uncoveredTools = sortedNames(uncovered.map((s) => s.name));

  if (covered.length === 0) {
    return skipped(
      EVAL_ID, instance.id,
      'No configured schema matches any tool in this instance. Tools '
      + `present: ${sortedNames(toolCalls.map((s) => s.name)).join(', ')}.`,
      'Add an entry to schemas for one of the tools this agent '
      + 'actually calls. Matching is exact and case sensitive on the '
      + 'span name.',
      { uncovered_tools: uncoveredTools },
    );
  }

  // Python refuses a schema on the wrong draft or with a remote $ref here, per
  // tool. That refusal is portable and is kept, so these skips match Python.
  const schemaMap = schemas as Record<string, unknown>;
  for (const tool of sortedNames(covered.map((s) => s.name))) {
    const problem = schemaProblem(schemaMap[tool]);
    if (problem !== null) {
      return skipped(
        EVAL_ID, instance.id,
        `The schema for tool "${tool}" ${problem}.`,
        `Fix the schemas entry for "${tool}" in the pack file. It is `
        + 'not validated under the wrong draft and remote '
        + 'references are never fetched.',
        { uncovered_tools: uncoveredTools },
      );
    }
  }

  const nullInput = cfgStr(config, 'null_input', 'fail');

  const checked: EvalSpan[] = [];
  const violations: Record<string, unknown>[] = [];
  const invalidSpans: EvalSpan[] = [];
  let uncoveredSpanCount = uncovered.length;

  for (const span of covered) {
    if (span.input === null && nullInput === 'skip_span') {
      uncoveredSpanCount += 1;
      continue;
    }
    checked.push(span);
    if (span.input === null) {
      invalidSpans.push(span);
      violations.push({
        span_id: span.id, tool: span.name, pointer: '',
        keyword: 'null_input', message: 'input is null',
      });
      continue;
    }
    const error = firstError(schemaMap[span.name], span.input);
    if (error === null) continue;
    invalidSpans.push(span);
    // One violation per span, so a single badly shaped input cannot produce
    // fifty entries. No argument values appear here.
    violations.push({
      span_id: span.id, tool: span.name, pointer: pointerOf(error.path),
      keyword: error.keyword, message: shortMessage(error),
    });
  }

  const values = {
    checked_spans: checked.length,
    invalid_spans: invalidSpans.length,
    uncovered_spans: uncoveredSpanCount,
    uncovered_tools: uncoveredTools,
    violations,
  };

  if (violations.length === 0) {
    return passed(
      EVAL_ID, instance.id,
      `All ${pyD(checked.length)} covered tool_call inputs validated, `
      + `${pyD(uncoveredSpanCount)} span(s) uncovered.`,
      undefined, values,
    );
  }

  const first = violations[0]!;
  return failed(
    EVAL_ID, instance.id,
    `${pyD(violations.length)} of ${pyD(checked.length)} covered tool_call `
    + `inputs failed schema validation, first: "${first['tool']}" ${first['message']}.`,
    spanIds(invalidSpans), values,
  );
}
