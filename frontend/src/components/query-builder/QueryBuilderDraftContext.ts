import { createContext, type Dispatch, type SetStateAction } from "react"
import type { RuleGroupType } from "react-querybuilder"

export function createEmptyQuery(): RuleGroupType {
  return { combinator: "and", rules: [] }
}

export interface QueryBuilderDraftState {
  query: RuleGroupType
  setQuery: Dispatch<SetStateAction<RuleGroupType>>
  resetQuery: () => void
}

export const QueryBuilderDraftContext = createContext<QueryBuilderDraftState | null>(null)
