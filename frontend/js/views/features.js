/* views/features.js — 题材特征看板（终端版）
   1 题材赚钱效应榜（横向条形 + 列表 + 亏钱效应）
   2 交易拥挤度（拥挤分进度条 + level 徽章，预警高拥挤）
   3 市场主线（多日累积排名 / 数据不足时 today_top + note）
   4 情绪龙头梯队（连板梯队 + 人气榜，最高板只在梯队中出现一次） */

import { api, fmtMoney, fmtPct, fmtNum, pctClass, isTradingTime } from '../api.js';
import { dataStamp, emptyState, skeleton, escapeHtml } from '../ui.js';
import { mkChart, disposeCharts, moneyEffectBarOption } from '../charts.js';

let root = null;
let timers = [];
// 挂载代号：主题切换 route(true) 复用同一容器重挂载，旧挂载的在途响应若不作废，
// 会 dispose 掉新视图的活图表并把实例绑到已脱离 DOM 的旧节点上
let gen = 0;
let meChart = null;  // 唯一图表实例，自管 dispose（不入数组，避免死引用无界堆积）
let loadAllRunning = false;

const CROWD_COLORS = {
  '过热拥挤': 'var(--up)',
  '偏拥挤': 'var(--amber)',
  '正常': 'var(--flat)',
  '冷清': 'var(--cyan)',
};

