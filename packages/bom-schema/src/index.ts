export interface BOMNode {
  id: string
  parent_id: string | null
  level: number
  part_number: string | null
  part_name: string
  description: string | null
  quantity: number
  uom: string
  material: string | null
  weight: number | null
  supplier: string | null
  unit_cost: number | null
  notes: string | null
  style: Record<string, unknown>
  source_ref: Record<string, unknown> | null
  confidence: number
  sort_order: number
}

export interface BOM {
  id: string
  name: string
  source_file_id: string | null
  nodes: BOMNode[]
}
