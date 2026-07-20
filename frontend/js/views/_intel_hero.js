/* views/_intel_hero.js — 首页顶部「外盘领先 → A股映射」资讯流
   --------------------------------------------------------------------
   母文档 §4.2/§4.3：美股/港股不是行情，是「映射」——隔夜外盘表现领先 A股相关方向。
   所以这里要的是「讯息(隔夜外盘真实涨跌) + 股票(A股受益链 chips)」，不是干巴巴的指数报价。

   铁律边界：
   - 隔夜外盘的【涨跌幅/点位】= 真实数据(腾讯源，经 /news/multi)，绝不编。
   - A股受益股 chips = 产业链【映射知识】(英伟达→算力·光模块·CPO 等)，是「传导参考」不是
     实时因果、不是荐股；chips 只给名称(点击→真实个股诊断出价格/动作)，绝不给 chip 编价格。
   - 外盘全取不到 → 整块隐藏，不堆假卡。

   色彩(A股口径)：外盘涨=利多红、跌=利空绿、平=中性灰。
   接线：容器 <div id="intel-hero"></div>，renderIntelHero(el, {isCurrent})。复用 GET /api/news/multi。 */

import { api, fmtNum, fmtPct, pctClass } from '../api.js';
import { escapeHtml } from '../ui.js';

// 产业链映射知识库：外盘标的(按名称子串匹配) → A股受益方向 + 链条 + 代表受益股(代码+名)。
// 代表股为该链条公认核心标的，仅作传导参考，点击进个股诊断看真实价格/动作。
const CHAIN_MAP = [
  { keys: ['英伟达', 'NVIDIA', 'NVDA'], dir: 'AI算力链', chain: '算力 · 光模块 · CPO · 铜连接',
    stocks: [['中际旭创', '300308'], ['新易盛', '300502'], ['天孚通信', '300394'], ['沪电股份', '002463'], ['工业富联', '601138']] },
  { keys: ['台积电', 'TSM', '台积'], dir: '半导体制造/先进封装', chain: '晶圆 · 封测 · 设备',
    stocks: [['中芯国际', '688981'], ['长电科技', '600584'], ['北方华创', '002371'], ['通富微电', '002156']] },
  { keys: ['AMD'], dir: 'GPU/算力硬件', chain: '服务器 · PCB · 散热',
    stocks: [['工业富联', '601138'], ['沪电股份', '002463'], ['寒武纪', '688256'], ['浪潮信息', '000977']] },
  { keys: ['美光', 'Micron', 'MU'], dir: '存储芯片', chain: 'DRAM · NAND · 模组 · 接口',
    stocks: [['兆易创新', '603986'], ['江波龙', '301308'], ['澜起科技', '688008'], ['德明利', '001309']] },
  { keys: ['特斯拉', 'Tesla', 'TSLA'], dir: '新能源车/人形机器人', chain: '机器人 · 汽车电子 · 电池',
    stocks: [['三花智控', '002050'], ['拓普集团', '601689'], ['宁德时代', '300750'], ['汇川技术', '300124']] },
  { keys: ['费城半导体', 'SOX', '半导体指数'], dir: '半导体全链', chain: '设计 · 制造 · 设备 · 材料',
    stocks: [['北方华创', '002371'], ['中微公司', '688012'], ['韦尔股份', '603501'], ['沪硅产业', '688126']] },
  { keys: ['苹果', 'Apple', 'AAPL'], dir: '消费电子/果链', chain: '组装 · 光学 · 声学 · 结构件',
    stocks: [['立讯精密', '002475'], ['歌尔股份', '002241'], ['蓝思科技', '300433'], ['领益智造', '002600']] },
  { keys: ['恒生科技', 'HSTECH'], dir: 'A股科技情绪联动', chain: 'AI · 互联网 · 创新药 · 机器人',
    stocks: [['恒生科技ETF', '513130'], ['寒武纪', '688256'], ['金山办公', '688111']] },
];

// 指数(纳指/标普/道指/恒生)→ 不映射个股，只算「全球风险偏好」总览（母文档 §4.2：宏观风险偏好辅助）。
const IDX_KEYS = ['纳斯达克', '纳指', 'NASDAQ', '标普', 'S&P', 'SP500', '道琼斯', '道指', 'DOW', '恒生指数'];

function isIndex(name) { return IDX_KEYS.some((k) => String(name || '').includes(k)); }
function mapOf(name) { return CHAIN_MAP.find((m) => m.keys.some((k) => String(name || '').includes(k))); }