export function mount(el) {
  gen += 1;
  root = el;
  root.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">主线深研</h2>
      <p class="page-sub">交叉验证题材赚钱效应、交易拥挤度、持续性与龙头梯队，识别当日主线与分歧风险</p>
    </div>
    <div class="card ft-workbench-card">
      <div class="card-head">
        <span class="card-title">主线结构深研 · MARKET STRUCTURE</span>
        <span class="card-extra">赚钱效应 × 拥挤度 × 持续性 × 龙头梯队</span>
      </div>
      <div class="card-body ft-workbench-body">
        <div class="ft-workbench">
          <div class="ft-workbench-col">
            <section class="ft-module" id="ft-money">
              <div class="ft-module-head"><span class="ft-module-title">题材赚钱效应榜 · MONEY EFFECT</span><span class="card-extra" id="ft-money-stamp"></span></div>
              <div class="ft-module-body" id="ft-money-body">${skeleton(6)}</div>
            </section>
            <section class="ft-module" id="ft-mainline">
              <div class="ft-module-head"><span class="ft-module-title">市场主线 · MAINLINE</span><span class="card-extra" id="ft-mainline-stamp"></span></div>
              <div class="ft-module-body" id="ft-mainline-body">${skeleton(5)}</div>
            </section>
          </div>
          <div class="ft-workbench-col">
            <section class="ft-module" id="ft-crowd">
              <div class="ft-module-head"><span class="ft-module-title">交易拥挤度 · CROWDEDNESS</span><span class="card-extra" id="ft-crowd-stamp"></span></div>
              <div class="ft-module-body" id="ft-crowd-body">${skeleton(6)}</div>
            </section>
          </div>
        </div>
        <section class="ft-module ft-module-wide" id="ft-leaders">
          <div class="ft-module-head"><span class="ft-module-title">情绪龙头梯队 · LEADERS</span><span class="card-extra" id="ft-leaders-stamp"></span></div>
          <div class="ft-module-body" id="ft-leaders-body">${skeleton(6)}</div>
        </section>
      </div>
    </div>`;

  loadAll();

  timers.push(setInterval(() => {
    if (!isTradingTime()) return;
    loadAll();
  }, 25000));
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  if (meChart) { disposeCharts([meChart]); meChart = null; }
  root = null;
}

async function loadAll() {
  if (loadAllRunning) return;
  loadAllRunning = true;
  try {
    // 四个模块共享涨停池/板块快照。冷进程同时打四路会形成缓存击穿，
    // 把每个请求都拖到前端 12 秒预算外。先用赚钱效应预热共享底座，
    // 其余三路再并发；模块仍独立降级，不让一个坏源拖垮整页。
    await loadMoneyEffect();
    await Promise.allSettled([loadCrowdedness(), loadMainline(), loadLeaders()]);
  } finally {
    loadAllRunning = false;
  }
}

function gotoStock(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.chat.code', code);
  location.hash = '#/chat';
}

function kindBadge(kind) {
  if (kind === 'industry') return '<span class="ft-kind ind">行业</span>';
  if (kind === 'concept') return '<span class="ft-kind con">概念</span>';
  return '';
}

async function fetchFeature(path) {
  try {
    return { data: await api.get(path), transportFailed: false, error: null };
  } catch (error) {
    return { data: null, transportFailed: true, error };
  }
}

function renderFeatureTransportFailure(body, label, error) {
  const message = String(error?.message || error || '');
  const timedOut = /超时|abort|aborted|timeout/i.test(message);
  body.innerHTML = emptyState(
    `${label}加载失败`,
    timedOut
      ? '本模块超过交互时间预算，其他模块仍可使用；稍后可单独重试'
      : '后端请求未成功，请检查服务连接后重试',
    '',
    'alert',
  );
}

function factorNotice(data, label) {
  if (!data || data.data_state !== 'degraded') return '';
  const coverage = data.coverage || {};
  const factors = Object.entries(coverage)
    .filter(([key, value]) => key !== 'themes' && key !== 'rows' && value && typeof value === 'object')
    .map(([, value]) => `${Number(value.complete || 0)}/${Number(value.total || 0)}`);
  const reasons = (data.degraded_factors || []).slice(0, 3).map(escapeHtml).join('、');
  return `<div class="ft-note"><b>${escapeHtml(label)}为部分因子结果</b>${factors.length ? ` · 覆盖 ${factors.join(' / ')}` : ''}<br><span>不与完整因子分数等置比较${reasons ? `；${reasons}` : ''}。</span></div>`;
}

/* ================= 1 题材赚钱效应榜 ================= */

async function loadMoneyEffect() {
  const body = root?.querySelector('#ft-money-body');
  if (!body) return;
  const g = gen;
  const { data: d, transportFailed, error } = await fetchFeature('/features/money-effect');
  // 卸载或重挂载后的陈旧响应直接丢弃（防 null root 解引用、防 dispose 新视图的活图表）
  if (g !== gen || !root) return;
  if (transportFailed) { renderFeatureTransportFailure(body, '赚钱效应', error); return; }
  const stamp = root.querySelector('#ft-money-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d);
  const themes = (d?.themes || []).filter((t) => t && t.name);
  if (!themes.length) {
    body.innerHTML = emptyState('赚钱效应本次未计算', '公开行情源本次未返回可展示的涨跌分布样本');
    if (meChart) { disposeCharts([meChart]); meChart = null; }
    return;
  }
  const top = themes.slice(0, 12);
  const losers = (d?.losers || []).filter((t) => t && t.name).slice(0, 6);

  body.innerHTML = `
    ${factorNotice(d, '赚钱分')}
    <div class="ft-me-chart" id="ft-me-chart"></div>
    <div class="ft-me-list">
      ${top.map((t) => {
        const cls = pctClass(t.change_pct);
        const msCls = pctClass(t.money_score);
        return `<div class="ft-me-row">
          <span class="ft-theme clickable" data-tip="${escapeHtml(`${t.name}\n涨停 ${t.zt_count ?? '--'} · 最高连板 ${t.max_lianban ?? '--'}\n上涨/下跌 ${t.up_count ?? '--'}/${t.down_count ?? '--'} · 成交 ${fmtMoney(t.amount)}`)}">${escapeHtml(t.name)}</span>
          ${kindBadge(t.kind)}
          <span class="ft-me-chg ${cls} num">${fmtPct(t.change_pct)}</span>
          <span class="ft-me-score ${msCls} num" title="${t.score_coverage?.state === 'partial' ? '部分因子分，不等同完整评分' : '赚钱分'}">${t.money_score == null ? '--' : Number(t.money_score).toFixed(1)}</span>
          <span class="ft-me-cell num" title="涨停/最高连板">${t.zt_count ?? '--'}<span class="u">板</span>${t.max_lianban != null ? '/' + t.max_lianban : ''}</span>
          <span class="ft-me-leader clickable" data-tip="领涨股">${escapeHtml(t.leader_name || '--')}<span class="${pctClass(t.leader_change_pct)} num">${t.leader_name ? ' ' + fmtPct(t.leader_change_pct) : ''}</span></span>
          <span class="ft-me-flow num" title="板块成交额">${fmtMoney(t.amount, 1)}</span>
        </div>`;
      }).join('')}
    </div>
    ${losers.length ? `<div class="ft-losers">亏钱效应 · <span class="down">${losers.map((t) => `${escapeHtml(t.name)} ${fmtPct(t.change_pct)}`).join('　')}</span></div>` : ''}`;

  const host = body.querySelector('#ft-me-chart');
  if (meChart) { disposeCharts([meChart]); meChart = null; }
  meChart = mkChart(host);
  if (meChart) { meChart.setOption(moneyEffectBarOption(top), true); }
  else if (host) host.innerHTML = '<div class="empty-state"><div class="es-title">图表库未加载</div></div>';
}

/* ================= 2 交易拥挤度 ================= */

async function loadCrowdedness() {
  const body = root?.querySelector('#ft-crowd-body');
  if (!body) return;
  const g = gen;
  const { data: d, transportFailed, error } = await fetchFeature('/features/crowdedness');
  if (g !== gen || !root) return;
  if (transportFailed) { renderFeatureTransportFailure(body, '交易拥挤度', error); return; }
  const stamp = root.querySelector('#ft-crowd-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d);
  const sectors = (d?.sectors || []).filter((s) => s && s.name);
  if (!sectors.length) {
    body.innerHTML = emptyState('交易拥挤度本次未计算', '成交与题材集中度样本尚未达到本次计算条件');
    return;
  }
  // 高拥挤在上（按 crowd_score 降序），醒目预警
  const rows = sectors.slice().sort((a, b) => (Number(b.crowd_score) || 0) - (Number(a.crowd_score) || 0)).slice(0, 14);
  body.innerHTML = `${factorNotice(d, '拥挤度')}<div class="ft-crowd-list">${rows.map((s) => {
    const score = Math.max(0, Math.min(100, Number(s.crowd_score) || 0));
    const color = CROWD_COLORS[s.level] || 'var(--flat)';
    const lvlCls = s.level === '过热拥挤' ? 'hot' : s.level === '偏拥挤' ? 'warm' : s.level === '冷清' ? 'cold' : '';
    return `<div class="ft-crowd-row">
      <div class="ft-crowd-top">
        <span class="ft-theme">${escapeHtml(s.name)}</span>
        ${kindBadge(s.kind)}
        <span class="ft-crowd-lvl ${lvlCls}">${escapeHtml(s.level || '--')}</span>
        <span class="ft-crowd-chg ${pctClass(s.change_pct)} num">${fmtPct(s.change_pct)}</span>
      </div>
      <div class="ft-crowd-track" data-tip="拥挤分 ${score} · 成交占比 ${s.amt_share != null ? fmtNum(s.amt_share, 1) + '%' : '--'}">
        <div class="ft-crowd-bar" style="width:${score}%;background:${color}"></div>
        <span class="ft-crowd-num num">${Math.round(score)}</span>
      </div>
      <div class="ft-crowd-meta num">成交占比 ${s.amt_share != null ? fmtNum(s.amt_share, 1) + '%' : '--'}${s.zt_count != null ? ` · 涨停 ${s.zt_count}` : ''}</div>
    </div>`;
  }).join('')}</div>`;
}

/* ================= 3 市场主线 ================= */

async function loadMainline() {
  const body = root?.querySelector('#ft-mainline-body');
  if (!body) return;
  const g = gen;
  const { data: d, transportFailed, error } = await fetchFeature('/features/mainline');
  if (g !== gen || !root) return;
  if (transportFailed) { renderFeatureTransportFailure(body, '主线数据', error); return; }
  const stamp = root.querySelector('#ft-mainline-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d);

  const switchBar = d?.switch_signal
    ? `<div class="ft-switch">⚠ ${escapeHtml(d.switch_signal)}</div>` : '';

  // 数据不足：显示 note + today_top
  const mainlines = (d.mainlines || []).filter((m) => m && m.name);
  if (d.note || !mainlines.length) {
    const top = (d.today_top || []).filter((t) => t && t.name);
    body.innerHTML = `${switchBar}
      <div class="ft-note">${escapeHtml(d.note || '主线数据需多日累积，暂以今日榜首为参考')}</div>
      ${top.length ? `<div class="ft-today-top">
        <div class="ft-sub-title">今日榜首</div>
        ${top.slice(0, 8).map((t, i) => `<div class="ft-tt-row">
          <span class="ft-rank num">${i + 1}</span>
          <span class="ft-theme">${escapeHtml(t.name)}</span>
          ${kindBadge(t.kind)}
          <span class="ft-tt-score ${pctClass(t.money_score ?? t.change_pct)} num">${t.money_score != null ? Number(t.money_score).toFixed(1) : fmtPct(t.change_pct)}</span>
        </div>`).join('')}
      </div>` : emptyState('今日榜单本次未形成', '当前批次没有达到榜单规则的可展示标的')}`;
    return;
  }

  const maxStrength = Math.max(1, ...mainlines.map((m) => Number(m.strength) || 0));
  body.innerHTML = `${switchBar}
    <div class="ft-sub-title">近 ${d.history_days ?? '--'} 日主线排名</div>
    <div class="ft-mainline-list">${mainlines.slice(0, 8).map((m, i) => {
      const strength = Number(m.strength) || 0;
      const w = Math.max(4, (strength / maxStrength) * 100);
      return `<div class="ft-ml-row">
        <div class="ft-ml-top">
          <span class="ft-rank num">${i + 1}</span>
          <span class="ft-theme">${escapeHtml(m.name)}</span>
          ${kindBadge(m.kind)}
          <span class="ft-ml-days num" data-tip="上榜天数">${m.days_on_board ?? '--'}天</span>
        </div>
        <div class="ft-ml-strength" data-tip="强度 ${strength.toFixed(1)} · 平均赚钱分 ${m.avg_money_score != null ? Number(m.avg_money_score).toFixed(1) : '--'}">
          <div class="ft-ml-bar" style="width:${w}%"></div>
          <span class="ft-ml-snum num">${strength.toFixed(1)}</span>
        </div>
        <div class="ft-ml-meta num">均分 ${m.avg_money_score != null ? Number(m.avg_money_score).toFixed(1) : '--'} · 强度仅含持续性与赚钱效应</div>
      </div>`;
    }).join('')}</div>`;
}

/* ================= 4 情绪龙头梯队 ================= */

async function loadLeaders() {
  const body = root?.querySelector('#ft-leaders-body');
  if (!body) return;
  const g = gen;
  const { data: d, transportFailed, error } = await fetchFeature('/features/leaders');
  if (g !== gen || !root) return;
  if (transportFailed) { renderFeatureTransportFailure(body, '龙头数据', error); return; }
  const stamp = root.querySelector('#ft-leaders-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.data_date || d.ts, d.missing);

  const kongban = d?.kongban || null;
  const rawLadder = (d?.ladder || []).filter((l) => l && (l.stocks || []).length);
  // 后端的 kongban 与 ladder 最高档是同一批股票。只渲染一套梯队；
  // 极端情况下梯队字段为空，才用 kongban 原样补成一个最高档，避免丢数据。
  const ladder = rawLadder.length ? rawLadder : (kongban && (kongban.stocks || []).length ? [{
    label: kongban.height === 1 ? '首板' : `${kongban.height ?? '--'}连板`,
    lianban: kongban.height,
    count: kongban.stocks.length,
    stocks: kongban.stocks,
  }] : []);
  const popularity = (d?.popularity || []).filter((p) => p && p.name);

  if (!kongban && !ladder.length && !popularity.length) {
    body.innerHTML = emptyState('龙头梯队本次未形成', '当前批次未取得可用于梯队归类的连板样本');
    return;
  }

  const ladderHtml = ladder.length ? `
    <div class="ft-ladder">${ladder.map((l) => {
      const hot = (l.lianban ?? 0) >= 4;
      return `<div class="ladder-row">
        <span class="ladder-tag ${hot ? 'hot' : ''}">${escapeHtml(l.label || (l.lianban != null ? l.lianban + '连板' : '--'))}<span class="lt-cnt">${l.count ?? (l.stocks || []).length}只</span></span>
        <div class="ladder-stocks">${(l.stocks || []).map((s) => `
          <span class="stock-chip ${s.is_leader ? 'leader' : ''}" data-code="${escapeHtml(s.code || '')}" data-tip="${escapeHtml(`${s.name} ${s.code || ''}\n封单 ${fmtMoney(s.seal_amount)} · 换手 ${s.turnover_rate != null ? fmtNum(s.turnover_rate, 1) + '%' : '--'}\n首封 ${s.first_time || '--'}`)}">
            ${s.is_leader ? '<span class="sc-star">★</span>' : ''}
            <span class="sc-name">${escapeHtml(s.name || '--')}</span>
            ${s.reason ? `<span class="sc-reason">${escapeHtml(s.reason)}</span>` : ''}
          </span>`).join('')}</div>
      </div>`;
    }).join('')}</div>` : '';

  const popHtml = popularity.length ? `
    <div class="ft-pop">
      <div class="ft-sub-title">人气榜 · POPULARITY</div>
      ${popularity.slice(0, 10).map((p, i) => `<div class="ft-pop-row">
        <span class="ft-rank num">${i + 1}</span>
        <span class="ft-pop-name clickable" data-code="${escapeHtml(p.code || '')}">${escapeHtml(p.name || '--')}${p.lianban != null ? `<span class="ft-pop-lb num">${p.lianban}板</span>` : ''}</span>
        <span class="ft-pop-seal num" data-tip="${escapeHtml(p.reason || '')}">${fmtMoney(p.seal_amount, 0)}${p.seal_ratio != null ? ` · ${fmtNum(p.seal_ratio, 1)}%` : ''}</span>
      </div>`).join('')}
    </div>` : '';

  body.innerHTML = `<div class="ft-leaders-grid">
    <div class="ft-leaders-main">${ladderHtml || (popHtml ? '' : emptyState('暂无连板梯队'))}</div>
    ${popHtml ? `<div class="ft-leaders-side">${popHtml}</div>` : ''}
  </div>`;

  body.querySelectorAll('.stock-chip[data-code], .ft-pop-name[data-code]').forEach((c) => {
    c.addEventListener('click', () => gotoStock(c.dataset.code));
  });
}
