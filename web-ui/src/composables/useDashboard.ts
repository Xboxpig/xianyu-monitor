import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as dashboardApi from '@/api/dashboard'
import * as resultsApi from '@/api/results'
import { useWebSocket } from '@/composables/useWebSocket'
import type {
  DashboardSnapshot,
  DashboardSuggestion,
  DashboardTaskSummary,
  SuggestionItem,
} from '@/types/dashboard.d.ts'
import type { ResultInsights } from '@/types/result.d.ts'

function buildSuggestion(
  focusTask: DashboardTaskSummary | undefined,
  insights: ResultInsights | null,
  _t: (key: string, params?: Record<string, unknown>) => string,
): DashboardSuggestion {
  if (!focusTask || focusTask.task_id === null) {
    return {
      title: 'AI 智能分析',
      items: [{
        icon: 'Zap',
        label: '创建第一个监控任务',
        detail: '还没有监控任务，创建后即可开始跟踪商品价格变化',
        actionLabel: '新建任务',
        routeName: 'Tasks',
        query: { create: '1' },
        severity: 'info',
      }],
    }
  }

  const items: SuggestionItem[] = []
  const baseQuery: Record<string, string> = {
    edit: String(focusTask.task_id),
    taskName: focusTask.task_name,
    keyword: focusTask.keyword,
  }
  const m = insights?.market_summary

  if (m && m.sample_count > 0 && m.avg_price) {
    const mid = Math.round(m.avg_price)
    items.push({
      icon: 'Filter',
      label: '建议设置价格范围',
      detail: '市场均价 ¥' + mid + '，建议设价格上限 ¥' + (mid * 15 / 10) + ' 过滤高价商品，下限 ¥' + Math.round(mid * 0.5) + ' 过滤异常低价',
      actionLabel: '一键设置',
      routeName: 'Tasks',
      query: { ...baseQuery, minPrice: String(Math.round(mid * 0.5)), maxPrice: String(mid * 15 / 10) },
      severity: 'info',
    })
  }

  const newItemRatio = focusTask.total_items > 0
    ? (focusTask.total_items - focusTask.recommended_items) / focusTask.total_items
    : 0
  if (newItemRatio > 0.5 && focusTask.total_items > 10) {
    items.push({
      icon: 'RefreshCw',
      label: '新商品占比高 (' + Math.round(newItemRatio * 100) + '%)',
      detail: '扫描到 ' + focusTask.total_items + ' 条商品，其中大量为新收录，建议增加 AI 筛选精准度',
      actionLabel: '优化 AI Prompt',
      routeName: 'Tasks',
      query: { ...baseQuery },
      severity: 'warning',
    })
  }

  if (focusTask.region) {
    items.push({
      icon: 'MapPin',
      label: '地区筛选: ' + focusTask.region.replace(/\//g, ' > '),
      detail: '当前仅搜索指定地区商品，如需扩大范围可移除地区限制',
      actionLabel: '修改地区',
      routeName: 'Tasks',
      query: { ...baseQuery },
      severity: 'info',
    })
  } else {
    items.push({
      icon: 'Globe',
      label: '未设置地区筛选',
      detail: '当前搜索全国范围商品，建议按需设置地区缩小范围提高精准度',
      actionLabel: '设置地区',
      routeName: 'Tasks',
      query: { ...baseQuery, region: '广东/广州/全广州' },
      severity: 'warning',
    })
  }

  return {
    title: 'AI 智能策略',
    items,
  }
}

export function useDashboard() {
  const { t } = useI18n()
  const { on } = useWebSocket()
  const snapshot = ref<DashboardSnapshot | null>(null)
  const focusInsights = ref<ResultInsights | null>(null)
  const isLoading = ref(false)
  const error = ref<Error | null>(null)
  const selectedTaskId = ref<number | null>(null)

  async function fetchSummary() {
    isLoading.value = true
    error.value = null
    try {
      snapshot.value = await dashboardApi.getDashboardSummary()
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      isLoading.value = false
    }
  }

  const taskSummaries = computed(() => snapshot.value?.task_summaries || [])
  const activities = computed(() => snapshot.value?.recent_activities || [])

  const stats = computed(() => {
    const summary = snapshot.value?.summary
    return {
      totalTasks: summary?.total_tasks ?? 0,
      enabledTasks: summary?.enabled_tasks || 0,
      runningTasks: summary?.running_tasks || 0,
      scannedItems: summary?.scanned_items || 0,
      recommendedItems: summary?.recommended_items || 0,
      aiRecommendedItems: summary?.ai_recommended_items || 0,
      keywordRecommendedItems: summary?.keyword_recommended_items || 0,
      resultFiles: summary?.result_files || 0,
    }
  })

  function selectTask(taskId: number) {
    selectedTaskId.value = taskId
  }

  const focusTask = computed(() => {
    if (selectedTaskId.value !== null) {
      const selected = taskSummaries.value.find((item) => item.task_id === selectedTaskId.value)
      if (selected) return selected
    }
    return taskSummaries.value.find((item) => item.filename === snapshot.value?.focus_file) ||
    taskSummaries.value.find((item) => item.filename) ||
    taskSummaries.value[0]
  })

  async function fetchFocusInsights(filename: string | null | undefined) {
    if (!filename) {
      focusInsights.value = null
      return
    }
    try {
      focusInsights.value = await resultsApi.getResultInsights(filename)
    } catch {
      focusInsights.value = null
    }
  }

  watch(
    () => focusTask.value?.filename,
    (filename) => {
      fetchFocusInsights(filename)
    },
    { immediate: true }
  )

  const suggestion = computed(() => buildSuggestion(focusTask.value, focusInsights.value, t))

  on('tasks_updated', fetchSummary)
  on('results_updated', fetchSummary)
  on('task_status_changed', fetchSummary)

  fetchSummary()

  return {
    snapshot,
    focusInsights,
    stats,
    taskSummaries,
    activities,
    focusTask,
    suggestion,
    isLoading,
    error,
    fetchSummary,
    selectTask,
    selectedTaskId,
  }
}
