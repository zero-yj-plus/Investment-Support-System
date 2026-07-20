/* charts.js — ECharts 终端化主题与图表构建（echarts 为 UMD 全局）
   设计语言：OBSIDIAN 终端 · 琥珀金 × 红涨绿跌 · 等宽数字
   配色铁律：option 体内不写死暗色 hex —— 一律引用随主题刷新的模块变量，
   使日间/夜间切换（toggleTheme→refreshChartTheme→route(true) 重建）后图表色随之更新。 */

import { fmtMoney, fmtPct, fmtPrice } from './api.js';

/* 读取当前主题的 CSS 变量（随日间/夜间切换），让图表配色与全站一致 */
function cssVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch { return fallback; }
}

// 这些导出量随主题刷新（ES 模块实时绑定，引用方在渲染时取到最新值）
export let UP = '#ff4d5e';
export let DOWN = '#11cc8e';
export let FLAT = '#767d8c';
export let AMBER = '#ffb020';
export let CYAN = '#46c9ff';
export let BRAND1 = AMBER;
export let BRAND2 = '#ff8f12';

// 文本/面板/分隔线随主题刷新（替代各 option 体内写死的暗色 hex）
let TX = '#e9ecf2';   // --text
let TX2 = '#9aa1ae';  // --text-2
let TX3 = '#656c79';  // --text-3
let PANEL = '#0e1014'; // --panel（treemap/pie 间隙边框与卡片底色一致）
let HAIR = '#31353f';  // --border-strong（坐标/缩略轴细线，两主题都可见）

// 相位语义色相不走 CSS 变量：深浅两套字面映射，refreshChartTheme 按主题重赋值；
// 引用方（dashboard/sentiment/archive）为 ES 实时绑定，渲染时自动取到当前主题变体
const PHASE_COLORS_DARK = {
  '冰点期': '#3d7eff',
  '修复期': '#41c7ff',
  '发酵期': '#ffb000',
  '高潮期': '#ff4757',
  '分歧期': '#c77dff',
  '退潮期': '#00c98a',
};
// 浅色加深变体：白底/白色 tooltip 上原亮色对比不足 2:1，加深后≈4.5:1，色相与深色版一致
const PHASE_COLORS_LIGHT = {
  '冰点期': '#2456cc',
  '修复期': '#0a7fb5',
  '发酵期': '#b5740a',
  '高潮期': '#e02d3c',
  '分歧期': '#7d2fc4',
  '退潮期': '#0a8a5f',
};
export let PHASE_COLORS = PHASE_COLORS_DARK;

const MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';
let AXIS_LABEL = { color: TX3, fontSize: 10, fontFamily: MONO };
let SPLIT_LINE = { lineStyle: { color: 'rgba(255,255,255,0.045)' } };
let AXIS_LINE = { lineStyle: { color: 'rgba(255,255,255,0.10)' } };
let PTR_LABEL = { backgroundColor: '#1a1d23', color: '#ffb000', fontFamily: MONO, fontSize: 10 };
let TOOLTIP_BG = 'rgba(10,11,13,0.96)';
let TOOLTIP_TX = '#e6e9ee';
let TOOLTIP_BORDER = 'rgba(255,176,0,0.25)';

/** 从 CSS 变量重新读取主题色（主题切换时调用）。 */
export function refreshChartTheme() {
  PHASE_COLORS = document.documentElement.dataset.theme === 'light' ? PHASE_COLORS_LIGHT : PHASE_COLORS_DARK;
  UP = cssVar('--up', UP);
  DOWN = cssVar('--down', DOWN);
  FLAT = cssVar('--flat', FLAT);
  AMBER = cssVar('--amber', AMBER);
  CYAN = cssVar('--cyan', CYAN);
  BRAND1 = AMBER;
  BRAND2 = cssVar('--amber-2', BRAND2);
  TX = cssVar('--text', TX);
  TX2 = cssVar('--text-2', TX2);
  TX3 = cssVar('--text-3', TX3);
  PANEL = cssVar('--panel', PANEL);
  HAIR = cssVar('--border-strong', HAIR);
  const line = cssVar('--line', 'rgba(255,255,255,0.045)');
  const border = cssVar('--border-strong', 'rgba(255,255,255,0.10)');
  AXIS_LABEL = { color: TX3, fontSize: 10, fontFamily: MONO };
  SPLIT_LINE = { lineStyle: { color: line } };
  AXIS_LINE = { lineStyle: { color: border } };
  TOOLTIP_BG = cssVar('--modal-bg', TOOLTIP_BG);
  TOOLTIP_TX = cssVar('--text', TOOLTIP_TX);
  TOOLTIP_BORDER = cssVar('--amber-line', TOOLTIP_BORDER);
  PTR_LABEL = { backgroundColor: cssVar('--panel-3', '#1a1d23'), color: AMBER, fontFamily: MONO, fontSize: 10 };
  if (window.echarts) registerHermesTheme();
}

