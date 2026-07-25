/**
 * THE SEAM. The only file in this package that knows Prefactor exists.
 *
 * Replace it with your own loader that emits schema/v1 shapes and everything
 * else in the library works unchanged. Pulling real production traces from
 * Prefactor is the point, but the choice is yours.
 *
 * This file speaks HTTP rather than importing the Prefactor SDK because both
 * SDKs are write only: they create instances and spans and expose no read path.
 * There is nothing to wrap. So this issues its own requests against the
 * documented endpoints, and the coupling stays confined to one file.
 *
 * This is the line for line port of the Python source.py.
 */

import { comparePython } from './fmt.js';
import {
  type EvalInstance,
  type EvalSpan,
  durationMs,
  parseTimestamp,
  resolveSpanName,
  resolveSpanType,
  sortSpans,
} from './schema.js';

const DEFAULT_PAGE_SIZE = 100;

// The documented Prefactor cloud host: docs.prefactor.ai/api publishes this as
// the API base URL. Used when PREFACTOR_API_URL is not set, so cloud users,
// which is nearly everyone, configure exactly one thing: the token. Anyone on a
// different host sets the variable and it wins.
export const DEFAULT_API_URL = 'https://app.prefactorai.com';

// Where the one thing you must configure comes from. Matches the Admin UI and
// docs.prefactor.ai/admin-ui/account/api-tokens.
const TOKEN_REMEDY = 'PREFACTOR_API_TOKEN is not set. Create an account-wide API token in the '
  + 'Prefactor Admin UI under Account, API Tokens, Create API token, and copy '
  + 'it when shown, it is only displayed once.';

// Sort key for rows with no start time, so they land last under newest first
// ordering rather than crashing the comparison.
const EPOCH = new Date(0);

export class PrefactorSourceError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PrefactorSourceError';
  }
}

type Json = Record<string, unknown>;

function env(name: string): string {
  const value = (globalThis as { process?: { env?: Record<string, string | undefined> } })
    .process?.env?.[name];
  return value ?? '';
}

/**
 * One place for every Prefactor HTTP call, read or write.
 *
 * Kept module level so the read source and the write publisher share exactly
 * the same auth, error mapping, and 401 message, and so all Prefactor coupling
 * still lives in this single file.
 */
async function httpRequest(
  method: string, url: string, token: string, timeout: number, body?: Json,
): Promise<Json> {
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  let data: string | undefined;
  if (body !== undefined) {
    data = JSON.stringify(body);
    headers['Content-Type'] = 'application/json';
  }
  let response: Response;
  try {
    response = await fetch(url, {
      method, headers, body: data,
      signal: AbortSignal.timeout(timeout * 1000),
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new PrefactorSourceError(`Could not reach ${url}: ${detail}`);
  }
  if (!response.ok) {
    if (response.status === 401) {
      throw new PrefactorSourceError(
        'Prefactor returned 401. The read and quality endpoints need an '
        + 'account-wide API token, created in the Admin UI under Account, '
        + 'API Tokens. A deployment scoped token or the SDK ingestion '
        + 'token used for instrumentation will not work here.',
      );
    }
    throw new PrefactorSourceError(
      `Prefactor returned HTTP ${response.status} for ${method} ${url}`,
    );
  }
  const text = await response.text();
  return text.trim() ? (JSON.parse(text) as Json) : {};
}

/** Spans written with sensitive_encoding carry {"$sensitive": type, "value":
 * ...} wrappers. Comparing config against the wrapper would fail incorrectly,
 * so unwrap to the value wherever it appears. */
function unwrapSensitive(value: unknown): unknown {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Json;
    if ('$sensitive' in record) return unwrapSensitive(record['value']);
    const out: Json = {};
    for (const [k, v] of Object.entries(record)) out[k] = unwrapSensitive(v);
    return out;
  }
  if (Array.isArray(value)) return value.map(unwrapSensitive);
  return value;
}

/** Accepts a Date, an ISO string, or a shorthand like `7d`, `24h`, `30m`.
 *
 * The shorthand is relative to now, which is the one clock read in the whole
 * library. It happens here, at fetch time, never inside an eval. */
function parseSince(since: unknown): Date | null {
  if (since === null || since === undefined) return null;
  if (since instanceof Date) return since;
  const text = String(since).trim();
  const unit = text.slice(-1);
  const head = text.slice(0, -1);
  if (text && 'dhm'.includes(unit) && head.length > 0 && /^\d+$/.test(head)) {
    const amount = Number(head);
    const ms = { d: 86400000, h: 3600000, m: 60000 }[unit]!;
    return new Date(Date.now() - amount * ms);
  }
  return parseTimestamp(text);
}

function asObject(value: unknown): Json {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Json) : {};
}

