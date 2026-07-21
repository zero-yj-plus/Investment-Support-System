/* views/evening.js — 交易节奏工作站「次日策略」(母文档 §26 · 19:30 发布)
   ------------------------------------------------------------------
   定位：盘后给出明日作战计划——明日基调/风险等级预判/仓位上限/重点·禁碰方向/
        确定性策略≤3/高盈亏比策略≤3/趋势票/持仓处理/观察池排序/强于·符合·弱于预期三剧本。
   报告正文(明日计划全文)由后端 AI 引擎(rtype=evening)按注入的真实数据流式生成——本文件不碰生成逻辑。

   本页在报告【下方】用 bottomRender 注入一个结构化面板：「明日观察池排序」——
   把已建的观察池(/api/watchlist)串进次日计划：把想买没买的标的按接近买点 A→E 排好，
   明日只盯到价信号、A 接近买点优先看。

   铁律：现价/涨跌/评级(grade)全部来自 /api/watchlist 端点的真实返回(grade 由后端按真实现价算)；
        本文件绝不生成/推算任何数字——现价缺失显示「现价缺」、区间缺显示「—」，绝不编造。 */

import { makeReportView } from './_report_page.js';
import { api, fmtPrice, fmtPct, fmtNum, pctClass } from '../api.js';
import { escapeHtml, icon, emptyState, skeleton } from '../ui.js';

// 观察池评级配色（A 绿/B 蓝/C 灰/D 橙/E 红）——与首页驾驶舱观察池卡片同一套语义。
// 注意：这是「接近买点的优先级」配色，不是 A股涨跌红绿，故 A=绿(最该看)、E=红(最远)。
const GRADE_COLOR = { A: '#11cc8e', B: '#3b82f6', C: 'var(--text-3)', D: '#f5a623', E: '#ff4d5e' };
const GRADE_ORDER = { A: 0, B: 1, C: 2, D: 3, E: 4 };  // null/未知评级排末尾

// 点股票 → 跳逐票深度诊断（全站统一约定）
function gotoDiag(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.diagnosis.code', code);
  location.hash = '#/diagnosis';
}

