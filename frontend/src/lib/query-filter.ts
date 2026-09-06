/** Wire-shape mapping for Query Builder filters (issues #2055 / #2059).
 *
 * The backend translator requires an array value for the multi-value operators
 * (`between` needs exactly two elements; `in` / `notIn` need a list), while
 * react-querybuilder's default editors store a comma-joined string ("0.1,0.5").
 * The builder runs with `listsAsArrays`, which makes the two-input `between`
 * editor store an array; the single text editor used for `in` / `notIn` still
 * stores one string. `normalizeFilterForApi` therefore splits any string it
 * meets — fresh `in` / `notIn` input, saved queries, restored drafts — with the
 * library's own escape-aware list syntax, so every request sends the typed
 * contract. `toEditorFilter` is the inverse for a stored filter entering the
 * builder: an `in` / `notIn` array is joined back with the same escaping, so a
 * later edit round-trips without re-splitting a value that legitimately
 * contains a comma.
 */

import { joinWith, toArray } from "react-querybuilder"

import type { RuleGroupModel, RuleModel } from "@/types/query-builder"

/** Operators whose value the backend validates as an array. */
const MULTI_VALUE_OPERATORS = new Set(["between", "in", "notIn"])

function isRuleGroup(node: RuleGroupModel | RuleModel): node is RuleGroupModel {
  return Array.isArray((node as RuleGroupModel).rules)
}

/** Split a comma-joined list with react-querybuilder's own list syntax.
 *
 * `toArray` trims tokens and honours the library's escape for a literal comma
 * (`Alzheimer\, late onset,Parkinson` → `["Alzheimer, late onset", "Parkinson"]`),
 * so condition and disease names survive the round trip. `between` keeps empty
 * tokens so a half-filled pair still reaches the backend's "requires a
 * two-element array" validation instead of being silently truncated; `in` /
 * `notIn` drop them (a trailing comma is not a value).
 */
function splitListValue(operator: string, value: string): unknown[] {
  return toArray(value, { retainEmptyStrings: operator === "between" })
}

function normalizeRule(rule: RuleModel): RuleModel {
  if (!MULTI_VALUE_OPERATORS.has(rule.operator) || typeof rule.value !== "string") {
    return rule
  }
  return { ...rule, value: splitListValue(rule.operator, rule.value) }
}

/** Return a copy of `filter` whose multi-value rules carry array values.
 *
 * Array values, single-value operators, and rule/group metadata are left
 * untouched; the input is never mutated.
 */
export function normalizeFilterForApi(filter: RuleGroupModel): RuleGroupModel {
  return {
    ...filter,
    rules: filter.rules.map((node) =>
      isRuleGroup(node) ? normalizeFilterForApi(node) : normalizeRule(node),
    ),
  }
}

function editorRule(rule: RuleModel): RuleModel {
  if (rule.operator === "between" || !MULTI_VALUE_OPERATORS.has(rule.operator)) {
    return rule
  }
  if (!Array.isArray(rule.value)) {
    return rule
  }
  return { ...rule, value: joinWith(rule.value.map(String), ",") }
}

/** Return a copy of `filter` shaped for react-querybuilder's editors.
 *
 * `in` / `notIn` arrays become the editor's escaped list string (a literal
 * comma inside a value is written as `\,`, which `normalizeFilterForApi`
 * reads back); `between` arrays are left alone because the two-input editor
 * takes an array under `listsAsArrays`. Everything else is untouched.
 */
export function toEditorFilter(filter: RuleGroupModel): RuleGroupModel {
  return {
    ...filter,
    rules: filter.rules.map((node) => (isRuleGroup(node) ? toEditorFilter(node) : editorRule(node))),
  }
}