function registerHermesTheme() {
  echarts.registerTheme('hermes', {
    backgroundColor: 'transparent',
    textStyle: { color: TX2, fontFamily: 'inherit' },
    color: [AMBER, CYAN, UP, DOWN, '#c77dff', BRAND2],
    tooltip: {
      backgroundColor: TOOLTIP_BG,
      borderColor: TOOLTIP_BORDER,
      borderWidth: 1,
      textStyle: { color: TOOLTIP_TX, fontSize: 11, fontFamily: MONO },
      extraCssText: 'border-radius:5px;box-shadow:var(--shadow-pop, 0 8px 28px rgba(0,0,0,.6));padding:8px 11px;',
    },
  });
}

function ensureTheme() {
  if (!window.echarts) return;
  registerHermesTheme();
}

// 模块加载即按当前主题初始化一次
refreshChartTheme();

/** 创建图表（自动 resize；调用方负责 dispose） */
export function mkChart(el) {
  if (!window.echarts || !el) return null;
  ensureTheme();
  const chart = echarts.init(el, 'hermes');
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(() => { if (!chart.isDisposed()) chart.resize(); });
    ro.observe(el);
    chart._ro = ro;
  }
  return chart;
}

export function disposeCharts(list) {
  (list || []).forEach((c) => {
    if (!c) return;
    // 先无条件断开 ResizeObserver（即便实例已被 dispose，观察器仍可能挂在脱离的 DOM 上造成泄漏）
    if (c._ro) {
      try { c._ro.disconnect(); } catch { /* noop */ }
      c._ro = null;
    }
    if (typeof c.isDisposed === 'function' && !c.isDisposed()) c.dispose();
  });
  if (Array.isArray(list)) list.length = 0;
}

/* ================= 指数主图：K 线（日/周）MA5/10/20/60 + 量 ================= */

function ma(closes, n) {
  return closes.map((_, i) => {
    if (i < n - 1) return null;
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += closes[j];
    return +(s / n).toFixed(2);
  });
}

// MA60 用主题文本色（深色=浅字 / 浅色=深字），其余为跨主题可读的固定品牌色
function maDefs() {
  return [
    { n: 5, color: AMBER },
    { n: 10, color: CYAN },
    { n: 20, color: '#c77dff' },
    { n: 60, color: TX },
  ];
}

