<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bot, Play, Square } from 'lucide-vue-next'
import { useLogs } from '@/composables/useLogs'
import { useTasks } from '@/composables/useTasks'
import {
  cancelAiAnalysis,
  getAiAnalysisStatus,
  retryFailedAiAnalysis,
  type AiAnalysisStatus,
} from '@/api/tasks'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { toast } from '@/components/ui/toast'

const { t } = useI18n()
const { tasks } = useTasks()
const { logs, isAutoRefresh, clearLogs, toggleAutoRefresh, fetchLogs, setTaskId, loadLatest, loadPrevious, isFetchingHistory, hasMoreHistory } = useLogs()
const logContainer = ref<HTMLElement | null>(null)
const autoScroll = ref(true)
const isClearDialogOpen = ref(false)
const selectedTaskId = ref('')
const isPrepending = ref(false)
const lastScrollTop = ref(0)
const lastScrollHeight = ref(0)
const aiStatus = ref<AiAnalysisStatus>({ active: false, active_count: 0, mode: null })
const isAiActionPending = ref(false)
let aiStatusInterval: number | null = null

const selectedTask = computed(() => (
  tasks.value.find((task) => String(task.id) === selectedTaskId.value) || null
))
const isAiRunning = computed(() => aiStatus.value.active)
const aiProgress = computed(() => {
  const progress = aiStatus.value.progress
  const total = Number(progress?.total || 0)
  if (aiStatus.value.mode === 'recovery' && total > 0) {
    return Math.min(100, ((Number(progress?.completed || 0) + Number(progress?.failed || 0)) / total) * 100)
  }
  const attempt = Number(aiStatus.value.attempt || 1)
  const maxAttempts = Number(aiStatus.value.max_attempts || 10)
  return Math.min(100, Math.max(8, (attempt / maxAttempts) * 100))
})
const aiRingOffset = computed(() => 207.35 * (1 - aiProgress.value / 100))
const aiActionTitle = computed(() => (
  isAiRunning.value ? t('logs.aiRunning') : t('logs.aiIdle')
))

// Auto-scroll logic
watch(logs, async () => {
  if (isPrepending.value) {
    await nextTick()
    if (logContainer.value) {
      const delta = logContainer.value.scrollHeight - lastScrollHeight.value
      logContainer.value.scrollTop = lastScrollTop.value + delta
    }
    isPrepending.value = false
    return
  }
  if (autoScroll.value) {
    await nextTick()
    scrollToBottom()
  }
})

watch(tasks, (list) => {
  if (!list.length) {
    selectedTaskId.value = ''
    setTaskId(null)
    return
  }
  if (selectedTaskId.value && list.some((task) => String(task.id) === selectedTaskId.value)) {
    return
  }
  const running = list.find((task) => task.is_running)
  const fallback = list[0]
  if (!fallback) {
    selectedTaskId.value = ''
    setTaskId(null)
    return
  }
  selectedTaskId.value = String(running ? running.id : fallback.id)
}, { immediate: true })

watch(selectedTaskId, (taskId) => {
  const resolvedTaskId = taskId ? Number(taskId) : null
  setTaskId(resolvedTaskId)
  if (resolvedTaskId) {
    loadLatest(50)
    refreshAiStatus()
  } else {
    aiStatus.value = { active: false, active_count: 0, mode: null }
  }
})

async function refreshAiStatus() {
  const taskId = Number(selectedTaskId.value)
  if (!taskId) return
  try {
    aiStatus.value = await getAiAnalysisStatus(taskId)
  } catch {
    // The log poll already surfaces connectivity problems; keep this control quiet.
  }
}

