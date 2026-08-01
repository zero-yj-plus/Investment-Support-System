<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ParsedWatchlistImport, WatchlistImportItem } from '../types/watchlist'
import { parseWatchlistText, readWatchlistTextFile } from '../utils/watchlistImport'

const props = defineProps<{
  existingCodes: string[]
  busy: boolean
  error: string
}>()

const emit = defineEmits<{
  submit: [items: WatchlistImportItem[]]
  cancel: []
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const parsed = ref<ParsedWatchlistImport | null>(null)
const fileName = ref('')
const localError = ref('')
const parsing = ref(false)
let generation = 0

const sourceLabel = computed(() => parsed.value?.source === 'ths' ? '同花顺' : '东方财富')
const previewItems = computed(() => parsed.value?.items.slice(0, 12) || [])
const previewSkipped = computed(() => parsed.value?.skipped.slice(0, 8) || [])

async function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  const currentGeneration = ++generation
  parsing.value = true
  localError.value = ''
  parsed.value = null
  fileName.value = file.name
  try {
    const text = await readWatchlistTextFile(file)
    if (currentGeneration !== generation) return
    parsed.value = parseWatchlistText(text, props.existingCodes)
  } catch (caught) {
    if (currentGeneration !== generation) return
    localError.value = caught instanceof Error ? caught.message : 'TXT 解析失败，请重新选择文件。'
  } finally {
    if (currentGeneration === generation) parsing.value = false
  }
}

function submit() {
  if (props.busy || parsing.value || !parsed.value?.items.length) return
  emit('submit', parsed.value.items)
}

function requestClose() {
  if (props.busy || parsing.value) return
  dialog.value?.close()
  emit('cancel')
}

function handleBackdrop(event: MouseEvent) {
  if (event.target === dialog.value) requestClose()
}

onMounted(() => {
  dialog.value?.showModal()
  void nextTick(() => fileInput.value?.focus())
})

onBeforeUnmount(() => {
  generation += 1
  if (dialog.value?.open) dialog.value.close()
})
</script>

<template>
  <dialog
    ref="dialog"
    class="watchlist-import"
    aria-labelledby="watchlist-import-title"
    aria-describedby="watchlist-import-description"
    @cancel.prevent="requestClose"
    @click="handleBackdrop"
  >
    <header class="watchlist-import__header">
      <div>
        <p class="eyebrow">追加导入 · 自动去重</p>
        <h2 id="watchlist-import-title">批量导入观察项</h2>
      </div>
      <button type="button" class="secondary-button" :disabled="busy || parsing" @click="requestClose">取消</button>
    </header>

    <form @submit.prevent="submit">
      <div class="watchlist-import__body">
        <p id="watchlist-import-description" class="watchlist-import__description">
          支持东方财富和同花顺导出的 TXT，自动识别 UTF-8、GBK/GB18030。只追加 A 股代码、名称和所属行业，其他观察条件保持为空；已有代码、港股和无效记录不会写入。
        </p>

        <label class="watchlist-import__picker">
          <span>选择 TXT 文件 <b aria-hidden="true">*</b></span>
          <input
            ref="fileInput"
            type="file"
            accept=".txt,text/plain"
            :disabled="busy || parsing"
            required
            @change="onFileChange"
          />
          <small>单个文件不超过 2 MB；一次最多导入 200 条有效 A 股记录。</small>
        </label>

        <div v-if="parsing" class="watchlist-import__notice" role="status" aria-live="polite">
          正在解析 {{ fileName }}…
        </div>
        <div v-else-if="localError || error" class="watchlist-import__notice is-error" role="alert">
          <strong>文件未导入</strong>
          <span>{{ localError || error }}</span>
          <span>请重新选择东方财富或同花顺直接导出的 TXT。</span>
        </div>

        <template v-if="parsed">
          <dl class="watchlist-import__summary" aria-label="导入预览统计">
            <div><dt>识别来源</dt><dd>{{ sourceLabel }}</dd></div>
            <div><dt>文件记录</dt><dd class="mono">{{ parsed.totalRows }}</dd></div>
            <div><dt>可导入</dt><dd class="mono">{{ parsed.items.length }}</dd></div>
            <div><dt>将跳过</dt><dd class="mono">{{ parsed.skipped.length }}</dd></div>
          </dl>

          <section class="watchlist-import__preview" aria-labelledby="watchlist-import-valid-title">
            <div class="watchlist-import__section-title">
              <h3 id="watchlist-import-valid-title">待导入记录</h3>
              <span v-if="parsed.items.length > previewItems.length">显示前 {{ previewItems.length }} 条</span>
            </div>
            <ul v-if="previewItems.length" class="watchlist-import__rows">
              <li v-for="item in previewItems" :key="item.code">
                <span class="mono">{{ item.code }}</span>
                <strong>{{ item.name || '名称缺失' }}</strong>
                <span>{{ item.sector || '行业缺失' }}</span>
              </li>
            </ul>
            <p v-else class="watchlist-import__empty">没有可追加记录。请查看下方跳过原因或更换文件。</p>
          </section>

          <section v-if="previewSkipped.length" class="watchlist-import__preview" aria-labelledby="watchlist-import-skipped-title">
            <div class="watchlist-import__section-title">
              <h3 id="watchlist-import-skipped-title">跳过记录</h3>
              <span v-if="parsed.skipped.length > previewSkipped.length">显示前 {{ previewSkipped.length }} 条</span>
            </div>
            <ul class="watchlist-import__rows is-skipped">
              <li v-for="item in previewSkipped" :key="`${item.line}-${item.code}`">
                <span class="mono">第 {{ item.line }} 行</span>
                <strong>{{ item.code || item.name || '未识别代码' }}</strong>
                <span>{{ item.reason }}</span>
              </li>
            </ul>
          </section>
        </template>
      </div>

      <footer class="watchlist-import__footer">
        <span>确认后一次性追加；不会清空或覆盖现有观察池。</span>
        <button type="submit" class="primary-button" :disabled="busy || parsing || !parsed?.items.length">
          {{ busy ? '导入中…' : `确认导入${parsed?.items.length ? ` ${parsed.items.length} 条` : ''}` }}
        </button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.watchlist-import { width: min(780px, calc(100% - 24px)); max-height: calc(100dvh - 24px); padding: 0; overflow: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); color: var(--text); }
