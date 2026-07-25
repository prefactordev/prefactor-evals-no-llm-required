#!/usr/bin/env node
/**
 * Command line entry point.
 *
 * Flag names are identical to the Python package by design. Args are parsed by
 * hand, no dependency, so the zero runtime dependency promise holds.
 *
 * This is the line for line port of the Python cli.py, with one exception: the
 * HTML dashboard (--html / --open) is only in the Python build, so those flags
 * are accepted and ignored here with a note to stderr.
 */

import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

import { runPack, STANDARD_PACK } from './runner.js';
import { scorecard, writeJson } from './report.js';
import { comparePython } from './fmt.js';

// Read from package.json rather than hardcoded, so it cannot drift from the
// released version. Published in the score block's tool_version.
const TOOL_VERSION: string = (createRequire(import.meta.url)('../package.json') as {
  version: string;
}).version;

type FlagType = 'string' | 'int' | 'float' | 'bool';
interface FlagDef {
  type: FlagType;
  default?: string | number | boolean | null;
  required?: boolean;
}
type Parsed = Record<string, string | number | boolean | null>;

class ArgError extends Error {}

/** Parse `--flag value`, `--flag=value`, and store-true flags by hand. Errors
 * read like argparse's and, like it, mean exit code 2. */
function parseFlags(rest: string[], defs: Record<string, FlagDef>): Parsed {
  const result: Parsed = {};
  for (const [key, def] of Object.entries(defs)) {
    result[key] = def.type === 'bool' ? false : (def.default ?? null);
  }
  let i = 0;
  while (i < rest.length) {
    const token = rest[i]!;
    if (!token.startsWith('--')) {
      throw new ArgError(`unrecognized arguments: ${token}`);
    }
    let name = token;
    let inline: string | undefined;
    const eq = token.indexOf('=');
    if (eq !== -1) {
      name = token.slice(0, eq);
      inline = token.slice(eq + 1);
    }
    const key = name.slice(2);
    const def = defs[key];
    if (!def) {
      throw new ArgError(`unrecognized arguments: ${token}`);
    }
    if (def.type === 'bool') {
      result[key] = true;
      i += 1;
      continue;
    }
    let raw: string | undefined;
    if (inline !== undefined) {
      raw = inline;
      i += 1;
    } else {
      raw = rest[i + 1];
      if (raw === undefined) {
        throw new ArgError(`argument --${key}: expected one argument`);
      }
      i += 2;
    }
    if (def.type === 'int') {
      if (!/^-?\d+$/.test(raw.trim())) {
        throw new ArgError(`argument --${key}: invalid int value: '${raw}'`);
      }
      result[key] = Number(raw.trim());
    } else if (def.type === 'float') {
      const value = Number(raw.trim());
      if (raw.trim() === '' || !Number.isFinite(value)) {
        throw new ArgError(`argument --${key}: invalid float value: '${raw}'`);
      }
      result[key] = value;
    } else {
      result[key] = raw;
    }
  }
  for (const [key, def] of Object.entries(defs)) {
    if (def.required && (result[key] === null || result[key] === undefined)) {
      throw new ArgError(`the following arguments are required: --${key}`);
    }
  }
  return result;
}

const RUN_DEFS: Record<string, FlagDef> = {
  pack: { type: 'string', default: null },
  agent: { type: 'string', required: true },
  since: { type: 'string', default: null },
  until: { type: 'string', default: null },
  state: { type: 'string', default: null },
  environment: { type: 'string', default: null },
  purpose: { type: 'string', default: 'live' },
  limit: { type: 'int', default: null },
  report: { type: 'string', default: './agent-evals-report.json' },
  'no-report': { type: 'bool' },
  html: { type: 'string', default: null },
  'no-fail-exit': { type: 'bool' },
  'env-file': { type: 'string', default: null },
  open: { type: 'bool' },
  publish: { type: 'bool' },
  'include-active': { type: 'bool' },
};

const SCHEMA_DEFS: Record<string, FlagDef> = {
  agent: { type: 'string', required: true },
  version: { type: 'string', default: 'agent-evals' },
  'schema-version': { type: 'string', default: 'agent-evals-quality-v1' },
  environment: { type: 'string', default: null },
  print: { type: 'bool' },
};