let styled = false;
function injectStyle() {
  if (styled || document.getElementById('intel-hero-style')) { styled = true; return; }
  const css = `
  .intel-hero{position:relative;background:linear-gradient(135deg,var(--panel-2),var(--panel));
    border:1px solid var(--border-strong);border-radius:var(--radius);padding:14px 18px 16px;
    box-shadow:var(--shadow-soft);overflow:hidden}
  .intel-hero::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;
    background:linear-gradient(180deg,var(--up),var(--amber));opacity:.85}
  .ih-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .ih-live{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:700;
    letter-spacing:1.2px;color:var(--up);font-family:var(--mono,monospace);text-transform:uppercase}
  .ih-live .d{width:8px;height:8px;border-radius:50%;background:var(--up);
    box-shadow:0 0 8px var(--up);animation:ihpulse 1.6s infinite}
  @keyframes ihpulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.7)}}
  .ih-sub{font-size:13px;color:var(--text-3)}
  .ih-stamp{margin-left:auto;font-size:12px;color:var(--text-3);font-family:var(--mono,monospace)}
  .ih-risk{margin-top:10px;font-size:13px;line-height:1.55;color:var(--text-2);
    background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 12px}
  .ih-risk b{font-family:var(--mono,monospace)}
  .ih-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(258px,1fr));gap:11px;margin-top:12px}
  .ih-card{position:relative;border:1px solid var(--border);border-left-width:3px;border-radius:var(--radius-sm);
    background:var(--panel);padding:11px 13px;display:flex;flex-direction:column;gap:6px;min-width:0}
  .ih-card.dir-up{border-left-color:var(--up)}
  .ih-card.dir-down{border-left-color:var(--down)}
  .ih-card.dir-flat{border-left-color:var(--flat)}
  .ih-card-top{display:flex;align-items:center;justify-content:space-between;gap:8px;
    font-size:11px;color:var(--text-3);font-family:var(--mono,monospace)}
  .ih-tag{font-size:11px;font-weight:700;letter-spacing:.5px;padding:1px 7px;border-radius:3px;border:1px solid}
  .ih-tag.dir-up{color:var(--up);border-color:var(--up-line);background:var(--up-bg)}
  .ih-tag.dir-down{color:var(--down);border-color:var(--down-line);background:var(--down-bg)}
  .ih-tag.dir-flat{color:var(--text-2);border-color:var(--border)}
  .ih-title{font-size:15px;font-weight:800;line-height:1.3;color:var(--text);
    display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
  .ih-quote{font-size:14px;font-weight:700;font-family:var(--mono,monospace)}
  .ih-map{font-size:12px;color:var(--text-2);line-height:1.4}
  .ih-map .ih-dir{color:var(--accent);font-weight:700}
  .ih-chips{display:flex;flex-wrap:wrap;gap:5px}
  .ih-chip{font-size:12px;font-weight:600;color:var(--text);background:var(--panel-2);
    border:1px solid var(--border);border-radius:11px;padding:2px 9px;cursor:pointer;transition:all .14s;white-space:nowrap}
  .ih-chip:hover{border-color:var(--accent);color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,var(--panel-2))}
  .ih-chip .cc{font-family:var(--mono,monospace);font-size:10px;color:var(--text-3);margin-left:3px}
  .ih-foot{font-size:11px;color:var(--text-3);margin-top:9px;line-height:1.4}
  @media (max-width:760px){.ih-grid{grid-template-columns:1fr}.ih-sub{display:none}}`;
  const tag = document.createElement('style');
  tag.id = 'intel-hero-style';
  tag.textContent = css;
  document.head.appendChild(tag);
  styled = true;
}

// 外盘个股(有映射) → 一张「讯息 + 受益股」卡。dir 由真实 change_pct 定。
function mapCard(x, market, mapping) {
  const chg = x.change_pct;
  const dir = pctClass(chg);
  const dirText = dir === 'up' ? '利多' : dir === 'down' ? '利空' : '中性';
  const q = x.close != null ? x.close : x.price;
  const chips = mapping.stocks.map(([n, c]) =>
    `<span class="ih-chip" data-code="${c}" title="点击查看 ${escapeHtml(n)} 个股诊断">${escapeHtml(n)}<span class="cc">${c}</span></span>`).join('');
  return `<div class="ih-card dir-${dir}">
    <div class="ih-card-top"><span>⚡ ${escapeHtml(market)}</span><span class="ih-tag dir-${dir}">${dirText}</span></div>
    <div class="ih-title"><span>${escapeHtml(x.name || '')}</span>
      <span class="ih-quote ${dir}">${q != null ? fmtNum(q, 2) : '--'} ${fmtPct(chg)}</span></div>
    <div class="ih-map">→ 映射A股 <span class="ih-dir">${escapeHtml(mapping.dir)}</span> · ${escapeHtml(mapping.chain)}</div>
    <div class="ih-chips">${chips}</div>
  </div>`;
}

