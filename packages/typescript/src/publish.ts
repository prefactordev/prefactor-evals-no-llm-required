/**
 * Publish eval scores into Prefactor's Quality tab.
 *
 * The payload shaping here is pure and has no idea Prefactor exists. The actual
 * HTTP PUT lives in source.ts, which is the only file allowed to know. A
 * publisher object is passed in, so this module and its tests never touch the
 * network.
 *
 * Publishing is always opt in. The runner and a plain CLI run never call it.
 *
 * This is the line for line port of the Python publish.py.
 */

import { FAIL, PASS, SKIP, type EvalResult, type Status } from './result.js';
import type { RunReport } from './runner.js';
import type { EvalInstance } from './schema.js';
import { pyD } from './fmt.js';
import { comparePython } from './fmt.js';
import { CALM_VALUES, qualitySpans, scorePct } from './recordedQuality.js';

// States where a score is meaningful. A run still in flight has an incomplete
// span set, so scoring it would publish a verdict that changes on the next poll.
export const PUBLISHABLE_STATES = ['complete', 'failed', 'cancelled', 'terminated'] as const;

/** One word for the whole instance. Any failure dominates. An instance that
 * only skipped is reported as skip, never as a pass, matching the rule that a
 * skip is never a pass. */
export function overallResult(passCount: number, failCount: number, skipCount: number): string {
  if (failCount) return FAIL;
  if (passCount) return PASS;
  return SKIP;
}

// The canonical quality schema an agent declares once, at register time, to get
// formatted eval summaries in its Prefactor Quality tab. The template is a plain
// passthrough of the `report` field this tool emits: the whole markdown report
// is built here, in code we can test, rather than in Liquid, whose for-loops
// cannot reach nested fields. Rendering binds to the schema version at
// registration, so an agent that wants formatted summaries must include this in
// its own registration. An external eval tool can only PUT the payload; it
// cannot make an already registered instance render.
export const QUALITY_TEMPLATE = '{{ report }}';

export const QUALITY_SCHEMA = {
  title: 'Agent evals',
  description: 'Eval results for this run: what the run recorded about '
    + 'itself, and what the deterministic checks measured.',
  schema: {
    type: 'object',
    properties: {
      report: { type: 'string', title: 'Summary' },
      result: {
        type: 'string', title: 'Check result',
        enum: ['pass', 'fail', 'skip'],
      },
      checks_passed: {
        type: 'integer', title: 'Checks passed', minimum: 0,
      },
      checks_failed: {
        type: 'integer', title: 'Checks failed', minimum: 0,
      },
      checks_not_run: {
        type: 'integer', title: 'Checks not run', minimum: 0,
      },
      // What the agent's own evals recorded, lifted out of its spans so the
      // dashboard reflects the evals the team already runs.
      recorded_verdict: { type: 'string', title: 'Recorded verdict' },
      recorded_scores: { type: 'object', title: 'Recorded scores' },
      signals: { type: 'integer', title: 'Quality signals', minimum: 0 },
      signals_flagged: {
        type: 'integer', title: 'Signals of concern', minimum: 0,
      },
      worst_signal: { type: 'string', title: 'Worst signal' },
      pack: { type: 'string', title: 'Pack' },
      scored_while_running: { type: 'boolean', title: 'Scored mid run' },
    },
    required: ['report'],
  },
  template: QUALITY_TEMPLATE,
};

/** Lift the agent's own eval results out of its spans.
 *
 * The evals a team already runs are written into spans by their own
 * instrumentation. Nothing carries them to the instance, which is why the
 * Quality tab stays empty even though the results exist. This reads them so they
 * can be published where the dashboard looks. */
export function summariseRecordedQuality(instance: EvalInstance): Record<string, unknown> {
  const [judgements, signals] = qualitySpans(instance);
  const out: Record<string, unknown> = {};

  for (const [, payload] of judgements) {
    for (const [key, value] of Object.entries(payload)) {
      // Code points, not UTF-16 units, matching Python's len(): a verdict with
      // an astral character must clear the gate in both languages or neither.
      if (typeof value === 'string' && value.trim() && [...value].length < 40) {
        if (!('recorded_verdict' in out)) out['recorded_verdict'] = value;
      } else if (value !== null && typeof value === 'object' && !Array.isArray(value)
          && Object.keys(value as Record<string, unknown>).length > 0
          && Object.values(value as Record<string, unknown>).every((v) => scorePct(v) !== null)) {
        if (!('recorded_scores' in out)) out['recorded_scores'] = { ...(value as Record<string, unknown>) };
      }
    }
  }

  if (signals.length) {
    out['signals'] = signals.length;
    const flagged: Record<string, unknown>[] = [];
    let worst: unknown = null;
    let worstScore = -1.0;
    for (const [, payload] of signals) {
      const label = payload['signal'];
      let concerning = Object.entries(payload).some(
        ([k, v]) => typeof v === 'boolean' && v && k.toLowerCase() === 'struggling');
      if (typeof label === 'string' && !CALM_VALUES.has(label.trim().toLowerCase())) {
        concerning = true;
      }
      if (concerning) {
        flagged.push(payload);
        const score = scorePct(payload['score']) || 0.0;
        if (score > worstScore) {
          worstScore = score;
          worst = label;
        }
      }
    }
    out['signals_flagged'] = flagged.length;
    if (worst) out['worst_signal'] = String(worst);
  }
  return out;
}

