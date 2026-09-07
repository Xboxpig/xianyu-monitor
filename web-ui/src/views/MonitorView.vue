<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { Task, TaskUpdate } from '@/types/task.d.ts'
import { useTasks } from '@/composables/useTasks'
import { useTaskGenerationJob } from '@/composables/useTaskGenerationJob'
import { useResults } from '@/composables/useResults'
import TaskListPane from '@/components/monitor/TaskListPane.vue'
import TaskGenerationDialog from '@/components/tasks/TaskGenerationDialog.vue'
import ResultsFilterBar from '@/components/results/ResultsFilterBar.vue'
import ResultsInsightsPanel from '@/components/results/ResultsInsightsPanel.vue'
import ResultsGrid from '@/components/results/ResultsGrid.vue'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toast'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

const { t } = useI18n()
const route = useRoute()

const {
  tasks,
  isLoading: isTasksLoading,
  fetchTasks,
  updateTask,
  removeTask,
  startTask,
  stopTask,
  stoppingTaskIds,
} = useTasks()

const {
  files,
  selectedFile,
  results,
  insights,
  totalItems,
  filters,
  isLoading: isResultsLoading,
  error: resultsError,
  refreshResults,
  exportSelectedResults,
  deleteSelectedFile,
  toggleItemBlock,
  blacklistKeywords,
  isSavingBlacklist,
  saveBlacklistRules,
  fileOptions,
  isFileOptionsReady,
} = useResults()

const activeTaskId = ref<number | null>(null)
const isDeleteDialogOpen = ref(false)
const isBlacklistDialogOpen = ref(false)
const blacklistDraft = ref('')
const isCriteriaProgressOpen = ref(false)
const {
  activeJob: criteriaGenerationJob,
  pollingError: criteriaPollingError,
  beginPolling: beginCriteriaPolling,
  clearJob: clearCriteriaJob,
} = useTaskGenerationJob()

function normalizeKeyword(value: string) {
  return value.trim().toLowerCase().replace(/\s+/g, '_')
}

const fileForTask = (task: Task) =>
  task.keyword ? `${normalizeKeyword(task.keyword)}_full_data.jsonl` : null

const activeTask = computed(() => tasks.value.find((task) => task.id === activeTaskId.value) || null)

const selectedTaskLabel = computed(() => {
  if (activeTask.value) return activeTask.value.task_name
  if (!selectedFile.value || !isFileOptionsReady.value) return null
  const match = fileOptions.value.find((option) => option.value === selectedFile.value)
  return match?.taskName || null
})

function selectTask(task: Task) {
  activeTaskId.value = task.id
  const file = fileForTask(task)
  selectedFile.value = file && files.value.includes(file) ? file : null
}

watch(selectedFile, (file) => {
  if (!file) {
    activeTaskId.value = null
    return
  }
  const match = tasks.value.find((task) => fileForTask(task) === file)
  activeTaskId.value = match ? match.id : null
})

// 支持从结果列表跳转带 ?file= 预选（旧结果页入口）
watch(
  [files, () => route.query.file],
  ([fileList]) => {
    const queryFile = typeof route.query.file === 'string' ? route.query.file : null
    if (queryFile && fileList.includes(queryFile)) {
      selectedFile.value = queryFile
    }
  }
)