// 全球风险偏好总览：纳指/标普/恒生科技真实涨跌 → 一句对 A股科技情绪的客观传导提示(不预测、不编数字)。
function riskLine(usIdx, hkIdx) {
  const find = (arr, kw) => (arr || []).find((x) => kw.some((k) => String(x.name || '').includes(k)) && typeof x.change_pct === 'number');
  const nas = find(usIdx, ['纳斯达克', '纳指', 'NASDAQ']);
  const sp = find(usIdx, ['标普', 'S&P', 'SP']);
  const hst = find(hkIdx, ['恒生科技']);
  const parts = [];
  if (nas) parts.push(`纳指 <b class="${pctClass(nas.change_pct)}">${fmtPct(nas.change_pct)}</b>`);
  if (sp) parts.push(`标普 <b class="${pctClass(sp.change_pct)}">${fmtPct(sp.change_pct)}</b>`);
  if (hst) parts.push(`恒生科技 <b class="${pctClass(hst.change_pct)}">${fmtPct(hst.change_pct)}</b>`);
  if (!parts.length) return '';
  const chgs = [nas, sp, hst].filter(Boolean).map((x) => x.change_pct);
  const avg = chgs.reduce((a, b) => a + b, 0) / chgs.length;
  const lean = avg > 0.5 ? '海外科技风险偏好回升，利多 A股科技情绪，关注算力/半导体链能否承接。'
    : avg < -0.5 ? '海外科技承压，注意 A股高位科技股的回踩与传导风险，不追高。'
    : '海外科技情绪平稳，A股科技方向以结构性机会为主，按价格条件执行。';
  return `<div class="ih-risk">隔夜外围：${parts.join(' / ')} → ${lean}</div>`;
}

/**
 * 渲染「外盘领先 → A股映射」hero。
 * @param {HTMLElement} container 目标空容器
 * @param {{isCurrent?:()=>boolean}} [opts] 防卸载后回写脏 DOM
 */
export async function renderIntelHero(container, opts = {}) {
  if (!container) return;
  injectStyle();
  const alive = () => container.isConnected && (typeof opts.isCurrent !== 'function' || opts.isCurrent());

  let d = null;
  try { d = await api.get('/news/multi'); } catch { /* 整块隐藏 */ }
  if (!alive()) return;

  const us = d?.us || {}, hk = d?.hk || {};
  const usIdx = us.indexes || [], usTech = us.tech_stocks || [], hkIdx = hk.indexes || [];
  const valid = (x) => x && typeof x.change_pct === 'number' && isFinite(x.change_pct);

  // 组卡：优先「有产业链映射的外盘标的」(科技股 + 恒生科技指数)，这才是「股票+讯息」。
  const cards = [];
  usTech.filter(valid).forEach((x) => { const m = mapOf(x.name); if (m) cards.push(mapCard(x, '隔夜美股 · US', m)); });
  // 港股指数里只有「恒生科技」有映射(联动 A股科技情绪)，其余纯指数只进风险偏好总览行。
  hkIdx.filter(valid).forEach((x) => { const m = mapOf(x.name); if (m) cards.push(mapCard(x, '港股 · HK', m)); });
  // 去重保险（同名标的只留一张卡）
  const seen = new Set();
  const uniq = cards.filter((html) => { const k = html.match(/<span>([^<]+)<\/span>/)?.[1]; if (!k || seen.has(k)) return false; seen.add(k); return true; });

  const risk = riskLine(usIdx, hkIdx);

  // 既无映射卡也无风险偏好行 → 整块隐藏。
  if (!uniq.length && !risk) { container.innerHTML = ''; container.style.display = 'none'; return; }
  container.style.display = '';

  const stamp = d?.ts ? String(d.ts).slice(11) : '';
  container.innerHTML = `
    <div class="intel-hero">
      <div class="ih-top">
        <span class="ih-live"><span class="d"></span>外盘领先 → A股映射 LIVE</span>
        <span class="ih-sub">隔夜外盘已动，谁是 A股受益链 —— 点受益股看个股诊断</span>
        ${stamp ? `<span class="ih-stamp">${escapeHtml(stamp)}</span>` : ''}
      </div>
      ${risk}
      ${uniq.length ? `<div class="ih-grid">${uniq.slice(0, 6).join('')}</div>` : ''}
      <div class="ih-foot">受益股为产业链传导参考(非实时因果、非荐股)；涨跌幅为外盘真实行情，个股价格/动作以诊断为准。</div>
    </div>`;

  container.querySelectorAll('.ih-chip').forEach((c) => c.addEventListener('click', () => {
    const code = c.dataset.code;
    if (!code) return;
    sessionStorage.setItem('hermes.diagnosis.code', code);
    location.hash = '#/diagnosis';
  }));
}
