/* views/overseas.js — 美股 · 港股 个股 + 基础财务（真数据）

   市场切换（美股 / 港股）+ 代码搜索 → 实时行情卡 + 基础财务表。
   行情走 /api/overseas/quote（腾讯 qt.gtimg.cn，客户机东财被封时仍可达）；
   财务走 /api/overseas/financials（东财源，客户机封锁则后端标 missing，本页如实显示"暂不可用"）。
   红涨绿跌沿用本产品统一 A 股口径（api.pctClass：涨=红、跌=绿）。
   金额单位为该市场本币（美元/港元），不强转人民币——故本页用货币感知的本地格式化，
   不复用 fmtMoney（其 万/亿 后缀是人民币口径）。 */

import { api, fmtPct, fmtPrice, pctClass } from '../api.js';
import { icon, escapeHtml, dataStamp, emptyState, skeleton, toast } from '../ui.js';

let root = null;
// 挂载代号：卸载/换市场/换股后在途响应不得再触碰已重渲染的 DOM（防 null root 与陈旧帧串台）
let gen = 0;
let market = 'us';
let curCode = '';

const MARKETS = [
  { id: 'us', label: '美股', ph: '美股代码，如 AAPL / NVDA / MSFT', samples: ['AAPL', 'NVDA', 'MSFT', 'TSLA'] },
  { id: 'hk', label: '港股', ph: '港股代码，如 00700 / 09988 / 03690', samples: ['00700', '09988', '03690', '00005'] },
];

const CCY_SYM = { USD: '$', HKD: 'HK$', CNY: '¥' };

function injectStyle() {
  if (document.getElementById('ov-style')) return;
  const css = `
  .ov-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .ov-search{display:flex;gap:8px;align-items:center;flex:1;min-width:240px}
  .ov-search .input{flex:1;max-width:300px;font-family:var(--mono);letter-spacing:1px;text-transform:uppercase}
  .ov-samples{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
  .ov-grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:var(--gap);margin-top:var(--gap);align-items:start}
  @media (max-width:900px){.ov-grid{grid-template-columns:minmax(0,1fr)}}
  .ov-fin-table{width:100%;border-collapse:collapse;font-size:14px}
  .ov-fin-table td{padding:8px 6px;border-bottom:1px solid var(--hover)}
  .ov-fin-table td.lab{color:var(--text-3)}
  .ov-fin-table td.val{text-align:right;font-family:var(--mono);font-weight:600}
  .ov-ccy-pill{font-size:11px;color:var(--text-3);border:1px solid var(--hairline);border-radius:6px;padding:1px 7px;margin-left:6px;font-family:var(--mono)}
  .ov-period{font-size:13px;color:var(--text-3);font-family:var(--mono)}
  `;
  const el = document.createElement('style');
  el.id = 'ov-style';
  el.textContent = css;
  document.head.appendChild(el);
}

/* ---------- 货币感知格式化（本币，不转人民币） ---------- */

// 大额金额 → 该市场本币 + 万亿/亿/百万 量级（避免一长串 0）。input 为本币元。
function fmtCcyMoney(v, ccy) {
  if (v == null || !isFinite(v)) return '--';
  const sym = CCY_SYM[ccy] || (ccy ? ccy + ' ' : '');
  const n = Number(v);
  const abs = Math.abs(n);
  let body;
  if (abs >= 1e12) body = (n / 1e12).toFixed(2) + ' 万亿';
  else if (abs >= 1e8) body = (n / 1e8).toFixed(2) + ' 亿';
  else if (abs >= 1e6) body = (n / 1e6).toFixed(2) + ' 百万';
  else if (abs >= 1e4) body = (n / 1e4).toFixed(1) + ' 万';
  else body = n.toFixed(0);
  return sym + body;
}

