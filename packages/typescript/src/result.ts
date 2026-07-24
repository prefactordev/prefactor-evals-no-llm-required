/** The result object. Identical semantics and identical JSON shape to Python. */

export const PASS = 'pass';
export const FAIL = 'fail';
export const SKIP = 'skip';
export type Status = typeof PASS | typeof FAIL | typeof SKIP;

export interface Evidence {
  span_ids: string[];
  values: Record<string, unknown>;
}

/** Field names are snake_case to match the Python result JSON exactly. The
 * conformance suite diffs these objects, so a camelCase rename here would look
 * like a behaviour difference. */
export interface EvalResult {
  eval_id: string;
  instance_id: string;
  status: Status;
  details: string;
  evidence: Evidence;
  /** Only set on skip. Names the config key that would enable this check, so a
   * scorecard can say how to fix it rather than only that it did not run. */
  remedy?: string;
}

function evidence(spanIds?: readonly string[], values?: Record<string, unknown>): Evidence {
  return { span_ids: spanIds ? [...spanIds] : [], values: values ? { ...values } : {} };
}

export function passed(
  evalId: string, instanceId: string, details: string,
  spanIds?: readonly string[], values?: Record<string, unknown>,
): EvalResult {
  return {
    eval_id: evalId, instance_id: instanceId, status: PASS, details,
    evidence: evidence(spanIds, values),
  };
}

export function failed(
  evalId: string, instanceId: string, details: string,
  spanIds?: readonly string[], values?: Record<string, unknown>,
): EvalResult {
  return {
    eval_id: evalId, instance_id: instanceId, status: FAIL, details,
    evidence: evidence(spanIds, values),
  };
}

/** Skip is never a pass. It is counted separately everywhere. */
export function skipped(
  evalId: string, instanceId: string, details: string,
  remedy?: string, values?: Record<string, unknown>,
): EvalResult {
  return {
    eval_id: evalId, instance_id: instanceId, status: SKIP, details,
    evidence: evidence([], values), ...(remedy ? { remedy } : {}),
  };
}
