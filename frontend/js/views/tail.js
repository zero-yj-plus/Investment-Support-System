/* views/tail.js — 交易节奏工作站「尾盘策略」(母文档 §24，14:45/14:50)
   ------------------------------------------------------------------
   §24 尾盘的第一判断是「今天能不能做」——要敢于说「今日无高质量尾盘机会，建议休息」。
   本页 = 报告正文(AI 按真实数据流式生成) + 该时点的真实结构化面板：
     · topRender：报告上方「今天能不能做尾盘」风险闸横幅（取 /api/cockpit 的 command.risk_level）
     · bottomRender：报告下方两块真实面板
         ① 推荐验证战绩（/api/recommendations/stats + /api/recommendations）——尾盘要敢做才做、做了要留痕回验
         ② 观察池就绪机会（/api/watchlist，A→E）——尾盘从观察池里挑「到价的好机会」

   铁律（不可破）：
   - 报告正文里的「买入区间/买入前提/不买条件/止损/止盈/仓位/T+1·T+2验证」全部由后端 AI 引擎
     按注入的真实数据生成，本文件不碰生成逻辑、绝不在前端拼/编任何机会数字。
   - 面板里的风险等级/文案/战绩/评级一律来自真实端点；取不到就标 missing / 显示「—」，绝不编造。
   - innerHTML 注入全部经 escapeHtml；面板 fetch 用 ctx.isAlive() 守卫，防卸载后写脏 DOM。 */

import { makeReportView } from './_report_page.js';
import { api, fmtPrice, fmtPct, fmtNum, pctClass } from '../api.js';
import { icon, escapeHtml, emptyState, skeleton, toast } from '../ui.js';
import {
  latestReview,
  reviewCoverage,
  reviewStateMeta,
  reviewWindowRows,
  statsPercent,
} from './_recommend_review.js';

// 风险等级配色（危险度斜坡，独立于 A股涨跌红绿，避免歧义）：1蓝(正常)→5红(极端)。与 cockpit.js 一致。
// 同一风险等级在今日决策 / 盘前 / 尾盘使用同一套 AA 深色；白字徽章与浅色面板文字均可读。
const LV_COLOR = { 1: '#1d4ed8', 2: '#0f766e', 3: '#a16207', 4: '#c2410c', 5: '#be123c' };
// A—E 是白字实心徽章，也必须使用对白色达到 4.5:1 的底色。
const GRADE_COLOR = { A: '#0f766e', B: '#1d4ed8', C: '#475569', D: '#a16207', E: '#be123c' };

const view = makeReportView({
  rtype: 'tail',
  title: '尾盘策略',
  sub: '14:45/14:50 · 先判断今天能不能做尾盘',
  notBefore: '14:45',
  topRender,
  bottomRender,
});

export function mount(el) { injectStyle(); view.mount(el); }
export function unmount() { view.unmount(); }

/* 跳诊断（点观察池/推荐里的票→逐票深度诊断给价格区间/买卖动作） */
function gotoDiag(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.diagnosis.code', code);
  location.hash = '#/diagnosis';
}

/* ============================================================
   topRender —— 报告上方「今天能不能做尾盘」风险闸横幅
   §24：第一判断是能不能做。等级/文案/原则/允许·禁止全来自 /api/cockpit 真实返回，不自编。
   ============================================================ */