/** core.latency_budget to "latency budget", for human readable summaries. */
function pretty(evalId: string): string {
  const tail = evalId.includes('.') ? evalId.slice(evalId.indexOf('.') + 1) : evalId;
  return tail.replace(/_/g, ' ');
}

const STATUS_LABEL: Record<Status, string> = { [PASS]: 'Pass', [FAIL]: 'Fail', [SKIP]: 'Not run' };
// Failures first, then passes, then not run, in the per check breakdown.
const STATUS_ORDER: Record<Status, number> = { [FAIL]: 0, [PASS]: 1, [SKIP]: 2 };

/** Collapse to a single line. The summary field is rendered as plain text with
 * whitespace collapsed, so embedded newlines would silently run together anyway.
 * Doing it here keeps the stored value honest about how it renders. */
function line(text: string): string {
  return (text || '').split(/\s+/).filter((piece) => piece.length > 0).join(' ');
}

interface Row {
  name: string;
  status: Status;
  detail: string;
  where: string;
}

/** The one line summary the Quality tab renders.
 *
 * This field is HTML-escaped plain text inserted into HTML: markdown renders as
 * literal characters, HTML tags render as literal characters, and newlines
 * collapse to spaces. So structure has to come from sentence order and
 * punctuation, and every fragment closes its own sentence. */
function reportLine(
  resultWord: string, counts: Record<Status, number>, ran: number, rows: Row[],
  pack: string, scoredWhileRunning: boolean,
  recorded: Record<string, unknown> = {},
): string {
  const bits: string[] = [];
  if (recorded['recorded_verdict']) {
    const scores = (recorded['recorded_scores'] as Record<string, unknown> | undefined) || {};
    const detail = Object.entries(scores)
      .sort((a, b) => comparePython(a[0], b[0]))
      .map(([k, v]) => `${k} ${String(v)}`)
      .join(', ');
    bits.push(`Agent's own eval: ${recorded['recorded_verdict']}${detail ? ` (${detail})` : ''}.`);
  }
  if (recorded['signals']) {
    const flagged = (recorded['signals_flagged'] as number) || 0;
    let text = `${pyD(recorded['signals'] as number)} quality signals recorded`;
    if (flagged) {
      text += `, ${pyD(flagged)} of concern`;
      if (recorded['worst_signal']) {
        text += ` (worst: ${recorded['worst_signal']})`;
      }
    }
    bits.push(text + '.');
  }
  bits.push(`${pack} pack: ${resultWord.toUpperCase()}.`);
  let stat = `${pyD(counts[PASS])} of ${pyD(ran)} checks passed`;
  if (counts[FAIL]) {
    stat += `, ${pyD(counts[FAIL])} failed`;
  }
  bits.push(stat + '.');

  const failures = rows.filter((r) => r.status === FAIL)
    .sort((a, b) => comparePython(a.name, b.name));
  for (const failure of failures) {
    const detail = line(failure.detail).replace(/\.+$/, '');
    const where = failure.where ? ` (at ${failure.where})` : '';
    bits.push(`FAILED ${pretty(failure.name)}: ${detail}${where}.`);
  }
  if (counts[SKIP]) {
    bits.push(`${pyD(counts[SKIP])} checks not run, they need config.`);
  }
  if (scoredWhileRunning) {
    bits.push('Scored mid run, this instance had not finished.');
  }
  return bits.join(' ');
}

/** The score block for one instance, shaped to read as a report.
 *
 * Deterministic unless a timestamp is passed, so tests can assert on it. */
