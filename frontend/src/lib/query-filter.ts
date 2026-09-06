/** Wire-shape normalisation for Query Builder filters (issues #2055 / #2059).
 *
 * The backend translator requires an array value for the multi-value operators
 * (`between` needs exactly two elements; `in` / `notIn` need a list), while
 * react-querybuilder's default value editor historically stored those values as
 * a comma-joined string ("0.1,0.5"). The builder now runs with `listsAsArrays`,
 * so fresh rules already carry arrays; this pass bridges anything that still
 * carries the legacy string shape — saved queries, restored drafts, or a value
 * typed before the option existed — so every request sends the typed contract.
 */

import type { RuleGroupModel, RuleModel } from "@/types/query-builder"

/** Operators whose value the backend validates as an array. */
const MULTI_VALUE_OPERATORS = new Set(["between", "in", "notIn"])

function isRuleGroup(node: RuleGroupModel | RuleModel): node is RuleGroupModel {
  return Array.isArray((node as RuleGroupModel).rules)
}

/** Split a comma-joined list into trimmed tokens.
 *
 * `between` keeps every token so a malformed pair still reaches the backend's
 * "requires a two-element array" validation instead of being silently padded or
 * truncated; `in` / `notIn` drop empty tokens (a trailing comma is not a value).
 */
function splitListValue(operator: string, value: string): string[] {
  const tokens = value.split(",").map((token) => token.trim())
  return operator === "between" ? tokens : tokens.filter((token) => token !== "")
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
