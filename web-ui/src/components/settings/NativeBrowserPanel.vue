<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { http } from '@/lib/http'
import { useBrowserStatus } from '@/composables/useBrowserStatus'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

const { t } = useI18n()
const { status, statusError, refresh } = useBrowserStatus()
const profiles = ref<{ directory: string; name: string; email: string }[]>([])
const selectedProfile = ref('')
const token = ref('')
const error = ref('')
const working = ref(false)
let autoShown = false
watch(status, (value) => {
  if (value?.backend === 'native_chrome' && !value.connected && !autoShown) {
    autoShown = true
    void setup()
  }
}, { immediate: true })
async function setup(launch = false) {
  working.value = true
  error.value = ''
  try {
    const response = await fetch('/api/browser/setup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ launch, profile_directory: selectedProfile.value || null }),
    })
    const result = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`)
    token.value = result.token
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    working.value = false
  }
}
onMounted(async () => {
  await refresh()
  if (status.value?.backend === 'native_chrome') {
    try { profiles.value = (await http('/api/browser/profiles')).profiles }
    catch (e) { error.value = (e as Error).message }
  }
})
onUnmounted(() => {
  token.value = ''
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
          <label for="native-chrome-account" class="block text-sm font-semibold">{{ t('nativeBrowser.selectAccount') }}</label>
          <select id="native-chrome-account" v-model="selectedProfile" class="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
            <option value="">{{ t('nativeBrowser.defaultAccount') }}</option>
            <option v-for="profile in profiles" :key="profile.directory" :value="profile.directory">{{ profile.name }}{{ profile.email ? ' · ' + profile.email : '' }}</option>
          </select>
          <div class="flex flex-wrap gap-3">
            <Button :disabled="working" @click="setup()">{{ t('nativeBrowser.showToken') }}</Button>
            <Button variant="outline" :disabled="working" @click="setup(true)">{{ t('nativeBrowser.openChrome') }}</Button>
          </div>
          <div v-if="token" class="space-y-2">
            <label for="native-pair-token" class="text-sm">{{ t('nativeBrowser.token') }}</label>
            <Input id="native-pair-token" :model-value="token" readonly class="font-mono" @focus="($event.target as HTMLInputElement).select()" />
            <Button variant="ghost" size="sm" @click="token = ''">{{ t('nativeBrowser.hideToken') }}</Button>
          </div>
        </div>
        <div v-if="status?.clients.length" class="space-y-2">
          <div v-for="client in status.clients" :key="client.client_id" class="flex flex-wrap justify-between gap-2 rounded-xl bg-slate-50 p-3 text-sm">
            <span>{{ client.name }} <span class="text-slate-500">{{ client.profile_directory }}</span></span>
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
