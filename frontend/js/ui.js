/* ui.js — 共享 UI：图标、toast、modal、markdown、骨架屏、空状态、数据角标 */

/* ====================== Phosphor 图标 ====================== */

const ICON_NAMES = Object.freeze({
  logo: 'lightning', dashboard: 'gauge', chat: 'chats-circle', reports: 'file-text',
  sentiment: 'chart-line', holdings: 'briefcase', chain: 'tree-structure', knowledge: 'books',
  archive: 'archive', settings: 'gear-six', edit: 'pencil-simple', datasources: 'database',
  sun: 'sun', moon: 'moon', refresh: 'arrow-clockwise', send: 'paper-plane-tilt', stop: 'stop',
  plus: 'plus', trash: 'trash', search: 'magnifying-glass', close: 'x', chevL: 'caret-left',
  chevR: 'caret-right', check: 'check', alert: 'warning-circle', info: 'info', play: 'play',
  fly: 'paper-plane-tilt', save: 'floppy-disk', empty: 'calendar-check', spark: 'sparkle',
  features: 'chart-bar', institutional: 'bank', diy: 'magic-wand',
  menu: 'list', image: 'image-square',
});

export const icons = Object.freeze(Object.fromEntries(
  Object.entries(ICON_NAMES).map(([key, value]) => [key, `<i class="ph ph-${value}" aria-hidden="true"></i>`]),
));

export function icon(name) { return icons[name] || ''; }

/* ====================== HTML 转义 / Markdown ====================== */

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ----- HTML 净化（白名单，防 DOM XSS） ----- */

// 允许保留的标签（marked 在 gfm 下生成的表格、列表、代码等均在内）
const ALLOWED_TAGS = new Set([
  'a', 'p', 'br', 'hr', 'span', 'div',
  'strong', 'b', 'em', 'i', 'u', 's', 'del', 'ins', 'mark', 'sub', 'sup', 'small',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'ul', 'ol', 'li', 'dl', 'dt', 'dd',
  'blockquote', 'pre', 'code', 'kbd', 'samp', 'var',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
  'abbr', 'cite', 'q', 'figure', 'figcaption',
]);
// 允许保留的属性（不含任何 on* 事件属性与 style）
const ALLOWED_ATTRS = new Set([
  'href', 'title', 'class', 'align', 'colspan', 'rowspan', 'start', 'type',
  'aria-label', 'role', 'dir', 'lang',
]);
// 危险标签：连同内容一并丢弃
const DROP_TAGS = new Set([
  'script', 'style', 'iframe', 'object', 'embed', 'noscript', 'template',
  'svg', 'math', 'link', 'meta', 'base',
  'form', 'input', 'button', 'textarea', 'select', 'option',
]);
// href 仅允许的安全协议（相对路径、锚点、邮箱、电话亦放行）
const SAFE_URL_RE = /^(?:https?:|mailto:|tel:|#|\/|\.\/|\.\.\/)/i;
// 控制字符与空白：剥离后再判定协议，阻断诸如 "java\tscript:" 的伪协议绕过。
// 用字符串构造正则，源码仅含可打印字符；u0000~u0020 覆盖全部 C0 控制符与空格。
// eslint-disable-next-line no-control-regex
const CTRL_WS_RE = new RegExp('[\\s\\u0000-\\u0020]+', 'g');

/** 净化 href：阻断 javascript:/data:/vbscript: 等危险伪协议 */
function safeHref(value) {
  const raw = String(value || '');
  const v = raw.replace(CTRL_WS_RE, '').toLowerCase();
  if (!v) return true;                       // 空 href 无害
  if (SAFE_URL_RE.test(raw.trim())) return true;
  // 既非白名单协议又带显式 scheme（含 ':'）的一律拒绝（如 javascript:/data:/vbscript:）
  return !/^[a-z][a-z0-9+.-]*:/.test(v);
}

/**
 * 基于 DOM 的白名单净化：移除 script/style/iframe/object/embed 等危险标签，
 * 剥离所有 on* 事件属性与 style，过滤 javascript:/data: 等危险 href。
 * 浏览器环境下用 DOMParser 解析后遍历；若已加载 DOMPurify 则优先使用之。
 */
function sanitizeHtml(html) {
  // 若运行环境已注入 DOMPurify（如后续 vendor 引入），优先使用其白名单净化
  if (typeof window !== 'undefined' && window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
    return window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
  }
  // 非浏览器或无 DOMParser 时退化为纯转义（绝不直出未净化 HTML）
  if (typeof document === 'undefined' || typeof DOMParser === 'undefined') {
    return escapeHtml(html);
  }
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const walk = (node) => {
    // 先递归处理子节点（拷贝成数组，避免遍历过程中被增删影响）
    Array.from(node.childNodes).forEach(walk);
    if (node.nodeType !== 1) return; // 仅处理元素节点
    const tag = node.tagName.toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) {
      if (DROP_TAGS.has(tag)) {
        // 危险标签：连同内容一并丢弃（script/style/iframe/object/embed/svg 等）
        node.remove();
      } else {
        // 其余未知或结构性标签：把子节点上提后移除自身，尽量保留正文文本
        const parent = node.parentNode;
        if (parent) {
          while (node.firstChild) parent.insertBefore(node.firstChild, node);
          parent.removeChild(node);
        } else {
          node.remove();
        }
      }
      return;
    }
    // 清洗属性：剥离全部 on* 事件、style 及不在白名单内的属性，校验 href 协议
    Array.from(node.attributes).forEach((attr) => {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on') || name === 'style' || !ALLOWED_ATTRS.has(name)) {
        node.removeAttribute(attr.name);
        return;
      }
      if (name === 'href' && !safeHref(attr.value)) {
        node.removeAttribute(attr.name);
      }
    });
    // 外链统一加 rel，避免 target=_blank 反向篡改（即便后续放开 target）
    if (tag === 'a' && node.getAttribute('href')) {
      node.setAttribute('rel', 'noopener noreferrer nofollow');
    }
  };
  Array.from(doc.body.childNodes).forEach(walk);
  return doc.body.innerHTML;
}