export function klineOption(bars, { zoomStart = 55 } = {}) {
  const data = bars || [];
  const dates = data.map((d) => d.d ?? '');
  const closes = data.map((d) => d.c);
  const k = data.map((d) => [d.o, d.c, d.l, d.h]);
  const vols = data.map((d, i) => ({
    value: d.v,
    itemStyle: { color: d.c >= d.o ? 'rgba(255,71,87,0.55)' : 'rgba(0,201,138,0.55)' },
  }));
  const MA_DEFS = maDefs();
  const maSeries = MA_DEFS.map((m) => ({
    name: `MA${m.n}`, type: 'line', data: ma(closes, m.n),
    xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, smooth: true,
    lineStyle: { width: 1, color: m.color, opacity: 0.9 }, z: 4, silent: true,
  }));
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: {
      top: 0, left: 4, itemWidth: 10, itemHeight: 2, icon: 'rect',
      textStyle: { color: TX3, fontSize: 10, fontFamily: MONO },
      data: MA_DEFS.map((m) => `MA${m.n}`), selectedMode: false,
    },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', label: PTR_LABEL },
      formatter: (ps) => {
        const p = ps.find((x) => x.seriesType === 'candlestick');
        if (!p) return '';
        const d = data[p.dataIndex] || {};
        const prev = data[p.dataIndex - 1];
        const chg = prev && prev.c ? ((d.c - prev.c) / prev.c) * 100 : null;
        const cc = chg == null ? FLAT : chg >= 0 ? UP : DOWN;
        return `<b style="color:${AMBER}">${p.axisValue}</b><br/>`
          + `开 ${fmtPrice(d.o)}　高 ${fmtPrice(d.h)}<br/>低 ${fmtPrice(d.l)}　收 <b style="color:${cc}">${fmtPrice(d.c)}</b>`
          + (chg != null ? `<br/>涨跌 <span style="color:${cc}">${fmtPct(chg)}</span>` : '')
          + `<br/>量 ${fmtMoney(d.v, 1)}`;
      },
    },
    grid: [
      { left: 2, right: 50, top: 20, height: '64%' },
      { left: 2, right: 50, top: '76%', height: '17%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false }, axisTick: { show: false }, axisLine: AXIS_LINE, boundaryGap: true },
      { type: 'category', data: dates, gridIndex: 1, axisTick: { show: false }, axisLine: AXIS_LINE, boundaryGap: true,
        axisLabel: { ...AXIS_LABEL, formatter: (v) => String(v).slice(5) } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, position: 'right', axisLabel: { ...AXIS_LABEL, formatter: (v) => v.toFixed(0) }, splitLine: SPLIT_LINE },
      { gridIndex: 1, position: 'right', axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: zoomStart, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], top: '95%', height: 14, start: zoomStart, end: 100,
        borderColor: HAIR, backgroundColor: 'transparent',
        fillerColor: cssVar('--amber-dim', 'rgba(255,176,0,0.10)'), handleStyle: { color: AMBER },
        moveHandleStyle: { color: AMBER }, textStyle: { color: TX3, fontSize: 9 },
        dataBackground: { lineStyle: { color: HAIR }, areaStyle: { color: cssVar('--line', 'rgba(255,255,255,0.04)') } } },
    ],
    series: [
      {
        name: 'K', type: 'candlestick', data: k, xAxisIndex: 0, yAxisIndex: 0, barMaxWidth: 11, z: 3,
        itemStyle: { color: UP, color0: 'transparent', borderColor: UP, borderColor0: DOWN, borderWidth: 1.2 },
      },
      ...maSeries,
      { name: 'VOL', type: 'bar', data: vols, xAxisIndex: 1, yAxisIndex: 1, barWidth: '62%' },
    ],
  };
}

/* ================= 指数/个股分时（价格+均价+昨收基准+量） ================= */