.watchlist-import::backdrop { background: rgb(7 13 22 / 58%); }
.watchlist-import__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding: 16px 18px 12px; border-bottom: 1px solid var(--border); }
.watchlist-import__header h2 { margin: 2px 0 0; font-size: 22px; }
.watchlist-import__header p { margin: 0; }
.watchlist-import__body { display: grid; gap: 14px; padding: 16px 18px; }
.watchlist-import__description { margin: 0; color: var(--text-2); line-height: 1.65; }
.watchlist-import__picker { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); }
.watchlist-import__picker > span { font-weight: 760; }
.watchlist-import__picker input { min-height: 44px; padding: 7px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); }
.watchlist-import__picker small { color: var(--text-3); }
.watchlist-import__notice { display: grid; gap: 3px; padding: 10px 12px; border: 1px solid var(--info); border-radius: var(--radius); background: var(--surface-2); }
.watchlist-import__notice.is-error { border-color: var(--danger); }
.watchlist-import__notice span { color: var(--text-2); }
.watchlist-import__summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 0; }
.watchlist-import__summary div { min-width: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); }
.watchlist-import__summary dt { color: var(--text-3); font-size: 12px; }
.watchlist-import__summary dd { margin: 2px 0 0; font-weight: 780; overflow-wrap: anywhere; }
.watchlist-import__preview { display: grid; gap: 7px; }
.watchlist-import__section-title { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.watchlist-import__section-title h3 { margin: 0; font-size: 15px; }
.watchlist-import__section-title span { color: var(--text-3); font-size: 12px; }
.watchlist-import__rows { display: grid; margin: 0; padding: 0; border: 1px solid var(--border); border-radius: var(--radius); list-style: none; overflow: hidden; }
.watchlist-import__rows li { display: grid; min-width: 0; grid-template-columns: 92px minmax(120px, .75fr) minmax(120px, 1fr); gap: 10px; padding: 8px 10px; border-bottom: 1px solid var(--border); align-items: center; }
.watchlist-import__rows li:last-child { border-bottom: 0; }
.watchlist-import__rows li > * { min-width: 0; overflow-wrap: anywhere; }
.watchlist-import__rows li > span:last-child { color: var(--text-2); }
.watchlist-import__rows.is-skipped { border-color: var(--warning); }
.watchlist-import__empty { margin: 0; padding: 12px; border: 1px solid var(--warning); border-radius: var(--radius); color: var(--text-2); }
.watchlist-import__footer { position: sticky; bottom: 0; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 18px; border-top: 1px solid var(--border); background: var(--surface); }
.watchlist-import__footer > span { color: var(--text-2); font-size: 13px; }
.watchlist-import button { min-height: 44px; }
@media (max-width: 620px) {
  .watchlist-import { width: calc(100% - 12px); max-height: calc(100dvh - 12px); }
  .watchlist-import__header, .watchlist-import__body, .watchlist-import__footer { padding-right: 12px; padding-left: 12px; }
  .watchlist-import__summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .watchlist-import__rows li { grid-template-columns: minmax(0, 1fr); gap: 2px; }
  .watchlist-import__footer { align-items: stretch; flex-direction: column; }
  .watchlist-import__footer button { width: 100%; }
}
</style>
