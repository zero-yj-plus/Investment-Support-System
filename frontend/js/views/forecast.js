/* views/forecast.js — 业绩预告掘金（中报/年报 · 确定性规则榜单）
   ------------------------------------------------------------------
   客户方法论（wudi 2026-07-02）四层规则的可视化：主板±50%强制阈值/扭亏 → 披露时点分层
   （主动交卷/中性/压哨）→ 基数陷阱沉底（透明可见，不偷偷删）→ 扣非疑点文本标记。
   铁律：本页所有数字来自 GET /api/forecast/mining（公开业绩预告原值，元/%），
   规则判定、AI 零参与；公开源失败/尚未更新 → 如实空态，绝不编造。 */

import { api, fmtMoney, fmtPct, fmtNum, pctClass } from '../api.js';
import { icon, escapeHtml, toast, emptyState, skeleton } from '../ui.js';

let root = null;
let data = null;
let tab = 'items';   // items=掘金池 traps=陷阱池
let gen = 0;         // 加载代际：快速连刷/切页时丢弃迟到的旧响应，防旧数据覆盖新数据

function gotoDiag(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.diagnosis.code', code);
  location.hash = '#/diagnosis';
}

function layerBadge(layer) {
  const map = {
    '主动交卷': ['var(--up)', '在披露截止日之前较早就交了业绩预告——通常是业绩有底气的积极信号（早交卷）'],
    '提前披露': ['var(--up)', '早于规定时点披露'],
    '中性': ['var(--text-3)', ''],
    '压哨': ['var(--amber)', '拖到披露截止日附近才交——警惕业绩承压、临门才敢报'],
    '超期/更正': ['var(--down)', ''],
    '非强制窗口': ['var(--text-3)', ''],
    '未知': ['var(--text-3)', ''],
  };
  const [c, tip] = map[layer] || ['var(--text-3)', ''];
  return `<span title="${escapeHtml(tip)}" style="font-size:11px;font-weight:700;color:${c};border:1px solid ${c};border-radius:3px;padding:0 5px;white-space:nowrap">${escapeHtml(layer || '—')}</span>`;
}

/* 名词解释面板：客户#4「不懂扣非待核/自愿抢跑/主动交卷」。原来只有 title= hover 提示，
   手机/微信没有 hover → 永远看不到、也没提示能看。改为常显、可点收起的 <details>（触屏友好、
   默认展开），把页面出现的黑话逐条用大白话讲清。数字无关，纯说明。 */
function termLegend() {
  const terms = [
    ['主动交卷 / 提前披露', 'var(--up)', '在规定的披露截止日之前，较早就交了业绩预告——通常是业绩有底气才敢早交，偏积极信号。'],
    ['压哨', 'var(--amber)', '拖到披露截止日附近才交——要警惕业绩承压、临门才敢报。'],
    ['自愿抢跑', 'var(--accent)', '创业板/科创板/北交所本可以不披露，公司却主动提前公布了业绩预告——通常有底气才抢跑，偏积极信号。'],
    ['⚠️ 扣非待核', 'var(--amber)', '这次利润大增，可能掺了一次性收益（卖资产、政府补贴等）。要看「扣非净利」——剔掉这些一次性项目后、真正靠主业赚的钱——是不是也同样增长，等正式中报出来再核实。'],
    ['🕳️ 基数陷阱', 'var(--down)', '去年同期利润极低（基数小），导致今年同比涨幅看着有几十倍、上千倍很唬人，其实绝对利润没多少。这类已被透明地沉到「陷阱池」单独标出，不偷偷删。'],
  ];
  return `<details open class="fc-legend" style="margin-bottom:10px;border:1px solid var(--line);border-radius:8px;padding:7px 11px;background:var(--panel)">
    <summary style="cursor:pointer;font-size:13px;font-weight:600;color:var(--text-2);user-select:none;list-style:none">📖 名词解释 · 看不懂下面这些词就点开（可收起）</summary>
    <div style="margin-top:9px;display:grid;gap:8px">
      ${terms.map(([t, c, d]) => `<div style="font-size:12.5px;line-height:1.6">
        <span style="font-weight:700;color:${c};white-space:nowrap">${t}</span>
        <span style="color:var(--text-2)"> —— ${escapeHtml(d)}</span></div>`).join('')}
    </div></details>`;
}

