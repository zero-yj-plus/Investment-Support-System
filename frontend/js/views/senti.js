/* views/senti.js — 舆情事件流（确定性 A 股实体/板块映射）
   --------------------------------------------------------------------
   参考姊妹产品 ashare 的 t-senti/sentiCard（事件卡瀑布 + 分类筛选 + 加载更多 + 统计），
   使用 GET /api/news/a-share：多源原文 → 双名录股票校验 → TDX 行业/概念关系 → 盘面时点闸门。

   铁律抉择（数据真实性，绝不编造）：
   - 股票必须同时存在于交易所官网股票池与 TDX instruments_a；公司只接受代码或完整正式简称命中。
   - 行业/概念只来自 TDX relation 或板块目录；概念原文命中只标“主题候选”，不冒充直接映射。
   - 不强标利多利空；盘面只展示同日观察。收盘后事件固定显示“下一交易日待观察”。
     每条新闻显式区分发布方、采集时点和证据等级；多端命中不冒充独立信源确认。
   - 取不到快讯 → 如实空态（"暂无快讯数据"），绝不堆占位假卡。

   视觉对齐 _intel_hero.js：左侧色条 + mono 时间戳 + 卡片网格；色调全用 app.css CSS 变量，自动跟随明暗主题。
   私有结构样式在本文件注入一次（幂等），不改共享 app.css。

   接线：app.js ROUTES 增 {path:'senti',label:'舆情事件',icon:'sentiment',view:senti} + import 即可，无需新后端端点。 */

import { api } from '../api.js';
import { dataStamp, emptyState, skeleton, escapeHtml, openModal } from '../ui.js';

let root = null;
let timers = [];
// 挂载代号：卸载/重挂载后的在途响应不得再触碰 DOM（与 dashboard.js 的 gen 机制一致）
let gen = 0;
let styled = false;

// 全量快讯（last-good）：后台刷新失败/为空时保留，绝不把已渲染的好列表刷成空态。
let items = [];
let lastTs = '';
let lastEventTime = '';
let lastMissing = [];
let refreshSeconds = 30;
let lastSourcesTried = 0;
let lastSourcesLive = 0;
let mappingSummary = {};
let observationSummary = {};
let viewFilter = '相关';  // 默认只看经过双名录或 TDX 行业目录确认的 A 股相关事件
let shown = 24;           // 已展示条数（加载更多客户端切片）
const PAGE = 24;