/** Run each item through fn with at most `limit` in flight, preserving input
 * order in the result. The async equivalent of Python's bounded ThreadPool: this
 * reads someone's production API, and the gain from more sockets flattens out
 * well before the rudeness does. */
async function mapBounded<T, R>(
  items: T[], limit: number, fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const out = new Array<R>(items.length);
  let next = 0;
  async function worker(): Promise<void> {
    while (true) {
      const index = next;
      next += 1;
      if (index >= items.length) return;
      out[index] = await fn(items[index]!);
    }
  }
  const workers = Array.from({ length: Math.min(limit, items.length) }, () => worker());
  await Promise.all(workers);
  return out;
}

export interface FetchFilters {
  since?: unknown;
  until?: unknown;
  state?: string | null;
  environment?: string | null;
  purpose?: string | null;
  limit?: number | null;
  withCost?: boolean;
  spanTypeMap?: Record<string, string>;
}

export class PrefactorSource {
  readonly apiUrl: string;
  readonly apiToken: string;
  readonly spanTypeMap: Record<string, string>;
  readonly timeout: number;
  readonly maxWorkers: number;

  constructor(options: {
    apiUrl?: string; apiToken?: string; spanTypeMap?: Record<string, string>;
    timeout?: number; maxWorkers?: number;
  } = {}) {
    this.apiUrl = (options.apiUrl || env('PREFACTOR_API_URL') || DEFAULT_API_URL)
      .replace(/\/+$/, '');
    this.apiToken = options.apiToken ?? env('PREFACTOR_API_TOKEN');
    this.spanTypeMap = options.spanTypeMap ?? {};
    this.timeout = options.timeout ?? 30.0;
    // Bounded on purpose. This reads someone's production API.
    this.maxWorkers = Math.max(1, Math.trunc(options.maxWorkers ?? 8));
    if (!this.apiToken) throw new PrefactorSourceError(TOKEN_REMEDY);
  }

  // -- HTTP -----------------------------------------------------------------

