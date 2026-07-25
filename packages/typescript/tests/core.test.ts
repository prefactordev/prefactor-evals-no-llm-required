/**
 * Tests for the primitives the whole library rests on.
 *
 * They run against dist/, the built output, so they exercise the artefact a
 * consumer actually gets rather than the sources.
 *
 * These deliberately do not restate what the conformance suite already proves.
 * Conformance checks that both languages agree on real fixtures; these pin the
 * specific places where JavaScript would drift from Python if someone
 * "simplified" the code later, and the safety rules that have no fixture.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import {
  asciiLower, canonical, compilePatterns, digest, patternProblem, readPath,
  schemaProblem, UnsafePattern,
} from '../dist/helpers.js';
import {
  cfgBool, cfgFloat, cfgInt, cfgList, cfgStrSet, ConfigError,
} from '../dist/config.js';
import { Baseline, buildBaseline, median } from '../dist/baseline.js';
import { resolveSpanName, resolveSpanType, sortSpans } from '../dist/schema.js';
import type { EvalInstance, EvalSpan } from '../dist/schema.js';

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

function instance(over: Partial<EvalInstance> = {}): EvalInstance {
  return {
    id: 'i1', agentId: 'a1', agentVersion: null, environment: null,
    startedAt: null, endedAt: null, state: 'complete', durationMs: null,
    spans: [], input: null, output: null, cost: null, metadata: {}, ...over,
  };
}

describe('canonical serialization, where the languages would drift', () => {
  it('sorts object keys so equal content hashes equally', () => {
    assert.equal(canonical({ b: 1, a: 2 }), canonical({ a: 2, b: 1 }));
    assert.equal(canonical({ b: 1, a: 2 }), '{"a":2,"b":1}');
  });

  it('writes undefined as null instead of dropping the key', () => {
    // JSON.stringify drops the key entirely, Python writes null. A dropped key
    // changes the hash and would break signature matching across languages.
    assert.equal(canonical({ a: undefined }), '{"a":null}');
  });

  it('treats an integral float as an integer', () => {
    // Python folds 1.0 to 1 so it hashes the same as an int.
    assert.equal(canonical(1.0), '1');
    assert.equal(canonical({ n: 5.0 }), canonical({ n: 5 }));
  });

  it('sorts keys by code point, not UTF-16 code unit', () => {
    // A plain `<` comparison orders the astral character first, which is where
    // JS and Python disagree. U+FFFD must sort before U+1F600.
    const out = canonical({ '\u{1F600}': 1, '�': 2 });
    assert.ok(
      out.indexOf('�') < out.indexOf('\u{1F600}'),
      `code point ordering broken: ${out}`,
    );
  });

  it('is stable, so digests are reproducible', () => {
    const value = { z: [3, 2, 1], a: { deep: true } };
    assert.equal(digest(value), digest({ a: { deep: true }, z: [3, 2, 1] }));
    assert.ok(digest(value).startsWith('sha256:'));
  });
});

describe('field paths', () => {
  it('reads keys, indexes and spreads', () => {
    const root = { a: { b: [{ c: 1 }, { c: 2 }] } };
    assert.deepEqual(readPath(root, 'a.b[0].c'), [1]);
    assert.deepEqual(readPath(root, 'a.b[].c'), [1, 2]);
  });

  it('yields nothing for a path that does not resolve', () => {
    // Distinct from resolving to null: the check can tell "absent" from "null".
    assert.deepEqual(readPath({ a: 1 }, 'b.c'), []);
    assert.deepEqual(readPath({ a: null }, 'a'), [null]);
  });
});

describe('timestamps parse the way Python parses them', () => {
  it('reads a naive ISO timestamp as UTC, never local time', async () => {
    // Python attaches UTC to a naive fromisoformat result. JavaScript's Date
    // reads a no-offset date-time as LOCAL time, which would shift every
    // filter and duration by the machine's zone. Pinned here because it is
    // invisible on a UTC machine like CI.
    const { parseTimestamp } = await import('../dist/schema.js');
    assert.equal(parseTimestamp('2026-07-01T12:00:00')!.toISOString(),
      '2026-07-01T12:00:00.000Z');
    assert.equal(parseTimestamp('2026-07-01 12:00:00')!.toISOString(),
      '2026-07-01T12:00:00.000Z');
    assert.equal(parseTimestamp('2026-07-01')!.toISOString(),
      '2026-07-01T00:00:00.000Z');
    assert.equal(parseTimestamp('2026-07-01T12:00:00+02:00')!.toISOString(),
      '2026-07-01T10:00:00.000Z');
  });

  it('rejects everything Python rejects instead of guessing', async () => {
    // new Date('5') is April 2001 and new Date('July 1 2026') parses; Python's
    // fromisoformat rejects both. A typo like --since 5 must mean "no filter"
    // in both languages, not a filter in one.
    const { parseTimestamp } = await import('../dist/schema.js');
    for (const junk of ['5', '7', 'July 1 2026', 'yesterday', '2026', '2026-07']) {
      assert.equal(parseTimestamp(junk), null, `"${junk}" should not parse`);
    }
  });
});

describe('span typing', () => {
  it('maps the named tool conventions of both SDKs', () => {
    assert.equal(resolveSpanType('ai-sdk:tool:issue_refund'), 'tool_call');
    assert.equal(resolveSpanType('langchain:tool:deposit_btc'), 'tool_call');
    assert.equal(resolveSpanName('ai-sdk:tool:issue_refund'), 'issue_refund');
  });

  it('never guesses by substring', () => {
    // "contains tool" would mistype this, and a silently mistyped span produces
    // a silently wrong result.
    assert.equal(resolveSpanType('protocol:sync'), 'other');
  });

  it('lets config win over the built ins', () => {
    assert.equal(resolveSpanType('x:custom', { 'x:custom': 'handoff' }), 'handoff');
  });

  it('orders spans by start time then id, putting undated ones last', () => {
    const a = span({ id: 'b', startedAt: new Date('2026-07-01T00:00:02Z') });
    const b = span({ id: 'a', startedAt: new Date('2026-07-01T00:00:02Z') });
    const c = span({ id: 'c', startedAt: null });
    assert.deepEqual(sortSpans([c, a, b]).map((s) => s.id), ['a', 'b', 'c']);
  });
});

describe('config is treated as hostile', () => {
  it('rejects a bare string where a list belongs', () => {
    // Iterating a string yields its characters, which matches nothing and
    // reports a pass on a check that measured nothing.
    assert.throws(() => cfgList({ forbidden: 'issue_refund' }, 'forbidden'), ConfigError);
    assert.throws(() => cfgStrSet({ tools: 'a' }, 'tools'), ConfigError);
  });

  it('names the key and the expected type', () => {
    try {
      cfgInt({ max_spans: 'lots' }, 'max_spans');
      assert.fail('should have thrown');
    } catch (error) {
      assert.ok(error instanceof ConfigError);
      assert.match(error.message, /max_spans/);
      assert.match(error.message, /whole number/);
      assert.match(error.remedy, /max_spans/);
    }
  });

  it('accepts a quoted number but never a boolean', () => {
    assert.equal(cfgInt({ n: '5' }, 'n'), 5);
    assert.equal(cfgFloat({ n: '2.5' }, 'n'), 2.5);
    // Python would silently read True as 1. Guessing intent from a wrong type
    // is how a check ends up measuring something nobody asked for.
    assert.throws(() => cfgInt({ n: true }, 'n'), ConfigError);
    assert.throws(() => cfgBool({ b: 1 }, 'b'), ConfigError);
  });

  it('falls back when the key is absent', () => {
    assert.equal(cfgInt({}, 'missing', 7), 7);
    assert.deepEqual(cfgList({}, 'missing'), []);
  });
});

describe('patterns that could hang a run are refused', () => {
  it('refuses nested quantifiers', () => {
    for (const bad of ['(a+)+$', '(a*)*$', '(a|a)*$', '(x+x+)+y']) {
      assert.ok(patternProblem(bad), `${bad} should be refused`);
      assert.throws(() => compilePatterns([bad]), UnsafePattern);
    }
  });

  it('still allows ordinary rules', () => {
    for (const ok of ['^admin:', '\\bcannot lose\\b', '[a-z]+@[a-z]+\\.[a-z]{2,}']) {
      assert.equal(patternProblem(ok), null, `${ok} should be allowed`);
    }
    assert.equal(compilePatterns(['^admin:']).length, 1);
  });
});

describe('schemas that would reach the network are refused', () => {
  it('refuses a remote $ref', () => {
    const problem = schemaProblem({ $ref: 'https://example.invalid/s.json' });
    assert.ok(problem && problem.includes('$ref'));
  });

  it('refuses one nested deep inside', () => {
    const problem = schemaProblem({
      type: 'object',
      properties: { a: { $ref: 'http://169.254.169.254/latest/meta-data/' } },
    });
    assert.ok(problem && problem.includes('$ref'));
  });

  it('refuses a local reference, which it does not resolve', () => {
    // The bundled validator resolves no references at all, so honouring a local
    // $ref would validate against less than the schema says. Refused, not
    // silently ignored.
    const problem = schemaProblem({ $ref: '#/definitions/a' });
    assert.ok(problem && problem.includes('$ref'));
  });
});

describe('schemas outside the supported subset are refused by keyword', () => {
  it('refuses an unsupported assertion keyword by name', () => {
    const problem = schemaProblem({ anyOf: [{ type: 'string' }, { type: 'object' }] });
    assert.ok(problem && problem.includes('anyOf'));
  });

  it('refuses items as a tuple', () => {
    const problem = schemaProblem({ type: 'array', items: [{ type: 'string' }] });
    assert.ok(problem && problem.includes('tuple'));
  });

  it('refuses an unsupported keyword nested in properties', () => {
    const problem = schemaProblem({
      type: 'object',
      properties: { a: { not: { type: 'string' } } },
    });
    assert.ok(problem && problem.includes('not'));
  });

  it('allows an ordinary object schema', () => {
    assert.equal(schemaProblem({
      type: 'object',
      properties: { id: { type: 'string' }, n: { type: 'integer', minimum: 0 } },
      required: ['id'],
      additionalProperties: false,
    }), null);
  });

  it('refuses a Python only pattern spelling, which would throw here', () => {
    // (?i) compiles in Python and throws in JavaScript; a named group likewise
    // means different things. Either would split the verdict between the two
    // languages, so the portable subset is enforced on schema patterns.
    for (const bad of ['(?i)yes', '(?P<x>a)']) {
      const problem = schemaProblem({ type: 'string', pattern: bad });
      assert.ok(problem && problem.includes('pattern'), `${bad} should be refused`);
    }
    // An ordinary anchored pattern is fine.
    assert.equal(schemaProblem({ type: 'string', pattern: '^[a-z ]+$' }), null);
  });
});

describe('self calibrating baselines', () => {
  it('takes the median the same way Python does', () => {
    assert.equal(median([1, 2, 3]), 2);
    assert.equal(median([1, 2, 3, 4]), 2.5);
    assert.equal(median([]), null);
  });

  it('is not ready below the minimum sample', () => {
    assert.equal(new Baseline([5, 5]).ready, false);
    assert.equal(new Baseline([5, 5, 5, 5, 5]).ready, true);
  });

  it('builds only from completed runs', () => {
    // A run that broke took the steps it did because it broke; counting it
    // would inflate what normal means.
    const batch = [
      ...Array.from({ length: 5 }, () => instance({ spans: [span(), span()] })),
      instance({ state: 'failed', spans: Array.from({ length: 80 }, () => span()) }),
    ];
    const baseline = buildBaseline(batch, (i) => i.spans.length);
    assert.equal(baseline.median, 2);
    assert.equal(baseline.sampleSize, 5);
  });

  it('never flags a run below the floor', () => {
    const baseline = new Baseline([1, 1, 1, 1, 1], 3, 12);
    assert.equal(baseline.exceeds(5), false, 'three times a median of one is still only three');
    assert.equal(baseline.exceeds(13), true);
  });
});

describe('bundled packs resolve by name', () => {
  it('loads standard and advanced without a file', async () => {
    const { loadPack, STANDARD_PACK, ADVANCED_PACK } = await import('../dist/runner.js');
    assert.equal(loadPack('standard'), STANDARD_PACK);
    assert.equal(loadPack('advanced'), ADVANCED_PACK);
    assert.equal(Object.keys(ADVANCED_PACK.evals).length, 12);
    // The advanced pack is the standard pack plus the five optional checks.
    for (const id of Object.keys(STANDARD_PACK.evals)) {
      assert.deepEqual(ADVANCED_PACK.evals[id], STANDARD_PACK.evals[id]);
    }
  });

  it('still errors on a path that is not a bundled name', async () => {
    const { loadPack } = await import('../dist/runner.js');
    assert.throws(() => loadPack('no-such-pack.json'));
  });
});

describe('ascii case folding', () => {
  it('folds ASCII only, so both languages agree', () => {
    assert.equal(asciiLower('ABC'), 'abc');
    // Locale aware lowering differs between the two languages on this.
    assert.equal(asciiLower('İ'), 'İ');
  });
});