function topRender(host, ctx) {
  if (!host) return;
  // 时点锁定（T9 §0-1）：横幅只读【报告生成当刻】的 cockpit 快照(尾盘定格 14:45)，绝不读实时——
  // 杜绝"上午一个样下午一个样"。快照缺失(报告未生成)→ 占位提示，绝不糊弄一个实时等级。
  const snap = ctx.report && ctx.report.cockpit;
  const c = (snap && snap.command) || null;
  if (!c || c.risk_level == null) {
    const generated = !!(ctx.report && ctx.report.content_md);
    const title = generated ? '本期报告无定格风险闸' : '尾盘策略生成后显示今日风险闸';
    const text = generated
      ? '本期报告生成于「风险闸时点锁定」上线前，无定格风险闸。下一期起，此处按生成时点（14:45）定格今日「能不能做尾盘」的判断，当日锁死不随盘变。'
      : '「尾盘策略」每交易日 14:45 按当刻信息生成并定格今日「能不能做尾盘」的判断（当日锁死、不随盘变）。生成后此处显示。';
    host.innerHTML = `<div class="tail-gate gate-unknown">
      <div class="tg-head"><span class="tg-icn">${icon('info')}</span>
        <span class="tg-title">${title}</span></div>
      <div class="tg-text">${text}</div>
      <div class="tg-foot">具体尾盘机会见下方报告（含买入区间 / 止损 / 止盈 / 仓位 / T+1 验证）。</div>
    </div>`;
    return;
  }

  const lv = Number(c.risk_level);
  const color = LV_COLOR[lv] || 'var(--flat)';
  const levelName = c.level_name || '';
  const principle = c.first_principle || '';
  const fmtList = (arr) => (Array.isArray(arr) ? arr : []).filter(Boolean);
  const forbid = fmtList(c.forbid);
  const allow = fmtList(c.allow);
  const blackswan = fmtList(c.blackswan);

  let mode, headline, badge, lead, listBlock = '', extra = '';

  if (lv >= 4) {
    // §24「敢说不做」：高风险醒目红横幅，不建议做尾盘、以防守为主，列出第一原则 + 禁止项。
    mode = 'danger';
    badge = '今日不做';
    headline = `今日风险 ${lv} 级 · ${levelName || '高风险'}，不建议做尾盘，以防守为主`;
    lead = principle ? `第一原则：${principle}` : '今日先保住本金，尾盘以观察/防守为主。';
    if (blackswan.length) extra = `<div class="tg-bs">⚠ 黑天鹅信号：${blackswan.map(escapeHtml).join('；')}</div>`;
    if (forbid.length) {
      listBlock = `<div class="tg-rule forbid"><span class="rh">✕ 今日禁止</span><span class="rule-chips">${
        forbid.map((x) => `<span class="rc">${escapeHtml(x)}</span>`).join('')}</span></div>`;
    }
  } else if (lv === 3) {
    // 琥珀：防守观察，尾盘仅看不抢，到价格条件再说。
    mode = 'warn';
    badge = '仅看不抢';
    headline = `今日风险 ${lv} 级 · ${levelName || '防守观察'}，尾盘仅看不抢，到价格条件再说`;
    lead = principle ? `第一原则：${principle}` : '尾盘不主动出击，仅跟踪观察池到价机会，不到价不动手。';
    if (forbid.length) {
      listBlock = `<div class="tg-rule forbid"><span class="rh">✕ 今日禁止</span><span class="rule-chips">${
        forbid.map((x) => `<span class="rc">${escapeHtml(x)}</span>`).join('')}</span></div>`;
    }
  } else {
    // lv <= 2：绿/中性，今日可做尾盘但严守纪律，列允许项 + 仓位上限。
    mode = 'ok';
    badge = '可做尾盘';
    headline = `今日风险 ${lv} 级 · ${levelName || '正常'}，今日可做尾盘，但严守纪律`;
    lead = principle ? `第一原则：${principle}` : '可按计划做尾盘，仍以严守纪律、控制仓位为前提。';
    if (allow.length) {
      listBlock = `<div class="tg-rule allow"><span class="rh">✓ 今日允许</span><span class="rule-chips">${
        allow.map((x) => `<span class="rc">${escapeHtml(x)}</span>`).join('')}</span></div>`;
    }
  }

  const cap = c.position_cap;  // 仓位上限：真实端点字段，缺则「—」
  host.innerHTML = `
    <div class="tail-gate gate-${mode}" style="--lv-c:${color}">
      <div class="tg-row1">
        <span class="tg-badge">${escapeHtml(badge)}</span>
        <span class="tg-headline">${escapeHtml(headline)}</span>
        ${cap ? `<span class="tg-cap" title="仓位上限">仓位上限 <b>${escapeHtml(cap)}</b></span>` : ''}
      </div>
      <div class="tg-text">${escapeHtml(lead)}</div>
      ${extra}
      ${listBlock}
      <div class="tg-foot">⏱ 本风险闸定格于尾盘报告生成时点（14:45）、当日锁死不随盘变。具体尾盘机会见下方报告（含买入区间 / 买入前提 / 不买条件 / 止损 / 止盈 / 仓位 / T+1·T+2 验证）。</div>
    </div>`;
}

