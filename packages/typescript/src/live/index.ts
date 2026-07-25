/**
 * Live, per-span evaluation.
 *
 * The batch checks in this library grade a run after it finishes. These grade a
 * run while it is still running, one span at a time, so a breach can stop the
 * run before it does more harm.
 */

export {
  Breach, LiveEvaluator, POLICY_OFF, POLICY_WARN, POLICY_TERMINATE,
} from './detectors.js';
