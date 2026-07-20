/* views/mainforce.js — 主力吸筹（盘后真数据 · 免费公开资金源）
   1 龙虎榜机构净买榜（机构专用席位合计净买，红进绿出）
   2 行业资金流榜（主力净额降序，红进绿出）
   （北向资金卡已删除 2026-07-03：交易所 2024-08 起停止披露北向净流入，空卡无信息量——用户裁定）

   全部盘后数据：盘中/未结算时后端取上一交易日并在 missing 里给提示；空集走 emptyState。
   颜色：红涨绿出沿用全局 pctClass（正=up 红 / 负=down 绿）。 */

import { api, fmtMoney, fmtPct, fmtNum, pctClass, isTradingTime } from '../api.js';
import { dataStamp, emptyState, skeleton, escapeHtml } from '../ui.js';

let root = null;
let timers = [];
// 挂载代号：与 features.js 同款防串扰，旧挂载的在途响应作废，避免写进已脱离 DOM 的旧节点
let gen = 0;

export function mount(el) {
  gen += 1;
  root = el;
  injectMainforceStyle();
  root.innerHTML = `
    <div class="grid12">
      <div class="card sp-12" id="mf-inst">
        <div class="card-head"><span class="card-title">龙虎榜机构净买 · INSTITUTION</span><span class="card-extra" id="mf-inst-stamp"></span></div>
        <div class="card-body" id="mf-inst-body">${skeleton(6)}</div>
      </div>

      <div class="card sp-12" id="mf-ind">
        <div class="card-head"><span class="card-title">行业资金流榜 · INDUSTRY FLOW</span><span class="card-extra" id="mf-ind-stamp"></span></div>
        <div class="card-body" id="mf-ind-body">${skeleton(6)}</div>
      </div>
    </div>`;

  loadAll();
  // 盘后数据，盘中不变；仅非交易时段首挂载已取上一交易日。低频兜底刷新即可（90s），
  // 且仅交易时段（收盘后结算窗口）触发，避免对盘后静态数据无谓请求。
  timers.push(setInterval(() => {
    if (!isTradingTime()) return;
    loadAll();
  }, 90000));
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  root = null;
}

function loadAll() {
  loadInst();
  loadIndustry();
}

function gotoStock(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.chat.code', code);
  location.hash = '#/chat';
}

