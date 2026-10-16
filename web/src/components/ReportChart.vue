<script setup lang="ts">
import { RadarChart } from 'echarts/charts'
import { AriaComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, type ECharts, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { DashboardReport } from '@/types'

const props = defineProps<{ report: DashboardReport }>()

use([AriaComponent, CanvasRenderer, LegendComponent, RadarChart, TooltipComponent])

const chartElement = ref<HTMLDivElement>()
let chart: ECharts | undefined

type ScoreDimension = { baseline: number; candidate: number }

const dimensions = computed((): Array<[string, ScoreDimension]> =>
  Object.entries(props.report.score_dimensions),
)
const isComparison = computed(() => props.report.mode === 'comparison')

function renderChart(): void {
  if (chartElement.value === undefined) {
    return
  }
  const isCompact = window.matchMedia('(max-width: 620px)').matches
  chart ??= init(chartElement.value)
  const baselineSeries = {
    name: '原版本',
    type: 'radar' as const,
    symbol: 'circle',
    symbolSize: 7,
    lineStyle: { width: 2.5 },
    itemStyle: { color: '#8568FF' },
    areaStyle: { color: 'rgba(133, 104, 255, .16)' },
    data: [{ value: dimensions.value.map(([, value]) => value.baseline), name: '原版本' }],
  }
  const candidateSeries = {
    name: '新版本',
    type: 'radar' as const,
    symbol: 'diamond',
    symbolSize: 8,
    lineStyle: { width: 3 },
    itemStyle: { color: '#E35CBE', shadowBlur: 10, shadowColor: 'rgba(227, 92, 190, .42)' },
    areaStyle: { color: 'rgba(227, 92, 190, .2)' },
    data: [{ value: dimensions.value.map(([, value]) => value.candidate), name: '新版本' }],
  }
  chart.setOption({
    animationDuration: 720,
    animationEasing: 'cubicOut',
    aria: {
      enabled: true,
      description: isComparison.value
        ? '新版本与原版本在理解需求、信息澄清、工具使用、安全合规四项评分上的对比图。'
        : '原版本在理解需求、信息澄清、工具使用、安全合规四项评分上的图表。',
    },
    color: ['#8568FF', '#E35CBE'],
    legend: {
      data: isComparison.value ? ['原版本', '新版本'] : ['原版本'],
      bottom: 0,
      icon: 'roundRect',
      itemWidth: 18,
      itemHeight: 8,
      textStyle: { color: '#5D5873', fontSize: 12 },
    },
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(28, 20, 55, .94)',
      borderWidth: 0,
      textStyle: { color: '#fff' },
    },
    radar: {
      center: isCompact ? ['46%', '47%'] : ['50%', '46%'],
      radius: isCompact ? '55%' : '67%',
      splitNumber: 5,
      axisName: { color: '#5D5873', fontSize: isCompact ? 11 : 13, fontWeight: 600 },
      indicator: dimensions.value.map(([name]) => ({ name, max: 25 })),
      axisLine: { lineStyle: { color: 'rgba(119, 92, 221, .2)' } },
      splitLine: { lineStyle: { color: ['rgba(119, 92, 221, .08)', 'rgba(119, 92, 221, .14)'] } },
      splitArea: { areaStyle: { color: ['rgba(133, 104, 255, .018)', 'rgba(133, 104, 255, .055)'] } },
    },
    series: isComparison.value ? [baselineSeries, candidateSeries] : [baselineSeries],
  })
}

function resizeChart(): void {
  renderChart()
  chart?.resize()
}

onMounted(async () => {
  await nextTick()
  renderChart()
  window.addEventListener('resize', resizeChart)
})

watch(dimensions, renderChart)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
})
</script>

<template>
  <div ref="chartElement" class="report-chart" role="img" :aria-label="isComparison ? '原版本与新版本的四项评分对比图' : '原版本的四项评分图'" />
</template>