function rangeTxt(x) {
  const a = x.p_chg_min, b = x.p_chg_max;
  if (a == null && b == null) return '<span style="color:var(--text-3)">幅度未披露</span>';
  if (a != null && b != null) return `<b class="${pctClass(a)}">${fmtPct(a, { digits: 0 })} ~ ${fmtPct(b, { digits: 0 })}</b>`;
  return `<b class="${pctClass(a)}">${fmtPct(a ?? b, { digits: 0 })}</b>`;
}

function rowsHtml(list) {
  if (!list.length) return emptyState('本池暂无标的', '窗口期内每日更新（预告公告多在晚间落库）');
  return `<div class="tbl-wrap"><table class="tbl">
    <thead><tr>
      <th class="l">标的</th><th>板块</th><th>类型</th><th>预告净利同比</th>
      <th>上年同期基数</th><th>披露日</th><th>时点分层</th><th class="l">标记</th><th class="l">变动原因(摘)</th>
    </tr></thead>
    <tbody>
      ${list.map((x) => {
        const ann = String(x.ann_date || '');
        const annH = ann.length === 8 ? `${ann.slice(4, 6)}/${ann.slice(6, 8)}` : (ann || '—');
        const marks = [
          x.is_new_today ? '<span style="color:var(--up);font-weight:700">🆕今日</span>' : '',
          !x.mandatory ? '<span style="color:var(--accent)" title="不是必须披露的，公司主动提前公布了业绩预告——通常是有底气才抢跑">自愿抢跑</span>' : '',
          x.nonrec_flag ? '<span style="color:var(--amber)" title="这次大增可能含一次性收益（卖资产/政府补贴等），要看扣非净利（剔除非经常损益后的真实经营利润）是否也增，正式中报再核">⚠️扣非待核</span>' : '',
          x.trap ? `<span style="color:var(--down)" title="${escapeHtml(x.trap_reason || '')}">🕳️${escapeHtml(x.trap_reason || '陷阱')}</span>` : '',
        ].filter(Boolean).join(' ');
        return `<tr class="fc-row" data-code="${escapeHtml(x.code || '')}" style="cursor:pointer">
          <td class="l"><b>${escapeHtml(x.name || x.code || '—')}</b> <span style="color:var(--text-3);font-size:12px">${escapeHtml(x.code || '')}</span></td>
          <td>${escapeHtml(x.board || '—')}</td>
          <td><b>${escapeHtml(x.type || '—')}</b></td>
          <td class="num">${rangeTxt(x)}</td>
          <td class="num">${x.last_net != null ? fmtMoney(x.last_net, 2) : '—'}</td>
          <td class="num">${annH}</td>
          <td>${layerBadge(x.layer)}</td>
          <td class="l" style="font-size:12px">${marks || '—'}</td>
          <td class="l" style="font-size:12px;color:var(--text-2);max-width:340px">${escapeHtml((x.reason || x.summary || '').slice(0, 80))}</td>
        </tr>`;
      }).join('')}
    </tbody>
  </table></div>`;
}

