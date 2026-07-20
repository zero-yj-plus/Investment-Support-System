/* freshness.js — 全局「免刷新」新鲜度层（2026-07-06，客户体验硬要求：打开即最新、不需要手动刷新）
 *
 * 与既有机制的分工（不重复造轮子）：
 *  - swr.js       解决「打开瞬间」：上次数据秒出 + 后台拉新无缝替换（跨会话）。
 *  - prewarmer    解决「服务端热度」：交易时段 15s 预热，后台拉新恒命中热缓存。
 *  - 本模块       解决「页面开着不动」：只刷新全局连接/阶段状态；行情页由各自的原地增量轮询
 *                 更新，切回标签页和断网恢复也遵循这一原则。绝不以 route(true) 重挂整个工作台。
 *
 * 安全设计（宁可不刷、绝不打扰）：
 *  - 输入保护：焦点在 input/textarea/select/可编辑元素上时跳过本轮（不打断打字）。
 *  - 会话型视图排除：chat / diy / settings（重挂载会重置会话滚动位/表单结构）。
 *  - 标签页隐藏时完全静默；切回可见立即补一刷（节流 ≥15s）。
 *  - 节奏按【北京时间】判定（客户浏览器时区无关）：交易时段(工作日 09:10-15:35) 45s；
 *    其余时段 5 分钟（晚间报告/PDF 出稿后页面也能自己变新）。
 *  - 本模块任何异常都自吞——新鲜度层挂了 = 退回手动刷新，绝不影响主功能。
 */

// 会话/表单型页面不需要周期性触发；行情类页面均已有自己的原地增量轮询。
// 这里继续保留白名单，是为了不在流式生成/输入时做任何后台动作。
const EXCLUDE = new Set([
  'chat', 'diy', 'settings',
  'morning', 'tail', 'close', 'evening', 'company', 'chain', 'restructuring', 'diagnosis', 'reports',
  'holdings',
]);
const HOT_MS = 45 * 1000;      // 交易时段轮询间隔
const COLD_MS = 300 * 1000;    // 非交易时段轮询间隔
const MIN_GAP_MS = 15 * 1000;  // 任意两次自动刷新的最小间隔（含切回自愈）

let _last = 0;

function typingNow() {
  const el = document.activeElement;
  if (!el) return false;
  const tag = (el.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable === true;
}

/** 北京时间视角：现在是否 A 股交易时段（工作日 09:10–15:35，含竞价与收盘尾巴）。 */
function tradingHoursCN() {
  try {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Shanghai', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date());
    const get = (t) => (parts.find((p) => p.type === t) || {}).value || '';
    const wd = get('weekday');
    if (wd === 'Sat' || wd === 'Sun') return false;
    const hm = Number(get('hour')) * 60 + Number(get('minute'));
    return hm >= 9 * 60 + 10 && hm <= 15 * 60 + 35;
  } catch { return false; }   // Intl 异常（极老 WebView）→ 按冷时段处理
}

export function initFreshness({ refresh, getPath }) {
  const soft = (reason) => {
    try {
      if (document.visibilityState !== 'visible') return;
      if (Date.now() - _last < MIN_GAP_MS) return;
      const path = (getPath() || '').replace(/^#?\//, '');
      if (EXCLUDE.has(path)) return;
      // 流式生成中不做后台状态请求，避免与用户发起的长任务争抢注意力。
      if (document.querySelector('.typing-cursor')) return;
      if (typingNow()) return;                    // 不打断输入；下一轮再刷
      _last = Date.now();
      refresh(reason);
    } catch { /* 新鲜度层绝不抛错影响主流程 */ }
  };

  // ① 开着不动 → 只刷新全局状态（内部再按间隔闸门判定）
  let due = 0;
  setInterval(() => {
    const period = tradingHoursCN() ? HOT_MS : COLD_MS;
    const now = Date.now();
    if (now >= due) { due = now + period; soft('poll'); }
  }, 5000);

  // ② 切回标签页 / 窗口重获焦点 → 立即自愈（治「切回来还是早上的数据」）
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') soft('visible');
  });

  // ③ 断网恢复 → 自愈
  window.addEventListener('online', () => soft('online'));
}
