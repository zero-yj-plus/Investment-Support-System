<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import type { PendingStockImage } from '../types/stockAsk'

const props = defineProps<{
  modelValue: string
  pendingImages: PendingStockImage[]
  streaming: boolean
  disabled: boolean
  error: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  addImages: [images: PendingStockImage[]]
  removeImage: [id: string]
  send: []
  stop: []
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const promptInput = ref<HTMLTextAreaElement | null>(null)
const fileError = ref('')
const readers = new Set<FileReader>()
let alive = true

const presets = [
  '能否继续持有？',
  '冲高回落怎么应对？',
  '现在放量是进攻还是出货？',
  '尾盘应该怎么处理？',
  '明天的关键预期是什么？',
  '现在适合追涨还是等待？',
  '当前处于什么交易阶段？',
  '失效条件和风险边界是什么？',
]

function choosePreset(value: string) {
  emit('update:modelValue', value)
  requestAnimationFrame(() => promptInput.value?.focus())
}

function readFile(file: File): Promise<PendingStockImage | null> {
  if (!file.type.startsWith('image/')) return Promise.resolve(null)
  if (file.size > 6 * 1024 * 1024) {
    fileError.value = `“${file.name}”超过 6MB，未加入本轮问题`
    return Promise.resolve(null)
  }
  return new Promise((resolve) => {
    const reader = new FileReader()
    readers.add(reader)
    reader.onload = () => {
      readers.delete(reader)
      const dataUrl = String(reader.result || '')
      resolve(alive && dataUrl.startsWith('data:image/')
        ? { id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, name: file.name, dataUrl }
        : null)
    }
    reader.onerror = () => {
      readers.delete(reader)
      fileError.value = `“${file.name}”读取失败`
      resolve(null)
    }
    reader.onabort = () => {
      readers.delete(reader)
      resolve(null)
    }
    reader.readAsDataURL(file)
  })
}

async function addFiles(fileList: FileList | File[]) {
  fileError.value = ''
  const files = Array.from(fileList)
  const loaded = (await Promise.all(files.map(readFile))).filter((item): item is PendingStockImage => Boolean(item))
  if (alive && loaded.length) emit('addImages', loaded)
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  if (target.files) void addFiles(target.files)
  target.value = ''
}

function onPaste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.items || [])
    .filter((item) => item.type.startsWith('image/'))
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file))
  if (!files.length) return
  event.preventDefault()
  void addFiles(files)
}

function onDrop(event: DragEvent) {
  if (event.dataTransfer?.files?.length) void addFiles(event.dataTransfer.files)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    if (!props.streaming && !props.disabled) emit('send')
  }
}

onBeforeUnmount(() => {
  alive = false
  readers.forEach((reader) => reader.abort())
  readers.clear()
})
</script>

<template>
  <div class="stock-ask-composer">
    <div class="stock-ask-presets" tabindex="0" aria-label="推荐问题，两行排列，可横向滚动">
      <button v-for="item in presets" :key="item" type="button" :disabled="streaming" @click="choosePreset(item)">{{ item }}</button>
    </div>

    <div v-if="pendingImages.length" class="stock-ask-images" aria-label="待发送图片">
      <figure v-for="image in pendingImages" :key="image.id">
        <img :src="image.dataUrl" :alt="image.name || '待发送图片'">
        <button type="button" :aria-label="`移除图片：${image.name}`" @click="emit('removeImage', image.id)">移除</button>
      </figure>
    </div>

    <label for="stock-ask-prompt">你的问题</label>
    <div
      class="stock-ask-input-row"
      @dragover.prevent
      @drop.prevent="onDrop"
    >
      <button
        type="button"
        class="stock-ask-upload"
        :disabled="streaming || pendingImages.length >= 4"
        aria-label="上传图片，最多四张"
        title="上传图片，支持粘贴或拖入"
        @click="fileInput?.click()"
      ><AppIcon name="upload" :size="20" /></button>
      <input ref="fileInput" class="sr-only" type="file" accept="image/*" multiple aria-label="选择本轮问股图片" @change="onFileChange">
      <textarea
        id="stock-ask-prompt"
        ref="promptInput"
        :value="modelValue"
        rows="1"
        :disabled="disabled"
        placeholder="针对当前股票提问；Enter 发送，Shift+Enter 换行"
        @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
        @keydown="onKeydown"
        @paste="onPaste"
      ></textarea>
      <button
        v-if="streaming"
        type="button"
        class="stock-ask-send is-stop"
        aria-label="停止生成"
        @click="emit('stop')"
      ><AppIcon name="stop" :size="19" /><span>停止</span></button>
      <button
        v-else
        type="button"
        class="stock-ask-send"
        :disabled="disabled"
        aria-label="发送问股问题"
        @click="emit('send')"
      ><AppIcon name="send" :size="19" /><span>发送</span></button>
    </div>
    <p v-if="fileError || error" class="stock-ask-composer__error" role="alert">{{ fileError || error }}</p>
    <p class="stock-ask-composer__note">图片只用于本轮上下文，不写入历史；服务端结论以真实行情、证据与风险闸为准。</p>
  </div>