// 幂等注入样式（带 id，重复 mount 不重复插）；全部走 CSS 变量跟随主题，手机端单列不溢出。
function injectStyle() {
  if (document.getElementById('evening-style')) return;
  const css = `
  .ev-panel{margin-top:var(--gap)}
  .ev-panel .card-head{display:flex;align-items:center;gap:8px}
  .ev-lead{font-size:13px;color:var(--text-2);line-height:1.6;margin:0 0 12px;
    background:var(--panel-2);border:1px solid var(--border);border-left:3px solid var(--accent);
    border-radius:var(--radius-sm);padding:9px 13px}
  .ev-lead b{color:var(--text);font-weight:700}
  .ev-row{display:flex;align-items:center;gap:12px;padding:11px 4px;border-bottom:1px solid var(--border);
    cursor:pointer;transition:background .12s}
  .ev-row:last-child{border-bottom:none}
  .ev-row:hover{background:var(--panel-2)}
  .ev-grade{flex:0 0 auto;width:24px;height:24px;border-radius:6px;display:inline-flex;align-items:center;
    justify-content:center;font-size:13px;font-weight:800;color:#fff}
  .ev-name{flex:0 0 auto;min-width:88px;display:flex;flex-direction:column;gap:1px}
  .ev-name .nm{font-size:14px;font-weight:700;color:var(--text);line-height:1.2}
  .ev-name .cd{font-size:11px;color:var(--text-3);font-family:var(--mono,monospace)}
  .ev-px{flex:0 0 auto;min-width:96px;text-align:right;font-family:var(--mono,monospace);font-weight:700;font-size:14px}
  .ev-px .miss{color:var(--text-3);font-weight:600;font-size:12px}
  .ev-px .chg{font-size:12px;font-weight:700;margin-left:2px}
  .ev-zone{flex:1 1 auto;min-width:120px;display:flex;flex-direction:column;gap:1px;text-align:right}
  .ev-zone .zk{font-size:11px;color:var(--text-3)}
  .ev-zone .zv{font-size:13px;font-weight:700;color:var(--text-2);font-family:var(--mono,monospace)}
  .ev-status{flex:0 0 auto;min-width:84px;text-align:right;font-size:12px;color:var(--text-3)}
  .ev-foot{margin-top:11px;font-size:11px;color:var(--text-3);line-height:1.6}
  @media (max-width:760px){
    .ev-row{flex-wrap:wrap;gap:7px 10px;padding:10px 2px}
    .ev-name{min-width:0;flex:1 1 auto}
    .ev-px{min-width:0;flex:0 0 auto;text-align:left}
    .ev-zone{flex:1 1 100%;flex-direction:row;justify-content:space-between;text-align:left}
    .ev-zone .zv{text-align:right}
    .ev-status{flex:1 1 100%;text-align:left;min-width:0}
  }`;
  const tag = document.createElement('style');
  tag.id = 'evening-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}

const view = makeReportView({
  rtype: 'evening',
  title: '次日策略',
  notBefore: '19:30',
  bottomRender,
});

export function mount(el) { injectStyle(); view.mount(el); }
export function unmount() { view.unmount(); }

/**
 * 报告下方面板：「明日观察池排序」。
 * @param {HTMLElement} host  bottomRender 提供的挂载容器(#rp-bottom 内)
 * @param {{reload:Function, isAlive:()=>boolean}} ctx  isAlive() 防卸载后写脏 DOM
 */
async function bottomRender(host, ctx) {
  if (!host) return;
  host.innerHTML = `
    <section class="card ev-panel">
      <div class="card-head">
        <span class="card-title">明日观察池排序 · WATCHLIST</span>
        <span class="card-extra"><span class="btn btn-sm btn-ghost" id="ev-manage">管理观察池</span></span>
      </div>
      <div class="card-body" id="ev-body">${skeleton(4)}</div>
    </section>`;
  host.querySelector('#ev-manage')?.addEventListener('click', () => { location.hash = '#/watchlist'; });

  let d = null;
  try { d = await api.get('/watchlist'); } catch (e) {
    if (!ctx.isAlive()) return;             // 卸载后到达的失败响应：丢弃，不写脏 DOM
    const body = host.querySelector('#ev-body');
    if (body) body.innerHTML = emptyState('观察池加载失败', e.message, '', 'alert');
    return;
  }
  if (!ctx.isAlive()) return;               // 卸载后到达的成功响应：同样丢弃
  const body = host.querySelector('#ev-body');
  if (!body) return;

  const items = (d?.items || []).slice();
  if (!items.length) {
    // 空时友好引导去添加（铁律：不堆假标的占位）
    body.innerHTML = emptyState(
      '观察池还没有标的',
      '把明天想买、还没到买点的股票加进来，系统按「理想买入区间」每日排 A—E：到价才提示、不到价不催买。',
      `<button class="btn btn-primary" id="ev-go" style="margin-top:8px">${icon('plus')}去添加观察标的</button>`,
      'search',
    );
    body.querySelector('#ev-go')?.addEventListener('click', () => { location.hash = '#/watchlist'; });
    return;
  }

  // 按接近买点排序 A→B→C→D→E→null（grade 由后端按真实现价算，前端只排序不重算）
  items.sort((a, b) => (GRADE_ORDER[a.grade] ?? 9) - (GRADE_ORDER[b.grade] ?? 9));
  const aCount = items.filter((it) => it.grade === 'A').length;

  const rows = items.map((it) => {
    const q = it.quote || {};
    const gc = GRADE_COLOR[it.grade] || 'var(--text-3)';
    const code = escapeHtml(it.code || '');
    const name = escapeHtml(it.name || it.code || '');
    // 现价：缺失诚实显示「现价缺」，绝不编（铁律）
    const priceHtml = q.price != null
      ? `${fmtPrice(q.price)}${q.change_pct != null ? `<span class="chg ${pctClass(q.change_pct)}">${fmtPct(q.change_pct)}</span>` : ''}`
      : '<span class="miss">现价缺</span>';
    // 理想买入区间：任一端缺即整体显示「—」（不拼半截区间误导）
    const zone = (it.target_low != null && it.target_high != null)
      ? `${fmtNum(it.target_low, 2)}–${fmtNum(it.target_high, 2)}`
      : '—';
    return `<div class="ev-row" data-code="${code}" role="button" tabindex="0">
      <span class="ev-grade" style="background:${gc}">${escapeHtml(it.grade || '—')}</span>
      <span class="ev-name"><span class="nm">${name}</span><span class="cd">${code}</span></span>
      <span class="ev-px">${priceHtml}</span>
      <span class="ev-zone"><span class="zk">理想买入区间</span><span class="zv">${zone}</span></span>
      <span class="ev-status">${escapeHtml(it.status || '')}</span>
    </div>`;
  }).join('');

  body.innerHTML = `
    <p class="ev-lead">明日重点盯这些到价信号，<b>A 接近买点优先看</b>${aCount ? `（当前 <b>${aCount}</b> 只评 A）` : ''}。到价才动手、不到价不追——评级与现价均由后端按真实行情计算。</p>
    ${rows}
    <div class="ev-foot">评级 A→E 按当前现价距理想买入区间远近排序（A 最近、E 最远）；点任意一行进入逐票深度诊断，看明日具体价格条件。</div>`;

  body.querySelectorAll('.ev-row').forEach((r) => {
    const go = () => gotoDiag(r.dataset.code);
    r.addEventListener('click', go);
    r.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
  });
}