export function minuteOption(minute, prevClose, { baseline = true } = {}) {
  const data = minute || [];
  const times = data.map((m) => m.t ?? '');
  const prices = data.map((m) => m.price);
  const avgs = data.map((m) => m.avg);
  const pc = Number(prevClose) || null;
  const last = prices[prices.length - 1];
  const lineColor = pc && last != null ? (last >= pc ? UP : DOWN) : AMBER;
  const vols = data.map((m, i) => ({
    value: m.volume,
    itemStyle: { color: (i > 0 && data[i - 1].price != null && m.price < data[i - 1].price) ? DOWN : UP },
  }));
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', label: PTR_LABEL },
      formatter: (ps) => {
        const i = ps[0]?.dataIndex ?? 0;
        const m = data[i] || {};
        const chg = pc && m.price != null ? ((m.price - pc) / pc) * 100 : null;
        return `<b style="color:${AMBER}">${ps[0]?.axisValue || ''}</b><br/>价 ${fmtPrice(m.price)}${chg != null ? ` <span style="color:${chg >= 0 ? UP : DOWN}">${fmtPct(chg)}</span>` : ''}<br/>均 ${fmtPrice(m.avg)}<br/>量 ${fmtMoney(m.volume, 1)}`;
      },
    },
    grid: [
      { left: 2, right: 50, top: 8, height: '66%' },
      { left: 2, right: 50, top: '80%', height: '16%' },
    ],
    xAxis: [
      { type: 'category', data: times, gridIndex: 0, axisLabel: { show: false }, axisTick: { show: false }, axisLine: AXIS_LINE },
      { type: 'category', data: times, gridIndex: 1, axisTick: { show: false }, axisLine: AXIS_LINE,
        axisLabel: { ...AXIS_LABEL, interval: Math.max(1, Math.floor(times.length / 4)) } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, position: 'right', axisLabel: { ...AXIS_LABEL, formatter: (v) => v.toFixed(v > 1000 ? 0 : 2) }, splitLine: SPLIT_LINE },
      { gridIndex: 1, position: 'right', axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '价', type: 'line', data: prices, xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { width: 1.4, color: lineColor }, z: 3,
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: lineColor === UP ? 'rgba(255,71,87,0.18)' : lineColor === DOWN ? 'rgba(0,201,138,0.16)' : 'rgba(255,176,0,0.15)' },
            { offset: 1, color: 'rgba(0,0,0,0)' },
          ]),
        },
        markLine: pc && baseline ? {
          silent: true, symbol: 'none', label: { show: false },
          data: [{ yAxis: pc }], lineStyle: { color: FLAT, type: 'dashed', width: 1, opacity: 0.7 },
        } : undefined,
      },
      { name: '均', type: 'line', data: avgs, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1, color: AMBER, opacity: 0.95 }, z: 2 },
      { name: '量', type: 'bar', data: vols, xAxisIndex: 1, yAxisIndex: 1, barWidth: '62%' },
    ],
  };
}

/* ================= 板块热力树图 ================= */

export function treemapOption(sectors) {
  const rows = (sectors || []).filter((s) => s.change_pct != null);
  const maxAbs = Math.max(3, ...rows.map((s) => Math.abs(s.change_pct)));
  // 标签压在红/绿色块上：深色主题白字+深描影可读；浅色主题色块为低 alpha 混白底（近粉白/浅灰），
  // 固定白字对比不足 1.5:1 → 换主题深色字+浅色描影
  const light = document.documentElement.dataset.theme === 'light';
  const labelColor = light ? TX : '#ffffff';
  const labelShadow = light ? 'rgba(255,255,255,0.75)' : 'rgba(0,0,0,0.55)';
  const colorOf = (chg) => {
    const t = Math.min(1, Math.abs(chg) / maxAbs);
    if (chg > 0.001) return `rgba(255,71,87,${0.16 + t * 0.72})`;
    if (chg < -0.001) return `rgba(0,201,138,${0.14 + t * 0.6})`;
    return 'rgba(115,121,138,0.18)';
  };
  return {
    tooltip: {
      formatter: (p) => {
        const s = p.data?.raw || {};
        return `<b style="color:${AMBER}">${p.name}</b><br/>涨跌 <span style="color:${s.change_pct >= 0 ? UP : DOWN}">${fmtPct(s.change_pct)}</span>`
          + (s.leader_name ? `<br/>领涨 ${s.leader_name} ${fmtPct(s.leader_change_pct)}` : '')
          + (s.amount != null ? `<br/>成交 ${fmtMoney(s.amount)}` : '')
          + (s.main_flow != null ? `<br/>主力 ${fmtMoney(s.main_flow)}` : '');
      },
    },
    series: [{
      type: 'treemap', roam: false, nodeClick: false,
      breadcrumb: { show: false },
      width: '100%', height: '100%', top: 0, left: 0,
      itemStyle: { borderColor: PANEL, borderWidth: 1.5, gapWidth: 1.5 },
      label: {
        show: true, position: 'insideTopLeft', padding: 6,
        formatter: (p) => {
          const s = p.data?.raw || {};
          return `{n|${p.name}}\n{c|${fmtPct(s.change_pct)}}`;
        },
        rich: {
          n: { fontSize: 11, color: labelColor, fontWeight: 700, lineHeight: 16, textShadowColor: labelShadow, textShadowBlur: 3 },
          c: { fontSize: 12, color: labelColor, fontFamily: MONO, fontWeight: 700, textShadowColor: labelShadow, textShadowBlur: 3 },
        },
      },
      data: rows.map((s) => ({
        name: s.name,
        value: Math.max(1, s.amount != null ? s.amount : Math.abs(s.change_pct) * 1e9),
        raw: s,
        itemStyle: { color: colorOf(s.change_pct) },
      })),
    }],
  };
}