/** Markdown → HTML（marked UMD 全局；缺失时降级为纯文本，输出经白名单净化） */
export function renderMd(text) {
  if (text == null || text === '') return '';
  let html = '';
  try {
    if (window.marked) {
      if (typeof marked.setOptions === 'function' && !renderMd._inited) {
        try { marked.setOptions({ breaks: true, gfm: true, mangle: false, headerIds: false }); } catch { /* 旧版本忽略 */ }
        renderMd._inited = true;
      }
      html = typeof marked.parse === 'function' ? marked.parse(String(text)) : marked(String(text));
    }
  } catch { html = ''; }
  if (!html) html = '<p>' + escapeHtml(text).replace(/\n/g, '<br>') + '</p>';
  // 白名单净化：移除危险标签、剥离 on* 事件属性、过滤 javascript:/data: 危险 href
  return sanitizeHtml(html);
}

/* ====================== Toast ====================== */

export function toast(msg, type = 'info', duration = 3200) {
  const root = document.getElementById('toast-root');
  if (!root) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const ic = type === 'success' ? 'check' : type === 'error' ? 'alert' : 'info';
  el.innerHTML = `${icon(ic)}<span>${escapeHtml(msg)}</span>`;
  root.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 260);
  }, duration);
}

/* ====================== Modal ====================== */

let modalSeq = 0;

export function openModal({ title = '', body = '', footer = '', onMount }) {
  closeModal();
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const root = document.getElementById('modal-root');
  const overlay = document.createElement('div');
  const titleId = `modal-title-${++modalSeq}`;
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="${titleId}" tabindex="-1">
      <div class="modal-head">
        <span id="${titleId}">${escapeHtml(title)}</span>
        <button class="icon-btn" data-close aria-label="关闭">${icon('close')}</button>
      </div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-foot">${footer}</div>` : ''}
    </div>`;
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay || e.target.closest('[data-close]')) closeModal();
  });
  root.appendChild(overlay);
  overlay._returnFocus = previousFocus;
  const modal = overlay.querySelector('.modal');
  const keyHandler = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeModal();
      return;
    }
    if (e.key !== 'Tab') return;
    const focusable = [...modal.querySelectorAll(
      'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
    )].filter((el) => !el.hidden && el.getClientRects().length > 0);
    if (!focusable.length) {
      e.preventDefault();
      modal.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  document.addEventListener('keydown', keyHandler);
  overlay._keyHandler = keyHandler;
  if (onMount) onMount(modal);
  requestAnimationFrame(() => {
    if (!overlay.isConnected) return;
    const initial = modal.querySelector('[data-close],button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])');
    (initial || modal).focus();
  });
  return overlay;
}

export function closeModal() {
  const root = document.getElementById('modal-root');
  let returnFocus = null;
  root.querySelectorAll('.modal-overlay').forEach((o) => {
    if (o._keyHandler) document.removeEventListener('keydown', o._keyHandler);
    if (o._returnFocus instanceof HTMLElement && o._returnFocus.isConnected) returnFocus = o._returnFocus;
    o.remove();
  });
  if (returnFocus) requestAnimationFrame(() => returnFocus.focus());
}

/** 确认对话框 → Promise<boolean> */
export function confirmModal(title, text, { danger = false, okText = '确认' } = {}) {
  return new Promise((resolve) => {
    // Escape 走 openModal 的全局 escHandler 直接关闭，不经过按钮回调——必须自行兜底 settle，否则 Promise 永久 pending
    const esc = (e) => { if (e.key === 'Escape') done(false); };
    const done = (val) => { document.removeEventListener('keydown', esc); resolve(val); };
    const overlay = openModal({
      title,
      body: `<p style="color:var(--text-2);font-size:15px;line-height:1.7">${escapeHtml(text)}</p>`,
      footer: `
        <button class="btn btn-ghost" data-act="cancel">取消</button>
        <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok">${escapeHtml(okText)}</button>`,
      onMount(modal) {
        modal.querySelector('[data-act="ok"]').addEventListener('click', () => { closeModal(); done(true); });
        modal.querySelector('[data-act="cancel"]').addEventListener('click', () => { closeModal(); done(false); });
      },
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay || e.target.closest('[data-close]')) done(false);
    });
    document.addEventListener('keydown', esc);
  });
}

