/* views/close.js — 交易节奏工作站「收盘复盘」(rtype=close · 母文档 §25)
   ------------------------------------------------------------------
   收盘复盘不是复述行情，而是**验证与修正**：今日判断对不对 / 主线是否验证 /
   赚钱亏钱效应 / **昨日推荐是否有效** / AI 自我纠错 / 明日修正。
   §25.2 结构含「昨日推荐验证 + 今日推荐验证」——本页用 bottomRender 在报告下方挂一个
   「推荐留痕回验」结构化面板，把已建的推荐模块串进复盘闭环。

   职责分工：
   - 报告正文：由后端 AI 引擎按注入的真实数据流式生成（_report_page.js 封装，本文件不碰生成逻辑）。
   - 下方面板：成功率摘要 + 一键回验 + 最近推荐留痕列表，数字全部来自真实端点：
       GET  /api/recommendations/stats   稳定胜率/原始观察比例/非评分状态/逐窗口统计
       POST /api/recommendations/verify  一键回验
       GET  /api/recommendations         最近推荐 + T0/T1/T2/T5 逐窗口回验

   铁律：胜率 / 收益率 / 评级一律取端点真实返回，绝不编造；取不到标 missing/「—」。
        历史判断固定、不事后改写——回验只是用真实价"对答案"，不改写已落库的推荐。 */

import { makeReportView } from './_report_page.js';
import { api, fmtPct, fmtPrice, fmtNum, pctClass } from '../api.js';
import { escapeHtml, icon, toast, emptyState, skeleton } from '../ui.js';
import {
  latestReview,
  reviewCoverage,
  reviewStateMeta,
  reviewWindowRows,
  statsPercent,
} from './_recommend_review.js';

const view = makeReportView({
  rtype: 'close',
  title: '收盘复盘',
  sub: '15:30 · 验证今日判断 + 昨日推荐回验',
  notBefore: '15:30',
  topRender,
  bottomRender,
});

export function mount(el) { view.mount(el); }
export function unmount() { view.unmount(); }