/* ============================================================
   bottomRender —— 报告下方两块真实结构化面板
   ① 推荐验证战绩（敢做才做、做了留痕回验）
   ② 观察池就绪机会（尾盘从观察池挑到价的好机会）
   ============================================================ */
function bottomRender(host, ctx) {
  if (!host) return;
  host.innerHTML = `
    <div class="tail-grid">
      <section class="card tail-card" id="tail-rec">
        <div class="card-head">
          <span class="card-title">尾盘前·推荐战绩复盘 · TRACK RECORD</span>
          <span class="card-extra"><button class="btn btn-sm btn-ghost" id="tail-verify">${icon('refresh')}回验</button></span>
        </div>
        <div class="card-body" id="tail-rec-body">${skeleton(4)}</div>
      </section>
      <section class="card tail-card" id="tail-watch">
        <div class="card-head">
          <span class="card-title">尾盘观察池·就绪机会 · WATCHLIST</span>
        </div>
        <div class="card-body" id="tail-watch-body">${skeleton(5)}</div>
      </section>
    </div>`;

  loadRecPanel(host, ctx);
  loadWatchPanel(host, ctx);

  // 「回验」按钮：调真实 verify 端点，用真实价回验昨日推荐是否兑现（不事后改写历史），完成后刷新本面板。
  host.querySelector('#tail-verify')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.innerHTML = '<span class="btn-spin"></span>回验中';
    try {
      const r = await api.post('/recommendations/verify', {});
      if (r && r.ok) toast(`已回验：核验 ${r.verified ?? 0} 条，结算 ${r.closed ?? 0} 条`, 'success');
      else toast('回验失败：' + (r?.error || '未知错误'), 'error');
    } catch (err) {
      toast('回验失败：' + err.message, 'error');
    } finally {
      if (ctx.isAlive() && host.isConnected) {
        btn.disabled = false; btn.innerHTML = `${icon('refresh')}回验`;
        loadRecPanel(host, ctx);
      }
    }
  });
}

