/* api.js — HTTP 封装、SSE 解析、格式化工具、共享状态 */

const BASE = '/api';

// GET 请求单飞 + 会话级 stale-while-revalidate：
// - 首次进入没有缓存时才等网络；
// - 页签来回切换时立即返回上次成功结果，后台静默刷新；
// - 每个视图仍按数据自带的 ts/data_date 显示新鲜度，不把旧值伪装成实时。
// 缓存只存当前页面内存，跨北京交易日自动失效。
const GET_CACHE = new Map();
const GET_INFLIGHT = new Map();

function getTtl(path) {
  const p = String(path || '');
  if (p === '/ext/views') return 5 * 60_000;
  if (p === '/shell-status') return 30_000;
  if (p === '/status') return 10_000;
  if (p === '/market/indices') return 3_000;
  if (/^\/(pulse|cockpit|sysrisk|sentiment\/today)(?:\?|$)/.test(p)) return 10_000;
  if (/^\/market\/(mainlines|overview|flow|stockflow)(?:\?|$)/.test(p)) return 15_000;
  if (/^\/market\/(multiperiod|sectors)(?:\?|$)/.test(p)) return 60_000;
  if (/^\/capital\/(?:trends|summary)(?:\?|$)/.test(p)) return 60_000;
  if (/^\/reports\//.test(p)) return 60_000;
  if (p === '/archive/dates') return 60_000;
  if (p.startsWith('/archive/')) return 5 * 60_000;
  return 10_000;
}

// 鲜缓存过期后仍可用于“秒开”的最长窗口。返回旧值的同时会立即后台取新；
// 盘中页面自身的 30/60s 轮询会在下一轮渲染新缓存。
function getStaleTtl(path) {
  const p = String(path || '');
  if (p === '/ext/views') return 30 * 60_000;
  if (/^\/(shell-status|status)(?:\?|$)/.test(p)) return 5 * 60_000;
  if (p === '/market/indices') return 5 * 60_000;
  if (/^\/(pulse|cockpit|sysrisk|sentiment\/today)(?:\?|$)/.test(p)) return 30 * 60_000;
  if (/^\/market\//.test(p)) return 30 * 60_000;
  if (/^\/capital\//.test(p)) return 30 * 60_000;
  if (/^\/(reports|archive)\//.test(p)) return 60 * 60_000;
  return 15 * 60_000;
}

function getTimeout(path) {
  const p = String(path || '');
  // GET 只负责查询。任一公开源卡死都必须释放浏览器连接和单飞槽，不能让整页
  // 永久停在骨架；真正的长模型生成使用独立 SSE 通道，不经过这里。
  if (/^\/(archive|reports)(?:\/|\?|$)/.test(p)) return 30_000;
  if (/^\/market\/(mainlines|multiperiod|flow|stockflow|sectors)(?:\?|$)/.test(p)) return 20_000;
  return 12_000;
}

/** 全局共享状态（顶栏状态等） */
export const appState = {
  status: null,        // /api/status 缓存
  isTradeDay: true,
};

async function request(path, options = {}) {
  const { timeoutMs = 0, signal: callerSignal, ...fetchOptions } = options || {};
  // 外部 signal（换股/切页取消）与内部墙钟超时必须同时生效。旧实现一旦调用方
  // 传 signal 就完全关闭 12 秒超时，页面可能永远挂在骨架；反过来内部超时又把
  // 原始英文 AbortError 直接展示给用户。
  const controller = (callerSignal || Number(timeoutMs) > 0) ? new AbortController() : null;
  let timedOut = false;
  const onCallerAbort = () => controller?.abort(callerSignal?.reason);
  if (callerSignal) {
    if (callerSignal.aborted) onCallerAbort();
    else callerSignal.addEventListener('abort', onCallerAbort, { once: true });
  }
  const timeout = controller && Number(timeoutMs) > 0
    ? setTimeout(() => { timedOut = true; controller.abort(); }, Number(timeoutMs))
    : null;
  try {
    const res = await fetch(BASE + path, {
      headers: { 'Content-Type': 'application/json', ...(fetchOptions.headers || {}) },
      ...fetchOptions,
      signal: controller?.signal,
    });
    const j = await res.json().catch(() => null);
    if (!res.ok) {
      const error = new Error(j?.error || `HTTP ${res.status} ${res.statusText}`);
      error.status = res.status;
      error.data = j;
      throw error;
    }
    // 全局数据新鲜度：任何接口成功即刷新"最近取数成功"时点（顶栏时点条据此渲染）。
    // 失联时旧数据不再无声冒充新数据——SWR 诚实性收口。
    appState.netOkAt = Date.now();
    appState.netFails = 0;
    return j;
  } catch (e) {
    if (e?.name === 'AbortError') {
      if (!timedOut) throw e; // 用户切页/换股：交给视图静默丢弃陈旧请求。
      const seconds = Math.max(1, Math.round(Number(timeoutMs) / 1000));
      const timeoutError = new Error(`数据源响应超过 ${seconds} 秒，已停止等待；已有成功数据会继续保留。`);
      timeoutError.name = 'TimeoutError';
      timeoutError.cause = e;
      throw timeoutError;
    }
    appState.netFails = (appState.netFails || 0) + 1;
    appState.netFailAt = Date.now();
    throw e;
  } finally {
    if (timeout) clearTimeout(timeout);
    if (callerSignal) callerSignal.removeEventListener('abort', onCallerAbort);
  }
}

async function getRequest(path, options = {}) {
  const {
    force = false,
    ttlMs = getTtl(path),
    staleTtlMs = getStaleTtl(path),
    timeoutMs = getTimeout(path),
    ...fetchOptions
  } = options || {};
  const cacheable = !fetchOptions.signal && Number(ttlMs) > 0;
  const now = Date.now();
  const cacheDay = todayStr();
  let cached = GET_CACHE.get(path);
  if (cached && cached.day !== cacheDay) {
    GET_CACHE.delete(path);
    cached = null;
  }
  if (!force && cacheable && cached && now - cached.at < Number(ttlMs)) {
    return cached.value;
  }
  // 过了 fresh TTL 也不把用户退回全页骨架：先给上次成功值，
  // 同时只启动一个后台刷新。后台失败保留 last-good，不会产生未处理 rejection。
  if (!force && cacheable && cached && now - cached.at < Number(staleTtlMs)) {
    if (!GET_INFLIGHT.has(path)) {
      const background = request(path, { ...fetchOptions, timeoutMs })
        .then((value) => {
          GET_CACHE.set(path, { at: Date.now(), day: todayStr(), value });
          return value;
        })
        .finally(() => {
          if (GET_INFLIGHT.get(path) === background) GET_INFLIGHT.delete(path);
        });
      GET_INFLIGHT.set(path, background);
      background.catch(() => undefined);
    }
    return cached.value;
  }
  if (!force && cacheable && GET_INFLIGHT.has(path)) {
    return GET_INFLIGHT.get(path);
  }

  const pending = request(path, { ...fetchOptions, timeoutMs }).then((value) => {
    if (cacheable) GET_CACHE.set(path, { at: Date.now(), day: todayStr(), value });
    return value;
  }).finally(() => {
    if (GET_INFLIGHT.get(path) === pending) GET_INFLIGHT.delete(path);
  });
  if (cacheable) GET_INFLIGHT.set(path, pending);
  return pending;
}

function invalidateGet(prefix = '') {
  const wanted = String(prefix || '');
  for (const key of GET_CACHE.keys()) {
    if (!wanted || key.startsWith(wanted)) GET_CACHE.delete(key);
  }
}

async function mutate(path, options) {
  const value = await request(path, options);
  // 写操作后不猜测它影响了哪张卡；清空短缓存保证下次读到新状态。
  invalidateGet();
  return value;
}

export const api = {
  get: (path, options = {}) => getRequest(path, options),
  post: (path, body, options = {}) => mutate(path, {
    ...options,
    method: 'POST',
    body: body != null ? JSON.stringify(body) : undefined,
  }),
  put: (path, body) => mutate(path, { method: 'PUT', body: JSON.stringify(body) }),
  del: (path) => mutate(path, { method: 'DELETE' }),
  invalidate: invalidateGet,
  prefetch: (path, options = {}) => getRequest(path, options).catch(() => null),
};

/** AI 功能入口预检：不把“未配置模型”留到发起长 SSE 后才报错。 */
export async function aiReadiness({ force = false } = {}) {
  try {
    const status = await getRequest('/status', { force, timeoutMs: 5000 });
    appState.status = status;
    const model = status?.model_connection || {};
    const capability = status?.hermes?.agent_capability || {};
    if (!model.configured) {
      return { ready: false, code: 'model_not_configured', reason: 'AI 增强未配置，请先到「系统设置 → 任务中心 / 模型连接」保存可用连接。' };
    }
    if (model.connected === false || capability.ready === false) {
      return {
        ready: false,
        code: capability.status || 'model_unavailable',
        reason: capability.reason || '当前模型连接或 Agent 权限预检未通过，请到系统设置检查。',
      };
    }
    return { ready: true, code: 'ready', reason: '' };
  } catch (error) {
    return { ready: false, code: 'status_unavailable', reason: `无法确认 AI 连接状态：${error?.message || '状态接口不可用'}` };
  }
}

/**
 * SSE over fetch（POST）。逐条解析 `data: {...}` JSON。
 * handlers: { onDelta(text), onDone(obj), onError(msg) }
 * 返回 Promise，流结束后 resolve。
 */
export async function ssePost(path, body, handlers = {}) {
  const { onDelta, onDone, onError, onStage, signal } = handlers;
  let res;
  try {
    res = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body || {}),
      signal,
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    onError && onError('网络请求失败：' + e.message);
    return;
  }
  if (!res.ok || !res.body) {
    onError && onError(`服务返回 HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  let finished = false;

  // 无输出看门狗：流连续 90s 无任何数据（阶段/增量）视为卡死，主动中止并报错，避免界面
  // 无限"生成中…"（网关/上游模型卡住或限流时）。任何 SSE 数据到达都会重置它，正常流式不受影响。
  let wdTimer = null;
  const clearWd = () => { if (wdTimer) { clearTimeout(wdTimer); wdTimer = null; } };
  const armWd = () => {
    clearWd();
    wdTimer = setTimeout(() => {
      if (finished) return;
      finished = true;
      onError && onError('长时间无响应（当前模型连接或上游服务可能卡住/限流），已自动中止。可点重试；若频繁出现，请到设置检查当前模型连接；仅在当前连接使用 Hermes 时再检查 Hermes 集成。');
      try { reader.cancel(); } catch { /* noop */ }
    }, 90000);
  };

  const handleEvent = (raw) => {
    for (const line of raw.split('\n')) {
      const m = /^data:\s?(.*)$/.exec(line.replace(/\r$/, ''));
      if (!m) continue;
      let obj;
      try { obj = JSON.parse(m[1]); } catch { continue; }
      if (obj.error != null) { finished = true; onError && onError(String(obj.error)); return; }
      if (obj.done) { finished = true; onDone && onDone(obj); return; }
      if (obj.stage != null) { onStage && onStage(String(obj.stage)); continue; }  // 首个 delta 前的阶段提示（采集/生成中），未接 onStage 则静默忽略
      if (obj.delta != null) onDelta && onDelta(String(obj.delta));
    }
  };

  armWd();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      armWd();  // 收到数据 → 重置看门狗
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        handleEvent(chunk);
        if (finished) { try { reader.cancel(); } catch { /* noop */ } return; }
      }
    }
    if (buf.trim()) handleEvent(buf);
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    if (!finished) { onError && onError('数据流中断：' + e.message); return; }
  } finally {
    clearWd();
  }
  // 没有服务端显式 done 就不能把断流/代理提前关闭冒充完成。
  if (!finished) onError && onError('数据流未返回完成标记，已按不完整输出隔离。');
}

/* ====================== 格式化工具 ====================== */

const isNil = (v) => v === null || v === undefined || (typeof v === 'number' && !isFinite(v));

/** 金额（元）→ 人性化 万/亿 */
export function fmtMoney(v, digits = 2) {
  if (isNil(v)) return '--';
  const n = Number(v);
  const abs = Math.abs(n);
  if (abs >= 1e12) return (n / 1e12).toFixed(digits) + '万亿';
  if (abs >= 1e8) return (n / 1e8).toFixed(digits) + '亿';
  if (abs >= 1e4) return (n / 1e4).toFixed(digits >= 2 ? 1 : digits) + '万';
  return n.toFixed(0);
}

/** 百分比（输入即百分数数值，0.85 → +0.85%） */
export function fmtPct(v, { signed = true, digits = 2 } = {}) {
  if (isNil(v)) return '--';
  const n = Number(v);
  const sign = signed && n > 0 ? '+' : '';
  return sign + n.toFixed(digits) + '%';
}

/** 普通数字千分位 */
export function fmtNum(v, digits = 0) {
  if (isNil(v)) return '--';
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 价格 */
export function fmtPrice(v, digits = 2) {
  if (isNil(v)) return '--';
  return Number(v).toFixed(digits);
}

/** 涨跌颜色 class：up/down/flat */
export function pctClass(v) {
  if (isNil(v)) return 'flat';
  const n = Number(v);
  if (n > 0.0001) return 'up';
  if (n < -0.0001) return 'down';
  return 'flat';
}

/** 北京时间各分量（A 股时段判断一律以北京时间为准，海外/云端部署也正确） */
export function cnNow() {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', weekday: 'short',
  }).formatToParts(new Date());
  const get = (t) => parts.find((p) => p.type === t)?.value || '';
  const weekMap = { 周日: 0, 周一: 1, 周二: 2, 周三: 3, 周四: 4, 周五: 5, 周六: 6 };
  return {
    y: get('year'), m: get('month'), d: get('day'),
    hh: Number(get('hour')) % 24, mm: Number(get('minute')), ss: Number(get('second')),
    day: weekMap[get('weekday')] ?? new Date().getDay(),
  };
}

/** 当前是否 A 股交易时段（北京时间 09:25–15:05，工作日且后端标记为交易日） */
export function isTradingTime() {
  const c = cnNow();
  if (c.day === 0 || c.day === 6) return false;
  if (appState.status && appState.status.is_trade_day === false) return false;
  const hm = c.hh * 60 + c.mm;
  return hm >= 9 * 60 + 25 && hm <= 15 * 60 + 5;
}

/** 今天 YYYY-MM-DD（北京时间） */
export function todayStr() {
  const c = cnNow();
  return `${c.y}-${c.m}-${c.d}`;
}

/* ====================== localStorage 工具 ====================== */

export function lsGet(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch { return fallback; }
}

export function lsSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 容量满等忽略 */ }
}
