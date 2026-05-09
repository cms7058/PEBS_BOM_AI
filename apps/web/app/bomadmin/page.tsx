import LoginForm from '@/components/LoginForm'

export const dynamic = 'force-dynamic'

export default function BOMAdminPage() {
  return <LoginForm forcedPlan="admin" />
}
