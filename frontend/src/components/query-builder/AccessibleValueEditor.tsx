/**
 * Value editor that gives each bound of a ``between`` rule an accessible name (#2378).
 *
 * react-querybuilder's default ``ValueEditor`` renders the two ``between`` /
 * ``notBetween`` editors with no label, title or placeholder of their own -- the
 * ``title`` lands on the wrapping ``<span>``, which names neither child -- so a
 * screen-reader user hears two unnamed fields and cannot tell the lower bound
 * from the upper one (axe ``label``, critical). This renders the same two
 * controls through the library's own ``useValueEditor`` hook, so value handling
 * (``listsAsArrays``, number coercion, the second bound's seeding) is unchanged,
 * and names each one. Every other operator falls through to the default editor,
 * whose single control already carries ``title``.
 */

import {
  ValueEditor,
  getFirstOption,
  useValueEditor,
  type ValueEditorProps,
} from "react-querybuilder"

const BOUND_LABELS = ["Lower bound", "Upper bound"] as const

function boundLabel(props: ValueEditorProps, index: number): string {
  const field = props.fieldData?.label ?? props.field
  return `${field} ${BOUND_LABELS[index].toLowerCase()}`.replace(/^./, (c) => c.toUpperCase())
}

function BetweenValueEditor(props: ValueEditorProps) {
  const {
    type = "text",
    values = [],
    className,
    title,
    disabled,
    separator = null,
    testID,
    listsAsArrays,
    schema,
  } = props
  const { valueAsArray, multiValueHandler, valueListItemClassName, inputTypeCoerced } =
    useValueEditor(props)
  const SelectorComponent = props.selectorComponent ?? schema.controls.valueSelector
  const placeholder = props.fieldData?.placeholder ?? ""

  const editors = [0, 1].map((index) => {
    const label = boundLabel(props, index)
    if (type === "text") {
      return (
        <input
          key={index}
          type={inputTypeCoerced}
          placeholder={placeholder}
          value={valueAsArray[index] ?? ""}
          className={valueListItemClassName}
          disabled={disabled}
          aria-label={label}
          onChange={(e) => multiValueHandler(e.target.value, index)}
        />
      )
    }
    return (
      <SelectorComponent
        key={index}
        {...props}
        schema={schema}
        className={valueListItemClassName}
        title={label}
        handleOnChange={(v: string) => multiValueHandler(v, index)}
        disabled={disabled}
        value={valueAsArray[index] ?? getFirstOption(values)}
        options={values}
        listsAsArrays={listsAsArrays}
      />
    )
  })

  return (
    <span data-testid={testID} className={className} title={title}>
      {editors[0]}
      {separator}
      {editors[1]}
    </span>
  )
}

export default function AccessibleValueEditor(props: ValueEditorProps) {
  const isBetween = props.operator === "between" || props.operator === "notBetween"
  const type = props.type ?? "text"
  if (isBetween && (type === "text" || type === "select")) {
    return <BetweenValueEditor {...props} />
  }
  return <ValueEditor {...props} />
}
