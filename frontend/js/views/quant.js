/* views/quant.js — 量化套件
   ① 沪深300 免费当日截面打分榜（TOP50，因子 z-score 合成，分项标准分）
   ② 因子有效性（本地 Point-in-Time 快照积累充分前禁用，禁止用今日估值倒灌历史）
   ③ 简单择时回测（沪深300 双均线 vs 买入持有，真净值曲线，无未来函数）

   并行纪律：本视图仅新建本文件；NAV 净值曲线的 ECharts option 内联自管（不改共享 charts.js）。 */

import { api, fmtMoney, fmtPct, fmtNum, fmtPrice, pctClass } from '../api.js';
import { dataStamp, emptyState, skeleton, escapeHtml } from '../ui.js';
import { mkChart, disposeCharts } from '../charts.js';

let root = null;
// 挂载代号：主题切换 route(true) 复用同一容器重挂载，旧挂载的在途响应若不作废，
// 会 dispose 掉新视图的活图表并把实例绑到脱离 DOM 的旧节点上（参照 features.js）
let gen = 0;
let navChart = null;   // 唯一图表实例，自管 dispose（不入数组，避免死引用堆积）

// 当前回测参数（预设）。慢/快均线择时窗口；切换后重拉。
const BT_PRESETS = [
  { key: '20/60', label: 'MA20/60 · 近1年', days: 250, fast: 20, slow: 60 },
  { key: '5/20', label: 'MA5/20 · 近半年', days: 120, fast: 5, slow: 20 },
  { key: '10/30', label: 'MA10/30 · 近1年', days: 250, fast: 10, slow: 30 },
  { key: '20/60L', label: 'MA20/60 · 近2年', days: 480, fast: 20, slow: 60 },
];
let btPreset = BT_PRESETS[0];

// 打分榜分项因子展示顺序与中文名（与后端 factor_scores 键一致）
const RANK_FACTORS = [
  { key: 'momentum', label: '当日强弱' },
  { key: 'value_pe', label: 'PE低估' },
  { key: 'value_pb', label: 'PB低估' },
  { key: 'turnover', label: '低换手' },
  { key: 'vol_ratio', label: '量比' },
];

function cssVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch { return fallback; }
}

