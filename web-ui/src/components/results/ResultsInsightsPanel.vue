<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ResultInsights } from '@/types/result.d.ts'
import PriceTrendChart from './PriceTrendChart.vue'
import { formatDateTime } from '@/i18n'
import { ChevronDown } from 'lucide-vue-next'

const props = defineProps<{
  insights: ResultInsights | null
  selectedTaskLabel?: string | null
}>()
const { t } = useI18n()

const expanded = ref(false)

const summaryCards = computed(() => {
  if (!props.insights) return []
  const market = props.insights.market_summary
  const history = props.insights.history_summary
  return [
    {
      label: t('results.insights.currentAvg'),
      value: market.avg_price ? `¥${market.avg_price}` : '—',
      hint: t('results.insights.sampleCount', { count: market.sample_count || 0 }),
    },
    {
      label: t('results.insights.historyAvg'),
      value: history.avg_price ? `¥${history.avg_price}` : '—',
      hint: t('results.insights.uniqueItems', { count: history.unique_items || 0 }),
    },
    {
      label: t('results.insights.currentMin'),
      value: market.min_price ? `¥${market.min_price}` : '—',
      hint: market.max_price
        ? t('results.insights.highestPrice', { price: market.max_price })
        : t('results.insights.noRange'),
    },
  ]
})

const latestSnapshotText = computed(() => {
  if (!props.insights?.latest_snapshot_at) return t('results.insights.noSnapshot')
  return t('results.insights.latestSnapshot', {
    time: formatDateTime(props.insights.latest_snapshot_at, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }),
  })
})
</script>

<template>
  <section class="mb-5 overflow-hidden">
    <button
      type="button"
      class="flex w-full items-center justify-between gap-3 px-5 py-3 text-left transition-colors hover:bg-secondary/40"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <div class="flex min-w-0 items-center gap-2.5">
        <span class="shrink-0 text-xs uppercase tracking-[0.2em] text-primary/70">Market Intelligence</span>
        <span class="truncate text-sm font-bold text-foreground">
          {{ selectedTaskLabel || t('results.insights.defaultTitle') }}
        </span>
      </div>
      <span class="flex shrink-0 items-center gap-1.5 text-xs font-semibold text-muted-foreground">
        {{ expanded ? t('results.insights.collapse') : t('results.insights.expand') }}
        <ChevronDown class="h-4 w-4 transition-transform duration-200" :class="expanded ? 'rotate-180' : ''" />
      </span>
    </button>

    <!-- 收起态：仅一行摘要卡片，给商品区留出空间 -->
    <div v-if="!expanded && summaryCards.length > 0" class="grid grid-cols-1 gap-3 px-5 pb-4 sm:grid-cols-3">
      <div
        v-for="card in summaryCards"
        :key="card.label"
        class="px-4 py-2.5"
      >
        <p class="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">{{ card.label }}</p>
        <p class="mt-1 text-lg font-semibold text-foreground">{{ card.value }}</p>
      </div>
    </div>

    <!-- 展开态：完整市场情报 -->
    <div v-if="expanded" class="grid gap-6 px-6 py-5 lg:grid-cols-[1.15fr_0.85fr]">
      <div class="space-y-5">
        <div class="space-y-2">
          <p class="text-sm leading-6 text-muted-foreground">
            {{ t('results.insights.subtitle') }}
          </p>
        </div>

        <div class="grid gap-4 md:grid-cols-3">
          <article
            v-for="card in summaryCards"
            :key="card.label"
            class="p-4"
          >
            <p class="text-xs uppercase tracking-[0.18em] text-muted-foreground">{{ card.label }}</p>
            <p class="mt-3 text-2xl font-semibold text-foreground">{{ card.value }}</p>
            <p class="mt-2 text-xs text-muted-foreground">{{ card.hint }}</p>
          </article>
        </div>

        <PriceTrendChart :points="insights?.daily_trend || []" />
      </div>

      <div class="space-y-4">
        <div class="rounded-[28px] bg-emerald-50 p-6 dark:bg-emerald-500/10">
          <p class="text-xs uppercase tracking-[0.24em] text-emerald-700 dark:text-emerald-400">Trend Reading</p>
          <p class="mt-4 text-3xl font-semibold text-emerald-900 dark:text-emerald-100">
            {{ t('results.insights.snapshotCount', { count: insights?.market_summary.sample_count || 0 }) }}
          </p>
          <p class="mt-2 text-sm leading-6 text-emerald-800 dark:text-emerald-200">
            {{ t('results.insights.trendReading') }}
          </p>
        </div>

        <div class="p-5">
          <p class="text-xs uppercase tracking-[0.2em] text-muted-foreground">Snapshot Note</p>
          <p class="mt-4 text-sm leading-6 text-muted-foreground">
            {{ latestSnapshotText }}
          </p>
          <div class="mt-4 grid gap-3 text-sm text-muted-foreground">
            <div class="rounded-2xl bg-secondary/50 px-4 py-3">
              {{ t('results.insights.currentMedian') }}
              <span class="font-semibold text-foreground">
                {{ insights?.market_summary.median_price ? `¥${insights.market_summary.median_price}` : '—' }}
              </span>
            </div>
            <div class="rounded-2xl bg-secondary/50 px-4 py-3">
              {{ t('results.insights.historyMin') }}
              <span class="font-semibold text-foreground">
                {{ insights?.history_summary.min_price ? `¥${insights.history_summary.min_price}` : '—' }}
              </span>
            </div>
            <div class="rounded-2xl bg-secondary/50 px-4 py-3">
              {{ t('results.insights.historyMax') }}
              <span class="font-semibold text-foreground">
                {{ insights?.history_summary.max_price ? `¥${insights.history_summary.max_price}` : '—' }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
