<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { Task, TaskUpdate } from '@/types/task.d.ts'
import { parseTaskFormDefaults } from '@/lib/taskFormQuery'
import TaskCreateDialog from '@/components/tasks/TaskCreateDialog.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'
import { listAccounts, type AccountItem } from '@/api/accounts'
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
import { Play, Square, Pencil, Trash2, Search, BrainCircuit, MapPin, Tags } from 'lucide-vue-next'

const props = defineProps<{
  tasks: Task[]
  isLoading: boolean
  stoppingIds?: Set<number>
  activeTaskId: number | null
}>()

const emit = defineEmits<{
  (e: 'select-task', task: Task): void
  (e: 'run-task', taskId: number): void
  (e: 'stop-task', taskId: number): void
  (e: 'delete-task', taskId: number): void
  (e: 'update-task', taskId: number, data: TaskUpdate, regenerateCriteria?: boolean): void
  (e: 'created'): void
}>()

const { t } = useI18n()
const route = useRoute()

const searchQuery = ref('')
const isEditDialogOpen = ref(false)
const isEditSubmitting = ref(false)
const selectedTask = ref<Task | null>(null)
const isCriteriaDialogOpen = ref(false)
const criteriaTask = ref<Task | null>(null)
const criteriaDescription = ref('')
const isCriteriaSubmitting = ref(false)
const isDeleteDialogOpen = ref(false)
const taskToDeleteId = ref<number | null>(null)
const accountOptions = ref<AccountItem[]>([])

const filteredTasks = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return props.tasks
  return props.tasks.filter((task) => task.task_name.toLowerCase().includes(q))
})

const runningCount = computed(() => props.tasks.filter((task) => task.is_running).length)

const isStopping = (id: number) => props.stoppingIds?.has(id) ?? false

const taskToDelete = computed(
  () => props.tasks.find((task) => task.id === taskToDeleteId.value) || null
)

const priceRangeText = (task: Task) => {
  const min = task.min_price
  const max = task.max_price
  if (!min && !max) return t('common.all')
  return `¥${min || '0'}–${max || t('common.all')}`
}

