/* views/morning.js — 交易节奏工作站「盘前计划」(母文档 §22，08:50)
   ------------------------------------------------------------------
   §22 盘前不是新闻汇总，而是**今日交易计划**——今天能不能做 / 风险等级 / 昨夜美股影响 /
   A股政策催化 / 重点方向 / 不做方向 / 持仓处理 / 观察池接近机会 / 仓位 / 什么情况必须休息。

   本页 = 报告正文(AI 按真实数据流式生成，rtype='morning'，本文件不碰生成)
        + 该时点的真实结构化面板(topRender)：照搬 tail.js「今天能不能做」风险闸范式：
          · risk_level>=4 → 红横幅「盘前即偏防守/避险」+ first_principle + forbid
          · ==3          → 琥珀「防守观察，盘前不急于布局」+ first_principle
          · <=2          → 绿/中性「今日可参与，按计划执行」+ allow + position_cap + today_action
          横幅下方提示「今日完整交易计划见下方报告」；可选一行「今日重点方向」(同 cockpit 方向卡逻辑)。

   铁律（不可破）：
   - 报告正文里的「能不能做 / 重点方向 / 不做方向 / 持仓处理 / 观察池 / 仓位 / 休息条件」全部由后端
     AI 引擎按注入的真实数据生成；本文件绝不在前端拼/编任何交易计划数字，不碰报告生成逻辑。
   - 面板里的风险等级/文案/原则/允许·禁止/仓位、以及重点方向的板块/资金流，一律来自真实端点
     (/api/cockpit、/api/market/sectors)；取不到就诚实降级(标"暂不可用"/"无主线")，绝不编一个等级或方向。
   - 财务年报、产业链上下游、美债/美元等若取不到，由报告 prompt 指示 AI 明确标「数据不全/缺失」(后端职责)。
   - innerHTML 注入全部经 escapeHtml；面板 fetch 用 ctx.isAlive() 守卫，防卸载后写脏 DOM。
   单位约定：价/成交额/市值=元，涨跌幅/换手=%。 */

import { makeReportView } from './_report_page.js';
import { api, fmtPct, fmtMoney, pctClass } from '../api.js';
import { icon, escapeHtml, skeleton } from '../ui.js';

// 风险等级配色（危险度斜坡，独立于 A股涨跌红绿，避免歧义）：1蓝(正常)→5红(极端)。与 cockpit.js / tail.js 一致。
// 同一风险等级在今日决策 / 盘前 / 尾盘使用同一套 AA 深色；白字徽章与浅色面板文字均可读。
const LV_COLOR = { 1: '#1d4ed8', 2: '#0f766e', 3: '#a16207', 4: '#c2410c', 5: '#be123c' };

const view = makeReportView({
  rtype: 'morning',
  title: '盘前报告',
  sub: '08:50 · 今天能不能做 + 重点方向',
  notBefore: '08:50',
  topRender,
});

export function mount(el) { injectStyle(); view.mount(el); }
export function unmount() { view.unmount(); }

/* ============================================================
   topRender —— 报告上方「今天能不能做」盘前风险闸横幅
   §22：盘前第一判断是能不能做。等级/文案/原则/允许·禁止/仓位全来自 /api/cockpit 真实返回，不自编。
   ============================================================ */
