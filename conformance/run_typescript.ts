/**
 * Emit the TypeScript implementation's results for the conformance fixtures.
 *
 * Same shape as run_python.py. Deliberately unclever: any normalization here
 * would risk hiding a real difference between the implementations.
 *
 * Imports the built output, so this also proves the package compiles and its
 * public entry points work, not just that the sources type check.
 */

import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadInstances } from '../packages/typescript/dist/fixtures.js';
import { run, STANDARD_PACK } from '../packages/typescript/dist/runner.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = dirname(HERE);
const FIXTURE = join(REPO, 'examples', 'synthetic-traces', 'standard.json');

const instances = loadInstances(FIXTURE, STANDARD_PACK.span_type_map);
const report = run(STANDARD_PACK, instances);

const [ran, requested] = report.coverage;
const [finished, total, rate] = report.resolution;

process.stdout.write(JSON.stringify({
  results: report.results,
  counts: report.counts,
  coverage: { ran, requested },
  resolution: { finished, total, rate },
}));