const statusInfo = (task: Task) => {
  if (task.is_running) {
    return { label: t('monitor.running'), cls: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400' }
  }
  if (!task.enabled) {
    return { label: t('common.disabled'), cls: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400' }
  }
  return { label: t('monitor.idle'), cls: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400' }
}

const editDefaults = computed(() => parseTaskFormDefaults(route.query))

function openEditDialog(task: Task) {
  selectedTask.value = task
  isEditDialogOpen.value = true
}

watch(
  () => [route.query.edit, props.tasks],
  () => {
    const editTaskId = typeof route.query.edit === 'string' ? Number(route.query.edit) : NaN
    if (!Number.isFinite(editTaskId)) return
    const match = props.tasks.find((task) => task.id === editTaskId)
    if (!match) return
    openEditDialog(match)
  },
  { immediate: true }
)

function handleUpdateTask(data: TaskUpdate) {
  if (!selectedTask.value) return
  emit('update-task', selectedTask.value.id, data)
  isEditDialogOpen.value = false
}

function openCriteriaDialog(task: Task) {
  criteriaTask.value = task
  criteriaDescription.value = task.description || ''
  isCriteriaDialogOpen.value = true
}

function handleRefreshCriteria() {
  if (!criteriaTask.value) return
  if (!criteriaDescription.value.trim()) {
    toast({
      title: t('tasks.toasts.descriptionRequired'),
      description: t('tasks.criteria.descriptionRequired'),
      variant: 'destructive',
    })
    return
  }
  emit('update-task', criteriaTask.value.id, { description: criteriaDescription.value }, true)
  isCriteriaDialogOpen.value = false
}

function openDeleteDialog(taskId: number) {
  taskToDeleteId.value = taskId
  isDeleteDialogOpen.value = true
}

function confirmDeleteTask() {
  if (!taskToDelete.value) return
  emit('delete-task', taskToDelete.value.id)
  isDeleteDialogOpen.value = false
  taskToDeleteId.value = null
}

async function fetchAccountOptions() {
  try {
    accountOptions.value = await listAccounts()
  } catch {
    /* 账号列表加载失败不影响任务列表 */
  }
}

onMounted(fetchAccountOptions)
</script>

<template>
  <div class="flex h-full flex-col">
    <div class="p-3 pb-2">
      <TaskCreateDialog :account-options="accountOptions" @created="emit('created')" />
    </div>

    <div class="px-3 pb-2">
      <div class="flex items-center gap-2 rounded-xl border border-border bg-secondary/50 px-3 py-2">
        <Search class="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <input
          v-model="searchQuery"
          type="text"
          :placeholder="t('monitor.searchTask')"
          class="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/70"
        />
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
      <div v-if="isLoading && tasks.length === 0" class="py-10 text-center text-sm text-muted-foreground">
        {{ t('common.loading') }}
      </div>
      <div v-else-if="tasks.length === 0" class="py-10 text-center">
        <p class="text-sm font-bold text-muted-foreground">{{ t('monitor.noTask') }}</p>
        <p class="mt-1 text-xs text-muted-foreground/70">{{ t('monitor.noTaskHint') }}</p>
      </div>
      <div
        v-for="task in filteredTasks"
        :key="task.id"
        class="group mb-1 cursor-pointer rounded-xl border p-2.5 transition-all duration-150"
        :class="
          task.id === activeTaskId
            ? 'border-primary/40 bg-primary/5'
            : 'border-transparent hover:border-border hover:bg-secondary/50'
        "
        @click="emit('select-task', task)"
      >
        <div class="flex items-center gap-2">
          <span class="min-w-0 flex-1 truncate text-[13px] font-bold text-foreground">
            {{ task.task_name }}
          </span>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold" :class="statusInfo(task).cls">
            {{ statusInfo(task).label }}
          </span>
        </div>
        <div class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
          <span class="inline-flex items-center gap-1"><MapPin class="h-3 w-3" />{{ task.region || t('common.all') }}</span>
          <span>{{ priceRangeText(task) }}</span>
          <span v-if="task.keyword_rules.length > 0" class="inline-flex items-center gap-1">
            <Tags class="h-3 w-3" />{{ task.keyword_rules.length }}
          </span>
          <span v-if="task.decision_mode === 'ai'" class="inline-flex items-center gap-1 text-primary">
            <BrainCircuit class="h-3 w-3" />AI
          </span>
        </div>
        <div class="mt-1.5 flex items-center justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <button
            v-if="!task.is_running"
            class="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary"
            :title="t('tasks.table.start')"
            @click.stop="emit('run-task', task.id)"
          >
            <Play class="h-3.5 w-3.5" />
          </button>
          <button
            v-else
            class="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-rose-500/10 hover:text-rose-500"
            :title="t('tasks.table.stop')"
            :disabled="isStopping(task.id)"
            @click.stop="emit('stop-task', task.id)"
          >
            <Square class="h-3.5 w-3.5" />
          </button>
          <button
            class="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            :title="t('tasks.table.edit')"
            @click.stop="openEditDialog(task)"
          >
            <Pencil class="h-3.5 w-3.5" />
          </button>
          <button
            v-if="task.decision_mode === 'ai'"
            class="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            :title="t('tasks.table.refreshCriteria')"
            @click.stop="openCriteriaDialog(task)"
          >
            <BrainCircuit class="h-3.5 w-3.5" />
          </button>
          <button
            class="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-rose-500/10 hover:text-rose-500"
            :title="t('tasks.table.delete')"
            @click.stop="openDeleteDialog(task.id)"
          >
            <Trash2 class="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>

    <div class="border-t border-border px-3 py-2 text-[11px] font-semibold text-muted-foreground">
      {{ t('monitor.tasksSummary', { count: tasks.length, running: runningCount }) }}
    </div>

    <Dialog v-model:open="isEditDialogOpen">
      <DialogContent class="max-h-[85vh] overflow-y-auto sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>{{ t('tasks.editDialog.title', { task: selectedTask?.task_name || '' }) }}</DialogTitle>
        </DialogHeader>
        <TaskForm
          v-if="selectedTask"
          mode="edit"
          :initial-data="selectedTask"
          :account-options="accountOptions"
          :default-values="editDefaults"
          @submit="(data) => handleUpdateTask(data as TaskUpdate)"
        />
        <DialogFooter>
          <Button type="submit" form="task-form" :disabled="isEditSubmitting">
            {{ isEditSubmitting ? t('common.saving') : t('tasks.editDialog.save') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="isCriteriaDialogOpen">
      <DialogContent class="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>{{ t('tasks.criteria.title') }}</DialogTitle>
          <DialogDescription>{{ t('tasks.criteria.description') }}</DialogDescription>
        </DialogHeader>
        <div class="grid gap-3">
          <label class="text-sm font-medium text-foreground">{{ t('tasks.form.description') }}</label>
          <Textarea
            v-model="criteriaDescription"
            class="min-h-[140px]"
            :placeholder="t('tasks.form.descriptionPlaceholder')"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" @click="isCriteriaDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button :disabled="isCriteriaSubmitting" @click="handleRefreshCriteria">
            {{ isCriteriaSubmitting ? t('tasks.criteria.generating') : t('tasks.criteria.action') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="isDeleteDialogOpen">
      <DialogContent class="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{{ t('tasks.deleteDialog.title') }}</DialogTitle>
          <DialogDescription>
            {{
              taskToDelete
                ? t('tasks.deleteDialog.descriptionWithTask', { task: taskToDelete.task_name })
                : t('tasks.deleteDialog.descriptionFallback')
            }}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" @click="isDeleteDialogOpen = false">{{ t('common.cancel') }}</Button>
          <Button variant="destructive" @click="confirmDeleteTask">{{ t('tasks.deleteDialog.confirm') }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