/* ====================== 样式（幂等注入，跟随主题 CSS 变量） ====================== */
function injectStyle() {
  if (document.getElementById('close-review-style')) return;
  const css = `
  .crv-card{margin-top:0}
  .crv-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .crv-head .ttl{font-size:15px;font-weight:800;color:var(--text);display:inline-flex;align-items:center;gap:6px}
  .crv-head .ttl svg{width:16px;height:16px;flex:0 0 auto}
  .crv-head .sub{font-size:12px;color:var(--text-3)}
  .crv-head .crv-actions{margin-left:auto;display:flex;gap:8px}
  /* 成功率摘要：一行 chip，胜率/收益/盈亏比都来自 stats 端点，缺失显「—」 */
  .crv-stats{display:flex;gap:8px;flex-wrap:wrap;margin:13px 0 4px}
  .crv-chip{display:flex;flex-direction:column;gap:2px;background:var(--panel-2);border:1px solid var(--border);
    border-radius:var(--radius-sm);padding:8px 13px;min-width:84px;flex:1}
  .crv-chip .ck-k{font-size:11px;color:var(--text-3);font-family:var(--mono,monospace)}
  .crv-chip .ck-v{font-size:18px;font-weight:800;color:var(--text);font-family:var(--mono,monospace)}
  .crv-chip .ck-v.up{color:var(--up)} .crv-chip .ck-v.down{color:var(--down)}
  .crv-warn{font-size:12px;color:var(--amber);border:1px solid var(--amber);background:color-mix(in srgb,var(--amber) 10%,transparent);
    border-radius:10px;padding:1px 9px;font-weight:700;white-space:nowrap}
  .crv-note{font-size:12px;color:var(--text-3);line-height:1.55;margin:8px 0 4px}
  .crv-note b{color:var(--text-2)}
  .crv-breakdown{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0 3px}
  .crv-breakdown span{font-size:11px;color:var(--text-2);border:1px solid var(--border);background:var(--panel-2);
    border-radius:10px;padding:3px 8px}
  .crv-per-window{display:grid;grid-template-columns:repeat(4,minmax(100px,1fr));gap:7px;margin-top:8px}
  .crv-pw{font-size:11px;color:var(--text-3);border:1px solid var(--border);background:var(--panel-2);
    border-radius:var(--radius-sm);padding:6px 8px;display:flex;flex-direction:column;gap:2px}
  .crv-pw b{font-size:12px;color:var(--text)}
  /* 推荐留痕列表：点任一行→诊断 */
  .crv-rec{display:block;padding:9px 4px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .12s}
  .crv-rec:last-child{border-bottom:none} .crv-rec:hover{background:var(--panel-2)}
  .crv-rec-main{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .crv-rec .r-name{font-weight:700;color:var(--text);min-width:74px}
  .crv-rec .r-code{color:var(--text-3);font-family:var(--mono,monospace);font-size:12px}
  .crv-rec .r-src{font-size:11px;color:var(--text-3);border:1px solid var(--border);border-radius:3px;padding:1px 6px}
  .crv-rec .r-px{margin-left:auto;font-family:var(--mono,monospace);font-size:13px;color:var(--text-2)}
  .crv-rec .r-ret{font-family:var(--mono,monospace);font-size:12px;font-weight:700;min-width:58px;text-align:right}
  .crv-rec .r-ret.up{color:var(--up)} .crv-rec .r-ret.down{color:var(--down)} .crv-rec .r-ret.flat{color:var(--text-3)}
  .crv-badge{font-size:12px;font-weight:700;white-space:nowrap;min-width:48px;text-align:center}
  .crv-badge.hit{color:#11cc8e} .crv-badge.miss{color:#ff4d5e} .crv-badge.pending{color:var(--text-3)}
  .crv-badge.positive{color:var(--up)} .crv-badge.negative{color:var(--down)}
  .crv-badge.warning{color:var(--amber)} .crv-badge.waiting,.crv-badge.neutral{color:var(--text-3)}
  .crv-timeline{display:grid;grid-template-columns:repeat(4,minmax(90px,1fr));gap:6px;margin-top:7px}
  .crv-window{border:1px solid var(--border);border-radius:6px;padding:4px 6px;background:var(--panel);
    display:flex;justify-content:space-between;gap:5px;font-size:11px;color:var(--text-3)}
  .crv-window b{color:var(--text-2)} .crv-window.positive span{color:var(--up)}
  .crv-window.negative span{color:var(--down)} .crv-window.warning span{color:var(--amber)}
  .crv-review-metrics{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;font-size:11px;color:var(--text-3)}
  .crv-review-metrics b{font-family:var(--mono,monospace);color:var(--text)}
  .crv-soon{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;
    gap:8px;padding:22px 14px;color:var(--text-3);min-height:120px}
  .crv-soon .icn{opacity:.45} .crv-soon .icn svg{width:30px;height:30px}
  .crv-soon .st{font-size:14px;font-weight:700;color:var(--text-2)}
  .crv-soon .sd{font-size:12px;line-height:1.6;max-width:300px}
  .crv-soon .btn-link{font-size:12px;color:var(--accent);cursor:pointer;text-decoration:underline}
  @media (max-width:760px){
    .crv-head .crv-actions{margin-left:0;width:100%}
    .crv-stats{gap:6px} .crv-chip{min-width:0;padding:7px 9px}
    .crv-rec-main{gap:6px 10px}.crv-rec .r-px{margin-left:auto}
    .crv-timeline,.crv-per-window{grid-template-columns:repeat(2,1fr)}
  }`;
  const tag = document.createElement('style');
  tag.id = 'close-review-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}

/* 点股票跳诊断（与 cockpit 同款约定） */
function gotoDiag(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.diagnosis.code', code);
  location.hash = '#/diagnosis';
}

/* ====================== topRender：昨日建议 vs 今日实际（策略自证） ====================== */
/**
 * 报告上方挂"策略自证"面板：昨日收盘对各持仓的处理倾向 → 今日真实价对账是否兑现 + 命中率。
 * 客户最核心诉求"系统说的到底准不准"——数字全程规则/真实价、AI 不参与，命中率自己说话。
 * 数据：GET /api/holdings/advice-review（昨日建议|昨日收盘|今日价|涨跌|兑现 + 命中统计）。
 */
function topRender(host, ctx) {
  if (!host) return;
  injectStyle();
  host.innerHTML = `
    <section class="card crv-card" style="margin-bottom:var(--gap)">
      <div class="card-body">
        <div class="crv-head">
          <span class="ttl">${icon('check')} 昨日建议 vs 今日实际 · 策略自证</span>
          <span class="sub">昨日收盘的处理倾向 → 今日真实价对账兑现没 · 规则/真实价，AI 不参与</span>
          <span class="crv-actions"><button class="btn btn-sm" id="adv-refresh">${icon('refresh')}刷新</button></span>
        </div>
        <div id="adv-body">${skeleton(3)}</div>
      </div>
    </section>`;
  const alive = () => (ctx?.isAlive ? ctx.isAlive() : true);
  host.querySelector('#adv-refresh')?.addEventListener('click', () => loadAdviceReview(host, alive));
  loadAdviceReview(host, alive);
}

/* 铁律：兑现/命中率全取端点真实回检值；取不到→留空/「—」，绝不编。 */
async function loadAdviceReview(host, alive) {
  const box = host.querySelector('#adv-body');
  if (!box) return;
  let d = null;
  try { d = await api.get('/holdings/advice-review'); } catch { /* 兜底：下行按缺失渲染 */ }
  if (!alive() || !host.querySelector('#adv-body')) return;

  if (!d || d.ok === false) {
    box.innerHTML = `<div class="crv-note">策略回检暂不可用，<b>不展示任何推断数字</b>（铁律：取不到即留空）。</div>`;
    return;
  }
  const rows = d.rows || [];
  if (!rows.length) {
    box.innerHTML = `<div class="crv-soon">
      <div class="icn">${icon('check')}</div>
      <div class="st">还没有可回检的昨日建议</div>
      <div class="sd">每个交易日收盘会按规则给你的持仓记一份处理倾向快照，<b>次日起</b>用真实价对账是否兑现、长期沉淀命中率。先去『持仓分析』保存持仓，并生成一次『收盘复盘』即可开启。</div>
    </div>`;
    return;
  }
  const s = d.stats || {};
  const wr = s.win_rate;
  const vCls = (v) => (v === '兑现' ? 'hit' : v === '未兑现' ? 'miss' : 'pending');
  const vTxt = (v) => (v === '兑现' ? '✓兑现' : v === '未兑现' ? '✗未兑现' : '中性');
  box.innerHTML = `
    <div class="crv-stats">
      <div class="crv-chip"><span class="ck-k">对照日</span><span class="ck-v" style="font-size:14px">${escapeHtml(d.base_date || '—')}</span></div>
      <div class="crv-chip"><span class="ck-k">兑现</span><span class="ck-v up">${s.hit != null ? s.hit : '—'}</span></div>
      <div class="crv-chip"><span class="ck-k">未兑现</span><span class="ck-v down">${s.miss != null ? s.miss : '—'}</span></div>
      <div class="crv-chip"><span class="ck-k">命中率</span><span class="ck-v">${wr != null ? fmtPct(wr, { signed: false }) : '—'}</span></div>
    </div>
    <div class="crv-note">昨日建议=收盘按规则给的处理倾向；今日涨跌=昨日收盘价→今日真实价；兑现=方向对没对（看多盼涨/看空盼跌，±0.5% 内记中性不计分）。<b>历史建议固定、不事后改写。</b></div>
    <div class="tbl-wrap"><table class="tbl">
      <thead><tr><th class="l">持仓</th><th>昨日收盘</th><th>今日价</th><th>今日涨跌</th><th class="l">昨日建议</th><th>兑现</th></tr></thead>
      <tbody>
        ${rows.map((r) => `<tr class="adv-row" data-code="${escapeHtml(r.code || '')}" style="cursor:pointer">
          <td class="l"><b>${escapeHtml(r.name || r.code || '—')}</b> <span style="color:var(--text-3);font-size:12px">${escapeHtml(r.code || '')}</span></td>
          <td class="num">${r.base_price != null ? fmtPrice(r.base_price) : '—'}</td>
          <td class="num">${r.today_price != null ? fmtPrice(r.today_price) : '<span style="color:var(--text-3)">今日价未取得 · 暂不回验</span>'}</td>
          <td class="num ${pctClass(r.change_pct)}">${r.change_pct != null ? fmtPct(r.change_pct) : '—'}</td>
          <td class="l" style="font-size:13px">${escapeHtml(r.tendency || '')}</td>
          <td><span class="crv-badge ${vCls(r.verdict)}">${vTxt(r.verdict)}</span></td>
        </tr>`).join('')}
      </tbody>
    </table></div>`;
  box.querySelectorAll('.adv-row').forEach((row) => row.addEventListener('click', () => gotoDiag(row.dataset.code)));
}

/* ====================== bottomRender：推荐留痕回验面板 ====================== */
/**
 * 在报告下方挂面板。ctx.isAlive() 防卸载后写脏 DOM；ctx.reload 是整页报告重载（这里不用，
 * 因为面板自身的刷新只重拉本面板，绝不触发整页报告重生成——回验是对账，不是重写判断）。
 */
function bottomRender(host, ctx) {
  if (!host) return;
  injectStyle();
  host.innerHTML = `
    <section class="card crv-card">
      <div class="card-body">
        <div class="crv-head">
          <span class="ttl">${icon('archive')} 推荐留痕回验</span>
          <span class="sub">历史判断固定 · 不事后改写 · 用真实价对答案</span>
          <span class="crv-actions">
            <button class="btn btn-sm" id="crv-refresh">${icon('refresh')}刷新</button>
            <button class="btn btn-sm btn-primary" id="crv-verify">${icon('check')}一键回验推荐</button>
          </span>
        </div>
        <div id="crv-stats">${skeleton(1, [40])}</div>
        <div id="crv-list">${skeleton(4)}</div>
      </div>
    </section>`;

  const alive = () => (ctx?.isAlive ? ctx.isAlive() : true);

  // 一键回验：POST /verify → 成功 toast → 只重拉本面板（统计+列表），不动整页报告
  host.querySelector('#crv-verify')?.addEventListener('click', async () => {
    const btn = host.querySelector('#crv-verify');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="btn-spin"></span>回验中'; }
    try {
      const r = await api.post('/recommendations/verify', {});
      if (r && r.ok) {
        toast(`已回验 ${r.verified != null ? r.verified : 0} 条，锁定 ${r.closed != null ? r.closed : 0} 条`, 'success');
      } else {
        toast('回验失败：' + (r?.error || '未知错误'), 'error');
      }
    } catch (e) {
      toast('回验失败：' + e.message, 'error');
    } finally {
      if (!alive()) return;
      const b = host.querySelector('#crv-verify');
      if (b) { b.disabled = false; b.innerHTML = `${icon('check')}一键回验推荐`; }
      // 只刷新本面板（统计 + 列表），绝不触发整页报告重生成
      loadStats(host, alive);
      loadRecs(host, alive);
    }
  });

  host.querySelector('#crv-refresh')?.addEventListener('click', () => {
    loadStats(host, alive);
    loadRecs(host, alive);
  });

  loadStats(host, alive);
  loadRecs(host, alive);
}

/* ---------- 成功率摘要：GET /api/recommendations/stats ---------- */
/* 铁律：只接受 percent 单位；样本未达门槛不显稳定胜率；
   waiting/untriggered/incomparable/excluded/missing 分开展示。 */
async function loadStats(host, alive) {
  const box = host.querySelector('#crv-stats');
  if (!box) return;
  let d = null;
  try { d = await api.get('/recommendations/stats'); } catch { /* 保留兜底，下行按缺失渲染 */ }
  if (!alive() || !host.querySelector('#crv-stats')) return;

  if (!d || d.ok === false) {
    box.innerHTML = `<div class="crv-note">成功率统计暂不可用，<b>不展示任何推断数字</b>（铁律：取不到即留空）。</div>`;
    return;
  }

  // v2 口径只接受 percent，不再根据数字大小猜测单位。
  const winPct = statsPercent(d, 'win_rate');
  const observedPct = statsPercent(d, 'observed_win_rate');
  const enough = d.enough_sample === true;
  const unitMismatch = d.win_rate_unit !== 'percent';
  const pf = d.profit_factor;

  const chip = (k, vHtml) => `<div class="crv-chip"><span class="ck-k">${escapeHtml(k)}</span><span class="ck-v">${vHtml}</span></div>`;
  box.innerHTML = `
    <div class="crv-stats">
      ${chip('可评分窗口', d.sample != null ? `<span>${fmtNum(d.sample)}</span><span style="font-size:11px;color:var(--text-3)"> / ${fmtNum(d.planned_windows ?? 0)}</span>` : '—')}
      ${chip('稳定胜率', winPct != null ? `<span>${fmtPct(winPct, { signed: false })}</span>` : '—')}
      ${chip('原始观察比例', observedPct != null ? `<span>${fmtPct(observedPct, { signed: false })}</span>` : '—')}
      <div class="crv-chip"><span class="ck-k">平均方向正收益</span><span class="ck-v ${pctClass(d.avg_win)}">${d.avg_win != null ? fmtPct(d.avg_win) : '—'}</span></div>
      <div class="crv-chip"><span class="ck-k">平均方向负收益</span><span class="ck-v ${pctClass(d.avg_loss)}">${d.avg_loss != null ? fmtPct(d.avg_loss) : '—'}</span></div>
      ${chip('盈亏比', pf != null ? `<span>${fmtNum(pf, 2)}</span>` : '—')}
    </div>
    <div class="crv-breakdown"><span>待到期 ${fmtNum(d.waiting ?? 0)}</span><span>未触发 ${fmtNum(d.untriggered ?? 0)}</span>
      <span>不可比 ${fmtNum(d.incomparable ?? 0)}</span><span>已排除推荐 ${fmtNum(d.excluded ?? 0)}</span></div>
    ${renderClosePerWindow(d)}
    ${enough ? '' : `<div class="crv-note"><span class="crv-warn">样本不足</span> &nbsp;可评分窗口 ${fmtNum(d.sample ?? 0)} 个，稳定胜率门槛 ${escapeHtml(String(d.minimum_stable_sample ?? '—'))}；只展示原始观察比例供审计。</div>`}
    ${unitMismatch ? '<div class="crv-note"><span class="crv-warn">单位异常</span> &nbsp;win_rate_unit 不是 percent，前端已拒绝猜测换算。</div>' : ''}
    ${(d.missing || []).length ? `<div class="crv-note"><b>统计缺口：</b>${d.missing.map(escapeHtml).join('；')}</div>` : ''}`;
}

function renderClosePerWindow(stats) {
  const rows = stats.per_window && typeof stats.per_window === 'object' ? stats.per_window : {};
  return `<div class="crv-per-window">${['T0', 'T1', 'T2', 'T5'].map((window) => {
    const row = rows[window] || {};
    const observed = statsPercent({ ...row, win_rate_unit: stats.win_rate_unit }, 'observed_win_rate');
    const stable = statsPercent({ ...row, win_rate_unit: stats.win_rate_unit }, 'win_rate');
    return `<div class="crv-pw"><b>${window} · ${fmtNum(row.sample ?? 0)} 样本</b><span>原始观察 ${observed == null ? '—' : fmtPct(observed, { signed: false })}</span>
      <span>稳定胜率 ${stable == null ? '—' : fmtPct(stable, { signed: false })}</span></div>`;
  }).join('')}</div>`;
}

/* ---------- 推荐留痕列表：GET /api/recommendations ---------- */
/* 列出最近若干条：逐窗口状态 + 标的涨跌 + 方向结果 + MFE/MAE。
   点股票跳诊断；空时引导去 #/recommend 记录。铁律：回验字段只取端点返回，不在前端补算。 */
async function loadRecs(host, alive) {
  const box = host.querySelector('#crv-list');
  if (!box) return;
  let d = null;
  try { d = await api.get('/recommendations'); } catch { /* 兜底 */ }
  if (!alive() || !host.querySelector('#crv-list')) return;

  const recs = (d?.recommendations || []).slice(0, 12);
  if (!recs.length) {
    box.innerHTML = `<div class="crv-soon">
      <div class="icn">${icon('archive')}</div>
      <div class="st">还没有推荐留痕</div>
      <div class="sd">每条推荐落库即固定，次日起用真实价回验是否兑现，<b>历史判断不事后改写</b>，长期沉淀成功率。</div>
      <span class="btn-link" id="crv-go-rec">去记录 / 查看推荐 →</span>
    </div>`;
    box.querySelector('#crv-go-rec')?.addEventListener('click', () => { location.hash = '#/recommend'; });
    return;
  }

  box.innerHTML = `
    <div class="crv-note" style="margin-top:12px">最近 ${recs.length} 条推荐 · 逐窗口展示 T0/T1/T2/T5；<b>标的涨跌</b>与<b>方向结果</b>分开，均只读真实回验证据。</div>
    ${recs.map((r) => {
      const rv = latestReview(r);
      const state = reviewStateMeta(rv);
      const fr = rv?.final_return;
      const dr = rv?.directional_return;
      const coverage = reviewCoverage(rv);
      return `<div class="crv-rec" data-code="${escapeHtml(r.code || '')}">
        <div class="crv-rec-main"><span class="r-name">${escapeHtml(r.name || r.code || '—')}</span>
          <span class="r-code">${escapeHtml(r.code || '')}</span>
          ${r.source ? `<span class="r-src">${escapeHtml(r.source)}</span>` : ''}
          <span class="r-px">荐价 ${r.rec_price != null ? fmtPrice(r.rec_price) : '—'}</span>
          <span class="crv-badge ${state.tone}" title="${escapeHtml(state.detail)}">${escapeHtml(state.label)}</span></div>
        ${renderCloseTimeline(r)}
        <div class="crv-review-metrics"><span>标的涨跌 <b class="${pctClass(fr)}">${fr != null ? fmtPct(fr) : '—'}</b></span>
          <span>方向结果 <b class="${pctClass(dr)}">${dr != null ? fmtPct(dr) : '—'}</b></span>
          <span>MFE <b>${rv?.max_return != null ? fmtPct(rv.max_return) : '—'}</b></span><span>MAE <b>${rv?.max_drawdown != null ? fmtPct(rv.max_drawdown) : '—'}</b></span>
          <span>路径 ${coverage.observed ?? '—'}/${coverage.expected ?? '—'}</span><span>目标日 ${escapeHtml(rv?.target_trade_day || '—')}</span></div>
      </div>`;
    }).join('')}`;
  box.querySelectorAll('.crv-rec').forEach((row) => row.addEventListener('click', () => gotoDiag(row.dataset.code)));
}

function renderCloseTimeline(rec) {
  return `<div class="crv-timeline">${reviewWindowRows(rec).map(({ window, review, meta }) => `<div class="crv-window ${meta.tone}" title="${escapeHtml(meta.detail)}">
    <b>${window}${review?.target_trade_day ? ` · ${escapeHtml(review.target_trade_day)}` : ''}</b><span>${escapeHtml(meta.label)}</span></div>`).join('')}</div>`;
}