async function handleRunTask(taskId: number) {
  try {
    await startTask(taskId)
  } catch (e) {
    toast({
      title: t('tasks.toasts.startFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

async function handleStopTask(taskId: number) {
  try {
    await stopTask(taskId)
  } catch (e) {
    toast({
      title: t('tasks.toasts.stopFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

async function handleUpdateTask(
  taskId: number,
  data: TaskUpdate,
  regenerateCriteria = false,
) {
  try {
    clearCriteriaJob()
    const result = await updateTask(taskId, data, regenerateCriteria)
    if (result?.job) {
      isCriteriaProgressOpen.value = true
      beginCriteriaPolling(result.job)
      return
    }
    await fetchTasks({ silent: true })
  } catch (e) {
    toast({
      title: t('tasks.toasts.updateFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

watch(
  () => criteriaGenerationJob.value?.status,
  (status, previousStatus) => {
    if (!status || status === previousStatus) return
    if (status === 'completed') {
      void fetchTasks({ silent: true })
      toast({ title: t('tasks.toasts.regenerated') })
      isCriteriaProgressOpen.value = false
      clearCriteriaJob()
      return
    }
    if (status === 'failed') {
      toast({
        title: t('tasks.toasts.regenerateFailed'),
        description: criteriaGenerationJob.value?.error || criteriaGenerationJob.value?.message,
        variant: 'destructive',
      })
    }
  },
)

watch(criteriaPollingError, (value) => {
  if (!value) return
  toast({
    title: t('tasks.toasts.progressFailed'),
    description: value.message,
    variant: 'destructive',
  })
})

async function handleDeleteTask(taskId: number) {
  try {
    await removeTask(taskId)
    if (activeTaskId.value === taskId) {
      activeTaskId.value = null
      selectedFile.value = null
    }
    await fetchTasks({ silent: true })
    toast({ title: t('tasks.toasts.deleted') })
  } catch (e) {
    toast({
      title: t('tasks.toasts.deleteFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

function openDeleteDialog() {
  if (!selectedFile.value) {
    toast({ title: t('results.filters.noResultToDelete'), variant: 'destructive' })
    return
  }
  isDeleteDialogOpen.value = true
}

async function handleDeleteResults() {
  if (!selectedFile.value) return
  try {
    await deleteSelectedFile(selectedFile.value)
    toast({ title: t('results.filters.resultDeleted') })
  } catch (e) {
    toast({
      title: t('results.filters.deleteFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  } finally {
    isDeleteDialogOpen.value = false
  }
}

function openBlacklistDialog() {
  if (!selectedFile.value) {
    toast({ title: t('results.filters.noResultSelected'), variant: 'destructive' })
    return
  }
  blacklistDraft.value = blacklistKeywords.value.join('\n')
  isBlacklistDialogOpen.value = true
}

function parseBlacklistKeywords(input: string) {
  return input
    .split(/[\n,，]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function handleSaveBlacklistRules() {
  try {
    await saveBlacklistRules(parseBlacklistKeywords(blacklistDraft.value))
    toast({ title: t('results.filters.blacklistSaved') })
    isBlacklistDialogOpen.value = false
  } catch (e) {
    toast({
      title: t('results.filters.blacklistSaveFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  }
}

const deleteConfirmText = computed(() => {
  return selectedTaskLabel.value
    ? t('results.filters.deleteDialogWithTask', { task: selectedTaskLabel.value })
    : t('results.filters.deleteDialogFallback')
})
</script>

<template>
  <div class="flex flex-col gap-6 lg:flex-row lg:items-start">
    <!-- 左侧任务列表 -->
    <div class="w-full shrink-0 lg:sticky lg:top-24 lg:h-[calc(100vh-7rem)] lg:w-[300px] lg:border-r lg:border-border/60 lg:pr-4">
      <TaskListPane
        :tasks="tasks"
        :is-loading="isTasksLoading"
        :stopping-ids="stoppingTaskIds"
        :active-task-id="activeTaskId"
        @select-task="selectTask"
        @run-task="handleRunTask"
        @stop-task="handleStopTask"
        @delete-task="handleDeleteTask"
        @update-task="handleUpdateTask"
        @created="fetchTasks()"
      />
    </div>

    <TaskGenerationDialog
      v-model:open="isCriteriaProgressOpen"
      :job="criteriaGenerationJob"
    />

    <!-- 右侧结果区 -->
    <div class="flex min-w-0 flex-1 flex-col">
      <ResultsFilterBar
        :files="files"
        :file-options="fileOptions"
        :is-ready="isFileOptionsReady"
        v-model:selectedFile="selectedFile"
        v-model:aiRecommendedOnly="filters.ai_recommended_only"
        v-model:keywordRecommendedOnly="filters.keyword_recommended_only"
        v-model:includeHidden="filters.include_hidden"
        v-model:sortBy="filters.sort_by"
        v-model:sortOrder="filters.sort_order"
        :is-loading="isResultsLoading"
        @refresh="refreshResults"
        @manage-blacklist="openBlacklistDialog"
        @export="exportSelectedResults"
        @delete="openDeleteDialog"
      />

      <div v-if="resultsError" class="app-alert-error mx-4 mb-2" role="alert">
        <strong class="font-bold">{{ t('common.error') }}</strong>
        <span class="block sm:inline">{{ resultsError.message }}</span>
      </div>

      <ResultsInsightsPanel :insights="insights" :selected-task-label="selectedTaskLabel" />

      <div class="min-w-0 flex-1">
        <ResultsGrid :results="results" :is-loading="isResultsLoading" @toggle-block="toggleItemBlock" />
      </div>

      <div class="flex items-center justify-between border-t border-border/70 px-1 py-2 text-[11px] font-semibold text-muted-foreground">
        <span>
          {{ selectedFile ? t('monitor.resultsCount', { count: totalItems }) : t('monitor.emptyResults') }}
        </span>
        <span v-if="selectedTaskLabel">{{ t('results.filters.taskNameLabel', { task: selectedTaskLabel }) }}</span>
      </div>
    </div>

    <!-- 删除结果确认 -->
    <Dialog v-model:open="isDeleteDialogOpen">
      <DialogContent class="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{{ t('results.filters.deleteDialogTitle') }}</DialogTitle>
          <DialogDescription>{{ deleteConfirmText }}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" @click="isDeleteDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button variant="destructive" :disabled="isResultsLoading" @click="handleDeleteResults">
            {{ t('results.filters.confirmDelete') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <!-- 黑名单编辑 -->
    <Dialog v-model:open="isBlacklistDialogOpen">
      <DialogContent class="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{{ t('results.filters.blacklistDialogTitle') }}</DialogTitle>
          <DialogDescription>{{ t('results.filters.blacklistDialogDescription') }}</DialogDescription>
        </DialogHeader>
        <div class="space-y-2">
          <label class="text-sm font-medium text-foreground">{{ t('results.filters.blacklistRulesLabel') }}</label>
          <Textarea
            v-model="blacklistDraft"
            class="min-h-[180px]"
            :placeholder="t('results.filters.blacklistRulesPlaceholder')"
          />
          <p class="text-xs leading-5 text-muted-foreground">{{ t('results.filters.blacklistRulesHint') }}</p>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="isBlacklistDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button :disabled="isSavingBlacklist" @click="handleSaveBlacklistRules">
            {{ t('results.filters.confirmBlacklistSave') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
