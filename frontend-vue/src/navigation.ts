export type NavigationIcon = 'dashboard' | 'market' | 'shield' | 'research' | 'history'

export interface NavigationChild {
  label: string
  path: string
  tab: string
  placement?: 'primary' | 'more'
}

export interface NavigationGroup {
  id: string
  label: string
  icon: NavigationIcon
  path: string
  tab: string
  children: NavigationChild[]
}

export const sidebarNavigation: NavigationGroup[] = [
  {
    id: 'today', label: '今日决策', icon: 'dashboard', path: 'today', tab: 'decision',
    children: [
      { path: 'today', tab: 'decision', label: '决策总览' },
      { path: 'premarket', tab: 'report', label: '盘前报告' },
      { path: 'tail', tab: 'decision', label: '尾盘决策' },
      { path: 'review', tab: 'close', label: '今日复盘' },
      { path: 'review', tab: 'tomorrow', label: '次日策略' },
    ],
  },
  {
    id: 'market', label: '市场结构', icon: 'market', path: 'today', tab: 'hotboard',
    children: [
      { path: 'today', tab: 'hotboard', label: '涨停题材' },
      { path: 'today', tab: 'mainline', label: '主线深研' },
      { path: 'today', tab: 'capital', label: '资金追踪' },
      { path: 'today', tab: 'timing', label: '资金择时' },
      { path: 'today', tab: 'news', label: '舆情事件' },
    ],
  },
  {
    id: 'risk', label: '持仓风控', icon: 'shield', path: 'risk', tab: 'holdings',
    children: [
      { path: 'risk', tab: 'holdings', label: '我的持仓' },
      { path: 'risk', tab: 'watchlist', label: '观察池' },
    ],
  },
  {
    id: 'research', label: '研究中心', icon: 'research', path: 'research', tab: 'ask',
    children: [
      { path: 'research', tab: 'ask', label: '问股' },
      { path: 'research', tab: 'diagnosis', label: '个股诊断' },
      { path: 'research', tab: 'screener', label: '条件选股' },
      { path: 'research', tab: 'technical-screener', label: '技术面选股' },
      { path: 'research', tab: 'quant-pick', label: '每日量化选股' },
      { path: 'research', tab: 'events', label: '事件掘金' },
      { path: 'research', tab: 'chain', label: '产业链分析' },
      { path: 'research', tab: 'company', label: '公司研究' },
      { path: 'research', tab: 'overseas', label: '海外市场' },
      { path: 'research', tab: 'restructuring', label: '重组拆解', placement: 'more' },
      { path: 'research', tab: 'macro', label: '宏观风险', placement: 'more' },
    ],
  },
  {
    id: 'history', label: '历史与知识', icon: 'history', path: 'history', tab: 'reports',
    children: [
      { path: 'history', tab: 'reports', label: '全部报告' },
      { path: 'history', tab: 'knowledge', label: '知识库' },
    ],
  },
]

export function buildNavigationHash(path: string, tab: string) {
  return `#/${path}?tab=${encodeURIComponent(tab)}`
}

export function findActiveNavigation(path: string, tab: string) {
  for (const group of sidebarNavigation) {
    const child = group.children.find((item) => item.path === path && item.tab === tab)
    if (child) return { group, child }
  }
  return undefined
}