  async get(path: string, params?: Record<string, string | number>): Promise<Json> {
    let url = this.apiUrl + path;
    if (params) {
      const search = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) search.set(k, String(v));
      url += `?${search.toString()}`;
    }
    return httpRequest('GET', url, this.apiToken, this.timeout);
  }

  /** Yield rows page by page. Filtering is client side, so a caller asking for
   * 10 instances would otherwise page through the agent's entire history. */
  private async* iterPages(path: string, params: Record<string, string>): AsyncGenerator<Json> {
    let offset = 0;
    while (true) {
      const page: Record<string, string | number> = { ...params };
      page['pagination[offset]'] = offset;
      page['pagination[page_size]'] = DEFAULT_PAGE_SIZE;
      const body = await this.get(path, page);
      const rows = (Array.isArray(body['summaries']) ? body['summaries'] : []) as Json[];
      for (const row of rows) yield row;
      const pagination = asObject(body['pagination']);
      const raw = pagination['next_page_offset'];
      // Converted BEFORE comparing. A server that returns the offset as a
      // string would otherwise never equal the number it was sent, and the
      // loop would refetch the same page forever against a production API.
      const nxt = raw === null || raw === undefined ? null : Number(raw);
      if (rows.length === 0 || nxt === null || Number.isNaN(nxt) || nxt === offset) return;
      offset = nxt;
    }
  }

  /** Internal, but not `private`: the controller's liveSpans reads through it,
   * and index-access to a private member only compiles by accident. */
  async paged(path: string, params: Record<string, string>, limit?: number): Promise<Json[]> {
    const out: Json[] = [];
    for await (const row of this.iterPages(path, params)) {
      out.push(row);
      if (limit !== undefined && out.length >= limit) break;
    }
    return out;
  }

  // -- Mapping --------------------------------------------------------------

  mapSpan(row: Json): EvalSpan {
    const payload = asObject(row['payload']);
    const schemaName = (row['schema_name'] as string) || '';

    let rawInput: unknown = payload['inputs'] ?? null;
    let rawOutput: unknown = row['result_payload'] ?? null;
    if (rawOutput === null || rawOutput === undefined
        || (typeof rawOutput === 'object' && !Array.isArray(rawOutput)
            && Object.keys(rawOutput as Json).length === 0)) {
      rawOutput = payload['outputs'] ?? null;
    }

    // sensitive_encoding is a top level span field, so the wrapper case is
    // detectable before parsing.
    if (row['sensitive_encoding']) {
      rawInput = unwrapSensitive(rawInput);
      rawOutput = unwrapSensitive(rawOutput);
    }

    let tokens: Record<string, unknown> | null = null;
    const usage = payload['token_usage'];
    if (usage !== null && typeof usage === 'object' && !Array.isArray(usage)) {
      const u = usage as Json;
      tokens = {
        prompt: u['prompt_tokens'] ?? null,
        completion: u['completion_tokens'] ?? null,
        total: u['total_tokens'] ?? null,
      };
    }

    let error: Record<string, unknown> | null = null;
    const rawError = payload['error'];
    if (rawError !== null && typeof rawError === 'object' && !Array.isArray(rawError)) {
      const e = rawError as Json;
      error = {
        type: e['error_type'] ?? null,
        message: e['message'] ?? null,
        stacktrace: e['stacktrace'] ?? null,
      };
    }

    const started = parseTimestamp(row['started_at']);
    const ended = parseTimestamp(row['finished_at']);

    return {
      // The server ID, not payload.span_id. Parentage refers to server IDs, so
      // using the client ID would produce a tree where no parent resolves.
      id: (row['id'] as string) || '',
      parentId: (row['parent_span_id'] as string | null) ?? null,
      instanceId: (row['agent_instance_id'] as string) || '',
      type: resolveSpanType(schemaName, this.spanTypeMap),
      name: resolveSpanName(schemaName, payload['name'] as string | null),
      schemaName,
      input: rawInput ?? null,
      output: rawOutput ?? null,
      state: (row['status'] as EvalSpan['state']) || 'pending',
      startedAt: started,
      endedAt: ended,
      durationMs: durationMs(started, ended),
      cost: null, // No per span cost exists anywhere in the API.
      tokens,
      error,
      metadata: asObject(payload['metadata']),
    };
  }

  mapInstance(row: Json, spans: EvalSpan[], detail?: Json | null): EvalInstance {
    const started = parseTimestamp(row['started_at']);
    const ended = parseTimestamp(row['finished_at']);
    const ordered = sortSpans(spans);

    // The instance record carries neither input nor output, so both are derived.
    // Real traces are almost always flat, so "the single root span" resolves on
    // essentially no real instance; deriving from the root alone would leave
    // input and output null everywhere. So there are two rules, and which one
    // fired is recorded, because a derivation that can be wrong must at least say
    // where it came from.
    let derivedInput: unknown = null;
    let inputSource: string | null = null;
    let derivedOutput: unknown = null;
    let outputSource: string | null = null;

    const roots = ordered.filter((s) => s.parentId === null);
    if (roots.length === 1) {
      derivedInput = roots[0]!.input;
      inputSource = 'root_span';
      derivedOutput = roots[0]!.output;
      outputSource = 'root_span';
    } else {
      for (const span of ordered) {
        if (span.input !== null && span.input !== undefined) {
          derivedInput = span.input;
          inputSource = 'first_span';
          break;
        }
      }
      for (let i = ordered.length - 1; i >= 0; i -= 1) {
        if (ordered[i]!.output !== null && ordered[i]!.output !== undefined) {
          derivedOutput = ordered[i]!.output;
          outputSource = 'last_output_span';
          break;
        }
      }
    }

    const metadata: Json = {
      account_id: row['account_id'] ?? null,
      termination_reason: row['termination_reason'] ?? null,
      purpose: row['purpose'] ?? null,
      inserted_at: row['inserted_at'] ?? null,
      updated_at: row['updated_at'] ?? null,
      input_source: inputSource,
      output_source: outputSource,
      root_span_count: roots.length,
    };
    let cost: number | null = null;
    if (detail) {
      const breakdown = detail['cost_breakdown'];
      if (breakdown !== null && typeof breakdown === 'object' && !Array.isArray(breakdown)) {
        cost = (breakdown as Json)['total_cost'] as number | null ?? null;
        metadata['cost_breakdown'] = breakdown;
      }
      for (const key of ['span_counts', 'span_schema_counts', 'risk_score',
        'quality_payload', 'quality_summary']) {
        if (key in detail) metadata[key] = detail[key];
      }
    }

    return {
      id: (row['id'] as string) || '',
      agentId: (row['agent_id'] as string) || '',
      agentVersion: (row['agent_version_id'] as string | null) ?? null,
      environment: (row['environment_id'] as string | null) ?? null,
      startedAt: started,
      endedAt: ended,
      state: (row['status'] as EvalInstance['state']) || 'pending',
      durationMs: durationMs(started, ended),
      spans: ordered,
      input: derivedInput ?? null,
      output: derivedOutput ?? null,
      cost,
      metadata,
    };
  }

  // -- Public API -----------------------------------------------------------

  /** The account's agents, as {id, name, status}. Exists because the agent id
   * is the one thing setup needs that the user cannot type from memory;
   * `agents` in the CLI prints this, so nobody has to fish an id out of a
   * dashboard URL. */
  async listAgents(): Promise<Array<{ id: string; name: string; status: string }>> {
    const rows = await this.paged('/api/v1/agent', {});
    return rows.map((r) => ({
      id: (r['id'] as string) || '',
      name: (r['name'] as string) || '',
      status: (r['status'] as string) || '',
    }));
  }

  /** Fetch normalized instances for an agent.
   *
   * Server side filtering is close to nonexistent: the instance list accepts
   * agent_id only, has no time window, and ignores status. So everything below
   * is filtered client side after fetching. A wide `since` window is expensive. */
  async fetchInstances(agentId: string, filters: FetchFilters = {}): Promise<EvalInstance[]> {
    const { state = null, environment = null, limit = null, withCost = false } = filters;
    const purpose = filters.purpose === undefined ? 'live' : filters.purpose;
    const sinceDt = parseSince(filters.since);
    const untilDt = parseSince(filters.until);

    // Newest first, ordered here rather than on the server: the live API rejects
    // every documented sorting value and defaults to oldest first, so `limit`
    // would otherwise score the agent's oldest runs.
    const rows: Json[] = [];
    for await (const row of this.iterPages('/api/v1/agent_instance', { agent_id: agentId })) {
      rows.push(row);
    }
    rows.sort((a, b) => {
      const ta = parseTimestamp(a['started_at'])?.getTime() ?? EPOCH.getTime();
      const tb = parseTimestamp(b['started_at'])?.getTime() ?? EPOCH.getTime();
      return tb - ta;
    });

    const selected: Json[] = [];
    for (const row of rows) {
      if (state && row['status'] !== state) continue;
      if (environment && row['environment_id'] !== environment) continue;
      // Default to live traffic. Running evals over your own eval and smoke test
      // runs measures the harness, not the agent.
      if (purpose && row['purpose'] && row['purpose'] !== purpose) continue;
      const started = parseTimestamp(row['started_at']);
      if (sinceDt && (started === null || started < sinceDt)) continue;
      if (untilDt && (started === null || started > untilDt)) continue;
      selected.push(row);
      if (limit !== null && selected.length >= limit) break;
    }

    const fetchOne = async (row: Json): Promise<EvalInstance> => {
      const spanRows = await this.paged('/api/v1/agent_spans',
        { agent_instance_id: (row['id'] as string) || '' });
      const spans = spanRows.map((s) => this.mapSpan(s));
      let detail: Json | null = null;
      if (withCost) {
        // Send no include_* flags. cost_breakdown and the counts come back
        // unconditionally, and include_risk_score=true returns 500.
        const body = await this.get(`/api/v1/agent_instance/${(row['id'] as string) || ''}`);
        detail = asObject(body['details']);
      }
      return this.mapInstance(row, spans, detail);
    };

    if (selected.length < 2 || this.maxWorkers <= 1) {
      const out: EvalInstance[] = [];
      for (const row of selected) out.push(await fetchOne(row));
      return out;
    }
    // Mapped in order, so the result order matches `selected` no matter which
    // request finishes first. Ordering feeds straight into eval results.
    return mapBounded(selected, Math.min(this.maxWorkers, selected.length), fetchOne);
  }
}