export function buildBlock(
  instanceId: string, report: RunReport, toolVersion: string,
  runTimestamp?: string | null,
): Record<string, unknown> {
  const results = report.results.filter((r) => r.instance_id === instanceId);
  const counts: Record<Status, number> = { [PASS]: 0, [FAIL]: 0, [SKIP]: 0 };
  const failures: { check: string; detail: string; span_ids: string[] }[] = [];
  const rows: Row[] = [];
  for (const result of results) {
    counts[result.status] = (counts[result.status] || 0) + 1;
    const spanIds = result.evidence?.span_ids ?? [];
    // Where a failure happened: the offending spans. One is enough for the
    // table, the full list is in the JSON report.
    let where = '';
    if (result.status === FAIL && spanIds.length) {
      where = `span ${spanIds[0]}`;
      if (spanIds.length > 1) {
        where += ` +${pyD(spanIds.length - 1)} more`;
      }
    }
    let detail = result.details;
    if (result.status === SKIP) {
      // For a not run check, the useful "detail" is the config it needs.
      detail = result.remedy || result.details;
    }
    rows.push({ name: result.eval_id, status: result.status, detail, where });
    if (result.status === FAIL) {
      failures.push({ check: result.eval_id, detail: result.details, span_ids: spanIds });
    }
  }
  failures.sort((a, b) => comparePython(a.check, b.check));

  const instance = report.instances.find((i) => i.id === instanceId) ?? null;
  const state = instance ? instance.state : null;
  const scoredWhileRunning = !(PUBLISHABLE_STATES as readonly string[]).includes(state as string);

  const ran = counts[PASS] + counts[FAIL];
  const resultWord = overallResult(counts[PASS], counts[FAIL], counts[SKIP]);

  // The per check breakdown, failures first. This is what the tab shows as
  // formatted JSON under Raw quality payload, and it is the closest thing to a
  // table the current UI can render.
  const checks = [...rows]
    .sort((a, b) => {
      const order = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
      if (order !== 0) return order;
      return comparePython(a.name, b.name);
    })
    .map((row) => ({
      check: pretty(row.name),
      result: STATUS_LABEL[row.status],
      detail: line(row.detail),
      where: row.where,
    }));

  // The agent's own eval results, lifted from its spans, so the dashboard
  // reflects the evals the team already runs and not only these checks.
  const recorded = instance ? summariseRecordedQuality(instance) : {};

  const block: Record<string, unknown> = {
    // Rendered as quality_summary when the agent has declared QUALITY_SCHEMA.
    report: reportLine(resultWord, counts, ran, rows, report.pack.pack, scoredWhileRunning),
    result: resultWord,
    checks_passed: counts[PASS],
    checks_failed: counts[FAIL],
    checks_not_run: counts[SKIP],
    checks,
    pack: report.pack.pack,
    scored_while_running: scoredWhileRunning,
    instance_state: state,
    tool_version: toolVersion,
  };
  Object.assign(block, recorded);
  block['report'] = reportLine(resultWord, counts, ran, rows, report.pack.pack,
    scoredWhileRunning, recorded);
  if (runTimestamp) block['evaluated_at'] = runTimestamp;
  return block;
}

export class PublishOutcome {
  readonly instance_id: string;
  // "published", "skipped", or "error".
  readonly status: string;
  readonly detail: string;
  readonly block: Record<string, unknown> | null;

  constructor(instanceId: string, status: string, detail: string,
    block: Record<string, unknown> | null = null) {
    this.instance_id = instanceId;
    this.status = status;
    this.detail = detail;
    this.block = block;
  }
}

interface Publisher {
  publish(instanceId: string, block: Record<string, unknown>): Promise<Record<string, unknown>>;
}

/** Push a score block for every publishable instance.
 *
 * A failure on one instance never stops the rest. Each instance gets an outcome,
 * so the caller can report exactly what landed and what did not. */
export async function publishReport(
  report: RunReport, publisher: Publisher, toolVersion: string,
  runTimestamp?: string | null, includeActive = false,
): Promise<PublishOutcome[]> {
  const outcomes: PublishOutcome[] = [];
  for (const instance of report.instances) {
    if (!(PUBLISHABLE_STATES as readonly string[]).includes(instance.state as string) && !includeActive) {
      outcomes.push(new PublishOutcome(
        instance.id, 'skipped',
        `state is ${instance.state}, not scorable until the run finishes `
        + '(use include_active to score anyway)',
      ));
      continue;
    }
    const block = buildBlock(instance.id, report, toolVersion, runTimestamp);
    try {
      const written = await publisher.publish(instance.id, block);
      outcomes.push(new PublishOutcome(
        instance.id, 'published',
        `result ${block['result']}, ${block['checks_passed']} pass, `
        + `${block['checks_failed']} fail, ${block['checks_not_run']} skip`,
        written,
      ));
    } catch (error) {
      const name = error instanceof Error ? error.constructor.name : 'Error';
      const message = error instanceof Error ? error.message : String(error);
      outcomes.push(new PublishOutcome(instance.id, 'error', `${name}: ${message}`));
    }
  }
  return outcomes;
}

export function summarize(outcomes: PublishOutcome[]): string {
  const published = outcomes.filter((o) => o.status === 'published').length;
  const skipped = outcomes.filter((o) => o.status === 'skipped').length;
  const errored = outcomes.filter((o) => o.status === 'error').length;
  const lines = [`Published scores to Prefactor Quality tab: ${published} instances.`];
  if (skipped) {
    lines.push(`  Skipped ${skipped} (unfinished runs).`);
  }
  for (const outcome of outcomes) {
    if (outcome.status === 'error') {
      lines.push(`  Error on ${outcome.instance_id.slice(0, 14)}: ${outcome.detail}`);
    }
  }
  if (errored) {
    lines.push(`  ${errored} instances failed to publish, see above.`);
  }
  return lines.join('\n');
}