/* ================= 情绪仪表盘（兼容旧调用，终端化配色） ================= */

export function gaugeOption(score) {
  const s = Math.max(0, Math.min(100, Number(score) || 0));
  return {
    series: [{
      type: 'gauge',
      startAngle: 210, endAngle: -30, min: 0, max: 100,
      radius: '100%', center: ['50%', '62%'],
      axisLine: {
        lineStyle: {
          width: 10,
          color: [[0.24, '#3d7eff'], [0.38, '#41c7ff'], [0.5, '#73798a'], [0.64, '#ffb000'], [0.8, '#ff8a00'], [1, '#ff4757']],
        },
      },
      pointer: { length: '60%', width: 3, itemStyle: { color: AMBER } },
      axisTick: { distance: -10, length: 3, lineStyle: { color: 'rgba(120,125,140,0.45)' } },
      splitLine: { distance: -10, length: 10, lineStyle: { color: 'rgba(120,125,140,0.6)', width: 1 } },
      axisLabel: { distance: -26, color: TX3, fontSize: 9, fontFamily: MONO },
      anchor: { show: true, size: 7, itemStyle: { color: AMBER } },
      title: { show: false },
      detail: {
        valueAnimation: true, offsetCenter: [0, '60%'],
        fontSize: 30, fontWeight: 800, color: TX, fontFamily: MONO,
        formatter: '{value}',
      },
      data: [{ value: s }],
    }],
  };
}

/* ================= 资金流向横向条形 ================= */

export function flowBarOption(sectors) {
  const rows = (sectors || []).slice().reverse();
  const names = rows.map((s) => s.name ?? '--');
  const vals = rows.map((s) => (s.main_net == null ? 0 : Number(s.main_net) / 1e8));
  return {
    grid: { left: 8, right: 56, top: 4, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const p = ps[0]; const r = rows[p.dataIndex] || {};
        return `<b style="color:${AMBER}">${p.name}</b><br/>主力净额 ${fmtMoney(r.main_net)}<br/>涨跌幅 ${fmtPct(r.change_pct)}`;
      },
    },
    xAxis: { type: 'value', axisLabel: { ...AXIS_LABEL, formatter: (v) => v + '亿' }, splitLine: SPLIT_LINE },
    yAxis: { type: 'category', data: names, axisLabel: { color: TX2, fontSize: 11 }, axisLine: { show: false }, axisTick: { show: false } },
    series: [{
      type: 'bar',
      data: vals.map((v) => ({
        value: v,
        itemStyle: { color: v >= 0 ? UP : DOWN, opacity: 0.85 },
      })),
      barMaxWidth: 10,
      label: { show: true, position: 'right', color: TX3, fontSize: 10, fontFamily: MONO, formatter: (p) => p.value.toFixed(1) },
    }],
  };
}

/* ================= 资金追踪：板块/热门股时序多线 =================
   已校准 CNY 数据按亿元展示；TDX raw 保留原值。两种单位由后端分段，
   不会在同一条曲线中混算。 */
