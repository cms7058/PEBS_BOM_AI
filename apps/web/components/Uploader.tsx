'use client'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { uploadCad, uploadSpreadsheet } from '@/lib/api'

type Mode = 'spreadsheet' | 'cad'

const SPREADSHEET_EXTS = ['xlsx', 'xls', 'xlsm', 'csv']
const CAD_EXTS = ['step', 'stp', 'stpz', 'iges', 'igs']

export default function Uploader({ mode = 'spreadsheet' }: { mode?: Mode }) {
  const router = useRouter()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true)
    setErr(null)
    try {
      const { bom_id } =
        mode === 'cad' ? await uploadCad(file) : await uploadSpreadsheet(file)
      router.push(`/bom/${bom_id}`)
    } catch (ex: any) {
      setErr(ex?.message || String(ex))
    } finally {
      setBusy(false)
    }
  }

  const accept =
    mode === 'cad'
      ? CAD_EXTS.map((e) => '.' + e).join(',')
      : SPREADSHEET_EXTS.map((e) => '.' + e).join(',')

  const busyMsg =
    mode === 'cad'
      ? '正在解析 CAD 装配树（STEP / IGES）...'
      : '正在解析并规范化 (LLM 调用中)...'

  return (
    <div>
      <input type="file" accept={accept} onChange={onPick} disabled={busy} />
      {busy && <p style={{ color: '#1677ff' }}>{busyMsg}</p>}
      {err && <p style={{ color: '#d93025' }}>错误: {err}</p>}
    </div>
  )
}
