<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Badge } from '@/components/ui/badge'
import type { TaskGenerationJob, TaskGenerationStep } from '@/types/task.d.ts'

const props = defineProps<{
  job: TaskGenerationJob
}>()
const { t } = useI18n()

const statusMeta = computed(() => {
  if (props.job.status === 'completed') {
    return { label: t('tasks.generation.status.completed'), variant: 'default' as const }
  }
  if (props.job.status === 'failed') {
    return { label: t('tasks.generation.status.failed'), variant: 'destructive' as const }
  }
  if (props.job.status === 'running') {
    return { label: t('tasks.generation.status.running'), variant: 'secondary' as const }
  }
  return { label: t('tasks.generation.status.queued'), variant: 'outline' as const }
})

function resolveStepDotClass(step: TaskGenerationStep) {
  if (step.status === 'completed') return 'border-emerald-500 bg-emerald-500'
  if (step.status === 'running') return 'border-blue-500 bg-blue-500 shadow-[0_0_0_4px_rgba(59,130,246,0.18)]'
  if (step.status === 'failed') return 'border-red-500 bg-red-500'
  return 'border-slate-300 bg-white'
}

function resolveStepTextClass(step: TaskGenerationStep) {
  if (step.status === 'completed') return 'text-slate-700'
  if (step.status === 'running') return 'text-slate-900'
  if (step.status === 'failed') return 'text-red-600'
  return 'text-slate-400'
}
</script>

<template>
  <section class="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 shadow-sm">
    <div class="flex items-start justify-between gap-4">
      <div class="space-y-1">
        <p class="text-sm font-semibold text-slate-900">
          {{ job.task_name }}
        </p>
        <p class="text-sm text-slate-600">
          {{ job.message }}
        </p>
      </div>
      <Badge :variant="statusMeta.variant">
        {{ statusMeta.label }}
      </Badge>
    </div>

    <div
      v-if="job.status === 'running' && job.current_step === 'llm'"
      class="mt-4 flex items-center justify-between rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-blue-700"
      role="status"
      aria-live="polite"
    >
      <span class="flex items-center gap-2 text-sm font-medium">
        <span class="h-2.5 w-2.5 animate-pulse rounded-full bg-blue-500" />
        {{ t('tasks.generation.receiving') }}
      </span>
      <span class="font-mono text-sm font-semibold tabular-nums">
        {{ t('tasks.generation.generatedCharacters', { count: job.generated_characters }) }}
      </span>
    </div>

    <div class="mt-4 grid gap-3">
      <div
        v-for="step in job.steps"
        :key="step.key"
        class="flex items-start gap-3 rounded-xl border border-white/70 bg-white px-3 py-2"
      >
        <span
          class="mt-1 h-3 w-3 shrink-0 rounded-full border-2 transition-colors"
          :class="resolveStepDotClass(step)"
        />
        <div class="min-w-0 space-y-1">
          <p class="text-sm font-medium" :class="resolveStepTextClass(step)">
            {{ step.label }}
          </p>
          <p
            v-if="step.message"
            class="text-xs"
            :class="step.status === 'failed' ? 'text-red-500' : 'text-slate-500'"
          >
            {{ step.message }}
          </p>
        </div>
      </div>
    </div>

    <p
      v-if="job.error"
      class="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600"
    >
      {{ job.error }}
    </p>
  </section>
</template>