async function handleAiAction() {
  const taskId = Number(selectedTaskId.value)
  if (!taskId || isAiActionPending.value) return
  isAiActionPending.value = true
  try {
    if (isAiRunning.value) {
      aiStatus.value = await cancelAiAnalysis(taskId)
      toast({ title: t('logs.aiCancelled') })
    } else {
      const status = await retryFailedAiAnalysis(taskId)
      aiStatus.value = status
      if (Number(status.progress?.total || 0) > 0) {
        toast({ title: t('logs.aiStarted') })
      } else {
        toast({ title: t('logs.aiNothingToRetry') })
      }
    }
  } catch (e) {
    toast({
      title: t('logs.aiActionFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  } finally {
    isAiActionPending.value = false
    await refreshAiStatus()
  }
}

onMounted(() => {
  aiStatusInterval = window.setInterval(refreshAiStatus, 1000)
})

onUnmounted(() => {
  if (aiStatusInterval !== null) window.clearInterval(aiStatusInterval)
})

function scrollToBottom() {
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}

async function handleScroll() {
  if (!logContainer.value) return
  if (!hasMoreHistory.value || isFetchingHistory.value) return
  if (logContainer.value.scrollTop > 120) return
  lastScrollTop.value = logContainer.value.scrollTop
  lastScrollHeight.value = logContainer.value.scrollHeight
  isPrepending.value = true
  await loadPrevious(50)
}

function openClearDialog() {
  isClearDialogOpen.value = true
}

async function handleClearLogs() {
  try {
    await clearLogs()
    toast({ title: t('logs.logsCleared') })
  } catch (e) {
    toast({
      title: t('logs.clearFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  } finally {
    isClearDialogOpen.value = false
  }
}
</script>

<template>
  <div class="flex h-[calc(100vh-100px)] flex-col gap-4">
    <div class="app-surface p-4">
      <div class="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div class="flex flex-col gap-4 lg:flex-row lg:items-center">
        <h1 class="text-2xl font-bold text-gray-800">{{ t('logs.title') }}</h1>
        <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Label class="text-sm text-gray-600">{{ t('logs.task') }}</Label>
          <Select v-model="selectedTaskId">
            <SelectTrigger class="w-full sm:w-[260px]">
              <SelectValue :placeholder="t('logs.selectTask')" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem v-for="task in tasks" :key="task.id" :value="String(task.id)">
                {{ task.task_name }}{{ task.is_running ? t('logs.taskRunningSuffix') : '' }}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      
      <div class="flex flex-col gap-3 md:flex-row md:flex-wrap md:items-center md:justify-end">
        <Button variant="outline" size="sm" :disabled="!selectedTaskId" @click="fetchLogs">
          {{ t('common.refresh') }}
        </Button>

        <div class="flex items-center space-x-2">
          <Switch id="auto-refresh" :model-value="isAutoRefresh" @update:model-value="toggleAutoRefresh" />
          <Label for="auto-refresh">{{ t('logs.autoRefresh') }}</Label>
        </div>

        <div class="flex items-center space-x-2">
          <Switch id="auto-scroll" v-model="autoScroll" />
          <Label for="auto-scroll">{{ t('logs.autoScroll') }}</Label>
        </div>

        <Button variant="destructive" size="sm" :disabled="!selectedTaskId" @click="openClearDialog">
          {{ t('logs.clearLogs') }}
        </Button>
      </div>
    </div>
    </div>

    <Card class="app-surface flex flex-1 flex-col overflow-hidden border-none">
      <CardContent class="flex-1 p-0 relative">
        <pre
          ref="logContainer"
          @scroll="handleScroll"
          class="absolute inset-0 p-4 bg-gray-950 text-gray-100 font-mono text-sm overflow-auto whitespace-pre-wrap break-all"
        >{{ logs }}</pre>
      </CardContent>
    </Card>

    <Teleport to="body">
      <button
        type="button"
        class="group fixed bottom-6 right-6 z-40 grid h-[72px] w-[72px] shrink-0 place-items-center rounded-full bg-slate-800 text-blue-300 shadow-lg ring-1 ring-blue-950/70 transition hover:bg-blue-600 hover:text-white disabled:cursor-not-allowed disabled:opacity-40 sm:right-[72px]"
        :class="isAiRunning ? 'bg-blue-600 text-white' : ''"
        :title="aiActionTitle"
        :aria-label="aiActionTitle"
        :disabled="!selectedTaskId || selectedTask?.decision_mode !== 'ai' || isAiActionPending"
        @click="handleAiAction"
      >
        <svg
          v-if="isAiRunning"
          class="pointer-events-none absolute inset-0 h-[72px] w-[72px] -rotate-90 animate-spin text-blue-300 [animation-duration:1.6s]"
          viewBox="0 0 72 72"
          aria-hidden="true"
        >
          <circle cx="36" cy="36" r="33" fill="none" stroke="currentColor" stroke-opacity="0.2" stroke-width="4" />
          <circle
            cx="36"
            cy="36"
            r="33"
            fill="none"
            stroke="currentColor"
            stroke-linecap="round"
            stroke-width="4"
            :stroke-dasharray="207.35"
            :stroke-dashoffset="aiRingOffset"
          />
        </svg>
        <Square v-if="isAiRunning" class="h-6 w-6 fill-current" />
        <template v-else>
          <Bot class="h-7 w-7 group-hover:hidden" />
          <Play class="hidden h-7 w-7 fill-current group-hover:block" />
        </template>
      </button>
    </Teleport>

    <Dialog v-model:open="isClearDialogOpen">
      <DialogContent class="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{{ t('logs.dialogTitle') }}</DialogTitle>
          <DialogDescription>
            {{ t('logs.dialogDescription') }}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" @click="isClearDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button variant="destructive" @click="handleClearLogs">{{ t('logs.confirmClear') }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
