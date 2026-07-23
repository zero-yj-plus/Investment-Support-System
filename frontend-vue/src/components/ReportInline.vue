<script setup lang="ts">
import type { InlineToken } from '../utils/reportMarkdown'

defineProps<{ tokens: InlineToken[] }>()
</script>

<template>
  <template v-for="(token, tokenIndex) in tokens" :key="`${tokenIndex}-${token.type}-${token.text}`">
    <strong v-if="token.type === 'strong'">{{ token.text }}</strong>
    <code v-else-if="token.type === 'code'">{{ token.text }}</code>
    <template v-else>{{ token.text }}</template>
  </template>
</template>
