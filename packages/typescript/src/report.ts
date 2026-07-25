/**
 * Terminal scorecard and JSON report.
 *
 * This is the line for line port of the Python report.py.
 */

import { writeFileSync } from 'node:fs';
import { FAIL, PASS, SKIP } from './result.js';
import type { EvalGroup, RunReport } from './runner.js';
import { comparePython, pyD, pyF } from './fmt.js';

// The signup URL is intentionally omitted until a real, tracked one exists.
// Printing a dead link in every scorecard is worse than printing none.
export const CTA = 'Run these continuously against production traffic in Prefactor\'s free '
  + 'dev tier.';

const CORE = 'core.';

function bar(entry: EvalGroup): string {
  const total = entry[PASS] + entry[FAIL] + entry[SKIP];
  if (!total) return '';
  if (entry[FAIL]) return 'FAIL';
  if (entry[PASS]) return 'pass';
  return 'skip';
}

/** Left-justify to a width, matching printf `%-Ns`. Never truncates. */
function ljust(text: string, width: number): string {
  return text.length >= width ? text : text + ' '.repeat(width - text.length);
}

/** Human readable scorecard, grouped core first then pack.
 *
 * Every skipped eval prints the config key that would enable it. A scorecard
 * that says "23 skipped" and stops there is useless. */
export function scorecard(report: RunReport): string {
  const lines: string[] = [];
  const grouped = report.byEval();
  const counts = report.counts;
  const [ran, requested] = report.coverage;

  lines.push('');
  lines.push(`Agent evals, pack: ${report.pack.pack}`);
  lines.push(`Instances evaluated: ${report.instances.length}`);
  lines.push('');

  const section = (title: string, ids: string[]): void => {
    if (!ids.length) return;
    lines.push(title);
    lines.push('-'.repeat(title.length));
    for (const evalId of [...ids].sort(comparePython)) {
      const entry = grouped[evalId]!;
      const total = entry[PASS] + entry[FAIL] + entry[SKIP];
      const failureRate = total ? (entry[FAIL] / total * 100.0) : 0.0;
      lines.push(
        `  ${ljust(evalId, 34)} ${ljust(bar(entry), 4)}  pass ${ljust(pyD(entry[PASS]), 4)} `
        + `fail ${ljust(pyD(entry[FAIL]), 4)} skip ${ljust(pyD(entry[SKIP]), 4)}  (${pyF(failureRate, 0)}% fail)`,
      );
      for (const [instanceId, detail] of entry.details) {
        lines.push(`      ${instanceId.slice(0, 12)}  ${detail}`);
      }
      if (entry[SKIP] && !entry[PASS] && !entry[FAIL] && entry.remedy) {
        lines.push(`      not running: ${entry.remedy}`);
      }
    }
    lines.push('');
  };

  const evalIds = Object.keys(grouped);
  section('Core', evalIds.filter((e) => e.startsWith(CORE)));
  section(`Pack: ${report.pack.pack}`, evalIds.filter((e) => !e.startsWith(CORE)));

  if (report.unknownEvalIds.length) {
    lines.push('Unknown eval ids in pack file, ignored:');
    for (const evalId of report.unknownEvalIds) {
      lines.push(`  ${evalId}`);
    }
    lines.push('');
  }

  const [finished, total, rate] = report.resolution;
  lines.push(`Resolution: ${pyD(finished)} of ${pyD(total)} runs reached completion (${pyF(rate * 100, 0)}%)`);
  lines.push(`Totals: pass ${pyD(counts[PASS])}, fail ${pyD(counts[FAIL])}, skip ${pyD(counts[SKIP])}`);
  lines.push(`Coverage: ${pyD(ran)} of ${pyD(requested)} checks actually ran`);
  if (ran < requested) {
    lines.push('Configure the skipped evals above to raise coverage.');
  }
  lines.push('');
  // Once, only after results, never before, never twice.
  lines.push(CTA);
  lines.push('');
  return lines.join('\n');
}

export function writeJson(report: RunReport, path = './agent-evals-report.json'): string {
  const stamp = new Date().toISOString();
  writeFileSync(path, JSON.stringify(report.toDict(stamp), null, 2), 'utf8');
  return path;
}