export function mount(el) {
  gen += 1;
  root = el;
  el.innerHTML = `
    <style>
      /* 打分榜：因子分项迷你色块条（垂直 5 格，0~100 高度映射） */
      .q-fbars{display:inline-flex;align-items:flex-end;gap:3px;height:26px}
      .q-fbar{position:relative;display:inline-flex;align-items:flex-end;justify-content:center;width:13px;height:100%;background:var(--track,rgba(255,255,255,0.05));border-radius:2px;cursor:help;overflow:hidden}
      .q-fbar-fill{position:absolute;left:0;right:0;bottom:0;border-radius:2px;transition:height .4s ease}
      .q-fbar-lab{position:relative;z-index:1;font-size:12px;color:var(--text);font-style:normal;opacity:.9;line-height:1.35}
      /* 因子有效性列表 */
      .q-factor-list{display:flex;flex-direction:column;gap:13px}
      .q-factor-row{cursor:help}
      .q-fr-top{display:flex;align-items:baseline;gap:8px}
      .q-fr-name{font-size:14px;font-weight:600;color:var(--text)}
      .q-fr-ic{margin-left:auto;font-size:14px;font-weight:700}
      .q-fr-icir{font-size:12px;color:var(--text-3)}
      .q-fr-bar-wrap{height:5px;background:var(--track,rgba(255,255,255,0.05));border-radius:3px;margin:5px 0 6px;overflow:hidden}
      .q-fr-bar{height:100%;border-radius:3px;transition:width .5s ease}
      .q-fr-bar.up{background:var(--up)} .q-fr-bar.down{background:var(--down)} .q-fr-bar.flat{background:var(--flat)}
      .q-fr-layers{display:flex;gap:5px;flex-wrap:wrap}
      .q-layer{display:inline-flex;flex-direction:column;align-items:center;min-width:48px;padding:4px 6px;border-radius:4px;background:var(--hover,rgba(255,255,255,0.03));font-size:12px}
      .q-layer em{font-style:normal;color:var(--text-3);font-size:12px}
      .q-layer b{font-size:13px;font-weight:700}
      .q-layer.up b{color:var(--up)} .q-layer.down b{color:var(--down)} .q-layer.flat b{color:var(--text-3)}
      .q-layer.ls{border:1px dashed var(--border-strong)}
      .q-fbar-fill{} .q-fr-ic.up{color:var(--up)} .q-fr-ic.down{color:var(--down)} .q-fr-ic.flat{color:var(--flat)}
      #q-bt .q-bt-controls{min-width:0}
      #q-bt-seg{max-width:100%;overflow-x:auto;overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch;scrollbar-width:thin}
      #q-bt-seg .seg{flex:0 0 auto}
      .q-backtest-kpis{grid-template-columns:repeat(6,minmax(0,1fr))}
      @media (max-width:1024px){.q-backtest-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
      @media (max-width:768px){
        #q-bt .q-bt-controls{width:100%;margin-left:0}
        #q-bt-seg{width:100%}
        .q-backtest-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
      }
    </style>
    <div class="page-head">
      <h2 class="page-title">量化套件</h2>
      <span class="page-sub">沪深300 当日截面打分 · 点时快照积累状态 · 双均线择时回测 · 免费公开数据</span>
    </div>
    <div class="card" style="padding:11px 15px;margin-bottom:var(--gap);font-size:12.5px;color:var(--text-2);line-height:1.75">
      <b style="color:var(--fg)">📖 这页是干嘛的（不想研究可直接跳过）：</b>它把「当日截面比较」和「真实历史回测」分开展示，
      <b>不给今天买什么的建议</b>。三块内容用大白话说：
      <div style="margin-top:6px">
        ① <b>当日多因子打分榜</b>——比较沪深300 成分股此刻的估值、换手、量比和当日强弱，不冒充历史验证结论；<br>
        ② <b>因子有效性 IC/ICIR</b>——需要连续积累当时快照后才能计算；样本不足时保持禁用，不把今天的估值倒灌到过去；<br>
        ③ <b>双均线择时回测</b>——假设用"金叉买、死叉卖"这套机械规则，回测它过去几年能不能跑赢"买了一直拿着不动"。
      </div>
      <div style="margin-top:6px">想知道<b style="color:var(--fg)">今天买什么</b>，请看
        <span style="color:var(--accent)">盘前计划 / 盘前简报 / 个股诊断 / 市场逻辑精选</span>，这页不用管。</div>
    </div>

    <div class="grid12" style="margin-bottom:var(--gap)">
      <section class="card hoverable sp-12" id="q-bt">
        <div class="card-head">
          <h3 class="card-title">择时回测 · 沪深300 双均线 vs 买入持有</h3>
          <div class="card-extra q-bt-controls">
            <div class="segmented" id="q-bt-seg">
              ${BT_PRESETS.map((p) => `<button class="seg${p.key === btPreset.key ? ' active' : ''}" data-key="${p.key}">${escapeHtml(p.label)}</button>`).join('')}
            </div>
            <span class="data-stamp" id="q-bt-stamp"></span>
          </div>
        </div>
        <div class="card-body" id="q-bt-body">${skeleton(3, [30, 50, 380])}</div>
      </section>
    </div>

    <div class="grid12" style="margin-bottom:var(--gap)">
      <section class="card hoverable sp-7" id="q-rank">
        <div class="card-head">
          <h3 class="card-title">多因子打分榜 · 沪深300 TOP50</h3>
          <div class="card-extra"><span class="data-stamp" id="q-rank-stamp"></span></div>
        </div>
        <div class="card-body" id="q-rank-body">${skeleton(8)}</div>
      </section>

      <section class="card hoverable sp-5" id="q-factor">
        <div class="card-head">
          <h3 class="card-title">因子有效性 · 点时样本门禁</h3>
          <div class="card-extra"><span class="data-stamp" id="q-factor-stamp"></span></div>
        </div>
        <div class="card-body" id="q-factor-body">${skeleton(6)}</div>
      </section>
    </div>`;

  bindBtSeg();
  loadBacktest();
  loadRank();
  loadFactor();
}