/* 私有样式注入一次（幂等）；视觉对齐 _intel_hero 的卡片语言，但中性灰为主（无方向语义）。 */
function injectStyle() {
  if (styled || document.getElementById('senti-feed-style')) { styled = true; return; }
  const css = `
  .senti-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px}
  .senti-stats{display:grid;grid-template-columns:repeat(4,minmax(118px,1fr));gap:8px;flex:1;min-width:520px}
  .senti-stat{display:flex;align-items:baseline;justify-content:space-between;gap:8px;font-size:13px;color:var(--text-3);
    background:var(--panel-2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 10px}
  .senti-stat b{font-size:18px;font-weight:800;color:var(--text);font-family:var(--mono,monospace)}
  .senti-filters{display:inline-flex;flex-wrap:wrap;gap:6px;margin-left:auto}
  .senti-fchip{font-size:13px;color:var(--text-2);background:var(--panel-2);border:1px solid var(--border-strong);
    border-radius:var(--radius-sm);padding:7px 11px;min-height:38px;cursor:pointer;transition:all .13s;white-space:nowrap}
  .senti-fchip:hover{color:var(--amber);border-color:var(--amber-line)}
  .senti-fchip.on{color:var(--amber);background:var(--amber-dim);border-color:var(--amber-line);font-weight:700}
  .senti-rule{display:flex;align-items:flex-start;gap:7px;font-size:13px;line-height:1.5;color:var(--text-2);
    margin:0 0 10px;padding:8px 10px;background:var(--panel-2);border-radius:var(--radius-sm)}
  .senti-rule b{color:var(--text);white-space:nowrap}
  .senti-table-head{display:grid;grid-template-columns:88px minmax(320px,1.45fr) minmax(250px,.9fr) 150px;
    gap:14px;padding:0 13px 7px;font-size:12px;font-weight:700;color:var(--text-3)}
  .senti-grid{display:flex;flex-direction:column;gap:7px}
  .senti-card{position:relative;border:1px solid var(--border);border-left:3px solid var(--border-strong);
    border-radius:var(--radius-sm);background:var(--panel);padding:11px 13px;display:grid;
    grid-template-columns:88px minmax(320px,1.45fr) minmax(250px,.9fr) 150px;gap:14px;align-items:start;min-width:0}
  .senti-card.is-mapped{border-left-color:var(--up)}
  .senti-card.is-candidate{border-left-color:var(--amber)}
  .senti-when{display:flex;flex-direction:column;gap:5px;min-width:0}
  .senti-time{color:var(--amber);font-size:14px;font-weight:800;font-family:var(--mono,monospace)}
  .senti-date,.senti-origin{font-size:12px;line-height:1.35;color:var(--text-3);overflow:hidden;text-overflow:ellipsis}
  .senti-event{min-width:0}
  .senti-event-head{display:flex;align-items:flex-start;gap:7px;min-width:0}
  .senti-kind{flex:0 0 auto;font-size:12px;line-height:1.45;padding:1px 6px;border:1px solid var(--border);
    border-radius:3px;color:var(--text-3);background:var(--panel-2)}
  .senti-kind.direct{color:var(--up);border-color:var(--up-line);background:var(--up-bg)}
  .senti-kind.related{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 38%,var(--border));background:color-mix(in srgb,var(--accent) 7%,var(--panel))}
  .senti-kind.candidate{color:var(--amber);border-color:var(--amber-line);background:var(--amber-dim)}
  .senti-title{font-size:15px;font-weight:740;line-height:1.45;color:var(--text);word-break:break-word;min-width:0}
  .senti-desc{font-size:13px;color:var(--text-2);line-height:1.55;word-break:break-word;margin-top:5px;
    overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  .senti-open{font-size:12px;color:var(--accent);margin-top:5px}
  .senti-links{display:flex;align-content:flex-start;gap:5px;flex-wrap:wrap;min-width:0}
  .senti-map-chip{display:inline-flex;align-items:center;gap:4px;max-width:100%;padding:3px 6px;
    border:1px solid var(--border);border-radius:3px;background:var(--panel-2);font-size:12px;white-space:normal}
  .senti-map-chip span{color:var(--text-3)}
  .senti-map-chip b{font-weight:700;color:var(--text)}
  .senti-no-link{font-size:13px;line-height:1.5;color:var(--text-3)}
  .senti-state{display:flex;flex-direction:column;align-items:flex-start;gap:5px}
  .senti-state-badge{display:inline-flex;font-size:12px;font-weight:750;padding:3px 7px;border-radius:3px;
    color:var(--text-2);background:var(--panel-2);border:1px solid var(--border)}
  .senti-state-badge.waiting,.senti-state-badge.pending_next_session{color:var(--amber);border-color:var(--amber-line);background:var(--amber-dim)}
  .senti-state-badge.observing{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 38%,var(--border))}
  .senti-state-badge.observed{color:var(--up);border-color:var(--up-line);background:var(--up-bg)}
  .senti-state-note{font-size:12px;line-height:1.4;color:var(--text-3)}
  .senti-observation{display:flex;gap:8px;flex-wrap:wrap;font-size:12px;color:var(--text-2);
    margin-top:5px;padding:4px 7px;background:var(--panel-2);border-radius:3px}
  .senti-more{width:100%;margin-top:14px;padding:10px;font-size:14px;color:var(--text-2);
    background:var(--panel-2);border:1px solid var(--border);border-radius:var(--radius-sm);cursor:pointer;
    transition:all .14s;font-family:var(--mono,monospace);letter-spacing:.5px}
  .senti-more:hover{color:var(--amber);border-color:var(--amber-line)}
  .senti-card:focus-visible{outline:2px solid var(--amber);outline-offset:2px}
  @media (max-width:1180px){
    .senti-table-head{display:none}
    .senti-card{grid-template-columns:78px minmax(0,1fr) 230px}
    .senti-links{grid-column:3;grid-row:1}
    .senti-state{grid-column:3;grid-row:2}
  }
  @media (max-width:760px){
    .senti-card{display:flex;flex-direction:column;gap:6px}
    .senti-stats{grid-template-columns:1fr 1fr;min-width:100%}
    .senti-filters{margin-left:0}
    .senti-when{flex-direction:row;align-items:center;gap:8px}
  }`;
  const tag = document.createElement('style');
  tag.id = 'senti-feed-style';
  tag.textContent = css;
  document.head.appendChild(tag);
  styled = true;
}