/* ====================== 组件模板 ====================== */

/**
 * 数据状态角标。兼容旧签名 dataStamp(ts, missing)，也支持第三个参数传入：
 * { freshness/state, fallback_level, delayed, stale, source, delay_text, fallback_label, notes }。
 *
 * 铁律：没有显式 freshness 时只说“更新于”，不冒充实时；
 * 状态回答“当前能否支撑决策”，来源/口径说明只进 tooltip，不再被误报成数据不全。
 */
export function dataStamp(ts, missing, options = {}) {
  let value = ts;
  let meta = options && typeof options === 'object' ? { ...options } : { freshness: options };
  if (ts && typeof ts === 'object' && !Array.isArray(ts)) {
    meta = { ...ts, ...meta };
    value = ts.ts || ts.as_of || ts.event_time || ts.data_date || ts.updated_at || '';
    if (missing == null) missing = ts.missing;
  }

  const normalizeList = (value) => Array.isArray(value)
    ? value.filter(Boolean).map(String)
    : (value ? [String(value)] : []);
  const legacyMisses = normalizeList(missing);
  const qualityAware = Boolean(
    meta.data_state || meta.state_label
    || Array.isArray(meta.missing_fields) || Array.isArray(meta.degraded_factors)
  );
  const misses = normalizeList(meta.missing_fields);
  const degraded = normalizeList(meta.degraded_factors);
  const legacyPartial = !qualityAware && legacyMisses.length > 0;
  const knownStates = ['live', 'complete', 'degraded', 'accumulating', 'delayed', 'stale', 'fallback', 'missing'];
  const explicit = [meta.freshness, meta.data_state, meta.state]
    .map((item) => String(item || '').toLowerCase())
    .find((item) => knownStates.includes(item)) || '';
  let state = explicit || 'asof';
  if (Number(meta.fallback_level || 0) > 0 || meta.fallback === true || meta.source_role === 'fallback') state = 'fallback';
  if (meta.stale === true || meta.expired === true) state = 'stale';
  else if (meta.delayed === true) state = 'delayed';
  if (misses.length || meta.missing === true || (!value && !explicit)) state = 'missing';
  else if (qualityAware && degraded.length && !explicit) state = 'degraded';
  else if (legacyPartial && !explicit) state = 'degraded';

  const notes = normalizeList(meta.notes);
  // 结构化质量响应中，旧 missing 仅为兼容字段；它可能包含来源/口径说明，不能再触发缺失。
  // 新响应已把同一批说明放进 notes 时，不再重复追加兼容字段。
  const legacyNotes = qualityAware && notes.length ? [] : legacyMisses;
  const labels = {
    live: '实时', complete: '决策可用', degraded: '增强项缺失', accumulating: '样本积累',
    delayed: meta.delay_text || '延迟', stale: '已过期',
    fallback: meta.fallback_label || '备用源', missing: '暂不可用', asof: '更新于',
  };
  if (legacyPartial && state === 'degraded' && !meta.state_label) labels.degraded = '部分覆盖';
  if (meta.state_label) labels[state] = String(meta.state_label);
  const rawTime = String(value || '');
  const match = rawTime.match(/(?:T|\s)(\d{2}:\d{2}(?::\d{2})?)/);
  const timeText = match ? match[1] : rawTime;
  const detail = [
    state === 'missing' && misses.length ? misses.join('；') : '',
    degraded.length ? degraded.join('；') : '',
    notes.length ? notes.join('；') : '',
    legacyNotes.length ? legacyNotes.join('；') : '',
    meta.source ? `来源：${meta.source}` : '',
  ].filter(Boolean).join('\n');
  return `<span class="data-stamp" data-state="${state}"${detail ? ` title="${escapeHtml(detail)}"` : ''}>`
    + '<span class="ds-dot" aria-hidden="true"></span>'
    + `<span class="ds-label">${escapeHtml(labels[state])}</span>`
    + `${timeText ? `<span class="num">${escapeHtml(timeText)}</span>` : ''}</span>`;
}

