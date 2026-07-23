<script setup lang="ts">
import type { QualityState } from '../types/dashboard'

defineProps<{ state: QualityState; label: string }>()
</script>

<template>
  <span class="data-status" :class="`is-${state}`" role="status">
    <span class="data-status__dot" aria-hidden="true"></span>
    <span class="data-status__label">{{ label }}</span>
  </span>
</template>