function render() {
  if (!root) return;
  const box = root.querySelector('#fc-main');
  if (!box) return;
  // 420px 只稳定加载骨架；接口完成后的空态/失败态不再制造整屏空白。
  if (!data) {
    box.style.minHeight = '420px';
    box.innerHTML = `<div class="card-body">${skeleton(6)}</div>`;
    return;
  }
  box.style.removeProperty('min-height');
  if (data.ok === false) {
    box.innerHTML = `<div class="card-body">${emptyState('预告数据不可用', (data.missing || []).join('；') || '免费公开预告源未更新或暂不可达——不展示任何推断数字', '', 'alert')}</div>`;
    return;
  }
  const st = data.stats || {};
  const chip = (k, v, cls = '') => `<div style="flex:1 1 0;min-width:110px;background:var(--panel-2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 12px">
    <div style="font-size:11px;color:var(--text-3)">${escapeHtml(k)}</div>
    <div class="${cls}" style="font-size:19px;font-weight:800;font-family:var(--mono,monospace)">${v}</div>
  </div>`;
  const byType = st['按类型'] || {};
  const typeLine = Object.entries(byType).map(([k, v]) => `${escapeHtml(k)} ${fmtNum(v)}`).join(' · ') || '—';
  const list = tab === 'items' ? (data.items || []) : (data.traps || []);
  box.innerHTML = `
    <div class="card-body">
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px">
        ${chip('报告期', escapeHtml(data.period || '—'))}
        ${chip('窗口截止', escapeHtml(data.deadline || '—'), 'down')}
        ${chip('预告总数', st['总数'] != null ? fmtNum(st['总数']) : '—')}
        ${chip('今日新增', st['今日新增'] != null ? fmtNum(st['今日新增']) : '—', 'up')}
        ${chip('掘金池', st['掘金池'] != null ? fmtNum(st['掘金池']) : '—', 'up')}
        ${chip('陷阱沉底', st['陷阱池'] != null ? fmtNum(st['陷阱池']) : '—', 'down')}
      </div>
      <div style="font-size:12px;color:var(--text-3);margin-bottom:10px">类型分布：${typeLine}${(data.missing || []).length ? ` · <span style="color:var(--amber)">${escapeHtml(data.missing.join('；'))}</span>` : ''}</div>
      <div class="tabs" style="margin-bottom:8px">
        <button class="tab ${tab === 'items' ? 'active' : ''}" data-tab="items">掘金池（达标 ${fmtNum((data.items || []).length)}）</button>
        <button class="tab ${tab === 'traps' ? 'active' : ''}" data-tab="traps">陷阱池（沉底 ${fmtNum((data.traps || []).length)}）</button>
      </div>
      <div style="font-size:12px;color:var(--text-3);margin-bottom:8px">
        规则：主板中报强制预告(截止7/15)取「盈利且同比≥+50% / 扭亏」；双创北交所为自愿披露标「抢跑」；
        低基数/数千倍式高增沉入陷阱池（透明可查，不偷偷删）；变动原因含非经常信号→⚠️扣非待核。<b>点行→个股诊断</b>。
      </div>
      ${termLegend()}
      <div id="fc-list">${rowsHtml(list)}</div>
    </div>`;
  box.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => { tab = t.dataset.tab; render(); }));
  box.querySelectorAll('.fc-row').forEach((r) => r.addEventListener('click', () => gotoDiag(r.dataset.code)));
}

async function load() {
  const g = ++gen;
  data = null;
  render();
  let d;
  try {
    d = await api.get('/forecast/mining');
  } catch (e) {
    d = { ok: false, missing: [e.message] };
  }
  if (g !== gen || !root) return;   // 已有更新的加载/页面已卸载 → 丢弃本次迟到响应
  data = d;
  render();
}

export function mount(container) {
  root = container;
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">业绩预告掘金</h2>
      <span class="page-sub">中报/年报预告 · ±50%政策阈值 + 披露时点分层 + 基数陷阱沉底 · 全规则判定，AI 不参与</span>
      <div class="ph-extra"><button class="btn btn-sm" id="fc-refresh">${icon('refresh')}刷新</button></div>
    </div>
    <section class="card" id="fc-main"></section>`;
  container.querySelector('#fc-refresh')?.addEventListener('click', () => { load(); toast('已刷新', 'success'); });
  load();
}

export function unmount() {
  gen += 1;   // 使在途请求作废
  root = null;
  data = null;
}
