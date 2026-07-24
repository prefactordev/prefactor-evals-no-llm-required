/**
 * Run a pack over a set of instances.
 *
 * Mirrors the Python runner, including how it handles a check that throws: a
 * bad config value becomes a skip naming the key, and anything else becomes a
 * skip that does not assert whose fault it is. A check must never take the run
 * down and must never be silently counted as a pass.
 */

import { ConfigError, type Config } from './config.js';
import { allEvals, type EvalContext } from './registry.js';
import { FAIL, PASS, SKIP, skipped, type EvalResult, type Status } from './result.js';
import type { EvalInstance } from './schema.js';

export interface Pack {
  pack: string;
  version?: number;
  schema_version?: string;
  span_type_map?: Record<string, string>;
  evals: Record<string, Config>;
}

/** The built in standard pack: the zero configuration health checks that run on
 * any agent. Defined in code so an installed package always has it. Must stay
 * in step with the Python STANDARD_PACK and spec/packs/standard.yaml. */
export const STANDARD_PACK: Pack = {
  pack: 'standard',
  version: 1,
  schema_version: 'v1',
  evals: {
    'core.errors': {},
    'core.loop_detection': { max_occurrences: 3 },
    'core.redundant_tool_calls': { max_repeats: 2 },
    'core.termination_state': { allow_states: ['complete'] },
    'core.latency_budget': { max_span_ms: 60000 },
    'core.efficiency': { tolerance: 3.0, floor: 12 },
    'core.conversation_length': { tolerance: 3.0, floor: 10 },
  },
};

export class RunReport {
  constructor(
    readonly pack: Pack,
    readonly results: EvalResult[],
    readonly instances: EvalInstance[],
    readonly unknownEvalIds: string[] = [],
  ) {}

  get counts(): Record<Status, number> {
    const out = { [PASS]: 0, [FAIL]: 0, [SKIP]: 0 } as Record<Status, number>;
    for (const r of this.results) out[r.status] += 1;
    return out;
  }

  /** How many checks actually ran, out of how many were requested. */
  get coverage(): [number, number] {
    const grouped = new Map<string, { ran: boolean }>();
    for (const r of this.results) {
      const entry = grouped.get(r.eval_id) ?? { ran: false };
      if (r.status === PASS || r.status === FAIL) entry.ran = true;
      grouped.set(r.eval_id, entry);
    }
    let ran = 0;
    for (const e of grouped.values()) if (e.ran) ran += 1;
    return [ran, grouped.size];
  }

  /** How many runs reached completion, and the rate. */
  get resolution(): [number, number, number] {
    const finished = this.instances.filter((i) => i.state === 'complete').length;
    const total = this.instances.length;
    return [finished, total, total ? finished / total : 0];
  }

  get failed(): boolean {
    return this.counts[FAIL] > 0;
  }
}

export function run(pack: Pack, instances: EvalInstance[]): RunReport {
  const registry = allEvals();
  const context: EvalContext = {
    instances,
    spanTypeMap: pack.span_type_map ?? {},
  };

  const results: EvalResult[] = [];
  const unknown: string[] = [];

  for (const evalId of Object.keys(pack.evals)) {
    const registered = registry[evalId];
    if (!registered) {
      unknown.push(evalId);
      continue;
    }
    const config = pack.evals[evalId] ?? {};
    for (const instance of instances) {
      let result: EvalResult | null;
      try {
        result = registered.run(instance, config, context);
      } catch (error) {
        if (error instanceof ConfigError) {
          // A wrong value in a hand written file. Name the key and the type
          // expected. This is the likeliest failure and it is the user's to
          // fix, so it must not read like a defect.
          result = skipped(evalId, instance.id, error.message, error.remedy);
        } else {
          // Anything else. Never fatal, never a pass, and it does not assert
          // whose fault it is: malformed config reaches older code just as
          // easily as a real defect does.
          const name = error instanceof Error ? error.name : 'Error';
          const message = error instanceof Error ? error.message : String(error);
          result = skipped(
            evalId, instance.id,
            `${evalId} could not run: ${name}: ${message}`,
            "Check this check's settings in the pack file first. If they look "
            + 'right, this is a bug worth reporting.',
          );
        }
      }
      if (result) results.push(result);
    }
  }

  return new RunReport(pack, results, instances, unknown);
}
