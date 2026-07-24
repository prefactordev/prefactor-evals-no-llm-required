/**
 * Reading user written config without crashing on it.
 *
 * Config is typed by hand into a file. Wrong types are not an edge case, they
 * are the normal failure. Every reader here either returns a usable value or
 * throws ConfigError naming the key, what was expected, and what arrived. The
 * runner turns that into a skip, so a typo costs a check rather than the run.
 *
 * Coercion is deliberately narrow. "5" becomes 5 because a quoting slip is
 * obvious and harmless. true does not become 1, and "yes" does not become true,
 * because guessing intent from a wrong type is how a check ends up silently
 * measuring something nobody asked for.
 */

export type Config = Record<string, unknown>;

export class ConfigError extends Error {
  constructor(
    public readonly key: string,
    public readonly expected: string,
    public readonly got: unknown,
  ) {
    const gotType = Array.isArray(got) ? 'list' : got === null ? 'null' : typeof got;
    let shown: string;
    try {
      shown = String(JSON.stringify(got)).slice(0, 60);
    } catch {
      shown = String(got);
    }
    super(`Config key "${key}" should be ${expected}, but got ${gotType} (${shown}).`);
    this.name = 'ConfigError';
  }

  get remedy(): string {
    return `Set "${this.key}" to ${this.expected} in the pack file.`;
  }
}

function missing(value: unknown): boolean {
  return value === null || value === undefined;
}

export function cfgInt(config: Config, key: string, fallback: number | null = null): number | null {
  const value = config[key];
  if (missing(value)) return fallback;
  if (typeof value === 'boolean') throw new ConfigError(key, 'a whole number', value);
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return value;
    throw new ConfigError(key, 'a whole number', value);
  }
  if (typeof value === 'string') {
    const text = value.trim();
    if (/^-?\d+$/.test(text)) return Number(text);
    throw new ConfigError(key, 'a whole number', value);
  }
  throw new ConfigError(key, 'a whole number', value);
}

export function cfgFloat(config: Config, key: string, fallback: number | null = null): number | null {
  const value = config[key];
  if (missing(value)) return fallback;
  if (typeof value === 'boolean') throw new ConfigError(key, 'a number', value);
  if (typeof value === 'number') return value;
  if (typeof value === 'string') {
    const text = value.trim();
    const n = Number(text);
    if (text !== '' && Number.isFinite(n)) return n;
    throw new ConfigError(key, 'a number', value);
  }
  throw new ConfigError(key, 'a number', value);
}

export function cfgBool(config: Config, key: string, fallback: boolean | null = null): boolean | null {
  const value = config[key];
  if (missing(value)) return fallback;
  if (typeof value === 'boolean') return value;
  throw new ConfigError(key, 'true or false', value);
}

export function cfgStr(config: Config, key: string, fallback: string | null = null): string | null {
  const value = config[key];
  if (missing(value)) return fallback;
  if (typeof value === 'string') return value;
  throw new ConfigError(key, 'a piece of text', value);
}

/**
 * A list. A bare string is rejected rather than treated as one item.
 *
 * Silently accepting `forbidden: issue_refund` would iterate the string one
 * character at a time, match nothing, and report a pass on a check that
 * measured nothing. A green tick that checked nothing is worse than an error.
 */
export function cfgList(config: Config, key: string, of?: 'string'): unknown[] {
  const value = config[key];
  if (missing(value)) return [];
  if (typeof value === 'string' || !Array.isArray(value)) {
    throw new ConfigError(key, 'a list', value);
  }
  if (of === 'string') {
    for (const item of value) {
      if (typeof item !== 'string') throw new ConfigError(key, 'a list of text', item);
    }
  }
  return value;
}

export function cfgStrSet(config: Config, key: string): Set<string> {
  return new Set(cfgList(config, key, 'string') as string[]);
}

export function cfgDict(config: Config, key: string): Record<string, unknown> {
  const value = config[key];
  if (missing(value)) return {};
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw new ConfigError(key, 'a mapping', value);
  }
  return value as Record<string, unknown>;
}
