/**
 * core.loop_detection
 *
 * Spec: spec/evals/core/loop_detection.md
 */

import type { Config } from '../config.js';
import { cfgInt, cfgList } from '../config.js';
import { comparePython } from '../fmt.js';
import { digest, spanIds, stripKeys, toolSignature } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance, EvalSpan } from '../schema.js';
import { spansOfType } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.loop_detection';

export function run(
  instance: EvalInstance,
  config: Config,
  _context: EvalContext,
): EvalResult | null {
  const maxOccurrences = cfgInt(config, 'max_occurrences', 3)!;
  const ignoreTools = cfgList(config, 'ignore_tools');
  const rawIgnoreArgKeys = config['ignore_arg_keys'];
  const ignoreArgKeys: string[] = Array.isArray(rawIgnoreArgKeys)
    ? (rawIgnoreArgKeys as string[]) : [];

  const toolCalls = spansOfType(instance, 'tool_call')
    .filter((s) => !ignoreTools.includes(s.name));
  if (toolCalls.length === 0) {
    return skipped(
      EVAL_ID, instance.id,
      'No tool_call spans on this instance, nothing to check.',
      'If this agent does call tools, its span schema names are not '
      + 'recognised. Map them with span_type_map in the pack file.',
    );
  }

  // A Map, not an object: object keys that look like integers would reorder,
  // and the group order has to match Python's insertion ordered dict.
  const groups = new Map<string, EvalSpan[]>();
  for (const span of toolCalls) {
    const signature = toolSignature(span, ignoreArgKeys);
    const group = groups.get(signature);
    if (group) group.push(span);
    else groups.set(signature, [span]);
  }

  const offenders = [...groups.entries()].filter(([, spans]) => spans.length > maxOccurrences);

  if (offenders.length === 0) {
    let worst = 0;
    for (const spans of groups.values()) worst = Math.max(worst, spans.length);
    return passed(
      EVAL_ID, instance.id,
      `No tool signature repeated more than ${maxOccurrences} times.`,
      undefined,
      { max_occurrences: maxOccurrences, worst_count: worst },
    );
  }

  // Deterministic ordering: worst first, then by tool name for stable ties.
  offenders.sort((a, b) => (b[1].length - a[1].length)
    || comparePython(a[1][0]!.name, b[1][0]!.name));
  const worstSpans = offenders[0]![1];

  const evidenceSpans = offenders.flatMap(([, spans]) => spans);
  return failed(
    EVAL_ID, instance.id,
    `Tool "${worstSpans[0]!.name}" called ${worstSpans.length} times with identical `
    + `arguments, limit is ${maxOccurrences}.`,
    spanIds(evidenceSpans),
    {
      max_occurrences: maxOccurrences,
      offenders: offenders.map(([, spans]) => ({
        tool: spans[0]!.name,
        count: spans.length,
        limit: maxOccurrences,
        // A hash, not the raw arguments: arguments can be large and can
        // contain sensitive values. They are one span lookup away in
        // Prefactor.
        arg_digest: digest(stripKeys(spans[0]!.input, ignoreArgKeys)),
      })),
    },
  );
}
