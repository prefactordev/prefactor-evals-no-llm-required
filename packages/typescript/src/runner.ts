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
import { existsSync, readFileSync } from 'node:fs';
import type { EvalInstance } from './schema.js';

/** One eval's tally across every instance, plus up to three failure details and
 * the config remedy for a skipped eval. Matches Python's by_eval() entry. */
export interface EvalGroup {
  pass: number;
  fail: number;
  skip: number;
  remedy: string | null;
  details: [string, string][];
}

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

/** The standard checks plus the optional ones, each with the config it needs
 * left empty so it skips and says what it wants. Mirrors
 * spec/packs/advanced.yaml and the Python bundled copy; a conformance run in CI
 * holds the two implementations to the same results on it. */
export const ADVANCED_PACK: Pack = {
  pack: 'advanced',
  version: 1,
  schema_version: 'v1',
  evals: {
    ...STANDARD_PACK.evals,
    'core.cost_budget': { max_cost: null },
    'core.forbidden_actions': { forbidden: [], forbidden_patterns: [] },
    'core.tool_arg_schema': { schemas: {} },
    'core.output_schema': { schema: null },
    'core.escalation_rule': { handoff_types: ['handoff'], trigger_names: [] },
  },
};

const BUNDLED_PACKS: Record<string, Pack> = {
  standard: STANDARD_PACK,
  advanced: ADVANCED_PACK,
};

/** Load a pack: a bare built in name (standard, advanced) resolves to the
 * bundled pack, anything else is read as a JSON file. YAML packs are read by
 * the Python side; every YAML pack has a JSON equivalent. A real file whose
 * name collides with a built in still wins, as the more deliberate act. */
export class PackError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PackError';
  }
}

export function loadPack(path: string): Pack {
  const bundled = BUNDLED_PACKS[path];
  if (bundled && !existsSync(path)) return bundled;
  if (!existsSync(path)) {
    throw new PackError(
      `Pack file not found: ${path}. Pass a path to a pack file, or a `
      + 'built in name: standard, advanced.');
  }
  let data: Partial<Pack>;
  try {
    data = JSON.parse(readFileSync(path, 'utf8')) as Partial<Pack>;
  } catch (error) {
    throw new PackError(`Pack file did not parse as JSON: ${path}. `
      + `${error instanceof Error ? error.message : String(error)}`);
  }
  // Normalized like Python's Pack constructor, so a pack file missing optional
  // keys never leaks undefined into scorecards or published payloads, where
  // JSON.stringify would silently drop the key while Python writes a value.
  return {
    pack: data.pack || 'custom',
    version: data.version ?? 1,
    schema_version: data.schema_version ?? 'v1',
    ...(data.span_type_map ? { span_type_map: data.span_type_map } : {}),
    evals: data.evals ?? {},
  };
}

/** Checks that need the instance cost breakdown, which is a second request per
 * instance and so is only fetched when one of these is in the pack. */
const COST_EVALS = ['core.cost_budget'];

/**
 * Load a pack, fetch instances through the seam, run everything. The one call
 * the CLI's run command makes. With no pack path, the built in standard pack is
 * used. `source` can be injected for tests; otherwise the Prefactor seam is used.
 */
export async function runPack(
  packPath: string | null,
  agentId: string,
  filters: {
    since?: unknown; until?: unknown; state?: string | null;
    environment?: string | null; purpose?: string | null; limit?: number | null;
  } = {},
  source?: { fetchInstances: (agentId: string, filters: Record<string, unknown>) => Promise<EvalInstance[]> },
): Promise<RunReport> {
  const pack = packPath ? loadPack(packPath) : STANDARD_PACK;
  const needsCost = Object.keys(pack.evals).some((e) => COST_EVALS.includes(e));

  let seam = source;
  if (!seam) {
    const { PrefactorSource } = await import('./source.js');
    seam = new PrefactorSource({ spanTypeMap: pack.span_type_map ?? {} });
  }

  const instances = await seam.fetchInstances(agentId, {
    since: filters.since, until: filters.until, state: filters.state,
    environment: filters.environment, purpose: filters.purpose,
    limit: filters.limit, withCost: needsCost,
  });
  return run(pack, instances);
}

export class RunReport {
  readonly pack: Pack;
  readonly results: EvalResult[];
  readonly instances: EvalInstance[];
  readonly unknownEvalIds: string[];

  constructor(
    pack: Pack,
    results: EvalResult[],
    instances: EvalInstance[],
    unknownEvalIds: string[] = [],
  ) {
    this.pack = pack;
    this.results = results;
    this.instances = instances;
    this.unknownEvalIds = unknownEvalIds;
  }

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

  /** Group results by eval id: counts, up to three failure details, and the
   * config remedy for a skipped eval. The scorecard renders from this. */
  byEval(): Record<string, EvalGroup> {
    const grouped: Record<string, EvalGroup> = {};
    for (const result of this.results) {
      let entry = grouped[result.eval_id];
      if (!entry) {
        entry = { pass: 0, fail: 0, skip: 0, remedy: null, details: [] };
        grouped[result.eval_id] = entry;
      }
      entry[result.status] += 1;
      if (result.status === SKIP && result.remedy && !entry.remedy) {
        entry.remedy = result.remedy;
      }
      if (result.status === FAIL && entry.details.length < 3) {
        entry.details.push([result.instance_id, result.details]);
      }
    }
    return grouped;
  }

  /** The serializable report shape. Mirrors Python's to_dict exactly, save the
   * pack path, which the TypeScript Pack does not carry. */
  toDict(runTimestamp?: string | null): Record<string, unknown> {
    const [ran, requested] = this.coverage;
    return {
      schema_version: this.pack.schema_version ?? 'v1',
      pack: { id: this.pack.pack, version: this.pack.version ?? 1, path: null },
      run_timestamp: runTimestamp ?? null,
      instance_count: this.instances.length,
      counts: this.counts,
      coverage: { ran, requested },
      unknown_eval_ids: this.unknownEvalIds,
      results: this.results.map((r) => resultToDict(r)),
    };
  }
}

/** One result as a plain object, dropping remedy when unset, like Python's
 * EvalResult.to_dict. */
function resultToDict(result: EvalResult): Record<string, unknown> {
  const out: Record<string, unknown> = {
    eval_id: result.eval_id,
    instance_id: result.instance_id,
    status: result.status,
    details: result.details,
    evidence: result.evidence,
  };
  if (result.remedy) out['remedy'] = result.remedy;
  return out;
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
            "Check this eval's settings in the pack file first. If they look "
            + 'right, this is a bug worth reporting.',
          );
        }
      }
      if (result) results.push(result);
    }
  }

  return new RunReport(pack, results, instances, unknown);
}
