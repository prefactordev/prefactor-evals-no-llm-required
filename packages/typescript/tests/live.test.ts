/**
 * Live, per-span evaluation, exercised without a network.
 *
 * Runs against dist/, the built output, like the other suites. Everything here
 * is deterministic: synthetic spans, no timers, no clock, no sockets.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import {
  Breach, LiveEvaluator, POLICY_OFF, POLICY_TERMINATE, POLICY_WARN,
} from '../dist/live/index.js';
import { DETECTORS } from '../dist/live/detectors.js';
import { evaluateSpans } from '../dist/live/watch.js';
import type { EvalSpan } from '../dist/schema.js';

// Copied from core.test.ts so this suite stands alone.
function span(over: Partial<EvalSpan> = {}): EvalSpan {
  return {
    id: 's1', parentId: null, instanceId: 'i1', type: 'tool_call', name: 'do',
    schemaName: 'x:do', input: {}, output: {}, state: 'complete',
    startedAt: new Date('2026-07-01T00:00:00Z'),
    endedAt: new Date('2026-07-01T00:00:01Z'),
    durationMs: 1000, cost: null, tokens: null, error: null, metadata: {},
    ...over,
  };
}

/** A run of identical tool calls: same name and args, so one tool signature. */
function identicalCalls(n: number): EvalSpan[] {
  return Array.from({ length: n }, (_unused, i) =>
    span({ id: `s${i}`, name: 'search', input: { q: 'x' } }));
}

describe('the loop detector', () => {
  it('fires once past its limit, with the count and limit in the reason', () => {
    const evaluator = new LiveEvaluator({ loops: POLICY_WARN }, { loops: { max_occurrences: 3 } });
    const breaches: Breach[] = [];
    for (const s of identicalCalls(5)) {
      breaches.push(...evaluator.onSpan(s));
    }
    // Trips on the 4th identical call (past a limit of 3), and only once: a loop
    // keeps tripping every call after the limit, but one breach is enough.
    assert.equal(breaches.length, 1);
    assert.equal(breaches[0]!.check, 'loops');
    assert.match(breaches[0]!.reason, /called 4 times with identical arguments, limit is 3/);
  });

  it('never fires for a signature under the limit', () => {
    const evaluator = new LiveEvaluator({ loops: POLICY_WARN }, { loops: { max_occurrences: 3 } });
    const breaches: Breach[] = [];
    for (const s of identicalCalls(3)) {
      breaches.push(...evaluator.onSpan(s));
    }
    assert.equal(breaches.length, 0);
  });
});

describe('evaluateSpans acts on policy', () => {
  it('a terminate breach fires the callback exactly once and stops', async () => {
    const evaluator = new LiveEvaluator({ loops: POLICY_TERMINATE }, { loops: { max_occurrences: 3 } });
    let terminations = 0;
    let lastReason = '';
    const result = await evaluateSpans('i1', identicalCalls(6), evaluator, {
      terminate: (reason: string) => { terminations += 1; lastReason = reason; },
    });
    assert.equal(terminations, 1, 'terminate called exactly once');
    assert.equal(result.terminated, true);
    // Stopped on the breaching span (the 4th), never reaching the remaining two.
    assert.equal(result.spansSeen, 4);
    assert.equal(result.breaches.length, 1);
    assert.match(lastReason, /limit is 3/);
  });

  it('a warn breach records but never terminates', async () => {
    const evaluator = new LiveEvaluator({ loops: POLICY_WARN }, { loops: { max_occurrences: 3 } });
    let terminations = 0;
    const seen: Breach[] = [];
    const result = await evaluateSpans('i1', identicalCalls(5), evaluator, {
      terminate: () => { terminations += 1; },
      onBreach: (b: Breach) => seen.push(b),
    });
    assert.equal(terminations, 0, 'warn never terminates');
    assert.equal(result.terminated, false);
    // Warn does not stop the run, so every span is seen.
    assert.equal(result.spansSeen, 5);
    assert.equal(seen.length, 1);
    assert.equal(result.breaches.length, 1);
  });

  it('an off check is never even instantiated, so it never fires', async () => {
    const evaluator = new LiveEvaluator({ loops: POLICY_OFF }, { loops: { max_occurrences: 3 } });
    let terminations = 0;
    const result = await evaluateSpans('i1', identicalCalls(10), evaluator, {
      terminate: () => { terminations += 1; },
    });
    assert.equal(result.breaches.length, 0);
    assert.equal(terminations, 0);
    assert.equal(result.terminated, false);
    assert.equal(result.spansSeen, 10);
  });

  it('awaits an async terminate callback', async () => {
    const evaluator = new LiveEvaluator({ loops: POLICY_TERMINATE }, { loops: { max_occurrences: 2 } });
    const order: string[] = [];
    const result = await evaluateSpans('i1', identicalCalls(5), evaluator, {
      terminate: async (reason: string) => {
        order.push('start');
        await Promise.resolve();
        order.push(`done:${reason.includes('limit is 2')}`);
      },
    });
    assert.deepEqual(order, ['start', 'done:true']);
    assert.equal(result.terminated, true);
  });
});

describe('the detector registry', () => {
  it('exposes the five live checks under their config names', () => {
    assert.deepEqual(
      Object.keys(DETECTORS).sort(),
      ['errors', 'latency', 'loops', 'redundant_calls', 'runaway'],
    );
  });

  it('rejects an unknown policy value, naming the check', () => {
    assert.throws(
      () => new LiveEvaluator({ loops: 'nope' }),
      /policy for "loops" must be one of/,
    );
  });
});
