/**
 * Self calibrating baselines.
 *
 * A benchmark check judges a run against the agent's own normal, computed from
 * the other runs in the same batch, rather than a number someone typed or an
 * industry figure that is always arguable for a given agent.
 *
 * "This run took four times the median" needs no configuration, calibrates to
 * whatever the agent actually does, and catches the agent getting worse rather
 * than merely differing from an external ideal.
 *
 * Deterministic: the median of a fixed set of runs is the same every time.
 */

import type { EvalInstance } from './schema.js';

/** A baseline drawn from too few runs is noise. Below this, a benchmark check
 * skips rather than flagging a run for differing from a median of two. */
export const MIN_RUNS_FOR_BASELINE = 5;

/** How many times the median a run may reach before it is flagged. Generous on
 * purpose: the point is to catch a run that is thrashing, not one that is
 * merely above average. */
export const DEFAULT_TOLERANCE = 3.0;

/** A run below this absolute figure is never flagged however far above the
 * median it sits, so a batch of tiny runs does not make a slightly larger one
 * look pathological. Three times a median of one is still only three. */
export const DEFAULT_FLOOR = 12;

export function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  if (ordered.length % 2 === 1) return ordered[mid]!;
  return (ordered[mid - 1]! + ordered[mid]!) / 2;
}

/**
 * Runs that reached completion. The baseline is built from these only.
 *
 * A run that failed or was cancelled took as many steps as it did because it
 * broke, not because that is how much work the task needs. Mixing those in
 * would inflate what counts as normal.
 */
export function completed(instances: readonly EvalInstance[]): EvalInstance[] {
  return instances.filter((i) => i.state === 'complete');
}

/** The agent's own normal for one measurement, and whether a run exceeds it. */
export class Baseline {
  readonly sampleSize: number;
  readonly median: number | null;

  constructor(
    values: readonly number[],
    readonly tolerance: number = DEFAULT_TOLERANCE,
    readonly floor: number = DEFAULT_FLOOR,
  ) {
    this.sampleSize = values.length;
    this.median = median(values);
  }

  get ready(): boolean {
    return this.sampleSize >= MIN_RUNS_FOR_BASELINE && this.median !== null;
  }

  /** The value at which a run is flagged: the larger of a multiple of the
   * median and the absolute floor. */
  get ceiling(): number | null {
    if (this.median === null) return null;
    return Math.max(this.median * this.tolerance, this.floor);
  }

  exceeds(value: number): boolean {
    const c = this.ceiling;
    return c !== null && value > c;
  }
}

export function buildBaseline(
  instances: readonly EvalInstance[],
  measure: (i: EvalInstance) => number | null,
  tolerance: number = DEFAULT_TOLERANCE,
  floor: number = DEFAULT_FLOOR,
): Baseline {
  const values = completed(instances)
    .map(measure)
    .filter((v): v is number => typeof v === 'number');
  return new Baseline(values, tolerance, floor);
}
