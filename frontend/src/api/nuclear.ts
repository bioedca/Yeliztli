/** React Query hook for Nuclear Delete (P4-21). */

import { throwApiError } from "@/api/errors"

import { useMutation, useQueryClient } from "@tanstack/react-query"

export interface NuclearDeleteResponse {
  deleted: boolean
  message: string
}

export function useNuclearDelete() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (): Promise<NuclearDeleteResponse> => {
      const res = await fetch("/api/data/nuclear", { method: "DELETE" })
      if (!res.ok) {
        await throwApiError(res, "Nuclear delete failed")
      }
      return await res.json()
    },
    onSuccess: () => {
      queryClient.clear()
      // Redirect to setup wizard — app is in fresh-install state
      window.location.href = "/"
    },
  })
}