</template>

<style scoped>
.stock-ask-composer { --composer-control-height: 52px; position: sticky; z-index: 10; bottom: 8px; display: grid; gap: 7px; padding: 10px 12px 12px; border-top: 1px solid var(--border); background: color-mix(in srgb, var(--surface) 96%, var(--bg)); }
.stock-ask-presets { display: grid; grid-auto-flow: column; grid-template-rows: repeat(2, minmax(44px, auto)); grid-auto-columns: minmax(164px, 1fr); gap: 6px; max-width: 100%; overflow-x: auto; overscroll-behavior-x: contain; padding: 1px 1px 4px; scrollbar-width: thin; }
.stock-ask-presets button { min-height: 44px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text-2); text-align: left; cursor: pointer; }
.stock-ask-presets button:hover { border-color: var(--border-strong); color: var(--text); }
.stock-ask-presets button:disabled { cursor: wait; opacity: .55; }
.stock-ask-images { display: flex; gap: 8px; overflow-x: auto; padding: 3px 1px; }
.stock-ask-images figure { position: relative; flex: 0 0 64px; margin: 0; }
.stock-ask-images img { display: block; width: 64px; height: 64px; object-fit: cover; border: 1px solid var(--border); border-radius: 6px; }
.stock-ask-images button { position: absolute; top: -5px; right: -5px; width: 44px; height: 44px; overflow: hidden; border: 0; border-radius: 50%; background: var(--danger); color: transparent; cursor: pointer; clip-path: circle(12px at 32px 12px); }
.stock-ask-images button::after { position: absolute; top: 2px; right: 6px; color: #fff; content: "×"; font-size: 18px; line-height: 20px; }
.stock-ask-composer > label { color: var(--text-2); font-size: 13px; font-weight: 700; }
.stock-ask-input-row { display: grid; grid-template-columns: var(--composer-control-height) minmax(0, 1fr) auto; gap: 8px; min-width: 0; align-items: stretch; }
.stock-ask-upload, .stock-ask-send, .stock-ask-input-row textarea { height: var(--composer-control-height); min-height: var(--composer-control-height); max-height: var(--composer-control-height); border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface-2); }
.stock-ask-upload { display: grid; width: var(--composer-control-height); place-items: center; padding: 0; color: var(--text-2); cursor: pointer; }
.stock-ask-input-row textarea { min-width: 0; resize: none; padding: 13px 14px; color: var(--text); line-height: 24px; }
.stock-ask-input-row textarea::placeholder { color: var(--text-3); }
.stock-ask-send { display: inline-flex; min-width: 88px; align-items: center; justify-content: center; gap: 7px; padding: 0 15px; border-color: var(--accent); background: var(--accent); color: #111a27; font-weight: 780; cursor: pointer; }
:global(:root[data-theme="light"] .stock-ask-send) { color: #fff; }
.stock-ask-send.is-stop { border-color: var(--danger); background: transparent; color: var(--danger); }
.stock-ask-upload:disabled, .stock-ask-send:disabled, .stock-ask-input-row textarea:disabled { cursor: wait; opacity: .55; }
.stock-ask-composer__error, .stock-ask-composer__note { margin: 0; }
.stock-ask-composer__error { color: var(--danger); font-size: 13px; }
.stock-ask-composer__note { color: var(--text-3); font-size: 12px; }

@media (max-width: 800px) {
  .stock-ask-composer { position: static; }
}

@media (max-width: 480px) {
  .stock-ask-composer { padding-inline: 8px; }
  .stock-ask-presets { grid-auto-columns: minmax(150px, 1fr); }
  .stock-ask-input-row { gap: 6px; }
  .stock-ask-send { min-width: var(--composer-control-height); width: var(--composer-control-height); padding: 0; }
  .stock-ask-send span { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
  .stock-ask-input-row textarea { padding-inline: 10px; }
}
</style>