const WATCH_DEFS: Record<string, FlagDef> = {
  agent: { type: 'string', required: true },
  terminate: { type: 'string', default: '' },
  poll: { type: 'float', default: 2.0 },
  'env-file': { type: 'string', default: null },
  once: { type: 'bool' },
};

const AGENTS_DEFS: Record<string, FlagDef> = {
  'env-file': { type: 'string', default: null },
};

export async function main(argv: string[] = process.argv.slice(2)): Promise<number> {
  const command = argv[0];
  const rest = argv.slice(1);

  if (command === 'declare-quality-schema') {
    return declareQualitySchemaCmd(rest);
  }
  if (command === 'watch') {
    return watchCmd(rest);
  }
  if (command === 'agents') {
    return agentsCmd(rest);
  }
  if (command !== 'run') {
    printHelp();
    return 0;
  }
  return runCmd(rest);
}

/** List the account's agents. The agent id is the one thing setup needs that
 * nobody can type from memory, so the tool fetches it rather than sending
 * people to fish it out of a dashboard URL. */
async function agentsCmd(rest: string[]): Promise<number> {
  let args: Parsed;
  try {
    args = parseFlags(rest, AGENTS_DEFS);
  } catch (error) {
    process.stderr.write(`${(error as Error).message}\n`);
    return 2;
  }

  if (args['env-file']) {
    loadEnvFile(args['env-file'] as string);
  }

  const { PrefactorSource, PrefactorSourceError } = await import('./source.js');

  let agents;
  try {
    agents = await new PrefactorSource().listAgents();
  } catch (error) {
    if (error instanceof PrefactorSourceError) {
      process.stderr.write(`${error.message}\n`);
      return 2;
    }
    throw error;
  }

  if (!agents.length) {
    console.log('No agents on this account yet. Instrument one with a Prefactor '
      + 'SDK and it will appear here.');
    return 0;
  }
  const width = Math.max(...agents.map((a) => a.id.length));
  for (const agent of agents) {
    let line = `${agent.id.padEnd(width)}  ${agent.name}`;
    if (agent.status && agent.status !== 'active') line += `  (${agent.status})`;
    console.log(line);
  }
  console.log('');
  console.log('Run evals on one with: prefactor-evals-no-llm run --agent <id> --since 7d');
  return 0;
}

async function runCmd(rest: string[]): Promise<number> {
  let args: Parsed;
  try {
    args = parseFlags(rest, RUN_DEFS);
  } catch (error) {
    process.stderr.write(`${(error as Error).message}\n`);
    return 2;
  }

  if (args['html'] || args['open']) {
    process.stderr.write('The HTML dashboard is only in the Python build for now; run without --html.\n');
  }

  if (args['env-file']) {
    loadEnvFile(args['env-file'] as string);
  }

  let report;
  try {
    report = await runPack(args['pack'] as string | null, args['agent'] as string, {
      since: args['since'], until: args['until'], state: args['state'] as string | null,
      environment: args['environment'] as string | null,
      purpose: args['purpose'] === 'any' ? null : (args['purpose'] as string),
      limit: args['limit'] as number | null,
    });
  } catch (error) {
    // Same prefixes and exit code as the Python CLI: a pack problem is the
    // user's file, named as such; anything else prints its own message.
    const message = error instanceof Error ? error.message : String(error);
    const prefix = error instanceof Error && error.name === 'PackError' ? 'Pack error: ' : '';
    process.stderr.write(`${prefix}${message}\n`);
    return 2;
  }

  console.log(scorecard(report));
  if (!args['no-report']) {
    const path = writeJson(report, args['report'] as string);
    console.log(`JSON report written to ${path}\n`);
  }

  if (args['publish']) {
    await publishRun(report, Boolean(args['include-active']));
  }

  if (report.failed && !args['no-fail-exit']) {
    return 1;
  }
  return 0;
}

