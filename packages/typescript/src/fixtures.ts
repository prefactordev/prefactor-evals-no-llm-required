/**
 * Load normalized instances from JSON files.
 *
 * A second, deliberately tiny source. It exists for two reasons: the tests need
 * traces without a network call, and it is the worked example of the claim that
 * the Prefactor seam is replaceable. It emits schema/v1 shapes and knows nothing
 * about Prefactor.
 *
 * It reads the same fixture files as the Python implementation, which is what
 * makes the cross language conformance suite possible.
 */

import { readFileSync } from 'node:fs';
import type { EvalInstance, EvalSpan, SpanType } from './schema.js';
import { durationMs, parseTimestamp, resolveSpanType, sortSpans } from './schema.js';

type Raw = Record<string, unknown>;

function spanFromRaw(raw: Raw, instanceId: string, spanTypeMap?: Record<string, string>): EvalSpan {
  const startedAt = parseTimestamp(raw['started_at']);
  const endedAt = parseTimestamp(raw['ended_at']);
  const schemaName = (raw['schema_name'] as string) ?? '';
  // Fixtures state `type` directly, since their point is to exercise checks
  // rather than the normalization. Fall back to derivation when omitted.
  const type = (raw['type'] as SpanType) ?? resolveSpanType(schemaName, spanTypeMap);
  const rawDuration = raw['duration_ms'];
  return {
    id: raw['id'] as string,
    parentId: (raw['parent_id'] as string | null) ?? null,
    instanceId: (raw['instance_id'] as string) ?? instanceId,
    type,
    name: (raw['name'] as string) || schemaName,
    schemaName,
    input: raw['input'] ?? null,
    output: raw['output'] ?? null,
    state: (raw['state'] as string) ?? 'complete',
    startedAt,
    endedAt,
    durationMs: rawDuration === undefined ? durationMs(startedAt, endedAt) : (rawDuration as number | null),
    cost: (raw['cost'] as number | null) ?? null,
    tokens: (raw['tokens'] as Record<string, unknown> | null) ?? null,
    error: (raw['error'] as Record<string, unknown> | null) ?? null,
    metadata: (raw['metadata'] as Record<string, unknown>) ?? {},
  };
}

export function instanceFromRaw(raw: Raw, spanTypeMap?: Record<string, string>): EvalInstance {
  const instanceId = raw['id'] as string;
  const rawSpans = (raw['spans'] as Raw[]) ?? [];
  const spans = sortSpans(rawSpans.map((s) => spanFromRaw(s, instanceId, spanTypeMap)));
  const startedAt = parseTimestamp(raw['started_at']);
  const endedAt = parseTimestamp(raw['ended_at']);
  const rawDuration = raw['duration_ms'];
  return {
    id: instanceId,
    agentId: (raw['agent_id'] as string) ?? '',
    agentVersion: (raw['agent_version'] as string | null) ?? null,
    environment: (raw['environment'] as string | null) ?? null,
    startedAt,
    endedAt,
    state: (raw['state'] as string) ?? 'complete',
    durationMs: rawDuration === undefined ? durationMs(startedAt, endedAt) : (rawDuration as number | null),
    spans,
    input: raw['input'] ?? null,
    output: raw['output'] ?? null,
    cost: (raw['cost'] as number | null) ?? null,
    metadata: (raw['metadata'] as Record<string, unknown>) ?? {},
  };
}

export function loadInstances(path: string, spanTypeMap?: Record<string, string>): EvalInstance[] {
  const data = JSON.parse(readFileSync(path, 'utf8')) as Raw | Raw[];
  const rows = Array.isArray(data) ? data : ((data['instances'] as Raw[]) ?? []);
  return rows.map((r) => instanceFromRaw(r, spanTypeMap));
}
