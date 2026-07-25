/**
 * Drive per-span evaluation over live runs.
 *
 * The loop is deliberately split from where the spans come from. `evaluateSpans`
 * takes a source of spans and a way to terminate, and knows nothing about
 * Prefactor, so it runs the same against the live API or against a fake in a
 * test. Delivery, polling the API or reacting to a subscription push, is the
 * caller's job.
 *
 * This is the line for line port of the Python live/watch.py.
 */

import type { Breach, LiveEvaluator } from './detectors.js';
import type { EvalSpan } from '../schema.js';

export class WatchResult {
  readonly instanceId: string;
  readonly breaches: Breach[];
  readonly terminated: boolean;
  readonly spansSeen: number;

  constructor(instanceId: string, breaches: Breach[], terminated: boolean, spansSeen: number) {
    this.instanceId = instanceId;
    this.breaches = breaches;
    this.terminated = terminated;
    this.spansSeen = spansSeen;
  }

  toDict(): Record<string, unknown> {
    return {
      instance_id: this.instanceId,
      spans_seen: this.spansSeen,
      terminated: this.terminated,
      breaches: this.breaches.map((b) => b.toDict()),
    };
  }
}

export interface EvaluateOptions {
  terminate?: (reason: string) => void | Promise<unknown>;
  onBreach?: (breach: Breach) => void;
  stopOnTerminate?: boolean;
}

/** Feed spans into the evaluator in order, acting on breaches.
 *
 * `spans` is any iterable of spans in arrival order. `terminate(reason)` is
 * called at most once, on the first breach whose policy says terminate. Warn
 * breaches are recorded and passed to `onBreach` but do not stop anything.
 *
 * Returns once the spans run out or the run is terminated. Pure apart from the
 * two callbacks, so it is fully testable without a network. */
export async function evaluateSpans(
  instanceId: string, spans: Iterable<EvalSpan>, evaluator: LiveEvaluator,
  options: EvaluateOptions = {},
): Promise<WatchResult> {
  const { terminate, onBreach, stopOnTerminate = true } = options;
  const breaches: Breach[] = [];
  let terminated = false;
  let seen = 0;
  for (const span of spans) {
    seen += 1;
    for (const breach of evaluator.onSpan(span)) {
      breaches.push(breach);
      if (onBreach !== undefined) onBreach(breach);
      if (breach.terminate && terminate !== undefined && !terminated) {
        await terminate(breach.reason);
        terminated = true;
      }
    }
    if (terminated && stopOnTerminate) break;
  }
  return new WatchResult(instanceId, breaches, terminated, seen);
}