/** Module level convenience wrapper, the one function the spec names. */
export async function fetchInstances(agentId: string, filters: FetchFilters = {}): Promise<EvalInstance[]> {
  const source = new PrefactorSource({ spanTypeMap: filters.spanTypeMap });
  return source.fetchInstances(agentId, filters);
}

/** Order raw span rows by start time then id, so successive live reads see a
 * stable prefix and only genuinely new spans at the end. */
export function sortSpanRows(rows: Json[]): Json[] {
  return [...rows].sort((a, b) => {
    const ta = parseTimestamp(a['started_at']);
    const tb = parseTimestamp(b['started_at']);
    const ka: [number, number, string] = ta
      ? [0, ta.getTime(), (a['id'] as string) || ''] : [1, 0, (a['id'] as string) || ''];
    const kb: [number, number, string] = tb
      ? [0, tb.getTime(), (b['id'] as string) || ''] : [1, 0, (b['id'] as string) || ''];
    if (ka[0] !== kb[0]) return ka[0] - kb[0];
    if (ka[1] !== kb[1]) return ka[1] - kb[1];
    return comparePython(ka[2], kb[2]);
  });
}

/** The one write action that stops a run: terminate.
 *
 * The endpoint is built for exactly this, an external party ending a live run
 * with a reason. Only active runs can be terminated; the agent learns of it on
 * the control flag that comes back on its next span, and stops. */
