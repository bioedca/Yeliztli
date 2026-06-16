/** React Query hooks for variant tagging API (P4-12b). */

import { useQuery } from "@tanstack/react-query"
import type { Tag } from "@/types/variants"

/** List all tags for a sample */
export function useTags(sampleId: number | null) {
  return useQuery({
    queryKey: ["tags", sampleId],
    queryFn: async (): Promise<Tag[]> => {
      const res = await fetch(`/api/tags?sample_id=${sampleId}`)
      if (!res.ok) throw new Error("Failed to fetch tags")
      return res.json()
    },
    enabled: sampleId != null,
    staleTime: 0, // Tags change frequently
  })
}