// 财务指标按 type 渲染：money=本币金额，pct=百分比，ratio=倍，num=纯数值（EPS 等）。
function fmtMetric(m, ccy) {
  if (m.value == null || !isFinite(m.value)) return '--';
  if (m.type === 'pct') return fmtPct(m.value, { signed: m.key.endsWith('_yoy'), digits: 2 });
  if (m.type === 'money') return fmtCcyMoney(m.value, ccy);
  if (m.type === 'ratio') return Number(m.value).toFixed(2);
  return Number(m.value).toFixed(2); // num（EPS/BPS）
}

/* ---------- 工具栏 ---------- */

function renderBar() {
  const m = MARKETS.find((x) => x.id === market) || MARKETS[0];
  const bar = root.querySelector('#ov-bar');
  bar.innerHTML = `
    <div class="segmented" id="ov-markets">
      ${MARKETS.map((x) => `<button class="seg ${x.id === market ? 'active' : ''}" data-mk="${x.id}">${x.label}</button>`).join('')}
    </div>
    <div class="ov-search">
      <input class="input" id="ov-code" placeholder="${escapeHtml(m.ph)}" value="${escapeHtml(curCode)}" autocomplete="off" spellcheck="false">
      <button class="btn btn-sm btn-primary" id="ov-go">${icon('search')}查询</button>
    </div>`;
  bar.querySelector('#ov-go').addEventListener('click', () => query(bar.querySelector('#ov-code').value));
  bar.querySelector('#ov-code').addEventListener('keydown', (e) => { if (e.key === 'Enter') query(e.target.value); });
  bar.querySelector('#ov-markets').addEventListener('click', (e) => {
    const seg = e.target.closest('.seg');
    if (!seg || seg.dataset.mk === market) return;
    market = seg.dataset.mk;
    curCode = '';
    renderBar();
    renderSamples();
    renderEmpty();
  });
  renderSamples();
}

function renderSamples() {
  const m = MARKETS.find((x) => x.id === market) || MARKETS[0];
  const el = root.querySelector('#ov-samples');
  el.innerHTML = '<span class="ctx-hint" style="font-size:13px;color:var(--text-3)">示例：</span>'
    + m.samples.map((s) => `<span class="chip clickable" data-s="${escapeHtml(s)}">${escapeHtml(s)}</span>`).join('');
}

function renderEmpty() {
  const m = MARKETS.find((x) => x.id === market) || MARKETS[0];
  root.querySelector('#ov-quote').innerHTML =
    `<div class="card" style="padding:8px">${emptyState('查询个股后显示实时行情', '输入' + m.label + '代码并回车', '', 'search')}</div>`;
  root.querySelector('#ov-fin').innerHTML =
    `<div class="card" style="padding:8px">${emptyState('基础财务', '查询后显示营收 / 净利 / EPS / ROE 等关键指标', '', 'reports')}</div>`;
}

/* ---------- 查询（行情 + 财务，并行各自降级） ---------- */

async function query(code) {
  code = (code || '').trim();
  if (!code) { toast('请输入股票代码', 'error'); return; }
  curCode = code;
  const myGen = ++gen;
  const qBox = root.querySelector('#ov-quote');
  const fBox = root.querySelector('#ov-fin');
  qBox.innerHTML = `<div class="card" style="padding:16px">${skeleton(6, [28, 44, 16, 16, 16, 16])}</div>`;
  fBox.innerHTML = `<div class="card" style="padding:16px">${skeleton(7, [22, 16, 16, 16, 16, 16, 16])}</div>`;

  // 行情与财务相互独立：任一失败不拖累另一边（财务被封是客户机常态，行情仍要出）。
  const mk = market;
  api.get(`/overseas/quote?market=${mk}&code=${encodeURIComponent(code)}`)
    .then((d) => { if (myGen === gen && root) paintQuote(d, code); })
    .catch((e) => { if (myGen === gen && root) qBox.innerHTML = `<div class="card" style="padding:8px">${emptyState('行情加载失败', e.message, '', 'alert')}</div>`; });

  api.get(`/overseas/financials?market=${mk}&code=${encodeURIComponent(code)}`)
    .then((d) => { if (myGen === gen && root) paintFin(d); })
    .catch((e) => { if (myGen === gen && root) fBox.innerHTML = `<div class="card" style="padding:8px">${emptyState('财务加载失败', e.message, '', 'alert')}</div>`; });
}

