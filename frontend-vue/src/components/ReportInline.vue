<script setup lang="ts">
import { computed } from 'vue'
import type { InlineToken } from '../utils/reportMarkdown'

const props = defineProps<{ tokens: InlineToken[]; diagnosisCodes?: string[] }>()

type InlinePart = InlineToken & { key: string; diagnosisCode?: string }

const parts = computed<InlinePart[]>(() => {
  const codes = [...new Set(
    (props.diagnosisCodes || []).map((code) => code.trim()).filter((code) => /^\d{6}$/.test(code)),
  )]
  if (!codes.length) {
    return props.tokens.map((token, tokenIndex) => ({ ...token, key: `${tokenIndex}-${token.type}-${token.text}` }))
  }

  const pattern = new RegExp(`\\b(${codes.join('|')})\\b`, 'g')
  return props.tokens.flatMap((token, tokenIndex) => {
    const tokenParts: InlinePart[] = []
    let cursor = 0
    for (const match of token.text.matchAll(pattern)) {
      const matchIndex = match.index || 0
      if (matchIndex > cursor) {
        tokenParts.push({ ...token, key: `${tokenIndex}-${cursor}`, text: token.text.slice(cursor, matchIndex) })
      }
      const diagnosisCode = match[1]
      tokenParts.push({
        type: 'text',
        text: diagnosisCode,
        key: `${tokenIndex}-${matchIndex}-${diagnosisCode}`,
        diagnosisCode,
      })
      cursor = matchIndex + diagnosisCode.length
    }
    if (cursor < token.text.length) {
      tokenParts.push({ ...token, key: `${tokenIndex}-${cursor}`, text: token.text.slice(cursor) })
    }
    return tokenParts
  })
})
</script>

<template>
  <template v-for="part in parts" :key="part.key">
    <a
      v-if="part.diagnosisCode"
      class="report-diagnosis-link"
      :href="`#/research?tab=diagnosis&code=${encodeURIComponent(part.diagnosisCode)}`"
      :aria-label="`进入 ${part.diagnosisCode} 个股诊断`"
    >{{ part.text }}</a>
    <strong v-else-if="part.type === 'strong'">{{ part.text }}</strong>
    <code v-else-if="part.type === 'code'">{{ part.text }}</code>
    <template v-else>{{ part.text }}</template>
  </template>
</template>

<style scoped>
.report-diagnosis-link { display: inline-flex; min-width: 44px; min-height: 44px; align-items: center; justify-content: center; padding-inline: 4px; border-radius: 4px; color: var(--accent); font-family: var(--font-num); font-weight: 750; text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; }
.report-diagnosis-link:hover { background: var(--surface-2); text-decoration-thickness: 2px; }
</style>
