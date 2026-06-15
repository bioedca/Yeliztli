/** React Query hooks for column preset CRUD (P1-15c). */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import type { ColumnPreset } from "@/types/variants"

export function useColumnPresets() {
  return useQuery({
    queryKey: ["column-presets"],
    queryFn: async (): Promise<ColumnPreset[]> => {
      const res = await fetch("/api/column-presets")
      if (!res.ok) throw new Error("Failed to fetch column presets")
      const data = await res.json()
      return data.presets
    },
    staleTime: Infinity,
  })
}

export function useCreatePreset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: { name: string; columns: string[] }) => {
      const res = await fetch("/api/column-presets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(text || "Failed to create preset")
      }
      return res.json()
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["column-presets"] }),
  })
}

export function useDeletePreset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(`/api/column-presets/${encodeURIComponent(name)}`, {
        method: "DELETE",
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(text || "Failed to delete preset")
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["column-presets"] }),
  })
}