/* ===== ① 推荐验证战绩：/api/recommendations/stats（汇总）+ /api/recommendations（明细） ===== */
async function loadRecPanel(host, ctx) {
  const body = host.querySelector('#tail-rec-body');
  if (!body) return;
  let stats = null, list = null;
  try {
    [stats, list] = await Promise.all([
      api.get('/recommendations/stats').catch(() => null),
      api.get('/recommendations').catch(() => null),
    ]);
  } catch { /* 双取兜底 */ }
  if (!ctx.isAlive() || !body.isConnected) return;

  const recs = (list && list.recommendations) || [];
  // 空状态：还没有任何推荐留痕 → 诚实空态，绝不堆假战绩（铁律）。
  if (!stats && !recs.length) {
    body.innerHTML = emptyState(
      '还没有推荐留痕',
      '尾盘要敢做才做。每条尾盘机会落库即固定，次日用真实价回验是否兑现，长期沉淀真实成功率——历史判断不事后改写。',
      '',
      'archive',
    );
    return;
  }

  // 战绩汇总（数字全来自 stats 真实端点，缺失显示「—」）
  let statsBlock = '';
  if (stats) {
    const enough = stats.enough_sample === true;
    const stableWinRate = statsPercent(stats, 'win_rate');
    const observedWinRate = statsPercent(stats, 'observed_win_rate');
    const winPct = stableWinRate != null ? fmtPct(stableWinRate, { signed: false }) : '—';
    const observedPct = observedWinRate != null ? fmtPct(observedWinRate, { signed: false }) : '—';
    const stat = (k, v, cls = '') => `<div class="ts-cell"><span class="ts-k">${k}</span><span class="ts-v ${cls}">${v}</span></div>`;
    statsBlock = `
      ${!enough ? `<div class="ts-note">可评分窗口 ${fmtNum(stats.sample || 0)} 个，未达稳定胜率门槛 ${escapeHtml(String(stats.minimum_stable_sample ?? '—'))}；只展示原始观察比例供审计。</div>` : ''}
      <div class="tail-stats">
        ${stat('稳定胜率', winPct)}
        ${stat('原始观察比例', observedPct)}
        ${stat('可评分窗口', stats.sample != null ? `<span class="num">${fmtNum(stats.sample)}</span>` : '—')}
        ${stat('盈亏比', stats.profit_factor != null ? `<span class="num">${fmtNum(stats.profit_factor, 2)}</span>` : '—')}
        ${stat('平均方向正收益', stats.avg_win != null ? fmtPct(stats.avg_win) : '—', pctClass(stats.avg_win))}
        ${stat('平均方向负收益', stats.avg_loss != null ? fmtPct(stats.avg_loss) : '—', pctClass(stats.avg_loss))}
      </div>
      <div class="tail-breakdown"><span>待到期 ${fmtNum(stats.waiting ?? 0)}</span><span>未触发 ${fmtNum(stats.untriggered ?? 0)}</span>
        <span>不可比 ${fmtNum(stats.incomparable ?? 0)}</span><span>已排除推荐 ${fmtNum(stats.excluded ?? 0)}</span></div>
      ${renderTailPerWindow(stats)}
      ${stats.win_rate_unit !== 'percent' ? '<div class="ts-note">win_rate_unit 不是 percent，前端拒绝猜测换算，已留空。</div>' : ''}
      ${(stats.missing || []).length ? `<div class="ts-note">统计缺口：${stats.missing.map(escapeHtml).join('；')}</div>` : ''}`;
  }

  const listBlock = recs.length
    ? recs.slice(0, 6).map((r) => {
        const rv = latestReview(r);
        const state = reviewStateMeta(rv);
        const fr = rv?.final_return;
        const dr = rv?.directional_return;
        const coverage = reviewCoverage(rv);
        return `<div class="tail-rec-row" data-code="${escapeHtml(r.code || '')}">
          <div class="tail-rec-main"><span class="tr-name">${escapeHtml(r.name || r.code || '—')}</span>
            <span class="tr-code num">${escapeHtml(r.code || '')}</span><span class="tr-src">${escapeHtml(r.source || r.rec_type || '')}</span>
            <span class="tr-px">荐价 <b class="num">${r.rec_price != null ? fmtPrice(r.rec_price) : '—'}</b></span>
            <span class="review-tag ${state.tone}" title="${escapeHtml(state.detail)}">${escapeHtml(state.label)}</span></div>
          ${renderTailTimeline(r)}
          <div class="tail-review-metrics"><span>标的涨跌 <b class="${pctClass(fr)}">${fr != null ? fmtPct(fr) : '—'}</b></span>
            <span>方向结果 <b class="${pctClass(dr)}">${dr != null ? fmtPct(dr) : '—'}</b></span><span>MFE <b>${rv?.max_return != null ? fmtPct(rv.max_return) : '—'}</b></span>
            <span>MAE <b>${rv?.max_drawdown != null ? fmtPct(rv.max_drawdown) : '—'}</b></span><span>路径 ${coverage.observed ?? '—'}/${coverage.expected ?? '—'}</span></div>
        </div>`;
      }).join('')
    : '<div class="ts-note">暂无推荐明细。</div>';

  body.innerHTML = statsBlock + `<div class="tail-rows">${listBlock}</div>`;
  body.querySelectorAll('.tail-rec-row').forEach((r) => r.addEventListener('click', () => gotoDiag(r.dataset.code)));
}

function renderTailPerWindow(stats) {
  const rows = stats.per_window && typeof stats.per_window === 'object' ? stats.per_window : {};
  return `<div class="tail-per-window">${['T0', 'T1', 'T2', 'T5'].map((window) => {
    const row = rows[window] || {};
    const observed = statsPercent({ ...row, win_rate_unit: stats.win_rate_unit }, 'observed_win_rate');
    const stable = statsPercent({ ...row, win_rate_unit: stats.win_rate_unit }, 'win_rate');
    return `<div><b>${window} · ${fmtNum(row.sample ?? 0)} 样本</b><span>原始 ${observed == null ? '—' : fmtPct(observed, { signed: false })}</span>
      <span>稳定 ${stable == null ? '—' : fmtPct(stable, { signed: false })}</span></div>`;
  }).join('')}</div>`;
}

