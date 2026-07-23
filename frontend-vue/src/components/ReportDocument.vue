<script setup lang="ts">
import { computed, watch } from 'vue'
import ReportInline from './ReportInline.vue'
import { parseReportMarkdown } from '../utils/reportMarkdown'

const props = defineProps<{ content: string }>()
const emit = defineEmits<{ outline: [items: ReturnType<typeof parseReportMarkdown>['outline']] }>()

const parsed = computed(() => parseReportMarkdown(props.content))
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
      ><ReportInline :tokens="block.inlines" /></component>

      <p v-else-if="block.type === 'paragraph'"><ReportInline :tokens="block.inlines" /></p>

      <blockquote v-else-if="block.type === 'quote'"><ReportInline :tokens="block.inlines" /></blockquote>

      <component :is="block.ordered ? 'ol' : 'ul'" v-else-if="block.type === 'list'">
        <li v-for="item in block.items" :key="item.key"><ReportInline :tokens="item.inlines" /></li>
      </component>

      <div v-else-if="block.type === 'table'" class="report-table-wrap" tabindex="0" aria-label="报告数据表，可横向滚动">
        <table>
          <thead>
            <tr><th v-for="(cell, cellIndex) in block.headers" :key="`head-${cellIndex}`" scope="col"><ReportInline :tokens="cell" /></th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in block.rows" :key="`row-${rowIndex}`">
              <td v-for="(cell, cellIndex) in row" :key="`cell-${rowIndex}-${cellIndex}`"><ReportInline :tokens="cell" /></td>
            </tr>
          </tbody>
        </table>
      </div>

      <hr v-else />
    </template>
  </div>
</template>