export function mount(el) {
  gen += 1;
  root = el;
  injectStyle();
  root.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">舆情事件</h2>
      <span class="page-sub" id="senti-sub">看事件、关联A股和盘面状态</span>
      <div class="ph-extra"><span class="data-stamp" id="senti-stamp"></span></div>
    </div>
    <section class="card">
      <div class="card-head">
        <span class="card-title">事件看板 · EVENT BOARD</span>
        <span class="card-extra" id="senti-cnt"></span>
      </div>
      <div class="card-body" id="senti-body">${skeleton(6, [16, 40, 40, 16, 40, 40])}</div>
    </section>`;

  load();
  // 新闻 7×24 小时继续产生，不能沿用行情的交易时段闸门；失败/为空仍保留 last-good。
  timers.push(setInterval(() => load(true), 30000));
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  items = [];
  lastTs = '';
  lastEventTime = '';
  lastMissing = [];
  refreshSeconds = 30;
  lastSourcesTried = 0;
  lastSourcesLive = 0;
  mappingSummary = {};
  observationSummary = {};
  viewFilter = '相关';
  shown = PAGE;
  root = null;
}

async function load(bg = false) {
  const body = root?.querySelector('#senti-body');
  if (!body) return;
  const g = gen;
  let d = null;
  try { d = await api.get('/news/a-share'); } catch { /* 下方兜底 */ }
  if (g !== gen || !root) return;     // 卸载/重挂载后不再触碰 DOM

  const cn = Array.isArray(d?.items) ? d.items.filter((n) => n && (n.title || n.content)) : [];
  // serve-last-good：后台刷新若失败 / ok===false / 快讯为空，保留上次的好列表，不刷成空态。
  if (bg && items.length) {
    if (!d || d.ok === false || !cn.length) {
      const stamp = root.querySelector('#senti-stamp');
      const attemptedAt = new Date().toLocaleTimeString('zh-CN', { hour12: false });
      if (stamp) stamp.innerHTML = dataStamp(lastTs, [], {
        freshness: 'delayed',
        delay_text: '沿用',
        notes: [`${attemptedAt} 刷新未成功，当前沿用上次成功数据`],
        source: '公开采集链',
      });
      return;
    }
  }
  items = cn;
  lastTs = d?.cn_fetched_at || d?.ts || '';
  lastEventTime = d?.latest_event_time || cn[0]?.time || '';
  lastMissing = d?.missing || [];
  refreshSeconds = Number(d?.refresh_seconds || 30);
  lastSourcesTried = Number(d?.sources_tried || 0);
  lastSourcesLive = Number(d?.sources_live || 0);
  mappingSummary = d?.mapping && typeof d.mapping === 'object' ? d.mapping : {};
  observationSummary = d?.observation && typeof d.observation === 'object' ? d.observation : {};

  const stamp = root.querySelector('#senti-stamp');
  if (stamp) stamp.innerHTML = dataStamp(lastTs, lastMissing, {
    freshness: lastTs ? (mappingSummary.state === 'unavailable' ? 'degraded' : 'live') : 'missing',
    state_label: lastTs ? '最近抓取' : '暂不可用',
    source: '公开采集链',
    notes: mappingSummary.missing || [],
  });
  const sub = root.querySelector('#senti-sub');
  if (sub) {
    const latest = timeParts(lastEventTime);
    sub.textContent = `只展示真实采集事件 · ${refreshSeconds}秒更新${latest.hm ? ` · 最新 ${latest.hm}` : ''}`;
  }

  if (!items.length) {
    const cnt = root.querySelector('#senti-cnt');
    if (cnt) cnt.textContent = '';
    body.innerHTML = emptyState('暂无快讯数据', '采集链尚未返回可展示的快讯；可查看数据状态确认是否未配置或暂不可达');
    return;
  }
  renderAll();
}

function filtered() {
  if (viewFilter === '相关') return items.filter((n) => n?.a_share?.state === 'mapped');
  if (viewFilter === '点名') return items.filter((n) => n?.a_share?.state === 'mapped' && n?.a_share?.level === 'direct_stock');
  if (viewFilter === '线索') return items.filter((n) => n?.a_share?.state === 'candidate'
    || (n?.a_share?.state === 'mapped' && n?.a_share?.level !== 'direct_stock'));
  return items;
}

// 首行只回答四件事：有多少事件、多少与A股相关、多少待核对、最新到几点。
function renderStats() {
  const parts = [`<span class="senti-stat">当前事件 <b>${items.length}</b></span>`];
  parts.push(`<span class="senti-stat" title="已通过股票双名录或 TDX 行业目录精确校验">A股相关 <b>${Number(mappingSummary.mapped || 0)}</b></span>`);
  // observationSummary 可能包含历史留痕总量；看板只统计当前这 40 条，避免出现“40 条事件、167 条待核对”。
  const pending = items.filter((n) => ['waiting', 'observing'].includes(n?.a_share?.observation?.status)).length;
  parts.push(`<span class="senti-stat" title="等待目标时点记录盘面表现，不代表利多或利空">待盘面核对 <b>${pending}</b></span>`);
  const latest = timeParts(lastEventTime);
  parts.push(`<span class="senti-stat">最新时间 <b>${escapeHtml(latest.hm || '--:--')}</b></span>`);
  return parts.join('');
}

function renderFilters() {
  const counts = {
    '相关': items.filter((n) => n?.a_share?.state === 'mapped').length,
    '点名': items.filter((n) => n?.a_share?.state === 'mapped' && n?.a_share?.level === 'direct_stock').length,
    '线索': items.filter((n) => n?.a_share?.state === 'candidate'
      || (n?.a_share?.state === 'mapped' && n?.a_share?.level !== 'direct_stock')).length,
    '全部': items.length,
  };
  return `<div class="senti-filters" role="group" aria-label="新闻范围筛选">${[
    ['相关', 'A股相关'], ['点名', '公司点名'], ['线索', '行业/主题线索'], ['全部', '全部事件'],
  ].map(([key, label]) => `<button type="button" class="senti-fchip ${viewFilter === key ? 'on' : ''}" data-view="${key}"
    aria-pressed="${viewFilter === key ? 'true' : 'false'}">${label} ${counts[key]}</button>`).join('')}</div>`;
}

// 取时间串里的 HH:MM 与 日期（数据里 time 形如「2024-01-01 09:30:00」或仅时间）。
function timeParts(t) {
  const str = String(t || '');
  const hm = str.match(/\d{1,2}:\d{2}/);
  const date = str.match(/\d{4}-\d{1,2}-\d{1,2}/);
  return { hm: hm ? hm[0] : '', date: date ? date[0] : '' };
}

function endpointCount(n) {
  const count = Number(n?.corroboration?.collection_endpoint_count);
  return Number.isFinite(count) && count > 0 ? count : Number(n?.confirms || (n?.multi ? 2 : 1));
}

function newsObservations(n) {
  return Array.isArray(n?.observations) ? n.observations.filter((item) => item && typeof item === 'object') : [];
}

function evidenceLabel(level) {
  return ({ tier_1: 'T1 授权通道', tier_2: 'T2 公开采集', tier_3: 'T3 仅作线索' })[level] || '证据等级未标注';
}

function relationLabel(relation) {
  return ({
    customer_authorized_feed: '客户授权数据通道',
    public_aggregator: '公开聚合端（非官方 SLA）',
    republication: '转载端',
    unknown: '上游关系未确认',
  })[relation] || '上游关系未确认';
}

function fullTime(value) {
  if (!value) return '上游未提供';
  return String(value).replace('T', ' ').replace(/\+08:00$/, '') || '上游未提供';
}

function safeExternalHref(value) {
  try {
    const url = new URL(String(value || ''));
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}

function renderNewsSourceDetail(n) {
  const observations = newsObservations(n);
  const count = endpointCount(n);
  const fetchedAt = n?.fetched_at || observations[0]?.fetched_at;
  const rows = observations.length ? observations.map((item) => {
    const href = safeExternalHref(item.source_url);
    const reasons = Array.isArray(item.degraded_reasons) ? item.degraded_reasons : [];
    const reasonText = reasons.map((reason) => ({
      original_url_unavailable: '原文链接未提供',
      published_at_unavailable: '来源时间未提供',
      published_date_inferred: '来源未给日期，按抓取日补全',
    })[reason] || reason).join('；');
    return `<div style="padding:9px 10px;border:1px solid var(--border);border-radius:6px;background:var(--panel-2)">
      <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><b>${escapeHtml(item.collection_platform_name || item.source_id || '未知采集端')}</b><span style="color:var(--accent)">${escapeHtml(evidenceLabel(item.evidence_level))}</span></div>
      <div style="font-size:12px;color:var(--text-2);line-height:1.65;margin-top:4px">
        上游发布方 ${escapeHtml(item.publisher_name || '未披露')} · ${escapeHtml(relationLabel(item.source_relation))} · 接入 ${escapeHtml(item.access_path || '未标注')}<br>
        来源时间 ${escapeHtml(fullTime(item.published_at))} · 抓取时间 ${escapeHtml(fullTime(item.fetched_at))}<br>
        ${href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">查看原文</a>` : '原文链接：上游未提供'}
        ${item.content_hash ? ` · 内容指纹 <code>${escapeHtml(String(item.content_hash).slice(0, 12))}</code>` : ''}
        ${reasonText ? `<div style="color:var(--amber);margin-top:3px">覆盖说明：${escapeHtml(reasonText)}</div>` : ''}
      </div>
    </div>`;
  }).join('') : '<div style="color:var(--amber);font-size:13px">该条为旧版兼容数据，未携带可核验的采集观测。</div>';
  return `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border)">
    <div style="font-weight:700;margin-bottom:7px">采集证据</div>
    <div style="font-size:12px;color:var(--text-2);line-height:1.6;margin-bottom:8px">
      采集端命中 ${count} 个 · 独立信源待确认 · 最近抓取 ${escapeHtml(fullTime(fetchedAt))}
    </div>
    <div style="display:flex;flex-direction:column;gap:7px">${rows}</div>
  </div>`;
}

function cleanDescription(title, content) {
  let desc = content && content !== title ? String(content).trim() : '';
  if (!desc) return '';
  const bracketed = desc.match(/^【([^】]+)】\s*/);
  if (bracketed) {
    const lead = bracketed[1].trim();
    if (title.includes(lead) || lead.includes(title)) desc = desc.slice(bracketed[0].length).trim();
  }
  if (title && desc.startsWith(title)) desc = desc.slice(title.length).trim();
  desc = desc.replace(/^[\s，,。；;：:]+/, '').trim();
  return /^[\s，,。；;：:！？!?、.]+$/.test(desc) ? '' : desc;
}

function mappingParts(n) {
  const map = n?.a_share && typeof n.a_share === 'object' ? n.a_share : {};
  const matches = Array.isArray(map.matches) ? map.matches : [];
  return {
    map,
    stocks: matches.filter((item) => item.kind === 'stock'),
    industries: matches.filter((item) => item.kind === 'industry'),
    concepts: matches.filter((item) => item.kind === 'concept'),
    candidates: Array.isArray(map.topic_candidates) ? map.topic_candidates : [],
    market: map.market_check && typeof map.market_check === 'object' ? map.market_check : {},
    observation: map.observation && typeof map.observation === 'object' ? map.observation : {},
  };
}

function mapNames(rows, withCode = false) {
  const list = rows.slice(0, 2).map((item) => withCode && item.code
    ? `${item.name || item.code}(${String(item.code).slice(0, 6)})`
    : (item.name || item.code || '')).filter(Boolean);
  return `${list.join(' / ')}${rows.length > 2 ? ` +${rows.length - 2}` : ''}`;
}

function observationLabel(observation, market) {
  if (!observation?.status) return { text: market?.label || '仅作资讯', note: '', state: market?.state || 'information_only' };
  const progress = observation.progress || {};
  const target = String(observation.target_trade_date || '').slice(5).replace('-', '/');
  if (observation.status === 'waiting') return { text: '待盘面核对', note: target ? `${target} 10:30` : '等待目标时点', state: 'waiting' };
  if (observation.status === 'observing') return { text: '核对中', note: `已记录 ${progress.complete || 0}/${progress.total || 3} 个时点`, state: 'observing' };
  if (observation.status === 'observed') return { text: '已留痕', note: '已记录至目标日收盘', state: 'observed' };
  return { text: observation.label || '暂不可比', note: '', state: 'incomparable' };
}

function renderObservationFact(observation) {
  if (!['observing', 'observed'].includes(observation?.status)) return '';
  const snap = observation?.result || observation?.latest;
  if (!snap) return '';
  const facts = [];
  const stock = Array.isArray(snap.stocks) ? snap.stocks[0] : null;
  const sector = Array.isArray(snap.sectors) ? snap.sectors[0] : null;
  if (stock) {
    const value = stock.since_baseline_pct ?? stock.change_pct;
    if (value !== null && value !== undefined) facts.push(`${stock.name || stock.code} ${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`);
  }
  if (sector?.median_change_pct !== null && sector?.median_change_pct !== undefined) facts.push(`${sector.name}中位 ${Number(sector.median_change_pct) >= 0 ? '+' : ''}${Number(sector.median_change_pct).toFixed(2)}%`);
  if (sector?.up_ratio_pct !== null && sector?.up_ratio_pct !== undefined) facts.push(`上涨占比 ${Number(sector.up_ratio_pct).toFixed(1)}%`);
  return facts.length ? `<div class="senti-observation">盘面记录（非因果） · ${facts.map(escapeHtml).join(' · ')}</div>` : '';
}

function renderEventLinks(n) {
  const { map, stocks, industries, concepts, candidates, market, observation } = mappingParts(n);
  if (!['mapped', 'candidate'].includes(map.state)) return '<span class="senti-no-link">暂无明确 A 股关联</span>';
  const chips = [];
  if (stocks.length) chips.push(`<span class="senti-map-chip"><span>公司</span><b>${escapeHtml(mapNames(stocks, true))}</b></span>`);
  if (industries.length) chips.push(`<span class="senti-map-chip"><span>行业</span><b>${escapeHtml(mapNames(industries))}</b></span>`);
  if (concepts.length) chips.push(`<span class="senti-map-chip"><span>概念</span><b>${escapeHtml(mapNames(concepts))}</b></span>`);
  if (candidates.length) chips.push(`<span class="senti-map-chip"><span>主题候选</span><b>${escapeHtml(mapNames(candidates))}</b></span>`);
  return chips.join('') || '<span class="senti-no-link">暂无明确 A 股关联</span>';
}

function renderEventState(n) {
  const { map, market, observation } = mappingParts(n);
  if (!['mapped', 'candidate'].includes(map.state)) {
    return '<span class="senti-state-badge">仅作资讯</span><span class="senti-state-note">未发现精确 A 股关联</span>';
  }
  if (map.state === 'candidate') {
    return '<span class="senti-state-badge pending_next_session">主题待确认</span><span class="senti-state-note">未通过公司或行业精确映射</span>';
  }
  const status = observationLabel(observation, market);
  return `<span class="senti-state-badge ${escapeHtml(status.state)}">${escapeHtml(status.text)}</span>${status.note ? `<span class="senti-state-note">${escapeHtml(status.note)}</span>` : ''}`;
}

function eventKind(n) {
  const map = n?.a_share && typeof n.a_share === 'object' ? n.a_share : {};
  if (map.state === 'mapped' && map.level === 'direct_stock') return { text: '公司点名', cls: 'direct' };
  if (map.state === 'mapped') return { text: 'A股相关', cls: 'related' };
  if (map.state === 'candidate') return { text: '主题线索', cls: 'candidate' };
  return { text: '市场资讯', cls: '' };
}

function primaryPlatformName(n) {
  const names = newsObservations(n).map((item) => item.collection_platform_name).filter(Boolean);
  return names[0] || n?.source || '公开采集';
}

function renderMappingDetail(n) {
  const { map, stocks, industries, concepts, candidates, market, observation } = mappingParts(n);
  if (map.state === 'none') return '<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border);color:var(--text-3)">未发现通过精确校验的 A 股公司或行业映射。</div>';
  if (map.state === 'unavailable') return '<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border);color:var(--text-3)">A 股映射底座暂不可用，当前仅保留新闻原文。</div>';
  const rows = [
    stocks.length ? ['直接公司', mapNames(stocks, true)] : null,
    industries.length ? ['关联行业', mapNames(industries)] : null,
    concepts.length ? ['公司概念', mapNames(concepts)] : null,
    candidates.length ? ['主题候选', mapNames(candidates)] : null,
    observation?.status ? ['观察状态', observationLabel(observation, market).text] : (market.label ? ['盘面口径', market.label] : null),
    observation?.target_trade_date ? ['目标日期', observation.target_trade_date] : null,
    observation?.progress ? ['检查进度', `${observation.progress.complete || 0}/${observation.progress.total || 3}`] : null,
  ].filter(Boolean);
  const missing = Array.isArray(map.coverage?.missing) ? map.coverage.missing : [];
  return `<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border)">
    <div style="font-weight:700;margin-bottom:8px">A股映射依据</div>
    ${rows.map(([label, value]) => `<div style="display:grid;grid-template-columns:72px 1fr;gap:8px;margin:5px 0"><span style="color:var(--text-3)">${escapeHtml(label)}</span><span>${escapeHtml(value)}</span></div>`).join('')}
    ${renderObservationFact(observation)}
    ${Array.isArray(observation?.checkpoints) && observation.checkpoints.length ? `<div style="margin-top:8px;font-size:12px;color:var(--text-3)">${observation.checkpoints.map((item) => `${escapeHtml(item.name)}：${item.status === 'complete' ? '已记录' : '待记录'}`).join('　')}</div>` : ''}
    <div style="font-size:12px;color:var(--text-3);margin-top:9px">“已观察”只表示目标交易日盘面已经留档；不判断利多利空，也不代表新闻造成涨跌。</div>
    ${missing.length ? `<div style="font-size:12px;color:var(--amber);margin-top:6px">覆盖说明：${escapeHtml(missing.join('；'))}</div>` : ''}
  </div>`;
}

// 一条快讯 → 一张事件卡（中性，无方向语义）。时间/来源/标题/摘要，全部真数据。
function sentiCard(n, i) {
  const { hm, date } = timeParts(n.published_at || n.time);
  const title = n.title || (n.content || '').slice(0, 60);
  const desc = cleanDescription(title, n.content);
  const { map } = mappingParts(n);
  const mapped = map.state === 'mapped';
  const candidate = map.state === 'candidate';
  const kind = eventKind(n);
  const latestDate = timeParts(lastEventTime).date;
  return `<article class="senti-card ${mapped ? 'is-mapped' : ''} ${candidate ? 'is-candidate' : ''} ${desc ? 'has-desc' : 'no-desc'}" data-i="${i}" role="button" tabindex="0"
    aria-label="查看快讯全文：${escapeHtml(title)}" style="cursor:pointer" title="点击看全文">
    <div class="senti-when">
      <span class="senti-time" title="来源时间">${escapeHtml(hm || '--:--')}</span>
      ${date && date !== latestDate ? `<span class="senti-date">${escapeHtml(date)}</span>` : ''}
      <span class="senti-origin" title="采集平台">${escapeHtml(primaryPlatformName(n))}</span>
    </div>
    <div class="senti-event">
      <div class="senti-event-head"><span class="senti-kind ${kind.cls}">${escapeHtml(kind.text)}</span>${title ? `<div class="senti-title">${escapeHtml(title)}</div>` : ''}</div>
      ${desc ? `<div class="senti-desc">${escapeHtml(desc)}</div>` : ''}
      <div class="senti-open">查看事件详情与来源 ›</div>
    </div>
    <div class="senti-links">${renderEventLinks(n)}</div>
    <div class="senti-state">${renderEventState(n)}</div>
  </article>`;
}

function renderAll() {
  const body = root?.querySelector('#senti-body');
  if (!body) return;
  const cnt = root.querySelector('#senti-cnt');
  const all = filtered();
  if (cnt) cnt.textContent = items.length ? `${all.length} / ${items.length} 条` : '';
  const list = all.slice(0, shown);
  const cards = list.length
    ? list.map((n, i) => sentiCard(n, i)).join('')
    : `<div style="padding:18px;color:var(--text-3);font-size:14px">${viewFilter === '相关' ? '本批事件暂无通过公司名录或行业目录精确确认的 A 股关联，可切换“全部事件”查看原始快讯。' : '当前筛选下暂无快讯。'}</div>`;
  const more = all.length > shown
    ? `<button class="senti-more" id="senti-more">加载更多（剩 ${all.length - shown} 条）</button>`
    : '';

  body.innerHTML = `
    <div class="senti-toolbar"><div class="senti-stats">${renderStats()}</div>${renderFilters()}</div>
    <div class="senti-rule"><b>怎么看：</b><span>先读事件，再看关联 A 股，最后看盘面是否已留痕。页面不判断利多利空；“已留痕”只表示盘面记录完成，不代表新闻造成涨跌。来源证据放在事件详情里。</span></div>
    <div class="senti-table-head"><span>时间 / 来源</span><span>发生了什么</span><span>关联 A 股</span><span>盘面状态</span></div>
    <div class="senti-grid">${cards}</div>
    ${more}`;

  body.querySelectorAll('.senti-fchip').forEach((b) => {
    b.addEventListener('click', () => {
      if (b.dataset.view === viewFilter) return;
      viewFilter = b.dataset.view;
      shown = PAGE;
      renderAll();
    });
  });
  const moreBtn = body.querySelector('#senti-more');
  if (moreBtn) moreBtn.addEventListener('click', () => { shown += PAGE; renderAll(); });
  // 点快讯卡 → 弹窗看全文（#10 修：此前卡片不可点、摘要被 CSS 截 4 行，客户"点击打不开"）
  body.querySelector('.senti-grid')?.addEventListener('click', (e) => {
    const card = e.target.closest('.senti-card');
    if (!card) return;
    const n = list[+card.dataset.i];
    if (!n) return;
    const t = timeParts(n.published_at || n.time);
    const matchedEndpoints = endpointCount(n);
    openModal({
      title: [(matchedEndpoints >= 2 ? `${matchedEndpoints} 采集端命中 · 独立信源待确认` : ''), [t.date, t.hm].filter(Boolean).join(' ')].filter(Boolean).join(' · ') || '快讯',
      body: `${n.title && n.title !== n.content ? `<div style="font-weight:600;font-size:15px;margin-bottom:8px">${escapeHtml(n.title)}</div>` : ''}
             <div style="white-space:pre-wrap;line-height:1.75;font-size:14px;max-height:45vh;overflow:auto">${escapeHtml(String(n.content || n.title || '（无正文）'))}</div>
             ${renderNewsSourceDetail(n)}
             ${renderMappingDetail(n)}`,
    });
  });
  body.querySelector('.senti-grid')?.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('.senti-card');
    if (!card) return;
    e.preventDefault();
    card.click();
  });
}