/** 卡片外壳 */
export function card({ title = '', extra = '', body = '', cls = '', span = '', hover = true }) {
  return `<section class="card ${hover ? 'hoverable' : ''} ${cls} ${span}">
    ${title ? `<div class="card-head"><h3 class="card-title">${title}</h3><div class="card-extra">${extra}</div></div>` : ''}
    <div class="card-body">${body}</div>
  </section>`;
}

/** 空状态 */
export function emptyState(title, sub = '', action = '', iconName = 'empty') {
  return `<div class="empty-state">
    ${icon(iconName)}
    <div class="es-title">${escapeHtml(title)}</div>
    ${sub ? `<div class="es-sub">${escapeHtml(sub)}</div>` : ''}
    ${action}
  </div>`;
}

/** 加载骨架 */
export function skeleton(lines = 3, heights = []) {
  let html = '<div class="skel-group">';
  for (let i = 0; i < lines; i++) {
    const h = heights[i] || (i === 0 ? 22 : 14);
    const w = 100 - (i % 3) * 12;
    html += `<div class="skel" style="height:${h}px;width:${w}%"></div>`;
  }
  return html + '</div>';
}

/** 数值变化闪烁 */
export function flashEl(el, dir) {
  if (!el) return;
  const cls = dir === 'down' ? 'flash-down' : 'flash-up';
  el.classList.remove('flash-up', 'flash-down');
  void el.offsetWidth;
  el.classList.add(cls);
}

/* ====================== 溯源可信度徽标 ====================== */

/**
 * 把 /api/xverify 返回对象（含 sources/confidence/spread_pct/per_source）渲染成
 * 「来源+可信度」小徽标，追加在价格/点位 <span> 之后。例如：
 *   `<span class="num ${pctClass(d.change_pct)}">${fmtPrice(xv.price)}</span>${sourceBadge(xv)}`
 * 红涨绿跌仍由价格本身的 pctClass 决定——本徽标只表达「数据可不可信」，与涨跌配色解耦
 * （high 用绿色表「可信」，并非涨跌语义；红色价格旁出现绿色高可信徽标是正常的）。
 */
export function sourceBadge(xv) {
  if (!xv || !xv.confidence) return '';
  const MAP = {
    high:   { txt: '高可信', color: '#22c55e' },
    low:    { txt: '存疑',   color: 'var(--amber, #f59e0b)' },
    single: { txt: '单源',   color: 'var(--text-3, #8b93a7)' },
    none:   { txt: '无源',   color: 'var(--text-3, #8b93a7)' },
  };
  const m = MAP[xv.confidence] || MAP.none;
  const srcs = Array.isArray(xv.sources) && xv.sources.length ? xv.sources.join('+') : '—';
  // tooltip 展开各源原始价 + 价差，便于人工核对分歧
  const detail = Object.entries(xv.per_source || {}).map(([k, v]) => `${k}:${v}`).join('  ');
  const spread = xv.spread_pct != null ? `  价差${xv.spread_pct}%` : '';
  const title = `来源 ${srcs}${spread}${detail ? '\n' + detail : ''}`;
  return `<span class="src-badge" title="${escapeHtml(title)}" `
    + `style="display:inline-flex;align-items:center;gap:3px;margin-left:6px;`
    + `padding:1px 6px;border-radius:6px;font-size:11px;line-height:1.4;`
    + `color:${m.color};border:1px solid ${m.color}55;background:${m.color}14;`
    + `vertical-align:middle;white-space:nowrap">`
    + `<i style="width:5px;height:5px;border-radius:50%;background:${m.color};display:inline-block"></i>`
    + `${escapeHtml(srcs)} · ${m.txt}</span>`;
}