function renderTailTimeline(rec) {
  return `<div class="tail-review-timeline">${reviewWindowRows(rec).map(({ window, review, meta }) => `<div class="tail-review-window ${meta.tone}" title="${escapeHtml(meta.detail)}">
    <b>${window}${review?.target_trade_day ? ` · ${escapeHtml(review.target_trade_day)}` : ''}</b><span>${escapeHtml(meta.label)}</span></div>`).join('')}</div>`;
}

/* ===== ② 观察池就绪机会：/api/watchlist（按 A→E 排序，到价的好机会优先） ===== */
async function loadWatchPanel(host, ctx) {
  const body = host.querySelector('#tail-watch-body');
  if (!body) return;
  let d = null;
  try { d = await api.get('/watchlist'); } catch { /* 兜底 */ }
  if (!ctx.isAlive() || !body.isConnected) return;

  const items = ((d && d.items) || []).slice();
  if (!items.length) {
    body.innerHTML = emptyState(
      '观察池还没有标的',
      '把想做尾盘还没买的票加进观察池，系统按「理想买入区间」每日排 A—E。尾盘只在到价的 A/B 机会里挑，不到价不抢。',
      '',
      'search',
    );
    return;
  }
  const order = { A: 0, B: 1, C: 2, D: 3, E: 4 };
  items.sort((a, b) => (order[a.grade] ?? 9) - (order[b.grade] ?? 9));

  body.innerHTML = `<div class="tail-hint">按接近买点排序(A→E)·点任意一行→逐票诊断给出价格区间/买卖动作</div>` +
    items.slice(0, 8).map((it) => {
      const q = it.quote || {};
      const gc = GRADE_COLOR[it.grade] || 'var(--text-3)';
      // 理想买入区间（target_low–target_high）来自真实端点；缺则「—」，绝不编区间。
      const zone = (it.target_low != null && it.target_high != null)
        ? `${fmtNum(it.target_low, 2)}–${fmtNum(it.target_high, 2)}` : '—';
      const px = q.price != null ? fmtPrice(q.price) : '<span class="muted">现价缺</span>';
      const pct = q.change_pct != null ? `<span class="${pctClass(q.change_pct)}" style="font-size:12px">${fmtPct(q.change_pct)}</span>` : '';
      return `<div class="tail-row" data-code="${escapeHtml(it.code || '')}">
        <span class="grade-badge" style="background:${gc}">${escapeHtml(it.grade || '—')}</span>
        <span class="tr-name">${escapeHtml(it.name || it.code || '—')}</span>
        <span class="tr-px">${px} ${pct}</span>
        <span class="tr-zone" title="理想买入区间">区间 <b class="num">${zone}</b></span>
        <span class="tr-status">${escapeHtml(it.status || '')}</span>
      </div>`;
    }).join('');
  body.querySelectorAll('.tail-row').forEach((r) => r.addEventListener('click', () => gotoDiag(r.dataset.code)));
}

/* ============================================================
   样式（幂等注入，跟随主题 CSS 变量；红涨绿跌沿用全局 --up/--down）
   ============================================================ */
