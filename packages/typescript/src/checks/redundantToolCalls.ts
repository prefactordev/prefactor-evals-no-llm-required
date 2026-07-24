/**
 * core.redundant_tool_calls
 *
 * Spec: spec/evals/core/redundant_tool_calls.md
 */

import type { Config } from '../config.js';
import { cfgInt, cfgList } from '../config.js';
import { comparePython } from '../fmt.js';
import { canonical, digest, spanIds, stripKeys, toolSignature } from '../helpers.js';
import type { EvalResult } from '../result.js';
import { failed, passed, skipped } from '../result.js';
import type { EvalInstance, EvalSpan } from '../schema.js';
import { sortSpans, spansOfType } from '../schema.js';
import type { EvalContext } from '../registry.js';

export const EVAL_ID = 'core.redundant_tool_calls';

export function run(
  instance: EvalInstance,
  config: Config,
  _context: EvalContext,
): EvalResult | null {
  const maxRepeats = cfgInt(config, 'max_repeats', 2)!;
  const ignoreTools = cfgList(config, 'ignore_tools');
  const rawIgnoreArgKeys = config['ignore_arg_keys'];
  const ignoreArgKeys: string[] = Array.isArray(rawIgnoreArgKeys)
    ? (rawIgnoreArgKeys as string[]) : [];

  if (spansOfType(instance, 'tool_call').length === 0) {
    return skipped(
      EVAL_ID, instance.id,
      'No tool_call spans on this instance, nothing to check.',
      'If this agent does call tools, its span schema names are not '
      + 'recognised. Map them with span_type_map in the pack file.',
    );
  }

  const runs: EvalSpan[][] = [];
  let current: EvalSpan[] | null = null;
  let currentKey: [string, string] | null = null;
  for (const span of sortSpans(instance.spans)) {
    // Anything that is not an unignored tool_call ends the run in progress,
    // because something other than the repeated tool happened.
    if (span.type !== 'tool_call' || ignoreTools.includes(span.name)) {
      current = null;
      currentKey = null;
      continue;
    }
    const key: [string, string] = [toolSignature(span, ignoreArgKeys), canonical(span.output)];
    if (current !== null && currentKey !== null
        && key[0] === currentKey[0] && key[1] === currentKey[1]) {
      current.push(span);
      continue;
    }
    current = [span];
    currentKey = key;
    runs.push(current);
  }

  const offenders = runs.filter((r) => r.length > maxRepeats);

  if (offenders.length === 0) {
    let worst = 0;
    for (const r of runs) worst = Math.max(worst, r.length);
    return passed(
      EVAL_ID, instance.id,
      `No tool called more than ${maxRepeats} times consecutively without progress.`,
      undefined,
      { max_repeats: maxRepeats, worst_run_length: worst },
    );
  }

  // Deterministic ordering: longest run first, then by tool name for stable ties.
  offenders.sort((a, b) => (b.length - a.length) || comparePython(a[0]!.name, b[0]!.name));
  const worstRun = offenders[0]!;

  const evidenceSpans = offenders.flat();
  return failed(
    EVAL_ID, instance.id,
    `Tool "${worstRun[0]!.name}" called ${worstRun.length} times consecutively with `
    + `identical arguments and no change in output, limit is ${maxRepeats}.`,
    spanIds(evidenceSpans),
    {
      max_repeats: maxRepeats,
      offenders: offenders.map((r) => ({
        tool: r[0]!.name,
        run_length: r.length,
        limit: maxRepeats,
        // Hashes, not raw values: both arguments and outputs can be large and
        // can carry sensitive data.
        arg_digest: digest(stripKeys(r[0]!.input, ignoreArgKeys)),
        output_digest: digest(r[0]!.output),
      })),
    },
  );
}