async function watchCmd(rest: string[]): Promise<number> {
  let args: Parsed;
  try {
    args = parseFlags(rest, WATCH_DEFS);
  } catch (error) {
    process.stderr.write(`${(error as Error).message}\n`);
    return 2;
  }

  if (args['env-file']) {
    loadEnvFile(args['env-file'] as string);
  }

  const { LiveEvaluator, POLICY_TERMINATE, POLICY_WARN } = await import('./live/index.js');
  const { DETECTORS } = await import('./live/detectors.js');
  const { evaluateSpans } = await import('./live/watch.js');
  const { PrefactorController, PrefactorSource, PrefactorSourceError } = await import('./source.js');

  const terminateChecks = new Set(
    (args['terminate'] as string).split(',').map((c) => c.trim()).filter((c) => c.length > 0));
  const unknown = [...terminateChecks].filter((c) => !(c in DETECTORS));
  if (unknown.length) {
    process.stderr.write(`Unknown check(s) in --terminate: ${unknown.sort(comparePython).join(', ')}. `
      + `Valid: ${Object.keys(DETECTORS).sort(comparePython).join(', ')}\n`);
    return 2;
  }
  const policy: Record<string, string> = {};
  for (const name of Object.keys(DETECTORS)) {
    policy[name] = terminateChecks.has(name) ? POLICY_TERMINATE : POLICY_WARN;
  }
  // Detector config borrows the standard pack's thresholds where they line up.
  const config = {
    loops: STANDARD_PACK.evals['core.loop_detection']!,
    redundant_calls: STANDARD_PACK.evals['core.redundant_tool_calls']!,
    latency: STANDARD_PACK.evals['core.latency_budget']!,
  };

  let source;
  let controller;
  try {
    source = new PrefactorSource();
    controller = new PrefactorController();
  } catch (error) {
    if (error instanceof PrefactorSourceError) {
      process.stderr.write(`${error.message}\n`);
      return 2;
    }
    throw error;
  }

  if (terminateChecks.size) {
    console.log(`Watching agent ${args['agent']}. Will STOP a run that breaches: `
      + `${[...terminateChecks].sort(comparePython).join(', ')}.`);
  } else {
    console.log(`Watching agent ${args['agent']}. Warn only, nothing will be stopped. Name checks `
      + 'in --terminate to stop breaching runs.');
  }
  console.log('');

  // Per run: its evaluator, and how many spans of it have been seen.
  const watched = new Map<string, InstanceType<typeof LiveEvaluator>>();
  const seenCounts = new Map<string, number>();

  const sweep = async (): Promise<void> => {
    const active = await source!.fetchInstances(args['agent'] as string, {
      purpose: null, state: 'active', limit: 50,
    });
    for (const instance of active) {
      if (!watched.has(instance.id)) {
        watched.set(instance.id, new LiveEvaluator(policy, config));
        seenCounts.set(instance.id, 0);
      }
      const spans = await controller!.liveSpans(instance.id, seenCounts.get(instance.id)!);
      if (!spans.length) continue;
      seenCounts.set(instance.id, seenCounts.get(instance.id)! + spans.length);

      const onBreach = (breach: InstanceType<typeof import('./live/detectors.js').Breach>): void => {
        const mark = breach.terminate ? 'STOP' : 'warn';
        console.log(`  [${mark}] ${breach.check} on ${instance.id.slice(0, 14)}: ${breach.reason}`);
      };

      const result = await evaluateSpans(instance.id, spans, watched.get(instance.id)!, {
        terminate: (reason: string) => controller!.terminate(instance.id, reason),
        onBreach,
      });
      if (result.terminated) {
        console.log(`  terminated ${instance.id}`);
      }
    }
  };

  try {
    if (args['once']) {
      await sweep();
    } else {
      let stop = false;
      let wake: (() => void) | null = null;
      const onSigint = (): void => {
        console.log('\nStopped watching.');
        stop = true;
        if (wake) wake();
      };
      process.on('SIGINT', onSigint);
      try {
        while (!stop) {
          await sweep();
          if (stop) break;
          const poll = args['poll'] as number;
          await new Promise<void>((resolve) => {
            // The timer is cleared on wake, or Ctrl-C would print "Stopped
            // watching." and then keep the process alive until the next poll
            // tick fired, minutes later on a long --poll.
            const timer = setTimeout(resolve, Math.max(0.2, poll) * 1000);
            wake = () => {
              clearTimeout(timer);
              resolve();
            };
          });
        }
      } finally {
        process.removeListener('SIGINT', onSigint);
      }
    }
  } catch (error) {
    if (error instanceof PrefactorSourceError) {
      process.stderr.write(`${error.message}\n`);
      return 2;
    }
    throw error;
  }
  return 0;
}