function topRender(host, ctx) {
  if (!host) return;
  // 时点锁定（T9 §0-1）：横幅只读【报告生成当刻】的 cockpit 快照(盘前定格 08:50)，绝不读实时——
  // 杜绝"上午一个样下午一个样"。快照缺失(报告未生成)→ 占位提示，绝不糊弄一个实时等级。
  const snap = ctx.report && ctx.report.cockpit;
  const c = (snap && snap.command) || null;

  if (!c || c.risk_level == null) {
    const generated = !!(ctx.report && ctx.report.content_md);
    const title = generated ? '本期报告无定格风险闸' : '盘前报告生成后显示今日风险闸';
    const text = generated
      ? '本期报告生成于「风险闸时点锁定」上线前，无定格风险闸。下一期起，此处按生成时点（08:50）定格今日风险等级与重点方向，当日锁死不随盘变。'
      : '「盘前报告」每交易日 08:50 按开盘前信息生成并定格今日风险等级与重点方向（当日锁死、不随盘变）。生成后此处显示。';
    host.innerHTML = `<div class="mp-gate gate-unknown">
      <div class="mg-head"><span class="mg-icn">${icon('info')}</span>
        <span class="mg-title">${title}</span></div>
      <div class="mg-text">${text}</div>
      <div class="mg-foot">今日完整交易计划见下方报告（含昨夜美股影响 / 政策催化 / 重点方向 / 不做方向 / 持仓处理 / 观察池 / 仓位 / 休息条件）。</div>
    </div>`;
    return;
  }

  const lv = Number(c.risk_level);
  const color = LV_COLOR[lv] || 'var(--flat)';
  const levelName = c.level_name || '';
  const principle = c.first_principle || '';
  const action = c.today_action || '';
  const fmtList = (arr) => (Array.isArray(arr) ? arr : []).filter(Boolean);
  const forbid = fmtList(c.forbid);
  const allow = fmtList(c.allow);
  const blackswan = fmtList(c.blackswan);

  let mode, badge, headline, lead, listBlock = '', extra = '';

  if (lv >= 4) {
    // §22「敢说休息」：高风险醒目红横幅，盘前即偏防守/避险，列出第一原则 + 禁止项。
    mode = 'danger';
    badge = '盘前防守';
    headline = `今日风险 ${lv} 级 · ${levelName || '高风险'}，盘前即偏防守 / 避险`;
    lead = principle ? `第一原则：${principle}` : '今日先保住本金，开盘以观察 / 防守为主，不急于布局。';
    if (blackswan.length) extra = `<div class="mg-bs">⚠ 黑天鹅信号：${blackswan.map(escapeHtml).join('；')}</div>`;
    if (forbid.length) {
      listBlock = `<div class="mg-rule forbid"><span class="rh">✕ 今日禁止</span><span class="rule-chips">${
        forbid.map((x) => `<span class="rc">${escapeHtml(x)}</span>`).join('')}</span></div>`;
    }
  } else if (lv === 3) {
    // 琥珀：防守观察，盘前不急于布局，到价格条件再说。
    mode = 'warn';
    badge = '防守观察';
    headline = `今日风险 ${lv} 级 · ${levelName || '防守观察'}，盘前不急于布局`;
    lead = principle ? `第一原则：${principle}` : '盘前不主动出击，跟踪观察池接近机会，不到价格条件不动手。';
    if (forbid.length) {
      listBlock = `<div class="mg-rule forbid"><span class="rh">✕ 今日禁止</span><span class="rule-chips">${
        forbid.map((x) => `<span class="rc">${escapeHtml(x)}</span>`).join('')}</span></div>`;
    }
  } else {
    // lv <= 2：绿/中性，今日可参与、按计划执行，列允许项 + 今日动作。
    mode = 'ok';
    badge = '可参与';
    headline = `今日风险 ${lv} 级 · ${levelName || '正常'}，今日可参与，按计划执行`;
    lead = principle ? `第一原则：${principle}` : '可按盘前计划参与，仍以严守纪律、控制仓位为前提。';
    if (action) extra = `<div class="mg-action"><span class="ah">▸ 今日动作</span>${escapeHtml(action)}</div>`;
    if (allow.length) {
      listBlock = `<div class="mg-rule allow"><span class="rh">✓ 今日允许</span><span class="rule-chips">${
        allow.map((x) => `<span class="rc">${escapeHtml(x)}</span>`).join('')}</span></div>`;
    }
  }

  const cap = c.position_cap;  // 仓位上限：真实端点字段，缺则不显示（绝不编一个仓位）
  host.innerHTML = `
    <div class="mp-gate gate-${mode}" style="--lv-c:${color}">
      <div class="mg-row1">
        <span class="mg-badge">${escapeHtml(badge)}</span>
        <span class="mg-headline">${escapeHtml(headline)}</span>
        ${cap ? `<span class="mg-cap" title="仓位上限">仓位上限 <b>${escapeHtml(cap)}</b></span>` : ''}
      </div>
      <div class="mg-text">${escapeHtml(lead)}</div>
      ${extra}
      ${listBlock}
      <div class="mg-dir" id="mp-dir"></div>
      <div class="mg-foot">⏱ 本风险闸定格于盘前报告生成时点（08:50）、当日锁死不随盘变。完整交易计划见下方报告（含昨夜美股影响 / 政策催化 / 重点方向 / 不做方向 / 持仓处理 / 观察池接近机会 / 仓位 / 什么情况必须休息）。</div>
    </div>`;

  // 「今日重点方向」：读报告内快照 dir（生成当刻的真·资金净流入前三），时点锁定、不读实时。
  loadDirLine(host, ctx);
}