export function unmount() {
  if (navChart) { disposeCharts([navChart]); navChart = null; }
  root = null;
}

function gotoStock(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.diagnosis.code', code);
  location.hash = '#/diagnosis';
}

function bindBtSeg() {
  const seg = root?.querySelector('#q-bt-seg');
  if (!seg) return;
  seg.addEventListener('click', (e) => {
    const btn = e.target.closest('.seg');
    if (!btn) return;
    const p = BT_PRESETS.find((x) => x.key === btn.dataset.key);
    if (!p || p.key === btPreset.key) return;
    btPreset = p;
    seg.querySelectorAll('.seg').forEach((b) => b.classList.toggle('active', b.dataset.key === p.key));
    loadBacktest();
  });
}

/* ================= ① 多因子打分榜 ================= */

async function loadRank() {
  const body = root?.querySelector('#q-rank-body');
  if (!body) return;
  const g = gen;
  let d = null;
  try { d = await api.get('/quant/rank'); } catch { /* 下方兜底 */ }
  if (g !== gen || !root) return;
  const stamp = root.querySelector('#q-rank-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.data_date || d.ts, d.missing);

  const stocks = (d?.stocks || []).filter((s) => s && s.code);
  if (!stocks.length) {
    body.innerHTML = emptyState('当前未形成打分榜', d?.missing?.length ? d.missing.join(' · ') : '当前筛选没有形成可展示结果；请确认最近交易日数据已完成采集。');
    return;
  }
  body.innerHTML = `<div class="tbl-wrap"><table class="tbl">
    <thead><tr>
      <th>#</th><th class="l">代码</th><th>现价</th><th>当日强弱</th>
      <th>PE(TTM)</th><th>PB</th><th>换手</th><th>量比</th><th>流通市值</th>
      <th class="l">因子分项(0~100)</th><th>综合分</th>
    </tr></thead>
    <tbody>${stocks.map((s, i) => {
      const score = Number(s.score) || 0;
      return `<tr>
        <td style="color:var(--text-3)">${i + 1}</td>
        <td class="l"><span class="code-link" data-code="${escapeHtml(s.code)}">${s.name && s.name !== s.code ? `${escapeHtml(s.name)} <span class="num" style="color:var(--text-3);font-size:12px">${escapeHtml(s.code)}</span>` : escapeHtml(s.code)}</span></td>
        <td>${fmtPrice(s.price)}</td>
        <td class="${pctClass(s.momentum)}">${s.momentum != null ? fmtPct(s.momentum) : '--'}</td>
        <td>${s.pe_ttm != null ? fmtNum(s.pe_ttm, 1) : '--'}</td>
        <td>${s.pb != null ? fmtNum(s.pb, 2) : '--'}</td>
        <td>${s.turnover_rate != null ? fmtNum(s.turnover_rate, 1) + '%' : '--'}</td>
        <td>${s.volume_ratio != null ? fmtNum(s.volume_ratio, 2) : '--'}</td>
        <td>${fmtMoney(s.circ_mv, 0)}</td>
        <td class="l">${factorBars(s.factor_scores)}</td>
        <td><div class="inst-score" data-tip="综合得分 ${score}（因子 z-score 合成映射 0~100）"><div class="inst-score-bar" style="width:${Math.max(4, score)}%"></div><span class="inst-score-num num">${score.toFixed(1)}</span></div></td>
      </tr>`;
    }).join('')}</tbody></table></div>
    <div style="font-size:13px;color:var(--text-3);margin-top:8px">
      因子：当日强弱 · PE/PB 低估(取倒数) · 低换手偏好 · 量比；各因子按当日横截面 z-score(3σ winsorize)后合成。
      点击代码会立即执行统一「个股诊断」；诊断完成后可按需让 AI 解释冻结结论。
      <span style="color:var(--text-2)">这是当日横截面排名，不是 20 日动量、历史 IC 结论或买入指令。</span>
    </div>`;

  body.querySelectorAll('.code-link').forEach((a) => {
    a.addEventListener('click', () => gotoStock(a.dataset.code));
  });
}

// 五个分项的迷你色块条（0~100），按值取色：高=琥珀，中=青，低=灰
function factorBars(fs) {
  if (!fs) return '<span style="color:var(--text-3)">--</span>';
  return `<span class="q-fbars">${RANK_FACTORS.map((f) => {
    const v = Math.max(0, Math.min(100, Number(fs[f.key]) || 0));
    const color = v >= 66 ? 'var(--amber)' : v >= 40 ? 'var(--cyan)' : 'var(--flat)';
    return `<span class="q-fbar" data-tip="${escapeHtml(f.label)} ${v.toFixed(0)}">
      <i class="q-fbar-fill" style="height:${Math.max(8, v)}%;background:${color}"></i>
      <em class="q-fbar-lab">${escapeHtml(f.label[0])}</em>
    </span>`;
  }).join('')}</span>`;
}

/* ================= ② 因子有效性 IC/ICIR ================= */

async function loadFactor() {
  const body = root?.querySelector('#q-factor-body');
  if (!body) return;
  const g = gen;
  let d = null;
  try { d = await api.get('/quant/factor'); } catch { /* 下方兜底 */ }
  if (g !== gen || !root) return;
  const stamp = root.querySelector('#q-factor-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.data_date || d.ts, d.missing);

  const factors = (d?.factors || []).filter((f) => f && f.name);
  if (!factors.length) {
    body.innerHTML = emptyState('因子评估尚未启用', d?.missing?.length ? d.missing.join(' · ') : '需继续积累本地 Point-in-Time 快照；样本不足时禁止计算 IC/ICIR');
    return;
  }
  const hold = d?.params?.hold ?? 5;
  // IC 绝对值最大者做条形基准
  const maxAbsIc = Math.max(0.01, ...factors.map((f) => Math.abs(Number(f.ic) || 0)));

  body.innerHTML = `
    <div class="q-factor-list">${factors.map((f) => {
      const ic = Number(f.ic) || 0;
      const icCls = ic > 0.001 ? 'up' : ic < -0.001 ? 'down' : 'flat';
      const w = (Math.abs(ic) / maxAbsIc) * 100;
      const ls = f.long_short;
      return `<div class="q-factor-row" data-tip="${escapeHtml(`${f.label}\nIC=${ic.toFixed(4)} · ICIR=${(Number(f.icir) || 0).toFixed(3)} · t=${(Number(f.t_stat) || 0).toFixed(2)}\n样本 ${f.n} 观测 / ${f.periods} 期`)}">
        <div class="q-fr-top">
          <span class="q-fr-name">${escapeHtml(f.label)}</span>
          <span class="q-fr-ic ${icCls} num">IC ${ic >= 0 ? '+' : ''}${ic.toFixed(3)}</span>
          <span class="q-fr-icir num" title="ICIR=IC/std(IC)，衡量稳定性">ICIR ${(Number(f.icir) || 0).toFixed(2)}</span>
        </div>
        <div class="q-fr-bar-wrap">
          <div class="q-fr-bar ${icCls}" style="width:${Math.max(3, w)}%"></div>
        </div>
        <div class="q-fr-layers" data-tip="五分层(L1低因子值→L5高)各层平均前向收益%">
          ${(f.layers || []).map((v, i) => {
            const cls = v == null ? 'flat' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
            return `<span class="q-layer ${cls}"><em>L${i + 1}</em><b class="num">${v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2)}</b></span>`;
          }).join('')}
          ${ls != null ? `<span class="q-layer ls"><em>L5-L1</em><b class="num ${ls >= 0 ? 'up' : 'down'}">${ls >= 0 ? '+' : ''}${Number(ls).toFixed(2)}</b></span>` : ''}
        </div>
      </div>`;
    }).join('')}</div>
    <div style="font-size:13px;color:var(--text-3);margin-top:6px">
      口径：沪深300 成分，周频调仓(${hold}日)，因子值 t 对齐 t+${hold} 前向收益的 Spearman 秩相关 IC；
      五分层为各层平均前向收益(%)。|IC|>0.03 且分层单调 → 因子有方向性。<span style="color:var(--text-2)">历史统计，不预示未来。</span>
    </div>`;
}

/* ================= ③ 择时回测 ================= */

async function loadBacktest() {
  const body = root?.querySelector('#q-bt-body');
  if (!body) return;
  const g = gen;
  const p = btPreset;
  let d = null;
  try { d = await api.get(`/quant/backtest?days=${p.days}&fast=${p.fast}&slow=${p.slow}`); } catch { /* 下方兜底 */ }
  if (g !== gen || !root) return;
  const stamp = root.querySelector('#q-bt-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.data_date || d.ts, d.missing);

  if (navChart) { disposeCharts([navChart]); navChart = null; }
  if (!d || !Array.isArray(d.dates) || !d.dates.length) {
    body.innerHTML = emptyState('当前尚不能生成回测', d?.missing?.length ? d.missing.join(' · ') : '当前条件的历史样本尚在积累，不足以生成可解读的统计。');
    return;
  }
  const m = d.metrics || {};
  const pct = (v, dg = 1) => (v == null ? '--' : (v >= 0 ? '+' : '') + (v * 100).toFixed(dg) + '%');
  const excessCls = pctClass(m.excess);

  body.innerHTML = `
    <div class="kpi-grid q-backtest-kpis">
      <div class="kpi hi"><div class="k-label">策略年化</div><div class="k-val ${pctClass(m.ann)}">${pct(m.ann)}</div><div class="k-sub">基准 ${pct(m.bench_ann)}</div></div>
      <div class="kpi"><div class="k-label">夏普</div><div class="k-val num">${m.sharpe != null ? Number(m.sharpe).toFixed(2) : '--'}</div></div>
      <div class="kpi"><div class="k-label">最大回撤</div><div class="k-val down">${pct(m.mdd)}</div><div class="k-sub">基准 ${pct(m.bench_mdd)}</div></div>
      <div class="kpi"><div class="k-label">持仓胜率</div><div class="k-val num">${m.winrate != null ? (m.winrate * 100).toFixed(1) + '%' : '--'}</div></div>
      <div class="kpi"><div class="k-label">超额收益</div><div class="k-val ${excessCls}">${pct(m.excess)}</div></div>
      <div class="kpi"><div class="k-label">换手 / 仓位占比</div><div class="k-val num">${m.trades ?? '--'}<span style="font-size:13px;color:var(--text-3)"> 次</span></div><div class="k-sub">${m.hold_ratio != null ? (m.hold_ratio * 100).toFixed(0) + '% 在仓' : ''}</div></div>
    </div>
    <div class="big-chart" id="q-bt-chart" style="margin-top:10px"></div>
    <div style="font-size:13px;color:var(--text-3);margin-top:6px">
      策略：第 t 日收盘算出 MA${p.fast}/MA${p.slow}，多头排列(MA${p.fast}≥MA${p.slow})则【次日】持仓吃当日指数收益，否则空仓（信号次日生效，无未来函数）。
      净值自 1.0 起。<span style="color:var(--text-2)">仅供研究，历史回测不代表未来收益。</span>
    </div>`;

  const host = body.querySelector('#q-bt-chart');
  navChart = mkChart(host);
  if (navChart) navChart.setOption(navOption(d), true);
  else if (host) host.innerHTML = '<div class="empty-state"><div class="es-title">图表库未加载</div></div>';
}

// NAV 净值曲线 option（内联自管，不改共享 charts.js）：策略 vs 基准双线 + 持仓段背景
function navOption(d) {
  const UP = cssVar('--up', '#ff4d5e');
  const DOWN = cssVar('--down', '#11cc8e');
  const AMBER = cssVar('--amber', '#ffb020');
  const CYAN = cssVar('--cyan', '#46c9ff');
  const TX2 = cssVar('--text-2', '#9aa1ae');
  const TX3 = cssVar('--text-3', '#656c79');
  const line = cssVar('--line', 'rgba(255,255,255,0.045)');
  const border = cssVar('--border-strong', 'rgba(255,255,255,0.10)');
  const MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';
  const dates = d.dates || [];
  const nav = d.nav || [];
  const bench = d.bench || [];
  const signal = d.signal || [];

  // 持仓段（signal==1 连续区间）→ markArea，淡琥珀色背景标注「在仓」
  const areas = [];
  let s = -1;
  for (let i = 0; i < signal.length; i++) {
    if (signal[i] === 1 && s < 0) s = i;
    if ((signal[i] !== 1 || i === signal.length - 1) && s >= 0) {
      const end = signal[i] === 1 ? i : i - 1;
      areas.push([{ xAxis: dates[s] }, { xAxis: dates[Math.max(s, end)] }]);
      s = -1;
    }
  }

  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      formatter: (ps) => {
        const i = ps[0]?.dataIndex ?? 0;
        const sv = signal[i];
        const navV = nav[i], benchV = bench[i];
        const ahead = (navV != null && benchV != null) ? (navV - benchV) : null;
        return `<b style="color:${AMBER}">${ps[0]?.axisValue || ''}</b>　${sv === 1 ? `<span style="color:${AMBER}">● 持仓</span>` : `<span style="color:${TX3}">○ 空仓</span>`}<br/>`
          + `策略净值 <b style="color:${AMBER}">${navV != null ? navV.toFixed(4) : '--'}</b><br/>`
          + `基准净值 <span style="color:${CYAN}">${benchV != null ? benchV.toFixed(4) : '--'}</span>`
          + (ahead != null ? `<br/>超越基准 <span style="color:${ahead >= 0 ? UP : DOWN}">${ahead >= 0 ? '+' : ''}${ahead.toFixed(4)}</span>` : '');
      },
    },
    legend: { top: 0, right: 4, textStyle: { color: TX2, fontSize: 11 }, icon: 'rect', itemWidth: 12, itemHeight: 3, data: ['策略(择时)', '买入持有'] },
    grid: { left: 8, right: 12, top: 30, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      axisLabel: { color: TX3, fontSize: 10, fontFamily: MONO, formatter: (v) => String(v).slice(5) },
      axisTick: { show: false }, axisLine: { lineStyle: { color: border } },
    },
    yAxis: {
      type: 'value', scale: true,
      axisLabel: { color: TX3, fontSize: 10, fontFamily: MONO, formatter: (v) => v.toFixed(2) },
      splitLine: { lineStyle: { color: line } },
    },
    series: [
      {
        name: '策略(择时)', type: 'line', data: nav, showSymbol: false, smooth: 0.2, z: 5,
        lineStyle: { width: 2, color: AMBER }, itemStyle: { color: AMBER },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(255,176,0,0.16)' }, { offset: 1, color: 'rgba(255,176,0,0)' },
          ]),
        },
        // 持仓段背景标注（淡琥珀），挂在策略线上
        markArea: areas.length ? {
          silent: true, itemStyle: { color: 'rgba(255,176,0,0.06)' }, data: areas,
        } : undefined,
      },
      {
        name: '买入持有', type: 'line', data: bench, showSymbol: false, smooth: 0.2, z: 4,
        lineStyle: { width: 1.4, color: CYAN, opacity: 0.9 }, itemStyle: { color: CYAN },
      },
    ],
  };
}
