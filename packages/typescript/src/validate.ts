/**
 * A minimal JSON Schema validator, draft 2020-12 subset.
 *
 * Spec: spec/shared/json-schema-subset.md
 *
 * This is the line for line port of the Python _schema_min module, and the
 * reason the two schema checks can be proven to agree on a verdict. It
 * implements the keywords with a single obvious meaning; everything else is
 * refused by schemaProblem before iterErrors ever runs. Coverage is traded for
 * the guarantee that both languages read a schema identically.
 *
 * Nothing here reaches the network or reports an offending value.
 */

import { canonical } from './helpers.js';

export interface SchemaError {
  path: Array<string | number>;
  keyword: string;
  validatorValue: unknown;
  instance: unknown;
}

// Keywords whose value carries its own message text in shortMessage.
const BOUND_KEYWORDS = new Set([
  'minimum', 'exclusiveMinimum', 'maximum', 'exclusiveMaximum',
  'minLength', 'maxLength', 'minItems', 'maxItems',
  'minProperties', 'maxProperties', 'multipleOf',
]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** A boolean is not a number, matching the JSON data model. */
function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** The JSON type name, with an integral number reported as an integer so the
 * two languages agree on a value one parsed as 1.0 and the other as 1. */
export function jsonTypeOf(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'boolean') return 'boolean';
  if (typeof value === 'number') return Number.isInteger(value) ? 'integer' : 'number';
  if (typeof value === 'string') return 'string';
  if (Array.isArray(value)) return 'array';
  if (typeof value === 'object') return 'object';
  return 'unknown';
}

function matchesType(value: unknown, name: string): boolean {
  switch (name) {
    case 'null': return value === null || value === undefined;
    case 'boolean': return typeof value === 'boolean';
    case 'integer': return jsonTypeOf(value) === 'integer';
    case 'number': return isNumber(value);
    case 'string': return typeof value === 'string';
    case 'array': return Array.isArray(value);
    case 'object': return isPlainObject(value);
    default: return false;
  }
}

/** The canonical form is the same one used for signatures: 1 equals 1.0 and key
 * order does not matter, identically in both languages. */
function equal(a: unknown, b: unknown): boolean {
  return canonical(a) === canonical(b);
}

function isMultiple(value: number, divisor: number): boolean {
  if (divisor === 0) return false;
  const quotient = value / divisor;
  const frac = quotient - Math.floor(quotient);
  return frac < 1e-9 || frac > 1 - 1e-9;
}

function allUnique(items: unknown[]): boolean {
  const seen = new Set<string>();
  for (const item of items) {
    const key = canonical(item);
    if (seen.has(key)) return false;
    seen.add(key);
  }
  return true;
}

