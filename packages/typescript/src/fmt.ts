/**
 * Cross language formatting and ordering primitives.
 *
 * Details strings are diffed byte for byte against the Python implementation,
 * so the printf conversions used there have to agree exactly. The three places
 * JavaScript would differ on its own:
 *
 *  1. Python's "%d" converts through int(), which truncates toward zero.
 *     Math.round would disagree on any fractional duration.
 *  2. Python's "%f" and "%g" round a tie to even. Number.prototype.toFixed
 *     rounds a tie away from zero, so 3.25 would print as 3.3, not 3.2.
 *  3. Python's "%g" is six significant digits with trailing zeros stripped,
 *     not a plain decimal rendering.
 */

/** Increment a bare digit string by one, growing it when it is all nines. */
function increment(digits: string): string {
  const out = digits.split('');
  let i = out.length - 1;
  while (i >= 0) {
    if (out[i] === '9') {
      out[i] = '0';
      i -= 1;
    } else {
      out[i] = String(Number(out[i]) + 1);
      break;
    }
  }
  return i < 0 ? `1${out.join('')}` : out.join('');
}

/** Python "%d". */
export function pyD(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  const truncated = Math.trunc(value);
  if (Math.abs(truncated) >= 1e21) return BigInt(truncated).toString();
  return String(truncated === 0 ? 0 : truncated);
}

/** Python "%.<digits>f", including its round half to even behaviour. */
export function pyF(value: number, digits: number): string {
  if (Number.isNaN(value)) return 'nan';
  if (!Number.isFinite(value)) return value > 0 ? 'inf' : '-inf';
  const negative = value < 0 || Object.is(value, -0);
  const abs = Math.abs(value);
  const fraction = digits > 0 ? `.${'0'.repeat(digits)}` : '';
  if (abs >= 1e21) return `${negative ? '-' : ''}${BigInt(abs).toString()}${fraction}`;

  // toFixed is correctly rounded, so asking for extra places exposes whether
  // the discarded tail is an exact tie.
  const expanded = abs.toFixed(Math.min(100, digits + 25));
  const dot = expanded.indexOf('.');
  const intPart = dot === -1 ? expanded : expanded.slice(0, dot);
  const frac = dot === -1 ? '' : expanded.slice(dot + 1);
  const rest = frac.slice(digits);
  let whole = intPart + frac.slice(0, digits);

  const half = `5${'0'.repeat(Math.max(0, rest.length - 1))}`;
  let roundUp = false;
  if (rest > half) roundUp = true;
  else if (rest === half) roundUp = Number(whole[whole.length - 1]) % 2 === 1;
  if (roundUp) whole = increment(whole);

  const intOut = digits > 0 ? whole.slice(0, whole.length - digits) : whole;
  const fracOut = digits > 0 ? `.${whole.slice(whole.length - digits)}` : '';
  return `${negative ? '-' : ''}${intOut || '0'}${fracOut}`;
}

function stripTrailing(text: string): string {
  if (!text.includes('.')) return text;
  return text.replace(/0+$/, '').replace(/\.$/, '');
}

/** Python "%g": six significant digits, trailing zeros stripped. */
export function pyG(value: number, precision = 6): string {
  if (Number.isNaN(value)) return 'nan';
  if (!Number.isFinite(value)) return value > 0 ? 'inf' : '-inf';
  const p = precision === 0 ? 1 : precision;
  if (value === 0) return '0';

  const scientific = value.toExponential(p - 1);
  const exponent = Number(scientific.slice(scientific.indexOf('e') + 1));
  if (exponent < -4 || exponent >= p) {
    const mantissa = stripTrailing(scientific.slice(0, scientific.indexOf('e')));
    const sign = exponent < 0 ? '-' : '+';
    return `${mantissa}e${sign}${String(Math.abs(exponent)).padStart(2, '0')}`;
  }
  return stripTrailing(pyF(value, p - 1 - exponent));
}

/**
 * Compare two strings by Unicode code point, matching Python's ordering.
 * Plain `<` compares UTF-16 code units, which orders an astral character
 * before U+E000..U+FFFF and would break a tie differently.
 */
export function comparePython(a: string, b: string): number {
  const ca = Array.from(a);
  const cb = Array.from(b);
  const len = Math.min(ca.length, cb.length);
  for (let i = 0; i < len; i += 1) {
    const pa = ca[i]!.codePointAt(0)!;
    const pb = cb[i]!.codePointAt(0)!;
    if (pa !== pb) return pa < pb ? -1 : 1;
  }
  return ca.length === cb.length ? 0 : ca.length < cb.length ? -1 : 1;
}
