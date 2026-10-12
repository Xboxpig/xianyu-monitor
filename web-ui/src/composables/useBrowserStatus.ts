import { onMounted, onUnmounted, ref } from 'vue'
import { http } from '@/lib/http'

export interface BrowserStatus {
  backend: string
  connected: boolean
  clients: { client_id: string; name: string; account_state_file?: string; profile_directory?: string; online: boolean }[]
  runs: { run_id: string; task_name: string; state: string; message: string; page: number }[]
}

const status = ref<BrowserStatus | null>(null)
const statusError = ref('')
let subscribers = 0
let timer: ReturnType<typeof setInterval> | undefined
let pending: Promise<void> | undefined

async function refresh() {
  if (pending) return pending
  pending = (async () => {
    try {
      status.value = await http('/api/browser/status')
      statusError.value = ''
    } catch (error) {
      statusError.value = (error as Error).message
    }
  })()
  try { await pending } finally { pending = undefined }
}

export function useBrowserStatus() {
  onMounted(() => {
    if (subscribers++ === 0) timer = setInterval(() => void refresh(), 5000)
    void refresh()
  })
  onUnmounted(() => {
    if (--subscribers === 0) {
      clearInterval(timer)
      timer = undefined
    }
  })
  return { status, statusError, refresh }
}