function injectStyle() {
  if (document.getElementById('tail-style')) return;
  const css = `
  /* —— 风险闸横幅 —— */
  .tail-gate{position:relative;border:1px solid var(--border);border-radius:var(--radius);
    padding:15px 18px 13px;margin-bottom:var(--gap);background:var(--panel);overflow:hidden;box-shadow:var(--shadow-soft)}
  .tail-gate::before{content:"";position:absolute;inset:0 auto 0 0;width:5px;background:var(--lv-c,var(--flat))}
  .tail-gate.skel-wrap{padding:15px 18px}
  .tail-gate .tg-row1{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .tail-gate .tg-badge{flex:none;font-size:13px;font-weight:800;color:#fff;background:var(--lv-c,var(--flat));
    border-radius:8px;padding:5px 12px;letter-spacing:.5px;
    box-shadow:0 2px 10px color-mix(in srgb,var(--lv-c,var(--flat)) 42%,transparent)}
  .tail-gate .tg-headline{font-size:16px;font-weight:800;color:var(--text);line-height:1.4}
  .tail-gate .tg-cap{margin-left:auto;font-size:12px;color:var(--text-3);white-space:nowrap}
  .tail-gate .tg-cap b{font-family:var(--mono,monospace);color:var(--text-2);font-size:13px}
  .tail-gate .tg-text{margin-top:10px;font-size:14px;line-height:1.6;color:var(--text);font-weight:600}
  .tail-gate .tg-foot{margin-top:11px;font-size:12px;color:var(--text-3);line-height:1.5;
    border-top:1px dashed var(--border);padding-top:9px}
  .tail-gate .tg-bs{margin-top:10px;font-size:13px;color:var(--down);background:var(--down-bg);
    border:1px solid var(--down-line,var(--border));border-radius:var(--radius-sm);padding:7px 12px;line-height:1.5}
  /* 禁止/允许改横排 chips（慎微 07-10：四条竖的改成一个横的） */
  .tail-gate .tg-rule{margin-top:11px;border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 12px;background:var(--panel-2);display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .tail-gate .tg-rule .rh{font-size:12px;font-weight:700;letter-spacing:.5px;font-family:var(--mono,monospace);flex:none}
  .tail-gate .tg-rule.allow .rh{color:var(--up)} .tail-gate .tg-rule.forbid .rh{color:var(--down)}
  .tail-gate .rule-chips{display:inline-flex;gap:8px;flex-wrap:wrap}
  .tail-gate .rc{font-size:12.5px;color:var(--text-2);background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:2px 11px;line-height:1.6;white-space:nowrap}
  /* 三种状态底色微染（仍以左侧色条为主信号） */
  .tail-gate.gate-danger{background:linear-gradient(100deg,color-mix(in srgb,var(--lv-c) 16%,var(--panel)),var(--panel) 60%)}
  .tail-gate.gate-warn{background:linear-gradient(100deg,color-mix(in srgb,var(--lv-c) 12%,var(--panel)),var(--panel) 62%)}
  .tail-gate.gate-ok{background:linear-gradient(100deg,color-mix(in srgb,var(--lv-c) 10%,var(--panel)),var(--panel) 64%)}
  .tail-gate.gate-unknown .tg-head{display:flex;align-items:center;gap:8px}
  .tail-gate.gate-unknown .tg-icn{color:var(--text-3);display:inline-flex}
  .tail-gate.gate-unknown .tg-icn svg{width:18px;height:18px}
  .tail-gate.gate-unknown .tg-title{font-size:15px;font-weight:800;color:var(--text)}

  /* —— 下方两块面板 —— */
  .tail-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:var(--gap);min-width:0}
  .tail-grid>*{min-width:0}
  .tail-card{display:flex;flex-direction:column;min-width:0}
  .tail-card .card-body{flex:1}
  .tail-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}
  .tail-stats .ts-cell{display:flex;flex-direction:column;gap:2px;background:var(--panel-2);
    border:1px solid var(--border);border-radius:var(--radius-sm);padding:7px 10px}
  .tail-stats .ts-k{font-size:12px;color:var(--text-3);font-family:var(--mono,monospace)}
  .tail-stats .ts-v{font-size:16px;font-weight:800;color:var(--text);font-family:var(--mono,monospace)}
  .ts-note{font-size:12px;color:var(--amber);background:color-mix(in srgb,var(--amber) 12%,transparent);
    border:1px solid color-mix(in srgb,var(--amber) 40%,transparent);border-radius:var(--radius-sm);
    padding:6px 10px;margin-bottom:9px;line-height:1.45}
  .tail-breakdown{display:flex;gap:6px;flex-wrap:wrap;margin:-2px 0 8px}
  .tail-breakdown span{font-size:12px;color:var(--text-2);border:1px solid var(--border);border-radius:10px;padding:3px 7px;background:var(--panel-2)}
  .tail-per-window{display:grid;grid-template-columns:repeat(4,minmax(82px,1fr));gap:6px;margin-bottom:9px}
  .tail-per-window>div{border:1px solid var(--border);border-radius:var(--radius-sm);padding:5px 7px;background:var(--panel-2);
    display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--text-3);min-width:0}
  .tail-per-window b{font-size:13px;color:var(--text)}
  .tail-hint{font-size:12px;color:var(--text-3);margin-bottom:6px}
  .tail-rows{display:flex;flex-direction:column}
  .tail-row{display:flex;align-items:center;gap:10px;padding:8px 4px;border-bottom:1px solid var(--border);
    cursor:pointer;transition:background .12s}
  .tail-row:last-child{border-bottom:none} .tail-row:hover{background:var(--panel-2)}
  .tail-row .tr-name{font-weight:700;color:var(--text);min-width:70px}
  .tail-row .tr-code{color:var(--text-3);font-size:12px}
  .tail-row .tr-src{color:var(--text-3);font-size:12px}
  .tail-row .tr-px{margin-left:auto;font-size:13px;color:var(--text-2);white-space:nowrap}
  .tail-row .tr-px b{font-family:var(--mono,monospace);color:var(--text)}
  .tail-row .tr-zone{font-size:12px;color:var(--text-3);white-space:nowrap}
  .tail-row .tr-zone b{font-family:var(--mono,monospace);color:var(--text-2)}
  .tail-row .tr-ret{font-family:var(--mono,monospace);font-size:12px;min-width:54px;text-align:right}
  .tail-row .tr-status{font-size:12px;white-space:nowrap}
  .tail-row .muted{color:var(--text-3)}
  .hit-tag{font-weight:700}
  .tail-rec-row{display:block;padding:8px 4px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .12s}
  .tail-rec-row:last-child{border-bottom:none}.tail-rec-row:hover{background:var(--panel-2)}
  .tail-rec-main{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .tail-rec-main .tr-name{font-weight:700;color:var(--text);min-width:70px}.tail-rec-main .tr-code,.tail-rec-main .tr-src{font-size:12px;color:var(--text-3)}
  .tail-rec-main .tr-px{margin-left:auto;font-size:12px;color:var(--text-2)}
  .review-tag{font-size:12px;font-weight:750}.review-tag.positive{color:var(--up)}.review-tag.negative{color:var(--down)}
  .review-tag.warning{color:var(--amber)}.review-tag.waiting,.review-tag.neutral{color:var(--text-3)}
  .tail-review-timeline{display:grid;grid-template-columns:repeat(4,minmax(82px,1fr));gap:5px;margin-top:6px}
  .tail-review-window{display:flex;justify-content:space-between;gap:4px;border:1px solid var(--border);border-radius:6px;
    padding:5px 6px;background:var(--panel);font-size:12px;color:var(--text-3);min-width:0}
  .tail-review-window b{color:var(--text-2)}.tail-review-window.positive span{color:var(--up)}
  .tail-review-window.negative span{color:var(--down)}.tail-review-window.warning span{color:var(--amber)}
  .tail-review-metrics{display:flex;gap:8px;flex-wrap:wrap;margin-top:5px;font-size:12px;color:var(--text-3)}
  .tail-review-metrics b{font-family:var(--mono,monospace);color:var(--text)}
  .grade-badge{flex:none;width:20px;height:20px;border-radius:5px;display:inline-flex;align-items:center;
    justify-content:center;font-size:12px;font-weight:800;color:#fff}
  .up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--text-3)}

  @media (max-width:768px){
    .tail-grid{grid-template-columns:1fr}
    .tail-gate .tg-cap{margin-left:0;width:100%}
    .tail-stats{grid-template-columns:repeat(2,1fr)}
    .tail-per-window,.tail-review-timeline{grid-template-columns:repeat(2,1fr)}
    .tail-row{flex-wrap:wrap;gap:6px 10px}
    .tail-row .tr-px{margin-left:0}
  }`;
  const tag = document.createElement('style');
  tag.id = 'tail-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}