async function declareQualitySchemaCmd(rest: string[]): Promise<number> {
  let args: Parsed;
  try {
    args = parseFlags(rest, SCHEMA_DEFS);
  } catch (error) {
    process.stderr.write(`${(error as Error).message}\n`);
    return 2;
  }

  const { QUALITY_SCHEMA } = await import('./publish.js');

  if (args['print']) {
    console.log(JSON.stringify(QUALITY_SCHEMA, null, 2));
    return 0;
  }

  const { PrefactorSourceError, declareQualitySchema } = await import('./source.js');

  let result;
  try {
    result = await declareQualitySchema(args['agent'] as string, {
      version: args['version'] as string,
      schemaVersion: args['schema-version'] as string,
      environmentId: args['environment'] as string | null,
    });
  } catch (error) {
    if (error instanceof PrefactorSourceError) {
      process.stderr.write(`${error.message}\n`);
      return 2;
    }
    throw error;
  }

  console.log(`Quality schema declared on agent ${args['agent']}`);
  console.log(`  agent version:  ${args['version']}`);
  console.log(`  schema version: ${args['schema-version']}`);
  console.log(`  probe instance: ${result.instance_id}`);
  console.log('');
  console.log('Runs registered from now on will render published eval results in their');
  console.log('Quality tab. Existing runs cannot be changed: rendering binds at');
  console.log('registration. To make this permanent, add the same quality_schema to your');
  console.log("agent's own registration (see --print).");
  return 0;
}

/** Read PREFACTOR_* settings from a file into process.env.
 *
 * Accepts both `KEY=value` and `KEY: value`, because real env files in the wild
 * carry a mix. Values already set in the environment win, so an explicit export
 * is never silently overridden. Nothing from the file is printed: these are
 * credentials. */
function loadEnvFile(path: string): void {
  if (!existsSync(path)) {
    process.stderr.write(`No env file at ${path}, using the environment as is.\n`);
    return;
  }
  const pattern = /^([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)$/;
  let loaded = 0;
  const text = readFileSync(path, 'utf8');
  for (let line of text.split(/\r?\n/)) {
    line = line.trim();
    if (!line || line.startsWith('#')) continue;
    const match = pattern.exec(line);
    if (!match) continue;
    const key = match[1]!;
    let value = match[2]!.trim();
    value = pystrip(value, '"');
    value = pystrip(value, "'");
    if (key.startsWith('PREFACTOR_') && !process.env[key]) {
      process.env[key] = value;
      loaded += 1;
    }
  }
  console.log(`Loaded ${loaded} setting${loaded === 1 ? '' : 's'} from ${path}`);
}

/** Strip all leading and trailing occurrences of a character, like Python
 * str.strip(char). */
function pystrip(text: string, char: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && text[start] === char) start += 1;
  while (end > start && text[end - 1] === char) end -= 1;
  return text.slice(start, end);
}

async function publishRun(report: Awaited<ReturnType<typeof runPack>>, includeActive: boolean): Promise<void> {
  const { PrefactorPublisher, PrefactorSourceError } = await import('./source.js');
  const { publishReport, summarize } = await import('./publish.js');

  let publisher;
  try {
    publisher = new PrefactorPublisher();
  } catch (error) {
    if (error instanceof PrefactorSourceError) {
      process.stderr.write(`Publish skipped: ${error.message}\n`);
      return;
    }
    throw error;
  }
  const stamp = new Date().toISOString();
  const outcomes = await publishReport(report, publisher, TOOL_VERSION, stamp, includeActive);
  console.log(summarize(outcomes));
}

function printHelp(): void {
  console.log('usage: prefactor-evals-no-llm [-h] {run,agents,declare-quality-schema,watch} ...');
  console.log('');
  console.log('Deterministic, non-LLM evals for AI agents.');
}

// Run main when invoked as the entry module. `process.exitCode` is set rather
// than calling process.exit so buffered stdout flushes first.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().then((code) => {
    process.exitCode = code;
  }).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 2;
  });
}