// 把后端 missing 里的"提示型"说明（盘后未结算/非交易日/无机构席位等）摘成一行灰字注解。
// 仅展示包含中文说明的条目，纯字段名（如「北向资金」）不当注解显示，避免噪音。
function noteLine(missing) {
  const notes = (missing || []).filter((m) => typeof m === 'string' && /[，。：(（]/.test(m));
  if (!notes.length) return '';
  // 「隔日/未结算/非交易日」这类=数据非今日，客户最容易误当实时→醒目黄条(对齐资金追踪#6)，
  // 别再用小灰字让客户看不见。其余提示(无机构席位等)仍走低调灰字。
  const isStale = (m) => /上一交易日|未结算|非交易日|最近交易日/.test(m);
  const stale = notes.filter(isStale);
  const other = notes.filter((m) => !isStale(m));
  let html = '';
  if (stale.length) {
    html += `<div style="margin-bottom:10px;padding:6px 10px;border:1px solid var(--amber-line,rgba(245,166,35,.4));background:rgba(245,166,35,.08);border-radius:7px;font-size:12.5px;color:var(--amber);line-height:1.55">⚠ ${escapeHtml(stale.join(' · '))}——<b>非今日实时</b>，与实时行情对比有差属正常。</div>`;
  }
  if (other.length) html += `<div class="mf-note">${escapeHtml(other.join(' · '))}</div>`;
  return html;
}

// 龙虎榜四态横幅（发布门禁）：今日完整 / 今日结算中·未发布 / 当前源异常 / 最近完整快照。
// 绝不一律写"不可用"；快照回退必须显眼标其日期，绝不冒充今日实时。
function longhuStatusBanner(d) {
  const status = String(d?.status || '');
  const dd = escapeHtml(d?.data_date || '');
  if (status === 'complete') {
    return `<div class="mf-lh-status complete">✓ 今日完整 · ${dd}</div>`;
  }
  if (status === 'last_complete') {
    const sd = escapeHtml(d?.snapshot_date || '');
    const why = (d?.missing || []).find((m) => /结算中|源异常|未发布/.test(m)) || '今日尚未发布';
    return `<div class="mf-lh-status snap">📌 最近完整快照（${sd}）—— ${escapeHtml(String(why).replace(/，下方.*/, ''))}。<b>非今日实时</b></div>`;
  }
  if (status === 'source_error') {
    return `<div class="mf-lh-status err">⚠ 当前数据源异常 · ${dd} —— 稍后自动重试；有历史快照将回退展示</div>`;
  }
  if (status === 'settling') {
    return `<div class="mf-lh-status settling">⏳ 今日龙虎榜结算中 / 尚未发布 · ${dd} —— 非不可用，盘后结算发布后自动出现</div>`;
  }
  return '';
}

function verificationCount(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.trunc(n) : 0;
}

function renderOfficialVerification(verification) {
  const hasContract = verification && typeof verification === 'object';
  const v = hasContract ? verification : {};
  const state = String(v.comparison_state || 'unavailable');
  const counts = v.counts && typeof v.counts === 'object' ? v.counts : {};
  const matched = verificationCount(counts.matched);
  const eastmoneyOnly = verificationCount(counts.eastmoney_only);
  const officialOnly = verificationCount(counts.official_only);
  const scope = '<span class="mf-verify-scope">只比股票代码集合，不比金额</span>';

  if (!hasContract) {
    return `<div class="mf-official-verify unavailable">
      <div class="mf-verify-main"><b>官方代码集核验状态未返回</b><span>当前未做差异分类。</span></div>${scope}
    </div>`;
  }

  if (state === 'complete') {
    return `<div class="mf-official-verify complete">
      <div class="mf-verify-main"><b>交易所官网代码集已核验</b><span>匹配 ${matched} · 东财独有 ${eastmoneyOnly} · 官网独有 ${officialOnly}</span></div>${scope}
    </div>`;
  }
  if (state === 'partial') {
    return `<div class="mf-official-verify partial">
      <div class="mf-verify-main"><b>交易所官网代码集部分核验</b><span>差异暂定：匹配 ${matched} · 东财独有 ${eastmoneyOnly} · 官网独有 ${officialOnly}</span></div>${scope}
    </div>`;
  }
  // 恢复回退：今日官方快照空/损坏 → 展示上一完整交易日快照，明确标日期，不做今日差异分类。
  if (state === 'reference_prior_day') {
    const rd = escapeHtml(String(v.official_cache?.recovered_from_date || ''));
    const rc = verificationCount(v.official_cache?.recovered_code_count);
    return `<div class="mf-official-verify partial">
      <div class="mf-verify-main"><b>官方今日结算中/未发布 · 参考上一完整交易日（${rd}）</b><span>该日官网 ${rc} 只；今日到达后再做代码集差异分类</span></div>${scope}
    </div>`;
  }

  const eastmoneyUnavailable = v.eastmoney_available === false;
  const title = eastmoneyUnavailable ? '未做代码集差异分类' : '官方快照待采集或更新';
  const detail = eastmoneyUnavailable
    ? '东财龙虎榜当前不可用，无法与官网代码集合比较。'
    : '后台将在 18:20 / 21:10 采集交易所官网快照；当前不把缓存缺失当作代码差异。';
  return `<div class="mf-official-verify unavailable">
    <div class="mf-verify-main"><b>${title}</b><span>${detail}</span></div>${scope}
  </div>`;
}

function injectMainforceStyle() {
  if (document.getElementById('mainforce-inline-style')) return;
  const style = document.createElement('style');
  style.id = 'mainforce-inline-style';
  style.textContent = `
    .mf-official-verify{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;padding:7px 9px;border:1px solid var(--border);border-left-width:3px;border-radius:7px;background:var(--panel-2);font-size:12px;line-height:1.45}
    .mf-official-verify.complete{border-left-color:var(--up)}
    .mf-official-verify.partial{border-left-color:var(--warn)}
    .mf-official-verify.unavailable{border-left-color:var(--text-3)}
    .mf-lh-status{margin-bottom:9px;padding:6px 10px;border-radius:7px;font-size:12.5px;line-height:1.5;border:1px solid var(--border)}
    .mf-lh-status.complete{color:var(--up);border-color:var(--up);background:rgba(46,160,67,.06)}
    .mf-lh-status.snap{color:var(--amber,#e3a008);border-color:var(--amber-line,rgba(245,166,35,.4));background:rgba(245,166,35,.08)}
    .mf-lh-status.snap b{color:var(--amber,#e3a008)}
    .mf-lh-status.settling{color:var(--text-2);border-color:var(--warn,#8a6d1f);background:var(--panel-2)}
    .mf-lh-status.err{color:var(--down,#f85149);border-color:var(--down,#f85149);background:rgba(248,81,73,.06)}
    .mf-verify-main{display:flex;align-items:center;gap:9px;min-width:0;flex-wrap:wrap}
    .mf-verify-main b{color:var(--text);font-size:12.5px}
    .mf-verify-main span{color:var(--text-2)}
    .mf-verify-scope{flex:none;color:var(--text-3)}
    @media (max-width:760px){.mf-official-verify{align-items:flex-start;flex-direction:column;gap:4px}.mf-verify-main{align-items:flex-start;flex-direction:column;gap:2px}}
  `;
  document.head.appendChild(style);
}

/* ================= 1 龙虎榜机构净买 ================= */

async function loadInst() {
  const body = root?.querySelector('#mf-inst-body');
  if (!body) return;
  const g = gen;
  let d = null;
  try { d = await api.get('/mainforce/longhu'); } catch { /* 下方兜底 */ }
  if (g !== gen || !root) return;
  const stamp = root.querySelector('#mf-inst-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.data_date || d.ts, d.missing);

  const statusBanner = longhuStatusBanner(d);
  const officialVerification = renderOfficialVerification(d?.official_verification);
  const inst = (d?.inst || []).filter((x) => x && x.code);
  if (!inst.length) {
    // 四态各自的空态文案，绝不一律"暂无/不可用"。
    const st = String(d?.status || '');
    const emptyTitle = st === 'settling' ? '今日龙虎榜结算中 / 尚未发布'
      : st === 'source_error' ? '当前龙虎榜数据源异常'
      : '暂无机构净买数据';
    const emptyMsg = st === 'settling' ? '盘后交易所结算并发布后自动出现，并非不可用；官方快照到达时会强制刷新。'
      : st === 'source_error' ? '数据源暂时异常，稍后自动重试；如有历史完整快照将回退展示。'
      : '当日有龙虎榜但无机构专用席位上榜，或盘后结算后可见';
    body.innerHTML = statusBanner + officialVerification + noteLine(d?.missing) + emptyState(emptyTitle, emptyMsg);
    return;
  }
  const maxAbs = Math.max(1, ...inst.map((x) => Math.abs(Number(x.net_buy) || 0)));
  const rows = inst.map((x, i) => {
    const v = Number(x.net_buy) || 0;
    const w = Math.max(3, (Math.abs(v) / maxAbs) * 100);
    const cls = pctClass(v);
    return `<div class="mf-inst-row clickable" data-code="${escapeHtml(x.code)}" data-tip="${escapeHtml(`${x.name || x.code}\n机构净买 ${fmtMoney(x.net_buy)}\n买入 ${fmtMoney(x.buy)} · 卖出 ${fmtMoney(x.sell)} · 席位 ${x.seats ?? '--'}`)}">
      <span class="mf-rank num">${i + 1}</span>
      <span class="mf-inst-name">${x.name && x.name !== x.code ? escapeHtml(x.name) : ''}<span class="mf-inst-code num">${escapeHtml(x.code)}</span></span>
      <span class="mf-inst-seats num" title="机构席位数">${x.seats ?? '--'}<span class="u">席</span></span>
      <span class="mf-inst-track"><span class="mf-inst-bar ${cls}" style="width:${w}%"></span></span>
      <span class="mf-inst-net num ${cls}">${fmtMoney(x.net_buy, 1)}</span>
    </div>`;
  }).join('');
  body.innerHTML = statusBanner + officialVerification + noteLine(d?.missing) + `<div class="mf-inst-list">${rows}</div>`;

  body.querySelectorAll('.mf-inst-row[data-code]').forEach((c) => {
    c.addEventListener('click', () => gotoStock(c.dataset.code));
  });
}

/* ================= 3 行业资金流榜 ================= */

async function loadIndustry() {
  const body = root?.querySelector('#mf-ind-body');
  if (!body) return;
  const g = gen;
  let d = null;
  try { d = await api.get('/mainforce/industry'); } catch { /* 下方兜底 */ }
  if (g !== gen || !root) return;
  const stamp = root.querySelector('#mf-ind-stamp');
  if (stamp && d) {
    const src = d.source ? ` · ${escapeHtml(d.source)}` : '';
    stamp.innerHTML = dataStamp(d.data_date || d.ts, d.missing) + (src ? `<span class="mf-src">${src}</span>` : '');
  }

  const inds = (d?.industries || []).filter((x) => x && x.name);
  if (!inds.length) {
    body.innerHTML = noteLine(d?.missing) + emptyState('暂无行业资金流数据', '盘后结算后可见，或当前账户无行业资金流接口权限');
    return;
  }
  const maxAbs = Math.max(1, ...inds.map((x) => Math.abs(Number(x.net_amount) || 0)));
  const rows = inds.map((x, i) => {
    const v = Number(x.net_amount) || 0;
    const w = Math.max(3, (Math.abs(v) / maxAbs) * 100);
    const cls = pctClass(v);
    const rate = x.net_rate != null ? ` · 净占比 ${fmtNum(x.net_rate, 1)}%` : '';
    const lead = x.lead_stock ? ` · 领涨 ${escapeHtml(x.lead_stock)}` : '';
    return `<div class="mf-ind-row">
      <span class="mf-rank num">${i + 1}</span>
      <span class="mf-ind-name" data-tip="${escapeHtml(`${x.name}\n主力净额 ${fmtMoney(x.net_amount)}${rate}${lead ? '\n领涨 ' + x.lead_stock : ''}`)}">${escapeHtml(x.name)}</span>
      <span class="mf-ind-chg ${pctClass(x.change_pct)} num">${fmtPct(x.change_pct)}</span>
      <span class="mf-ind-track"><span class="mf-ind-bar ${cls}" style="width:${w}%"></span></span>
      <span class="mf-ind-net num ${cls}">${fmtMoney(x.net_amount, 1)}</span>
    </div>`;
  }).join('');
  body.innerHTML = noteLine(d?.missing) + `<div class="mf-ind-list">${rows}</div>`;
}
