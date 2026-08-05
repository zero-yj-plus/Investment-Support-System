<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { QualityState } from '../types/dashboard'
import { buildNavigationHash, findActiveNavigation, sidebarNavigation, type NavigationGroup } from '../navigation'
import AppIcon from './AppIcon.vue'
import DataStatus from './DataStatus.vue'

const props = defineProps<{
  currentPath: string
  currentTab: string
  menuOpen: boolean
  collapsed: boolean
  quality: QualityState
  connectionLabel: string
  theme: 'dark' | 'light'
  brand: string
}>()

const emit = defineEmits<{
  closeMenu: []
  toggleTheme: []
  toggleCollapsed: []
  expandSidebar: []
}>()

const activeNavigation = computed(() => findActiveNavigation(props.currentPath, props.currentTab))
const openGroupId = ref<string | null>(activeNavigation.value?.group.id || 'today')
const sidebarRoot = ref<HTMLElement | null>(null)

watch(() => [props.currentPath, props.currentTab], () => {
  const group = activeNavigation.value?.group
  if (group) openGroupId.value = group.id
})

const groupIsActive = (group: NavigationGroup) => activeNavigation.value?.group.id === group.id
const childIsActive = (path: string, tab: string) => props.currentPath === path && props.currentTab === tab

function activateGroup(group: NavigationGroup) {
  if (props.collapsed) {
    emit('expandSidebar')
    openGroupId.value = group.id
    return
  }
  if (groupIsActive(group)) {
    openGroupId.value = openGroupId.value === group.id ? null : group.id
    return
  }
  openGroupId.value = group.id
  window.location.hash = buildNavigationHash(group.path, group.tab)
  emit('closeMenu')
}

function focusFirstItem() {
  sidebarRoot.value?.querySelector<HTMLElement>('.nav-parent')?.focus()
}

defineExpose({ focusFirstItem })
</script>

<template>
  <aside
    id="primary-navigation"
    ref="sidebarRoot"
    class="sidebar"
    :class="{ open: menuOpen, 'is-collapsed': collapsed }"
    aria-label="主导航"
  >
    <a class="brand" href="#/today?tab=decision" :aria-label="`返回 ${brand} 决策总览`" @click="emit('closeMenu')">
      <img class="brand__mark" src="/product-mark.svg" alt="" width="34" height="34" aria-hidden="true" draggable="false" />
      <span class="brand__copy"><strong>{{ brand }}</strong></span>
    </a>

    <nav class="side-nav" aria-label="交易工作台">
      <p class="nav-section-label">交易工作台</p>

      <section
        v-for="group in sidebarNavigation"
        :key="group.id"
        class="nav-workspace"
        :class="{ active: groupIsActive(group), open: openGroupId === group.id }"
      >
        <button
          class="nav-parent"
          type="button"
          :title="collapsed ? group.label : undefined"
          :aria-label="`${openGroupId === group.id ? '收起' : '展开'}${group.label}子目录`"
          :aria-expanded="openGroupId === group.id"
          :aria-controls="`nav-children-${group.id}`"
          @click="activateGroup(group)"
        >
          <AppIcon :name="group.icon" :size="19" />
          <span class="nav-parent__label">{{ group.label }}</span>
          <AppIcon class="nav-parent__chevron" name="chevron" :size="15" />
        </button>

        <div
          v-show="!collapsed && openGroupId === group.id"
          :id="`nav-children-${group.id}`"
          class="nav-children"
        >
          <template v-for="child in group.children" :key="`${child.path}-${child.tab}`">
            <p v-if="child.placement === 'more' && group.children.findIndex(item => item.placement === 'more') === group.children.indexOf(child)" class="nav-child-label">更多功能</p>
            <a
              class="nav-child"
              :class="{ active: childIsActive(child.path, child.tab) }"
              :href="buildNavigationHash(child.path, child.tab)"
              :aria-current="childIsActive(child.path, child.tab) ? 'page' : undefined"
              @click="emit('closeMenu')"
            >{{ child.label }}</a>
          </template>
        </div>
      </section>
    </nav>

    <div class="sidebar__bottom">
      <button class="nav-utility mobile-theme-button" type="button" @click="emit('toggleTheme')">
        <AppIcon :name="theme === 'dark' ? 'sun' : 'moon'" :size="19" />
        <span>{{ theme === 'dark' ? '切换到浅色主题' : '切换到深色主题' }}</span>
      </button>
      <a
        class="nav-utility"
        :class="{ active: currentPath === 'settings' }"
        href="#settings-tasks"
        :aria-current="currentPath === 'settings' ? 'page' : undefined"
        :title="collapsed ? '系统设置' : undefined"
        @click="emit('closeMenu')"
      >
        <AppIcon name="settings" :size="19" />
        <span>系统设置</span>
      </a>

      <div class="sidebar__status">
        <DataStatus :state="quality" :label="connectionLabel" />
        <span>仅作决策支持 · 不连接实盘</span>
      </div>

      <button
        class="sidebar-collapse"
        type="button"
        :aria-label="collapsed ? '展开导航' : '折叠导航'"
        :title="collapsed ? '展开导航' : '折叠导航'"
        @click="emit('toggleCollapsed')"
      >
        <AppIcon name="chevron" :size="15" />
        <span>{{ collapsed ? '展开' : '折叠导航' }}</span>
      </button>
    </div>
  </aside>
</template>
