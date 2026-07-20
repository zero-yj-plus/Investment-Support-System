/* views/sentiment.js — 情绪周期 */

import { api, fmtMoney, fmtPct, fmtNum } from '../api.js';
import { card, dataStamp, emptyState, skeleton, escapeHtml } from '../ui.js';
import { mkChart, disposeCharts, sentimentHistoryOption, lianbanOption, PHASE_COLORS } from '../charts.js';

let root = null;
let charts = [];
// 挂载代号：卸载/重挂载后的在途响应不得再触碰 DOM 或创建图表（防 null root 解引用与孤儿实例泄漏）
let gen = 0;

const PHASE_GUIDE = [
  {
    name: '冰点期', feat: '涨停个位数 · 连板断层 · 跌停增多 · 亏钱效应顶点',
    op: '<b>空仓或一成以内试错</b>。冰点往往孕育转折：重点观察跌停是否收敛、是否出现逆势连板的「冰点穿越者」，它常是下一周期主线龙头。打板成功率极低，禁止追高。',
  },
  {
    name: '修复期', feat: '跌停收敛 · 首板增多 · 炸板率回落 · 龙头试错出现',
    op: '<b>轻仓低吸试错</b>。优先做超跌反核与新题材首板，确认昨日涨停溢价转正后再加仓。此阶段成功率回升但波动大，严格设损，不与趋势对抗。',
  },
  {
    name: '发酵期', feat: '连板梯队成型 · 题材主线明确 · 赚钱效应扩散',
    op: '<b>主升阶段，可正常仓位进攻</b>。沿主线做中军与龙头：龙头不动摇、补涨找低位。接力打板与半路均可参与，重点跟踪封单与分时承接强度。',
  },
  {
    name: '高潮期', feat: '连板高度新高 · 涨停爆量 · 大面积涨停 · 情绪过热',
    op: '<b>只吃肉不接力，逐步兑现</b>。高潮即风险：缩量加速的龙头持有，放量滞涨立即减仓。切忌在情绪最亢奋时重仓接力高位板，明日大概率分歧。',
  },
  {
    name: '分歧期', feat: '高位板炸板 · 涨停溢价收敛 · 高低切换 · 分歧转一致反复',
    op: '<b>降仓观望，等待方向选择</b>。分歧不必然退潮——若龙头分歧转一致可博弈反包；若梯队全面断裂则按退潮处理。只做龙头修复，不碰跟风补涨。',
  },
  {
    name: '退潮期', feat: '连板高度坍塌 · 炸板率飙升 · 跌停潮 · 亏钱效应蔓延',
    op: '<b>现金为王，强制空仓</b>。退潮期任何抄底都是逆势，等待跌停家数见顶回落。可研究下一轮题材备选，把精力放在复盘而非交易上。',
  },
];