export function capitalTrendOption(rows) {
  const all = (rows || []).filter((r) => r && Array.isArray(r.series) && r.series.length);
  // 20 条线同时铺开既无法读图，也会把末端标签堆成一团。默认保留资金强弱
  // 两端各 4 条；完整 20 条仍在下方榜单中，图表 tooltip 负责逐时点精读。
  const inflow = all.filter((r) => Number(r.main_net) >= 0)
    .sort((a, b) => Number(b.main_net || 0) - Number(a.main_net || 0)).slice(0, 4);
  const outflow = all.filter((r) => Number(r.main_net) < 0)
    .sort((a, b) => Number(a.main_net || 0) - Number(b.main_net || 0)).slice(0, 4);
  const selected = new Set([...inflow, ...outflow]);
  const fill = all.filter((r) => !selected.has(r))
    .sort((a, b) => Math.abs(Number(b.main_net || 0)) - Math.abs(Number(a.main_net || 0)));
  const list = [...inflow, ...outflow, ...fill].slice(0, 8);
  const rawUnit = list.some((r) => r.unit_verified === false || r.value_unit === 'tdx_raw_unverified');
  const divisor = rawUnit ? 1 : 1e8;
  const unitLabel = rawUnit ? '原始值' : '亿';
  // 全部快照时间点并集，升序为公共 x 轴；各线缺失点置 null（connectNulls 连起来）
  const tset = new Set();
  list.forEach((r) => r.series.forEach((p) => { if (p && p.t) tset.add(p.t); }));
  const times = [...tset].sort();
  // 流入(正)红系 / 流出(负)绿系：按当前净额符号分配同系不同深浅，便于一眼分清进/出两侧
  const reds = ['#ff4d5e', '#ff7a4d', '#ff9f1c', '#ffb020', '#ff6b9d', '#e0563b', '#ffd24d', '#ff8f12'];
  const greens = ['#11cc8e', '#21d4c4', '#46c9ff', '#3d9bff', '#5bd66f', '#0aa37a', '#7de0b0', '#2bb6a6'];
  let ri = 0;
  let gi = 0;
  const series = list.map((r) => {
    const m = {};
    r.series.forEach((p) => { if (p && p.t) m[p.t] = p.v; });
    const data = times.map((t) => (m[t] == null ? null : +(Number(m[t]) / divisor).toFixed(2)));
    const positive = (Number(r.main_net) || 0) >= 0;
    const col = positive ? reds[ri++ % reds.length] : greens[gi++ % greens.length];
    return {
      name: r.name, type: 'line', data, showSymbol: false, smooth: true, connectNulls: true,
      lineStyle: { width: 1.6, color: col }, itemStyle: { color: col }, z: 3,
      emphasis: { focus: 'series', lineStyle: { width: 3 } },
      endLabel: { show: false },
    };
  });
  // 零轴虚线挂在第一条线上（silent，不参与图例/tooltip）
  if (series.length) {
    series[0].markLine = {
      silent: true, symbol: 'none', label: { show: false },
      data: [{ yAxis: 0 }], lineStyle: { color: FLAT, type: 'dashed', width: 1, opacity: 0.6 },
    };
  }
  return {
    animation: false,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'line', label: PTR_LABEL },
      formatter: (ps) => {
        if (!ps || !ps.length) return '';
        const sorted = ps.filter((p) => p.value != null).sort((a, b) => b.value - a.value);
        const head = `<b style="color:${AMBER}">${ps[0].axisValue}</b>`;
        const lines = sorted.slice(0, 14).map((p) => {
          const c = p.value >= 0 ? UP : DOWN;
          return `${p.marker}${p.seriesName} <b style="color:${c}">${p.value.toFixed(1)}${unitLabel}</b>`;
        });
        return head + '<br/>' + lines.join('<br/>');
      },
    },
    legend: {
      type: 'scroll', top: 0, left: 8, right: 8,
      textStyle: { color: TX3, fontSize: 11 },
      pageTextStyle: { color: TX3 }, pageIconColor: AMBER, pageIconInactiveColor: FLAT,
    },
    grid: { left: 8, right: 18, top: 42, bottom: 6, containLabel: true },
    xAxis: {
      type: 'category', data: times, boundaryGap: false,
      axisLabel: { ...AXIS_LABEL, interval: Math.max(0, Math.floor(times.length / 6)) },
      axisTick: { show: false }, axisLine: AXIS_LINE,
    },
    yAxis: {
      type: 'value', scale: true, position: 'right',
      axisLabel: { ...AXIS_LABEL, formatter: (v) => v + unitLabel }, splitLine: SPLIT_LINE,
    },
    series,
  };
}

/* ================= 题材赚钱效应：横向条形（money_score 正红负绿） ================= */

