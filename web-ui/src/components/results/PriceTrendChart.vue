<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { createChart, LineSeries, ColorType, type IChartApi, type ISeriesApi } from 'lightweight-charts'

interface TrendPoint {
  day: string
  avg_price: number | null
  median_price: number | null
}

const props = defineProps<{
  points: TrendPoint[]
}>()

const chartContainer = ref<HTMLDivElement>()
const hasData = ref(false)
let chart: IChartApi | null = null
let avgSeries: ISeriesApi<'Line'> | null = null
let medianSeries: ISeriesApi<'Line'> | null = null

function toChartData(points: TrendPoint[], field: 'avg_price' | 'median_price') {
  return points
    .filter(p => p[field] !== null && p[field] !== undefined)
    .map(p => ({ time: p.day, value: p[field] as number }))
}

function renderChart(points: TrendPoint[]) {
  if (!chart) return
  const valid = points.filter(p => p.avg_price !== null)
  hasData.value = valid.length > 0

  if (!hasData.value) return

  if (!avgSeries) {
    avgSeries = chart.addSeries(LineSeries, {
      color: '#EA580C',
      lineWidth: 3,
      lastValueVisible: true,
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    })
  }
  avgSeries.setData(toChartData(points, 'avg_price'))

  if (!medianSeries) {
    medianSeries = chart.addSeries(LineSeries, {
      color: '#F59E0B',
      lineWidth: 2,
      lineStyle: 2,
      lastValueVisible: true,
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    })
  }
  medianSeries.setData(toChartData(points, 'median_price'))

  chart.timeScale().fitContent()
}

onMounted(() => {
  if (!chartContainer.value) return

  chart = createChart(chartContainer.value, {
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: '#64748b',
    },
    grid: {
      vertLines: { color: '#e2e8f0', style: 2 },
      horzLines: { color: '#e2e8f0', style: 2 },
    },
    crosshair: {
      vertLine: { labelBackgroundColor: '#EA580C' },
      horzLine: { labelBackgroundColor: '#EA580C' },
    },
    rightPriceScale: { borderColor: '#e2e8f0' },
    timeScale: { borderColor: '#e2e8f0', timeVisible: false },
    height: 220,
    width: chartContainer.value.clientWidth || 720,
    handleScroll: false,
    handleScale: false,
  })

  renderChart(props.points)

  const resizeHandler = () => {
    if (!chart || !chartContainer.value) return
    chart.resize(chartContainer.value.clientWidth, 220)
  }
  window.addEventListener('resize', resizeHandler)

  onUnmounted(() => {
    window.removeEventListener('resize', resizeHandler)
    if (chart) {
      chart.remove()
      chart = null
    }
    avgSeries = null
    medianSeries = null
  })
})

watch(() => props.points, (val) => {
  renderChart(val)
}, { deep: true })
</script>

<template>
  <div class="app-surface-subtle p-4">
    <div class="mb-3 flex flex-col gap-3 text-xs uppercase tracking-[0.22em] text-slate-500 sm:flex-row sm:items-center sm:justify-between">
      <span>每日价格曲线</span>
      <div class="flex items-center gap-3">
        <span class="inline-flex items-center gap-1">
          <span class="h-2.5 w-2.5 rounded-full bg-orange-600" />
          均价
        </span>
        <span class="inline-flex items-center gap-1">
          <span class="h-2.5 w-2.5 rounded-full bg-amber-500" />
          中位价
        </span>
      </div>
    </div>

    <div v-if="!hasData" class="rounded-2xl border border-dashed border-slate-200 bg-white/70 px-4 py-10 text-center text-sm text-slate-500">
      暂无趋势数据
    </div>

    <div v-show="hasData" ref="chartContainer" class="w-full" style="min-height: 220px" />
  </div>
</template>
