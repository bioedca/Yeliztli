/** Query builder panel using react-querybuilder (P4-02).
 *
 * Wraps the react-querybuilder component with field metadata from
 * GET /api/query/fields and custom styling matching the app's design.
 *
 * ``listsAsArrays`` makes the multi-value operators (``between``, ``in``,
 * ``notIn``) store their value as an array rather than the library's default
 * comma-joined string, which is the shape the backend translator requires
 * (issues #2055 / #2059). Legacy string values that reach the API layer from
 * saved queries or restored drafts are bridged by ``normalizeFilterForApi``.
 */

import { useMemo } from "react"
import { QueryBuilder, type RuleGroupType, type Field, type ValueEditorType } from "react-querybuilder"
import "react-querybuilder/dist/query-builder.css"
import type { QueryFieldInfo } from "@/types/query-builder"

/** Map backend field types to react-querybuilder inputType. */
function fieldToInputType(type: string): string | undefined {
  switch (type) {
    case "integer":
    case "number":
      return "number"
    case "boolean":
      return "text"
    default:
      return "text"
  }
}

/** Map backend field types to react-querybuilder valueEditorType. */
function fieldToValueEditorType(type: string): ValueEditorType | undefined {
  if (type === "boolean") return "select"
  return undefined
}

/** Build values list for boolean fields. */
function fieldValues(type: string): Array<{ name: string; label: string }> | undefined {
  if (type === "boolean") {
    return [
      { name: "true", label: "True" },
      { name: "false", label: "False" },
    ]
  }
  return undefined
}

/** Map backend operator names to react-querybuilder operator format. */
const OPERATOR_MAP: Record<string, { name: string; label: string }> = {
  "=": { name: "=", label: "=" },
  "!=": { name: "!=", label: "!=" },
  "<": { name: "<", label: "<" },
  ">": { name: ">", label: ">" },
  "<=": { name: "<=", label: "<=" },
  ">=": { name: ">=", label: ">=" },
  contains: { name: "contains", label: "contains" },
  beginsWith: { name: "beginsWith", label: "begins with" },
  endsWith: { name: "endsWith", label: "ends with" },
  in: { name: "in", label: "in" },
  notIn: { name: "notIn", label: "not in" },
  between: { name: "between", label: "between" },
  null: { name: "null", label: "is null" },
  notNull: { name: "notNull", label: "is not null" },
}

/** Get operators for a given field type. */
function getOperatorsForType(type: string) {
  const common = ["=", "!=", "null", "notNull"]
  if (type === "integer" || type === "number") {
    return [...common, "<", ">", "<=", ">=", "between"]
  }
  if (type === "boolean") {
    return ["=", "!=", "null", "notNull"]
  }
  // text
  return [...common, "contains", "beginsWith", "endsWith", "in", "notIn"]
}

interface QueryBuilderPanelProps {
  fields: QueryFieldInfo[]
  query: RuleGroupType
  onQueryChange: (query: RuleGroupType) => void
}

export default function QueryBuilderPanel({
  fields: fieldMeta,
  query,
  onQueryChange,
}: QueryBuilderPanelProps) {
  const fields: Field[] = useMemo(
    () =>
      fieldMeta.map((f) => ({
        name: f.name,
        label: f.label,
        inputType: fieldToInputType(f.type),
        valueEditorType: fieldToValueEditorType(f.type),
        values: fieldValues(f.type),
        operators: getOperatorsForType(f.type)
          .map((op) => OPERATOR_MAP[op])
          .filter(Boolean),
      })),
    [fieldMeta],
  )

  return (
    <div className="query-builder-panel" data-testid="query-builder-panel">
      <QueryBuilder
        fields={fields}
        query={query}
        onQueryChange={onQueryChange}
        controlClassnames={{
          queryBuilder:
            "rounded-lg border border-border bg-card p-4 [&_.ruleGroup]:border [&_.ruleGroup]:border-border/50 [&_.ruleGroup]:rounded-md [&_.ruleGroup]:p-3 [&_.ruleGroup]:my-2 [&_.ruleGroup]:bg-muted/30",
          header: "flex flex-wrap items-center gap-2 mb-2",
          body: "flex flex-col gap-1",
          rule: "flex flex-wrap items-center gap-2 py-1",
          combinators:
            "rounded-md border border-input bg-background px-2 py-1.5 text-sm font-medium",
          fields:
            "rounded-md border border-input bg-background px-2 py-1.5 text-sm max-w-[200px]",
          operators:
            "rounded-md border border-input bg-background px-2 py-1.5 text-sm",
          value:
            "rounded-md border border-input bg-background px-2 py-1.5 text-sm flex-1 min-w-[120px]",
          addRule:
            "rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-xs font-medium hover:bg-primary/90 transition-colors",
          addGroup:
            "rounded-md bg-secondary text-secondary-foreground px-3 py-1.5 text-xs font-medium hover:bg-secondary/80 transition-colors",
          removeRule:
            "rounded-md text-destructive hover:bg-destructive/10 px-2 py-1 text-xs transition-colors",
          removeGroup:
            "rounded-md text-destructive hover:bg-destructive/10 px-2 py-1 text-xs transition-colors",
        }}
        addRuleToNewGroups
        showNotToggle
        listsAsArrays
      />
    </div>
  )
}