export function moneyEffectBarOption(themes) {
  // 取前若干并反转，使最高分在顶部（ECharts 横向条形 y 轴从下往上）
  const rows = (themes || []).slice().reverse();
  const names = rows.map((t) => t.name ?? '--');
  const vals = rows.map((t) => (t.money_score == null ? 0 : Number(t.money_score)));
  return {
    grid: { left: 8, right: 64, top: 4, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const p = ps[0]; const r = rows[p.dataIndex] || {};
        return `<b style="color:${AMBER}">${p.name}</b>`
          + `<br/>赚钱分 <span style="color:${(r.money_score ?? 0) >= 0 ? UP : DOWN}">${r.money_score == null ? '--' : Number(r.money_score).toFixed(1)}</span>`
          + `<br/>涨跌幅 <span style="color:${(r.change_pct ?? 0) >= 0 ? UP : DOWN}">${fmtPct(r.change_pct)}</span>`
          + (r.zt_count != null ? `<br/>涨停数 ${r.zt_count}` : '')
          + (r.leader_name ? `<br/>领涨 ${r.leader_name} ${fmtPct(r.leader_change_pct)}` : '');
      },
    },
    xAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    yAxis: {
      type: 'category', data: names,
      axisLabel: { color: TX2, fontSize: 11 },
      axisLine: { show: false }, axisTick: { show: false },
    },
    series: [{
      type: 'bar',
      data: vals.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? UP : DOWN, opacity: 0.85 } })),
      barMaxWidth: 13,
      label: { show: true, position: 'right', color: TX3, fontSize: 10, fontFamily: MONO, formatter: (p) => Number(p.value).toFixed(1) },
    }],
  };
}

/* ================= 日 K 缩略图（个股侧栏） ================= */

export function dailyOption(daily) {
  const data = daily || [];
  const dates = data.map((d) => d.d ?? '');
  const k = data.map((d) => [d.o, d.c, d.l, d.h]);
  return {
    animation: false,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', label: PTR_LABEL },
      formatter: (ps) => {
        const p = ps.find((x) => x.seriesType === 'candlestick');
        if (!p) return '';
        const d = data[p.dataIndex] || {};
        return `<b style="color:${AMBER}">${p.axisValue}</b><br/>开 ${fmtPrice(d.o)}　收 ${fmtPrice(d.c)}<br/>高 ${fmtPrice(d.h)}　低 ${fmtPrice(d.l)}<br/>量 ${fmtMoney(d.v, 1)}`;
      },
    },
    grid: { left: 4, right: 42, top: 8, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { ...AXIS_LABEL, interval: Math.max(1, Math.floor(dates.length / 4)), formatter: (v) => String(v).slice(5) },
      axisTick: { show: false }, axisLine: AXIS_LINE,
    },
    yAxis: { scale: true, position: 'right', axisLabel: { ...AXIS_LABEL, formatter: (v) => v.toFixed(1) }, splitLine: SPLIT_LINE },
    dataZoom: [{ type: 'inside', start: 55, end: 100 }],
    series: [{
      type: 'candlestick', data: k, barMaxWidth: 8,
      itemStyle: { color: UP, color0: 'transparent', borderColor: UP, borderColor0: DOWN, borderWidth: 1 },
    }],
  };
}

/* ================= 持仓饼图 ================= */