export class PrefactorController {
  readonly apiUrl: string;
  readonly apiToken: string;
  readonly timeout: number;

  constructor(options: { apiUrl?: string; apiToken?: string; timeout?: number } = {}) {
    this.apiUrl = (options.apiUrl || env('PREFACTOR_API_URL') || DEFAULT_API_URL)
      .replace(/\/+$/, '');
    this.apiToken = options.apiToken ?? env('PREFACTOR_API_TOKEN');
    this.timeout = options.timeout ?? 30.0;
    if (!this.apiToken) throw new PrefactorSourceError(TOKEN_REMEDY);
  }

  /** Stop a live run. `reason` is required by the API and is what the agent and
   * the audit trail see, so it should name the breach. */
  async terminate(instanceId: string, reason: string): Promise<Json> {
    if (!reason || !reason.trim()) throw new PrefactorSourceError('terminate needs a reason.');
    return httpRequest('POST',
      `${this.apiUrl}/api/v1/agent_instance/${instanceId}/terminate`,
      this.apiToken, this.timeout, { reason });
  }

  /** The instance's spans in start order, from `afterIndex` on. Used to pull
   * only the spans that have arrived since the last look. */
  async liveSpans(instanceId: string, afterIndex = 0): Promise<EvalSpan[]> {
    const source = new PrefactorSource({
      apiUrl: this.apiUrl, apiToken: this.apiToken, timeout: this.timeout,
    });
    const spanRows = await source.paged('/api/v1/agent_spans',
      { agent_instance_id: instanceId });
    const rows = sortSpanRows(spanRows).map((s) => source.mapSpan(s));
    return rows.slice(afterIndex);
  }
}

// The single key this library owns inside an instance's quality payload. Writing
// only under owned fields means a user who records their own quality data from
// their app is never clobbered.
export const QUALITY_KEY = 'evals_no_llm';

// The top level fields this library owns in a quality_payload. Anything else in
// the payload belongs to the agent and is never touched.
export const QUALITY_FIELDS = [
  'report', 'result', 'checks', 'checks_passed', 'checks_failed',
  'checks_not_run', 'recorded_verdict', 'recorded_scores', 'signals',
  'signals_flagged', 'worst_signal', 'pack', 'scored_while_running',
  'instance_state', 'tool_version', 'evaluated_at',
];

/** Declare the eval quality schema on an agent's schema version.
 *
 * The Quality tab renders from a schema declared at registration, so this is the
 * step that makes published results visible at all. There is no endpoint for
 * declaring a schema on its own, so this registers one probe instance carrying
 * the schema, then finishes it. Rendering binds to the schema version an
 * instance was registered under, so this affects later runs only. */