/* ---------- 实时行情卡（风格对齐 chat.js 盘口侧栏） ---------- */

function paintQuote(d, code) {
  const box = root.querySelector('#ov-quote');
  if (!box) return;
  if (!d || d.ok === false || !d.quote) {
    const why = (d && d.missing && d.missing.length) ? d.missing.join('；') : (d && d.error) || '未找到该标的';
    box.innerHTML = `<div class="card" style="padding:8px">${emptyState('暂无行情', why, '', 'alert')}</div>`;
    return;
  }
  const q = d.quote;
  const ccy = q.currency || '';
  const cls = pctClass(q.change_pct);
  const chgAbs = (q.price != null && q.prev_close != null) ? q.price - q.prev_close : null;
  const amp = (q.high != null && q.low != null && q.prev_close) ? ((q.high - q.low) / q.prev_close) * 100 : null;
  const timingTone = d.is_live ? 'ok' : 'warn';
  const timingText = [d.data_date ? `交易日 ${d.data_date}` : '', d.market_session || ''].filter(Boolean).join(' · ');
  const observedText = d.quote_as_of
    ? `行情时点 ${d.quote_as_of}（${d.market_name || '当地'}）`
    : '上游未提供行情事件时间';

  box.innerHTML = `
    <div class="card" style="padding:15px 16px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div class="quote-head">
          <span class="q-name">${escapeHtml(q.name || '--')}</span>
          <span class="q-code">${escapeHtml((q.code || code))}</span>
          <span class="ov-ccy-pill">${escapeHtml(ccy || '')}</span>
        </div>
        <div style="text-align:right">
          <span class="badge badge-${timingTone}">${escapeHtml(timingText || '时效待确认')}</span>
          <div class="ctx-hint" style="margin-top:5px">${escapeHtml(observedText)}</div>
        </div>
      </div>
      <div class="quote-price">
        <span class="q-px num ${cls}">${fmtPrice(q.price)}</span>
        <span class="q-chg num ${cls}">${chgAbs == null ? '' : (chgAbs >= 0 ? '+' : '') + chgAbs.toFixed(2)}　${fmtPct(q.change_pct)}</span>
      </div>
      <div class="quote-grid">
        <div class="qg"><span>今开</span><b class="num ${pctClass(q.open - q.prev_close)}">${fmtPrice(q.open)}</b></div>
        <div class="qg"><span>最高</span><b class="num up">${fmtPrice(q.high)}</b></div>
        <div class="qg"><span>最低</span><b class="num down">${fmtPrice(q.low)}</b></div>
        <div class="qg"><span>昨收</span><b class="num">${fmtPrice(q.prev_close)}</b></div>
        <div class="qg"><span>振幅</span><b class="num">${amp == null ? '--' : fmtPct(amp, { signed: false, digits: 1 })}</b></div>
        <div class="qg"><span>市盈率</span><b class="num">${q.pe == null ? '--' : Number(q.pe).toFixed(2)}</b></div>
        <div class="qg"><span>成交额</span><b class="num">${fmtCcyMoney(q.amount, ccy)}</b></div>
        <div class="qg"><span>成交量</span><b class="num">${q.volume == null ? '--' : Number(q.volume).toLocaleString('en-US')}</b></div>
        <div class="qg"><span>总市值</span><b class="num">${fmtCcyMoney(q.total_mv, ccy)}</b></div>
      </div>
      <div style="font-size:12px;color:var(--text-3);margin-top:11px">行情源：腾讯财经 · 页面加载于 ${escapeHtml(d.ts || '--')}（北京）· 金额为${escapeHtml(ccy || '本币')}原币</div>
    </div>`;
}