export function pointerOf(path: Array<string | number>): string {
  const parts = path.map((p) => String(p).replace(/~/g, '~0').replace(/\//g, '~1'));
  return parts.length ? `/${parts.join('/')}` : '';
}

/** Integral numbers print without a trailing .0 so a bound written 5.0 in one
 * language and 5 in the other yield the same message. */
function fmtNum(value: unknown): string {
  return String(value);
}

export function shortMessage(error: SchemaError): string {
  const { keyword } = error;
  if (keyword === 'required') {
    const instance = isPlainObject(error.instance) ? error.instance : {};
    const list = Array.isArray(error.validatorValue) ? error.validatorValue : [];
    const missing = list.filter((p) => !(String(p) in instance));
    const name = missing.length ? missing[0] : '';
    return `missing required property "${name}"`;
  }
  if (keyword === 'type') {
    const expected = Array.isArray(error.validatorValue)
      ? error.validatorValue.map((t) => String(t)).join(' or ')
      : String(error.validatorValue);
    return `expected ${expected}, got ${jsonTypeOf(error.instance)}`;
  }
  if (keyword === 'enum') return 'value not in enum';
  if (keyword === 'const') return 'value not equal to const';
  if (keyword === 'pattern') return `string does not match pattern "${String(error.validatorValue)}"`;
  if (BOUND_KEYWORDS.has(keyword)) return `violates ${keyword} ${fmtNum(error.validatorValue)}`;
  if (keyword === 'additionalProperties') return 'additional properties are not allowed';
  if (keyword === 'uniqueItems') return 'array items are not unique';
  return `failed ${keyword} constraint`;
}

/**
 * Every place the value violates the schema.
 *
 * Assumes schemaProblem has already accepted the schema, so only supported
 * keywords are present. Production order does not matter: callers sort by
 * (pointer, keyword) before reporting.
 */
export function iterErrors(
  schema: unknown,
  value: unknown,
  path: Array<string | number> = [],
): SchemaError[] {
  const errors: SchemaError[] = [];

  if (typeof schema === 'boolean') {
    if (schema === false) errors.push({ path: [...path], keyword: 'false', validatorValue: false, instance: value });
    return errors;
  }
  if (!isPlainObject(schema)) return errors;

  if ('type' in schema) {
    const declared = schema['type'];
    const names = Array.isArray(declared) ? declared : [declared];
    if (!names.some((n) => matchesType(value, String(n)))) {
      errors.push({ path: [...path], keyword: 'type', validatorValue: declared, instance: value });
    }
  }

  if ('enum' in schema) {
    const options = schema['enum'];
    if (Array.isArray(options) && !options.some((o) => equal(value, o))) {
      errors.push({ path: [...path], keyword: 'enum', validatorValue: options, instance: value });
    }
  }

  if ('const' in schema && !equal(value, schema['const'])) {
    errors.push({ path: [...path], keyword: 'const', validatorValue: schema['const'], instance: value });
  }

  if (isNumber(value)) {
    const push = (keyword: string, bound: unknown) => errors.push({ path: [...path], keyword, validatorValue: bound, instance: value });
    if (isNumber(schema['minimum']) && value < schema['minimum']) push('minimum', schema['minimum']);
    if (isNumber(schema['exclusiveMinimum']) && value <= schema['exclusiveMinimum']) push('exclusiveMinimum', schema['exclusiveMinimum']);
    if (isNumber(schema['maximum']) && value > schema['maximum']) push('maximum', schema['maximum']);
    if (isNumber(schema['exclusiveMaximum']) && value >= schema['exclusiveMaximum']) push('exclusiveMaximum', schema['exclusiveMaximum']);
    if (isNumber(schema['multipleOf']) && !isMultiple(value, schema['multipleOf'])) push('multipleOf', schema['multipleOf']);
  }

  if (typeof value === 'string') {
    const length = [...value].length; // code points, matching Python len.
    if (isNumber(schema['minLength']) && length < schema['minLength']) {
      errors.push({ path: [...path], keyword: 'minLength', validatorValue: schema['minLength'], instance: value });
    }
    if (isNumber(schema['maxLength']) && length > schema['maxLength']) {
      errors.push({ path: [...path], keyword: 'maxLength', validatorValue: schema['maxLength'], instance: value });
    }
    const pattern = schema['pattern'];
    if (typeof pattern === 'string' && new RegExp(pattern).test(value) === false) {
      errors.push({ path: [...path], keyword: 'pattern', validatorValue: pattern, instance: value });
    }
  }

  if (Array.isArray(value)) {
    if (isNumber(schema['minItems']) && value.length < schema['minItems']) {
      errors.push({ path: [...path], keyword: 'minItems', validatorValue: schema['minItems'], instance: value });
    }
    if (isNumber(schema['maxItems']) && value.length > schema['maxItems']) {
      errors.push({ path: [...path], keyword: 'maxItems', validatorValue: schema['maxItems'], instance: value });
    }
    if (schema['uniqueItems'] === true && !allUnique(value)) {
      errors.push({ path: [...path], keyword: 'uniqueItems', validatorValue: true, instance: value });
    }
    const items = schema['items'];
    if (isPlainObject(items) || typeof items === 'boolean') {
      value.forEach((element, index) => {
        errors.push(...iterErrors(items, element, [...path, index]));
      });
    }
  }

  if (isPlainObject(value)) {
    const keys = Object.keys(value);
    const required = schema['required'];
    if (Array.isArray(required) && required.some((p) => !(String(p) in value))) {
      errors.push({ path: [...path], keyword: 'required', validatorValue: required, instance: value });
    }
    if (isNumber(schema['minProperties']) && keys.length < schema['minProperties']) {
      errors.push({ path: [...path], keyword: 'minProperties', validatorValue: schema['minProperties'], instance: value });
    }
    if (isNumber(schema['maxProperties']) && keys.length > schema['maxProperties']) {
      errors.push({ path: [...path], keyword: 'maxProperties', validatorValue: schema['maxProperties'], instance: value });
    }
    const props = schema['properties'];
    const defined = isPlainObject(props) ? new Set(Object.keys(props)) : new Set<string>();
    if (isPlainObject(props)) {
      for (const [name, subschema] of Object.entries(props)) {
        if (name in value) errors.push(...iterErrors(subschema, value[name], [...path, name]));
      }
    }
    const additional = schema['additionalProperties'];
    if (additional === false) {
      if (keys.some((k) => !defined.has(k))) {
        errors.push({ path: [...path], keyword: 'additionalProperties', validatorValue: false, instance: value });
      }
    } else if (isPlainObject(additional)) {
      for (const key of keys) {
        if (!defined.has(key)) errors.push(...iterErrors(additional, value[key], [...path, key]));
      }
    }
  }

  return errors;
}

function byPointerThenKeyword(a: SchemaError, b: SchemaError): number {
  const pa = pointerOf(a.path);
  const pb = pointerOf(b.path);
  if (pa < pb) return -1;
  if (pa > pb) return 1;
  if (a.keyword < b.keyword) return -1;
  if (a.keyword > b.keyword) return 1;
  return 0;
}

export function firstError(schema: unknown, value: unknown): SchemaError | null {
  const errors = iterErrors(schema, value);
  if (!errors.length) return null;
  errors.sort(byPointerThenKeyword);
  return errors[0]!;
}

export function sortedErrors(schema: unknown, value: unknown): SchemaError[] {
  const errors = iterErrors(schema, value);
  errors.sort(byPointerThenKeyword);
  return errors;
}
