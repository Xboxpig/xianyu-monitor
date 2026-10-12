<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { http } from '@/lib/http'
import { useAuth } from '@/composables/useAuth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

interface BrowserStatus {
  backend: string
  connected: boolean
  clients: { client_id: string; name: string; account_state_file?: string; online: boolean }[]
  runs: { run_id: string; task_name: string; state: string; message: string; page: number }[]
}
const { t } = useI18n()
const { username } = useAuth()
const status = ref<BrowserStatus | null>(null)
const password = ref('')
const token = ref('')
const error = ref('')
const statusError = ref('')
const working = ref(false)
let timer: ReturnType<typeof setInterval> | undefined
let mounted = false
async function refresh() {
  try {
    status.value = await http('/api/browser/status')
    statusError.value = ''
  } catch (e) {
    statusError.value = (e as Error).message
  }
}
async function setup(launch = false) {
  working.value = true
  error.value = ''
  try {
    const response = await fetch('/api/browser/setup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value || '', password: password.value, launch }),
    })
    const result = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`)
    token.value = result.token
    password.value = ''
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    working.value = false
  }
}
onMounted(async () => {
  mounted = true
  await refresh()
  if (mounted) timer = setInterval(() => void refresh(), 5000)
})
onUnmounted(() => {
  mounted = false
  clearInterval(timer)
  token.value = ''
  password.value = ''
})
</script>

<template>
  <Card>
    <CardHeader>
      <CardTitle>{{ t('nativeBrowser.title') }}</CardTitle>
      <CardDescription>{{ t('nativeBrowser.description') }}</CardDescription>
    </CardHeader>
    <CardContent class="space-y-5">
      <p v-if="error" role="alert" class="app-alert-error">{{ error }}</p>
      <p v-if="statusError" role="alert" class="app-alert-error">{{ statusError }}</p>
      <p v-if="status && status.backend !== 'native_chrome'">{{ t('nativeBrowser.requiresNative') }}</p>
      <template v-else>
        <p class="font-semibold" :class="status?.connected ? 'text-emerald-600' : 'text-slate-500'">
          {{ t(status?.connected ? 'nativeBrowser.connected' : 'nativeBrowser.disconnected') }}
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm text-slate-600">
          <li>{{ t('nativeBrowser.install') }}</li>
          <li>{{ t('nativeBrowser.pair') }}</li>
          <li>{{ t('nativeBrowser.login') }}</li>
        </ol>
        <div class="space-y-3 rounded-xl border border-slate-200 p-4">
          <label for="native-admin-password" class="block text-sm font-semibold">{{ t('nativeBrowser.password') }}</label>
          <Input id="native-admin-password" v-model="password" type="password" autocomplete="current-password" />
          <div class="flex flex-wrap gap-3">
            <Button :disabled="working || !password" @click="setup()">{{ t('nativeBrowser.showToken') }}</Button>
            <Button variant="outline" :disabled="working || !password" @click="setup(true)">{{ t('nativeBrowser.openChrome') }}</Button>
          </div>
          <div v-if="token" class="space-y-2">
            <label for="native-pair-token" class="text-sm">{{ t('nativeBrowser.token') }}</label>
            <Input id="native-pair-token" :model-value="token" readonly class="font-mono" @focus="($event.target as HTMLInputElement).select()" />
            <Button variant="ghost" size="sm" @click="token = ''">{{ t('nativeBrowser.hideToken') }}</Button>
          </div>
        </div>
        <div v-if="status?.clients.length" class="space-y-2">
          <div v-for="client in status.clients" :key="client.client_id" class="flex flex-wrap justify-between gap-2 rounded-xl bg-slate-50 p-3 text-sm">
            <span>{{ client.name }} <span class="text-slate-500">{{ client.account_state_file }}</span></span>
            <span>{{ t(client.online ? 'nativeBrowser.online' : 'nativeBrowser.offline') }}</span>
          </div>
        </div>
        <div v-for="run in status?.runs || []" :key="run.run_id" class="rounded-xl border border-blue-100 bg-blue-50 p-3 text-sm">
          <p class="font-semibold">{{ run.task_name }} · {{ t('nativeBrowser.page', { page: run.page }) }}</p>
          <p>{{ run.message || t(run.state === 'analyzing' ? 'nativeBrowser.analyzing' : 'nativeBrowser.collecting') }}</p>
        </div>
      </template>
    </CardContent>
  </Card>
</template>
