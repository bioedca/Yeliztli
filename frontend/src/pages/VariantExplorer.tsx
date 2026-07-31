import { useSearchParams } from "react-router-dom"
import VariantTable from "@/components/variant-table/VariantTable"

export default function VariantExplorer() {
  const [searchParams] = useSearchParams()
  const rawId = searchParams.get("sample_id")
  const parsed = rawId ? Number(rawId) : NaN
  const sampleId = Number.isFinite(parsed) ? parsed : null

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-4 py-3">
        <h1 className="text-xl font-semibold">Variant Explorer</h1>
      </div>
      <div className="flex min-h-0 flex-1">
        <VariantTable sampleId={sampleId} />
      </div>
    </div>
  )
}
