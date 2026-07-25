/**
 * core.escalation_rule
 *
 * Spec: spec/evals/core/escalation_rule.md
 */

import type { Config } from '../config.js';
import { cfgDict, cfgList } from '../config.js';
import { comparePython, pyD } from '../fmt.js';
import { canonical, readPath, spanIds } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance, EvalSpan } from '../schema.js';
import { sortSpans } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.escalation_rule';

/** Python's truth test, needed where config is read raw via config.get. */
function truthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return false;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') return value.length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as object).length > 0;
  return true;
}

/**
 * Exact, type sensitive equality.
 *
 * Python treats True as 1, so booleans are separated explicitly: the string
 * "true" must not match the boolean true and the integer 1 must not match it
 * either. Numeric comparison stays by value, so 1 and 1.0 are equal. For any
 * other value the canonical form is compared, which is Python's structural ==
 * for JSON shaped data.
 */
function equal(left: unknown, right: unknown): boolean {
  const leftBool = typeof left === 'boolean';
  const rightBool = typeof right === 'boolean';
  if (leftBool !== rightBool) return false;
  if (leftBool) return left === right;
  const leftNum = typeof left === 'number';
  const rightNum = typeof right === 'number';
  if (leftNum !== rightNum) return false;
  return canonical(left) === canonical(right);
}

export function run(
  instance: EvalInstance,
  config: Config,
  context: EvalContext,
): EvalResult | null {
  const configuredHandoff = cfgList(config, 'handoff_types');
  const handoffTypes: unknown[] = configuredHandoff.length > 0 ? configuredHandoff : ['handoff'];

  // Prefactor has no wire level span type enum and `handoff` has no built in
  // mapping at all, so without configuration this eval would find zero
  // handoffs on a trace containing several. Check that before any trigger.
  const mapped = Object.values(context.spanTypeMap ?? {}).some((v) => handoffTypes.includes(v));
  const present = instance.spans.some((s) => handoffTypes.includes(s.type));
  if (!mapped && !present && !truthy(config['handoff_types'])) {
    return skipped(
      EVAL_ID, instance.id,
      'No handoff span type is configured, and nothing in this instance '
      + 'carries one, so an escalation could not be detected even if it '
      + 'happened.',
      'Map at least one schema_name to "handoff" in the pack '
      + 'file\'s span_type_map, for example '
      + '{"support:transfer_to_human": "handoff"}.',
      { handoff_types: handoffTypes },
    );
  }

  const triggerNames = cfgList(config, 'trigger_names');
  const triggerValues = cfgDict(config, 'trigger_values');
  if (!(triggerNames.length > 0 || Object.keys(triggerValues).length > 0)) {
    return skipped(
      EVAL_ID, instance.id,
      'No escalation trigger configured, so nothing can fire.',
      'Set trigger_names or trigger_values for this eval in the '
      + "pack file. What counts as an escalation trigger is specific "
      + "to one agent's design.",
      { handoff_types: handoffTypes },
    );
  }

  const ordered = sortSpans(instance.spans);
  const sortedValuePaths = Object.keys(triggerValues).sort(comparePython);

  const triggers: Array<{ index: number; span: EvalSpan; rule: string; matched: string }> = [];
  for (let index = 0; index < ordered.length; index += 1) {
    const span = ordered[index]!;
    let hit: { rule: string; matched: string } | null = null;
    if (triggerNames.includes(span.name)) {
      hit = { rule: 'name', matched: span.name };
    } else if (span.output !== null && typeof span.output === 'object' && !Array.isArray(span.output)) {
      for (const path of sortedValuePaths) {
        const found = readPath(span.output, path);
        if (found.length > 0 && equal(found[0], triggerValues[path])) {
          hit = { rule: 'value', matched: path };
          break;
        }
      }
    }
    if (hit !== null) triggers.push({ index, span, rule: hit.rule, matched: hit.matched });
  }

  const handoffIndexes: number[] = [];
  for (let i = 0; i < ordered.length; i += 1) {
    if (handoffTypes.includes(ordered[i]!.type)) handoffIndexes.push(i);
  }

  if (triggers.length === 0) {
    return passed(
      EVAL_ID, instance.id,
      'No escalation trigger fired, so the rule was never engaged. This is '
      + 'a vacuous pass, not evidence that an escalation was handled.',
      undefined,
      {
        trigger_count: 0,
        span_count: ordered.length,
        handoff_types: handoffTypes,
      },
    );
  }

  const first = triggers[0]!;
  const before = handoffIndexes.filter((i) => i < first.index).map((i) => ordered[i]!);
  const after = handoffIndexes.filter((i) => i > first.index).map((i) => ordered[i]!);

  // Positions are reported one based for readability. Ordering comparisons
  // above use the zero based index into schema order.
  const values = {
    trigger_span_id: first.span.id,
    trigger_rule: first.rule,
    trigger_matched: first.matched,
    trigger_index: first.index + 1,
    span_count: ordered.length,
    handoff_types: handoffTypes,
    handoff_spans_before_trigger: before.length,
    handoff_spans_after_trigger: after.length,
    trigger_count: triggers.length,
  };

  if (after.length > 0) {
    return passed(
      EVAL_ID, instance.id,
      `Trigger "${first.matched}" fired at span ${pyD(first.index + 1)} of `
      + `${pyD(ordered.length)} and a handoff span followed.`,
      spanIds([first.span, ...after]),
      values,
    );
  }

  return failed(
    EVAL_ID, instance.id,
    `Trigger "${first.matched}" fired at span ${pyD(first.index + 1)} of `
    + `${pyD(ordered.length)} and no handoff span followed.`,
    spanIds([first.span, ...before]),
    values,
  );
}
