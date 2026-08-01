<script setup lang="ts">
import { computed, watch } from 'vue'
import ReportInline from './ReportInline.vue'
import { parseReportMarkdown } from '../utils/reportMarkdown'

const props = defineProps<{
  content: string
  diagnosisCodes?: string[]
  diagnosisSection?: string
}>()
const emit = defineEmits<{ outline: [items: ReturnType<typeof parseReportMarkdown>['outline']] }>()

const parsed = computed(() => parseReportMarkdown(props.content))
const normalizedDiagnosisCodes = computed(() => [...new Set(
  (props.diagnosisCodes || []).map((code) => code.trim()).filter((code) => /^\d{6}$/.test(code)),
)])
const diagnosisBlockIndexes = computed(() => {
  const indexes = new Set<number>()
  const target = String(props.diagnosisSection || '').trim()
  if (!target || !normalizedDiagnosisCodes.value.length) return indexes

  let sectionLevel: number | null = null
  parsed.value.blocks.forEach((block, blockIndex) => {
    if (block.type === 'heading') {
      if (block.text === target) {
        sectionLevel = block.level
      } else if (sectionLevel != null && block.level <= sectionLevel) {
        sectionLevel = null
      }
      return
    }
    if (sectionLevel != null) indexes.add(blockIndex)
  })
  return indexes
})

function diagnosisCodesForBlock(blockIndex: number): string[] {
  return diagnosisBlockIndexes.value.has(blockIndex) ? normalizedDiagnosisCodes.value : []
}

watch(() => parsed.value.outline, (items) => emit('outline', items), { immediate: true })
</script>

<template>
  <div class="report-markdown">
    <template v-for="(block, blockIndex) in parsed.blocks" :key="`${blockIndex}-${block.type}`">
      <component
        :is="`h${block.level}`"
        v-if="block.type === 'heading'"
        :id="block.id"
        class="report-heading"
        tabindex="-1"
      ><ReportInline :tokens="block.inlines" :diagnosis-codes="diagnosisCodesForBlock(blockIndex)" /></component>

      <p v-else-if="block.type === 'paragraph'"><ReportInline :tokens="block.inlines" :diagnosis-codes="diagnosisCodesForBlock(blockIndex)" /></p>

      <blockquote v-else-if="block.type === 'quote'"><ReportInline :tokens="block.inlines" :diagnosis-codes="diagnosisCodesForBlock(blockIndex)" /></blockquote>

      <component :is="block.ordered ? 'ol' : 'ul'" v-else-if="block.type === 'list'">
        <li v-for="item in block.items" :key="item.key"><ReportInline :tokens="item.inlines" :diagnosis-codes="diagnosisCodesForBlock(blockIndex)" /></li>
      </component>

      <div v-else-if="block.type === 'table'" class="report-table-wrap" tabindex="0" aria-label="报告数据表，可横向滚动">
        <table>
          <thead>
            <tr><th v-for="(cell, cellIndex) in block.headers" :key="`head-${cellIndex}`" scope="col"><ReportInline :tokens="cell" :diagnosis-codes="diagnosisCodesForBlock(blockIndex)" /></th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in block.rows" :key="`row-${rowIndex}`">
              <td v-for="(cell, cellIndex) in row" :key="`cell-${rowIndex}-${cellIndex}`"><ReportInline :tokens="cell" :diagnosis-codes="diagnosisCodesForBlock(blockIndex)" /></td>
            </tr>
          </tbody>
        </table>
      </div>

      <hr v-else />
    </template>
  </div>
</template>