/* ---------- 基础财务表 ---------- */

function paintFin(d) {
  const box = root.querySelector('#ov-fin');
  if (!box) return;
  const ok = d && d.ok !== false && Array.isArray(d.metrics) && d.metrics.length;
  if (!ok) {
    const why = (d && d.missing && d.missing.length) ? d.missing.join('；') : (d && d.error) || '财务数据暂不可用';
    box.innerHTML = `<div class="card" style="padding:14px 16px">
      <div class="card-head" style="padding:0 0 8px"><h3 class="card-title">基础财务</h3></div>
      ${emptyState('财务数据暂不可用', why, '', 'reports')}
      <div style="font-size:12px;color:var(--text-3);margin-top:6px;text-align:center">财务取自东财，境内封锁的客户机常取不到（按数据真实性铁律标 missing，绝不用行情冒充）。</div>
    </div>`;
    return;
  }
  const ccy = d.currency || '';
  const rows = d.metrics.map((m) => {
    // 同比/盈利能力指标带涨跌色（正绿负红？——本产品红涨绿跌：增长为正用红，下滑用绿，与价格口径一致）
    const colorKeys = new Set(['revenue_yoy', 'net_profit_yoy', 'roe', 'roa', 'gross_margin', 'net_margin']);
    const valCls = colorKeys.has(m.key) ? pctClass(m.value) : '';
    return `<tr><td class="lab">${escapeHtml(m.label)}</td><td class="val ${valCls}">${escapeHtml(fmtMetric(m, ccy))}</td></tr>`;
  }).join('');
  box.innerHTML = `
    <div class="card" style="padding:14px 16px">
      <div class="card-head" style="padding:0 0 6px">
        <h3 class="card-title">基础财务</h3>
        <div class="card-extra"><span class="ov-period">${escapeHtml(d.period || '')}</span><span class="ov-ccy-pill">${escapeHtml(ccy)}</span></div>
      </div>
      <table class="ov-fin-table"><tbody>${rows}</tbody></table>
      <div style="font-size:12px;color:var(--text-3);margin-top:9px">财务源：东财（akshare）· 金额为${escapeHtml(ccy || '本币')}原币 · 同比/盈利指标红涨绿跌</div>
    </div>`;
}

/* ---------- 挂载 ---------- */

export function mount(container) {
  root = container;
  injectStyle();
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">海外市场</h2>
      <span class="page-sub">查询美股、港股实时行情与基础财务，辅助跨市场比较</span>
    </div>
    <div class="card" style="padding:14px 16px">
      <div class="ov-bar" id="ov-bar"></div>
      <div class="ov-samples" id="ov-samples"></div>
    </div>
    <div class="ov-grid">
      <div id="ov-quote"></div>
      <div id="ov-fin"></div>
    </div>`;

  // 示例 chip（事件委托）：点击即填入并查询
  container.querySelector('#ov-samples').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip[data-s]');
    if (!chip) return;
    const input = container.querySelector('#ov-code');
    if (input) input.value = chip.dataset.s;
    query(chip.dataset.s);
  });

  renderBar();
  renderEmpty();

  // 跨页携带意图：A 股盘口/其它页可写 sessionStorage 跳转过来直接查（与 chat 的 hermes.chat.code 同思路）
  const pendingMk = sessionStorage.getItem('hermes.overseas.market');
  const pendingCode = sessionStorage.getItem('hermes.overseas.code');
  if (pendingMk && MARKETS.some((x) => x.id === pendingMk)) { market = pendingMk; sessionStorage.removeItem('hermes.overseas.market'); }
  if (pendingCode) {
    sessionStorage.removeItem('hermes.overseas.code');
    renderBar();
    query(pendingCode);
  }
}

export function unmount() {
  gen++;       // 作废所有在途响应
  root = null;
}