/* 今日重点方向（一行）：读报告内快照 dir（生成当刻资金净流入前三），时点锁定；空则诚实标「今日无明显资金主线」。 */
function loadDirLine(host, ctx) {
  const slot = host.querySelector('#mp-dir');
  if (!slot) return;
  // 报告生成时已快照好的 top-3 净流入行业（{name,change_pct,main_flow}），不再实时取，杜绝盘中漂移。
  const inflow = (ctx.report && ctx.report.cockpit && ctx.report.cockpit.dir) || [];

  if (!inflow.length) {
    // 无资金流入主线 → 诚实标注，不退回涨幅伪装成主线（盘前不误导布局方向）。
    slot.innerHTML = `<span class="dl-k">今日重点方向</span>
      <span class="dl-none">两市资金普遍净流出，今日无明显资金主线，盘前以防守为主（详见下方报告）</span>`;
    return;
  }
  const chips = inflow.map((s, i) => {
    const cls = pctClass(s.change_pct);
    return `<span class="dl-chip" data-name="${escapeHtml(s.name || '')}">
      <b class="dl-name">${escapeHtml(s.name || '—')}</b>
      <span class="dl-pct ${cls}">${fmtPct(s.change_pct)}</span>
      ${s.main_flow != null ? `<span class="dl-flow up">净流入 ${escapeHtml(fmtMoney(s.main_flow, 1))}</span>` : ''}
      ${i === 0 ? '<span class="dl-tag">主线</span>' : ''}
      <span class="dl-vf"></span>
    </span>`;
  }).join('');
  slot.innerHTML = `<span class="dl-k">今日重点方向</span><span class="dl-chips">${chips}</span>`;
  verifyDirLive(slot, ctx);
}

/* 方向口径（慎微 07-10 拍板落地）：主体=盘前预判（报告快照 dir，08:50 定格不变）；
   叠加【盘中资金验证】实时对照层——只验证不改预判：该方向当前主力仍净流入 → ✓资金续流；
   已转净流出 → ⚠已转流出（提醒预判失效风险）。数据取实时 /market/flow（与板块资金榜同源）。 */
async function verifyDirLive(slot, ctx) {
  let d = null;
  try { d = await api.get('/market/flow'); } catch { return; }   // 验证层失败静默，预判主体不受影响
  if (!ctx.isAlive() || !slot.isConnected) return;
  const by = new Map((d?.main_flow_sectors || []).map((s) => [s.name, s.main_net]));
  if (!by.size) return;
  slot.querySelectorAll('.dl-chip').forEach((chip) => {
    const vf = chip.querySelector('.dl-vf');
    const net = by.get(chip.dataset.name);
    if (!vf || net == null) return;
    if (net > 0) { vf.textContent = '✓资金续流'; vf.className = 'dl-vf up'; vf.title = '盘中验证(实时)：该方向主力当前仍净流入，预判获资金确认'; }
    else { vf.textContent = '⚠已转流出'; vf.className = 'dl-vf down'; vf.title = '盘中验证(实时)：该方向主力已转净流出，盘前预判存在失效风险'; }
  });
  const k = slot.querySelector('.dl-k');
  if (k) k.title = '主体为盘前预判(08:50 定格不变)；✓/⚠ 为盘中资金实时验证对照，只验证、不改预判';
}

/* ============================================================
   样式（幂等注入，跟随主题 CSS 变量；红涨绿跌沿用全局 --up/--down）
   ============================================================ */
