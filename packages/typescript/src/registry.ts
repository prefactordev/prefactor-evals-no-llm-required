/**
 * Check registry.
 *
 * Every check registers under a stable ID. IDs appear in user config and in
 * stored reports, so renaming one is a breaking change and a semver major.
 */

import type { Config } from './config.js';
import type { EvalResult } from './result.js';
import type { EvalInstance } from './schema.js';

import * as conversationLength from './checks/conversationLength.js';
import * as efficiency from './checks/efficiency.js';
import * as errors from './checks/errors.js';
import * as latencyBudget from './checks/latencyBudget.js';
import * as loopDetection from './checks/loopDetection.js';
import * as redundantToolCalls from './checks/redundantToolCalls.js';
import * as terminationState from './checks/terminationState.js';

import * as costBudget from './checks/costBudget.js';
import * as escalationRule from './checks/escalationRule.js';
import * as forbiddenActions from './checks/forbiddenActions.js';
import * as outputSchema from './checks/outputSchema.js';
import * as toolArgSchema from './checks/toolArgSchema.js';

/**
 * Everything a check may see beyond its own instance.
 *
 * `instances` is the full fetched set, needed only by the two cross instance
 * checks. Within instance checks must ignore it.
 */
export interface EvalContext {
  instances: EvalInstance[];
  spanTypeMap: Record<string, string>;
}

export interface RegisteredEval {
  evalId: string;
  run: (instance: EvalInstance, config: Config, context: EvalContext) => EvalResult | null;
}

function entry(module: {
  EVAL_ID: string;
  run: (instance: EvalInstance, config: Config, context: EvalContext) => EvalResult | null;
}): RegisteredEval {
  return { evalId: module.EVAL_ID, run: module.run };
}

/** Insertion order matches the Python import order in evals/core/__init__.py. */
export const REGISTRY: Record<string, RegisteredEval> = {
  [conversationLength.EVAL_ID]: entry(conversationLength),
  [efficiency.EVAL_ID]: entry(efficiency),
  [errors.EVAL_ID]: entry(errors),
  [latencyBudget.EVAL_ID]: entry(latencyBudget),
  [loopDetection.EVAL_ID]: entry(loopDetection),
  [redundantToolCalls.EVAL_ID]: entry(redundantToolCalls),
  [terminationState.EVAL_ID]: entry(terminationState),

  // Optional, need configuration. Same order as Python's evals/core/__init__.py.
  [costBudget.EVAL_ID]: entry(costBudget),
  [escalationRule.EVAL_ID]: entry(escalationRule),
  [forbiddenActions.EVAL_ID]: entry(forbiddenActions),
  [outputSchema.EVAL_ID]: entry(outputSchema),
  [toolArgSchema.EVAL_ID]: entry(toolArgSchema),
};

export function get(evalId: string): RegisteredEval | undefined {
  return REGISTRY[evalId];
}

export function allEvals(): Record<string, RegisteredEval> {
  return { ...REGISTRY };
}