async function loadToday() {
  const el = root.querySelector('#st-today');
  const g = gen;
  let d;
  try { d = await api.get('/sentiment/today'); } catch { if (g === gen && root) el.innerHTML = emptyState('情绪数据加载失败', '', '', 'alert'); return; }
  if (g !== gen || !root) return;
  if (!d || d.ok === false) { el.innerHTML = emptyState('暂无今日情绪快照', d?.error || ''); return; }
  const m = d.metrics || {};
  const pc = PHASE_COLORS[d.phase] || '#8b93a7';
  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:12px">
      <span style="font-size:30px;font-weight:800" class="num">${d.score ?? '--'}<span style="font-size:15px;color:var(--text-3)"> /100</span></span>
      <span class="phase-badge" style="color:${pc};border-color:${pc}55;background:${pc}1a;margin-bottom:0">${escapeHtml(d.phase ?? '--')}</span>
      <span style="font-size:14px;color:var(--text-2)">${escapeHtml(d.phase_desc ?? '')}</span>
      <span style="margin-left:auto">${dataStamp(d.date, d.missing)}</span>
    </div>
    <div class="kpi-grid">
      <div class="kpi"><div class="k-label">涨停</div><div class="k-val up num">${fmtNum(m.limit_up)}</div></div>
      <div class="kpi"><div class="k-label">跌停</div><div class="k-val down num">${fmtNum(m.limit_down)}</div></div>
      <div class="kpi"><div class="k-label">连板高度</div><div class="k-val num" style="color:var(--warn)">${m.lianban_height ?? '--'}</div></div>
      <div class="kpi"><div class="k-label">连板家数</div><div class="k-val num">${fmtNum(m.lianban_count)}</div></div>
      <div class="kpi"><div class="k-label">炸板率</div><div class="k-val num">${fmtPct(m.zhaban_rate, { signed: false, digits: 1 })}</div></div>
      <div class="kpi"><div class="k-label">涨跌比</div><div class="k-val num">${m.up_down_ratio ?? '--'}</div></div>
      <div class="kpi"><div class="k-label">两市成交</div><div class="k-val num">${fmtMoney(m.total_amount)}</div></div>
    </div>
    ${d.advice ? `<div class="advice" style="margin-top:12px;font-size:14px;padding:9px 12px;border-radius:9px;background:linear-gradient(135deg,rgba(34,211,238,.08),rgba(139,92,246,.08));border:1px solid rgba(34,211,238,.22)"><b style="color:var(--brand1)">操作建议</b>　${escapeHtml(d.advice)}</div>` : ''}`;

  // 高亮当前阶段说明卡
  root.querySelectorAll('.phase-card').forEach((c) => c.classList.toggle('current', c.dataset.phase === d.phase));
}

async function loadHistory() {
  const mainEl = root.querySelector('#st-main-chart');
  const subEl = root.querySelector('#st-sub-chart');
  const g = gen;
  let d;
  try { d = await api.get('/sentiment/history?days=60'); } catch {
    if (g !== gen || !root) return;
    mainEl.innerHTML = emptyState('历史数据加载失败', '', '', 'alert');
    subEl.innerHTML = '';
    return;
  }
  // 卸载后继续 mkChart 会把 ECharts 实例 + ResizeObserver 挂在脱离 DOM 的节点上，纯泄漏
  if (g !== gen || !root) return;
  const days = Array.isArray(d?.days) ? d.days : [];
  if (!days.length) {
    mainEl.innerHTML = emptyState('暂无历史快照', '情绪快照按日累积，运行数日后这里将呈现完整周期曲线', '', 'sentiment');
    subEl.innerHTML = emptyState('暂无连板高度数据', '', '', 'sentiment');
    return;
  }
  const c1 = mkChart(mainEl);
  if (c1) { c1.setOption(sentimentHistoryOption(days)); charts.push(c1); }
  const c2 = mkChart(subEl);
  if (c2) { c2.setOption(lianbanOption(days)); charts.push(c2); }
}

export function mount(container) {
  gen += 1;
  root = container;
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">情绪周期</h2>
      <span class="page-sub">短线情绪量化 · 六阶段周期定位 · 近 60 个交易日</span>
    </div>
    <div class="grid12" style="margin-bottom:var(--gap)">
      <section class="card hoverable sp-12">
        <div class="card-head"><h3 class="card-title">今日情绪快照</h3><div class="card-extra"></div></div>
        <div class="card-body" id="st-today">${skeleton(3, [30, 50, 16])}</div>
      </section>
    </div>
    <div class="grid12" style="margin-bottom:var(--gap)">
      <section class="card hoverable sp-12">
        <div class="card-head"><h3 class="card-title">情绪分与涨跌停（阶段色带）</h3>
          <div class="card-extra" style="font-size:15px;color:var(--text-3);display:flex;gap:9px;flex-wrap:wrap">
            ${Object.entries(PHASE_COLORS).map(([k, v]) => `<span style="display:inline-flex;align-items:center;gap:4px"><i style="width:8px;height:8px;border-radius:50%;background:${v};display:inline-block"></i>${k}</span>`).join('')}
          </div>
        </div>
        <div class="card-body"><div class="big-chart" id="st-main-chart"></div></div>
      </section>
    </div>
    <div class="grid12" style="margin-bottom:var(--gap)">
      <section class="card hoverable sp-12">
        <div class="card-head"><h3 class="card-title">连板高度阶梯</h3></div>
        <div class="card-body"><div class="sub-chart" id="st-sub-chart"></div></div>
      </section>
    </div>
    <section class="card hoverable">
      <div class="card-head"><h3 class="card-title">六阶段操作手册</h3><div class="card-extra"><span style="font-size:15px;color:var(--text-3)">基于 A 股短线情绪周期理论</span></div></div>
      <div class="card-body">
        <div class="phase-cards">
          ${PHASE_GUIDE.map((p) => `
            <div class="phase-card" data-phase="${p.name}">
              <h4><span class="pdot" style="background:${PHASE_COLORS[p.name]}"></span>${p.name}</h4>
              <div class="pc-feat">${p.feat}</div>
              <div class="pc-op">${p.op}</div>
            </div>`).join('')}
        </div>
      </div>
    </section>`;

  loadToday();
  loadHistory();
}

export function unmount() {
  disposeCharts(charts);
  root = null;
}
