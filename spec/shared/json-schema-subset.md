# The JSON Schema subset

The two schema checks, `core.tool_arg_schema` and `core.output_schema`, validate
data against a user supplied JSON Schema. Both language implementations must
return byte identical results, so both validate against the **same** definition
of what a schema means. That definition is here, not in an external library.

The Python build used to depend on the `jsonschema` package and the TypeScript
build shipped no validator at all, so the two could not be proven to agree on a
validation verdict. This subset replaces that: one specification, ported to both
languages line for line, checked by the conformance suite.

## Why a subset

A full draft 2020-12 validator is thousands of lines and its behaviour on the
harder keywords (`$ref` resolution, `anyOf`/`oneOf` error selection, `if`/`then`)
differs between implementations in ways that are legal but not identical. Two
non identical validators produce two different verdicts on the same edge, which
is the one thing this project exists to prevent.

So this implements the keywords that have a single obvious meaning, and
**refuses** any schema that uses a keyword outside that set. A refusal is a skip
that names the keyword. Silently ignoring an unsupported keyword would validate
against a weaker schema than the author wrote and report a pass that checked
less than they asked for; that is the failure mode this whole tool is built to
avoid, so it is never allowed to happen quietly.

## Supported keywords

`type` (`null`, `boolean`, `integer`, `number`, `string`, `array`, `object`;
also a list of those), `enum`, `const`, `minimum`, `maximum`,
`exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, `minLength`, `maxLength`,
`pattern`, `items` (a single schema applied to every element), `minItems`,
`maxItems`, `uniqueItems`, `required`, `minProperties`, `maxProperties`,
`properties`, `additionalProperties` (`false` or a schema).

Annotation only keywords are accepted and ignored: `$schema`, `$id`, `$anchor`,
`$comment`, `$defs`, `definitions`, `title`, `description`, `default`,
`examples`, `readOnly`, `writeOnly`, `deprecated`, `format`.

## Refused, with the reason named

- **Any `$ref`.** A remote `$ref` is refused because resolving it would fetch a
  URL chosen by config, from inside CI, on every run: a request forgery
  primitive in a library documented to make no network calls outside the seam. A
  local `#/...` `$ref` is refused because this validator does not resolve
  references at all; inline the definition instead. Refusing both keeps the rule
  simple and the failure mode impossible.
- **A different draft.** A schema declaring a `$schema` that is not 2020-12 is
  refused rather than reinterpreted under rules its author did not write.
- **`items` as an array** (tuple validation). Per index subschemas are not
  supported, and validating only the first would be a silent gap.
- **An unsafe or unportable `pattern`.** Patterns go through the same guard as
  every other regex in the tool, so a catastrophic backtracker cannot hang a
  run, and the portable subset is enforced: a Python only spelling such as an
  inline flag `(?i)` or a named group compiles in one engine and throws in the
  other, splitting the verdict, so it is refused by name instead.
- **Any other assertion keyword** (`anyOf`, `oneOf`, `allOf`, `not`, `if`,
  `then`, `else`, `prefixItems`, `patternProperties`, `propertyNames`,
  `contains`, `dependentSchemas`, `dependentRequired`, ...). Each has real
  cross implementation divergence; each is refused by name.

## The cross language traps this pins

- **String length is counted in code points**, not UTF-16 code units, so a
  string containing an astral character measures the same in both languages.
- **`integer` accepts an integral number.** `1.0` satisfies `type: integer`, and
  a number's reported JSON type is `integer` when it has no fractional part, so
  the two languages agree even though one parses `1.0` as a float and the other
  as `1`.
- **A boolean is never a number.** `true` does not satisfy `type: integer` or
  `type: number`, matching the JSON data model rather than either language's
  habit of treating booleans as 1 and 0.
- **Equality for `enum` and `const` and `uniqueItems`** is the canonical form,
  the same one used for signatures, so `1` equals `1.0` and `{a:1,b:2}` equals
  `{b:2,a:1}` identically in both languages.
- **`pattern` runs under ASCII class semantics in both languages.** JavaScript's
  `\d`, `\w`, `\s` and `\b` are ASCII only; Python's are Unicode aware by
  default, so the same pattern over the same value could pass in one and fail in
  the other with no error anywhere. Python therefore compiles schema patterns
  with `re.ASCII`, which is also what the JSON Schema spec prescribes: pattern
  is an ECMA-262 regex.
- **Only normalized messages are emitted.** The raw wording never appears; each
  keyword maps to one fixed short phrase, and offending values are never
  included because they can be sensitive.

## Error selection

Every error carries a JSON pointer to where it occurred and the keyword that
failed. Errors are sorted by `(pointer, keyword)` so the first, and any first N,
are the same in both languages regardless of the order they were produced in.
`core.tool_arg_schema` reports the first error per span; `core.output_schema`
reports up to five.