export async function declareQualitySchema(agentId: string, options: {
  version?: string; schemaVersion?: string; environmentId?: string | null;
  apiUrl?: string; apiToken?: string; timeout?: number;
} = {}): Promise<{ instance_id: unknown; schema_version: string }> {
  const { QUALITY_SCHEMA } = await import('./publish.js');
  const version = options.version ?? 'agent-evals';
  const schemaVersion = options.schemaVersion ?? 'agent-evals-quality-v1';
  const url = (options.apiUrl || env('PREFACTOR_API_URL') || DEFAULT_API_URL)
    .replace(/\/+$/, '');
  const token = options.apiToken ?? env('PREFACTOR_API_TOKEN');
  // `||` not `??`: an empty string falls back to the environment variable,
  // matching Python's `environment_id or os.environ.get(...)`.
  const environment = options.environmentId || env('PREFACTOR_ENVIRONMENT_ID') || null;
  const timeout = options.timeout ?? 30.0;
  if (!token) throw new PrefactorSourceError(TOKEN_REMEDY);

  const body: Json = {
    agent_id: agentId,
    agent_version: {
      external_identifier: version,
      name: 'Agent evals',
      description: 'Eval results published by prefactor-evals-no-llm',
    },
    agent_schema_version: {
      external_identifier: schemaVersion,
      span_type_schemas: [],
      quality_schema: QUALITY_SCHEMA,
    },
  };
  if (environment) body['environment_id'] = environment;

  const registered = await httpRequest('POST', `${url}/api/v1/agent_instance/register`,
    token, timeout, body);
  const instanceId = asObject(registered['details'])['id'];
  if (instanceId) {
    // The probe instance exists only to carry the schema. Close it so it does
    // not sit in the agent's list looking like a run that never finished.
    try {
      await httpRequest('POST', `${url}/api/v1/agent_instance/${instanceId}/finish`,
        token, timeout, { status: 'cancelled' });
    } catch (error) {
      if (!(error instanceof PrefactorSourceError)) throw error;
    }
  }
  return { instance_id: instanceId, schema_version: schemaVersion };
}

/** Writes eval scores into an instance's Prefactor Quality tab.
 *
 * The only write path, opt in: a plain eval run never constructs one. The write
 * is a PUT of the whole quality_payload, so this reads the current payload first
 * and merges, never touching anything else the user stored there. */
export class PrefactorPublisher {
  readonly apiUrl: string;
  readonly apiToken: string;
  readonly timeout: number;

  constructor(options: { apiUrl?: string; apiToken?: string; timeout?: number } = {}) {
    this.apiUrl = (options.apiUrl || env('PREFACTOR_API_URL') || DEFAULT_API_URL)
      .replace(/\/+$/, '');
    this.apiToken = options.apiToken ?? env('PREFACTOR_API_TOKEN');
    this.timeout = options.timeout ?? 30.0;
    if (!this.apiToken) throw new PrefactorSourceError(TOKEN_REMEDY);
  }

  /** The instance's existing quality_payload, or an empty object. */
  async currentQualityPayload(instanceId: string): Promise<Json> {
    const body = await httpRequest('GET',
      `${this.apiUrl}/api/v1/agent_instance/${instanceId}`, this.apiToken, this.timeout);
    const details = asObject(body['details']);
    const payload = details['quality_payload'];
    return payload !== null && typeof payload === 'object' && !Array.isArray(payload)
      ? { ...(payload as Json) } : {};
  }

  /** Merge `block` into the quality_payload and PUT the whole thing. The block's
   * fields go at the top level, where the declared schema's template reads them.
   * Any existing keys this library does not own are preserved. */
  async publish(instanceId: string, block: Json): Promise<Json> {
    const merged = await this.currentQualityPayload(instanceId);
    delete merged[QUALITY_KEY]; // drop the old nested form if present
    Object.assign(merged, block);
    // The details wrapper is required by the API. Without it the server rejects
    // the body with a validation error.
    await httpRequest('PUT', `${this.apiUrl}/api/v1/agent_instance/${instanceId}`,
      this.apiToken, this.timeout, { details: { quality_payload: merged } });
    return merged;
  }

  /** Remove only the fields this library wrote, leaving anything else. */
  async clear(instanceId: string, blockKeys?: string[]): Promise<Json> {
    const merged = await this.currentQualityPayload(instanceId);
    delete merged[QUALITY_KEY];
    for (const key of blockKeys ?? QUALITY_FIELDS) delete merged[key];
    await httpRequest('PUT', `${this.apiUrl}/api/v1/agent_instance/${instanceId}`,
      this.apiToken, this.timeout, { details: { quality_payload: merged } });
    return merged;
  }
}