export function holdingsPieOption(rows) {
  return {
    tooltip: { trigger: 'item', formatter: (p) => `<b style="color:${AMBER}">${p.name}</b><br/>市值 ${fmtMoney(p.value)}<br/>占比 ${p.percent}%` },
    legend: {
      left: '25%', right: 8, top: 'middle', orient: 'horizontal', itemGap: 14,
      textStyle: { color: TX2, fontSize: 12 }, icon: 'rect', itemWidth: 9, itemHeight: 9,
    },
    series: [{
      type: 'pie', radius: [36, 56], center: ['12%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: PANEL, borderWidth: 2 },
      label: { show: false }, labelLine: { show: false },
      data: rows.map((r) => ({ name: r.name, value: r.value })),
    }],
  };
}

/* ================= 情绪历史主图 ================= */

export function sentimentHistoryOption(days) {
  const data = days || [];
  const dates = data.map((d) => d.date);
  const scores = data.map((d) => d.score);
  const lus = data.map((d) => d.limit_up);
  const lds = data.map((d) => (d.limit_down == null ? null : -Math.abs(d.limit_down)));

  const areas = [];
  let start = 0;
  for (let i = 1; i <= data.length; i++) {
    if (i === data.length || data[i].phase !== data[start].phase) {
      const phase = data[start].phase;
      if (phase && PHASE_COLORS[phase]) {
        areas.push([
          { xAxis: dates[start], itemStyle: { color: PHASE_COLORS[phase], opacity: 0.08 }, name: phase },
          { xAxis: dates[i - 1] },
        ]);
      }
      start = i;
    }
  }

  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross', label: PTR_LABEL },
      formatter: (ps) => {
        const i = ps[0]?.dataIndex ?? 0;
        const d = data[i] || {};
        const pc = PHASE_COLORS[d.phase] || FLAT;
        return `<b style="color:${AMBER}">${d.date || ''}</b>　<span style="color:${pc}">■ ${d.phase || '--'}</span><br/>`
          + `情绪分 <b>${d.score ?? '--'}</b><br/>涨停 <span style="color:${UP}">${d.limit_up ?? '--'}</span>　跌停 <span style="color:${DOWN}">${d.limit_down ?? '--'}</span><br/>`
          + `连板高度 ${d.lianban_height ?? '--'}　成交额 ${fmtMoney(d.total_amount)}`;
      },
    },
    legend: { top: 0, textStyle: { color: TX2, fontSize: 11 }, data: ['情绪分', '涨停', '跌停'] },
    grid: { left: 8, right: 8, top: 32, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category', data: dates, boundaryGap: true,
      axisLabel: { ...AXIS_LABEL, formatter: (v) => String(v).slice(5) },
      axisTick: { show: false }, axisLine: AXIS_LINE,
    },
    yAxis: [
      { name: '情绪分', min: 0, max: 100, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, nameTextStyle: { color: TX3, fontSize: 10 } },
      { name: '家数', axisLabel: AXIS_LABEL, splitLine: { show: false }, nameTextStyle: { color: TX3, fontSize: 10 } },
    ],
    series: [
      {
        name: '情绪分', type: 'line', data: scores, yAxisIndex: 0, smooth: 0.3,
        showSymbol: false, z: 5,
        lineStyle: { width: 2, color: AMBER },
        itemStyle: { color: AMBER },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(255,176,0,0.22)' }, { offset: 1, color: 'rgba(255,176,0,0)' },
          ]),
        },
        markArea: { silent: true, data: areas },
      },
      { name: '涨停', type: 'bar', data: lus, yAxisIndex: 1, stack: 'lim', barMaxWidth: 9, itemStyle: { color: 'rgba(255,71,87,0.8)' } },
      { name: '跌停', type: 'bar', data: lds, yAxisIndex: 1, stack: 'lim', barMaxWidth: 9, itemStyle: { color: 'rgba(0,201,138,0.8)' } },
    ],
  };
}

/* ================= 连板高度阶梯 ================= */

export function lianbanOption(days) {
  const data = days || [];
  return {
    tooltip: { trigger: 'axis', formatter: (ps) => `<b style="color:${AMBER}">${ps[0]?.axisValue}</b><br/>连板高度 <b>${ps[0]?.value ?? '--'}</b> 板` },
    grid: { left: 8, right: 8, top: 26, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category', data: data.map((d) => d.date),
      axisLabel: { ...AXIS_LABEL, formatter: (v) => String(v).slice(5) },
      axisTick: { show: false }, axisLine: AXIS_LINE,
    },
    yAxis: { name: '连板高度', minInterval: 1, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, nameTextStyle: { color: TX3, fontSize: 10 } },
    series: [{
      type: 'line', step: 'middle', data: data.map((d) => d.lianban_height),
      showSymbol: false, lineStyle: { width: 1.6, color: AMBER },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(255,176,0,0.2)' }, { offset: 1, color: 'rgba(255,176,0,0)' },
        ]),
      },
    }],
  };
}
