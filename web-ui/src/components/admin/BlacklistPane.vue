<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTasks } from '@/composables/useTasks'
import { getResultBlacklistRules, updateResultBlacklistRules } from '@/api/results'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { toast } from '@/components/ui/toast'
import { X, Plus } from 'lucide-vue-next'

const { t } = useI18n()
const { tasks, isLoading, fetchTasks } = useTasks()

const selectedTaskId = ref<string>('')
const keywords = ref<string[]>([])
const draft = ref('')
const isSaving = ref(false)

function normalizeKeyword(value: string) {
  return value.trim().toLowerCase().replace(/\s+/g, '_')
}

const selectedFile = computed(() => {
  const task = tasks.value.find((item) => String(item.id) === selectedTaskId.value)
  if (!task?.keyword) return null
  return `${normalizeKeyword(task.keyword)}_full_data.jsonl`
})

const fileReady = computed(() => !!selectedFile.value)

async function loadRules() {
  if (!selectedFile.value) {
    keywords.value = []
    return
  }
  try {
    const data = await getResultBlacklistRules(selectedFile.value)
    keywords.value = data.keywords || []
  } catch {
    keywords.value = []
  }
}

watch(selectedTaskId, loadRules)

function addKeyword() {
  const value = draft.value.trim()
  if (!value) return
  if (!keywords.value.includes(value)) {
    keywords.value = [...keywords.value, value]
  }
  draft.value = ''
}

function removeKeyword(value: string) {
  keywords.value = keywords.value.filter((item) => item !== value)
}

async function handleSave() {
  if (!selectedFile.value) return
  isSaving.value = true
  try {
    await updateResultBlacklistRules(selectedFile.value, keywords.value)
    toast({ title: t('admin.blacklist.saved') })
  } catch (e) {
    toast({
      title: t('admin.blacklist.saveFailed'),
      description: (e as Error).message,
      variant: 'destructive',
    })
  } finally {
    isSaving.value = false
  }
}

onMounted(fetchTasks)
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center">
      <label class="text-sm font-bold text-foreground">{{ t('admin.blacklist.selectTask') }}</label>
      <Select v-model="selectedTaskId">
        <SelectTrigger class="w-full sm:w-[300px]">
          <SelectValue :placeholder="t('admin.blacklist.selectTask')" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem v-for="task in tasks" :key="task.id" :value="String(task.id)">
            {{ task.task_name }}
          </SelectItem>
        </SelectContent>
      </Select>
      <span v-if="isLoading" class="text-xs text-muted-foreground">{{ t('common.loading') }}</span>
    </div>

    <div v-if="!fileReady" class="rounded-2xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
      {{ t('admin.blacklist.noFile') }}
    </div>

    <div v-else class="space-y-4">
      <div class="flex gap-2">
        <input
          v-model="draft"
          class="h-10 flex-1 rounded-xl border border-border bg-background px-3 text-sm outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15 sm:max-w-[360px]"
          :placeholder="t('admin.blacklist.inputPlaceholder')"
          @keydown.enter.prevent="addKeyword"
        />
        <Button @click="addKeyword">
          <Plus class="mr-1 h-4 w-4" />{{ t('admin.blacklist.add') }}
        </Button>
      </div>

      <div class="flex flex-wrap gap-2">
        <span
          v-for="keyword in keywords"
          :key="keyword"
          class="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary/60 px-3 py-1.5 text-sm font-semibold"
        >
          {{ keyword }}
          <button
            class="text-muted-foreground transition-colors hover:text-destructive"
            :aria-label="t('common.delete')"
            @click="removeKeyword(keyword)"
          >
            <X class="h-3.5 w-3.5" />
          </button>
        </span>
        <span v-if="keywords.length === 0" class="text-sm text-muted-foreground">{{ t('admin.blacklist.empty') }}</span>
      </div>

      <p class="text-xs leading-5 text-muted-foreground">{{ t('admin.blacklist.hint') }}</p>

      <Button :disabled="isSaving" @click="handleSave">
        {{ isSaving ? t('common.saving') : t('common.save') }}
      </Button>
    </div>
  </div>
</template>