function injectStyle() {
  if (document.getElementById('morning-style')) return;
  const css = `
  /* —— 盘前风险闸横幅（与 tail 风险闸同款危险度色条） —— */
  .mp-gate{position:relative;border:1px solid var(--border);border-radius:var(--radius);
    padding:15px 18px 13px;margin-bottom:var(--gap);background:var(--panel);overflow:hidden;box-shadow:var(--shadow-soft)}
  .mp-gate::before{content:"";position:absolute;inset:0 auto 0 0;width:5px;background:var(--lv-c,var(--flat))}
  .mp-gate.skel-wrap{padding:15px 18px}
  .mp-gate .mg-row1{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .mp-gate .mg-badge{flex:none;font-size:13px;font-weight:800;color:#fff;background:var(--lv-c,var(--flat));
    border-radius:8px;padding:5px 12px;letter-spacing:.5px;
    box-shadow:0 2px 10px color-mix(in srgb,var(--lv-c,var(--flat)) 42%,transparent)}
  .mp-gate .mg-headline{font-size:16px;font-weight:800;color:var(--text);line-height:1.4}
  .mp-gate .mg-cap{margin-left:auto;font-size:12px;color:var(--text-3);white-space:nowrap}
  .mp-gate .mg-cap b{font-family:var(--mono,monospace);color:var(--text-2);font-size:13px}
  .mp-gate .mg-text{margin-top:10px;font-size:14px;line-height:1.6;color:var(--text);font-weight:600}
  .mp-gate .mg-action{margin-top:10px;font-size:13px;line-height:1.6;color:var(--text-2);
    background:var(--panel-2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:7px 12px}
  .mp-gate .mg-action .ah{color:var(--accent);font-weight:700;margin-right:8px;font-family:var(--mono,monospace);font-size:12px}
  .mp-gate .mg-foot{margin-top:11px;font-size:12px;color:var(--text-3);line-height:1.5;
    border-top:1px dashed var(--border);padding-top:9px}
  .mp-gate .mg-bs{margin-top:10px;font-size:13px;color:var(--down);background:var(--down-bg);
    border:1px solid var(--down-line,var(--border));border-radius:var(--radius-sm);padding:7px 12px;line-height:1.5}
  /* 禁止/允许改横排 chips（慎微 07-10：四条竖的改成一个横的） */
  .mp-gate .mg-rule{margin-top:11px;border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 12px;background:var(--panel-2);display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .mp-gate .mg-rule .rh{font-size:12px;font-weight:700;letter-spacing:.5px;font-family:var(--mono,monospace);flex:none}
  .mp-gate .mg-rule.allow .rh{color:var(--up)} .mp-gate .mg-rule.forbid .rh{color:var(--down)}
  .mp-gate .rule-chips{display:inline-flex;gap:8px;flex-wrap:wrap}
  .mp-gate .rc{font-size:12.5px;color:var(--text-2);background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:2px 11px;line-height:1.6;white-space:nowrap}
  /* 三种状态底色微染（仍以左侧色条为主信号） */
  .mp-gate.gate-danger{background:linear-gradient(100deg,color-mix(in srgb,var(--lv-c) 16%,var(--panel)),var(--panel) 60%)}
  .mp-gate.gate-warn{background:linear-gradient(100deg,color-mix(in srgb,var(--lv-c) 12%,var(--panel)),var(--panel) 62%)}
  .mp-gate.gate-ok{background:linear-gradient(100deg,color-mix(in srgb,var(--lv-c) 10%,var(--panel)),var(--panel) 64%)}
  .mp-gate.gate-unknown .mg-head{display:flex;align-items:center;gap:8px}
  .mp-gate.gate-unknown .mg-icn{color:var(--text-3);display:inline-flex}
  .mp-gate.gate-unknown .mg-icn svg{width:18px;height:18px}
  .mp-gate.gate-unknown .mg-title{font-size:15px;font-weight:800;color:var(--text)}

  /* —— 今日重点方向（一行） —— */
  .mp-gate .mg-dir{margin-top:11px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px}
  .mp-gate .mg-dir:empty{display:none}
  .mp-gate .mg-dir .dl-k{flex:none;font-size:12px;font-weight:700;color:var(--text-3);font-family:var(--mono,monospace)}
  .mp-gate .mg-dir .dl-none{color:var(--text-3);line-height:1.5}
  .mp-gate .mg-dir .dl-chips{display:flex;gap:8px;flex-wrap:wrap}
  .mp-gate .mg-dir .dl-chip{display:inline-flex;align-items:center;gap:7px;background:var(--panel-2);
    border:1px solid var(--border);border-radius:var(--radius-sm);padding:4px 10px}
  .mp-gate .mg-dir .dl-name{color:var(--text);font-weight:700}
  .mp-gate .mg-dir .dl-pct{font-family:var(--mono,monospace);font-size:12px}
  .mp-gate .mg-dir .dl-flow{font-family:var(--mono,monospace);font-size:11px}
  .mp-gate .mg-dir .dl-tag{font-size:10px;font-weight:700;color:var(--amber);
    border:1px solid var(--amber);border-radius:8px;padding:0 7px}
  .mp-gate .mg-dir .dl-vf{font-size:11px;font-weight:700;cursor:help}
  .mp-gate .mg-dir .dl-vf:empty{display:none}
  .up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--text-3)}

  @media (max-width:760px){
    .mp-gate .mg-cap{margin-left:0;width:100%}
    .mp-gate .mg-dir{align-items:flex-start}
    .mp-gate .mg-dir .dl-chip{width:100%;justify-content:flex-start;flex-wrap:wrap}
  }`;
  const tag = document.createElement('style');
  tag.id = 'morning-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}
