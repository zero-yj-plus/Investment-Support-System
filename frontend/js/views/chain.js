/* views/chain.js — 产业链研究（结构化内核 / Markdown SSE 渐进兼容 + 历史 + 知识库） */

import { api, aiReadiness, ssePost, lsGet, lsSet } from '../api.js';
import { icon, escapeHtml, renderMd, toast, emptyState, confirmModal } from '../ui.js';
import {
  buildChainResearchRequest,
  chainResearchScopeHint,
  CHAIN_RESEARCH_CONTRACT_VERSION,
} from './_chain_shared.js';

const LS_KEY = 'hermes.chain.history';
const MAX_HISTORY = 20;
const STREAM_PAINT_MS = 120;

let root = null;
let history = [];
let activeIdx = -1;
let running = false;
let aborter = null;
let curIndustry = '';
let curContent = '';
let curStructured = null;
let curError = '';
let curStage = '';

const isObject = (value) => !!value && typeof value === 'object' && !Array.isArray(value);

function firstValue(obj, aliases, fallback = null) {
  if (!isObject(obj)) return fallback;
  for (const key of aliases) {
    if (obj[key] !== undefined && obj[key] !== null && obj[key] !== '') return obj[key];
  }
  return fallback;
}

function cleanText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value).trim();
  }
  if (Array.isArray(value)) return value.map(cleanText).filter(Boolean).join('；');
  if (isObject(value)) {
    const title = firstValue(value, ['title', 'name', 'label', 'stage', 'segment', '环节'], '');
    const detail = firstValue(value, ['detail', 'description', 'summary', 'logic', 'role', 'value', '结论'], '');
    if (title && detail && String(title) !== String(detail)) return `${cleanText(title)}：${cleanText(detail)}`;
    if (title || detail) return cleanText(title || detail);
    return Object.entries(value)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${k}：${cleanText(v)}`)
      .join('；');
  }
  return '';
}

function toItems(value) {
  if (value === null || value === undefined || value === '') return [];
  if (Array.isArray(value)) return value.filter((item) => item !== null && item !== undefined && item !== '');
  if (isObject(value)) {
    return Object.entries(value).map(([key, item]) => {
      if (isObject(item)) return { _label: key, ...item };
      return { _label: key, detail: item };
    });
  }
  return [value];
}

function parseWholeJson(raw) {
  let text = String(raw || '').trim();
  if (!text) return null;
  const fenced = /^```(?:json)?\s*([\s\S]*?)\s*```$/i.exec(text);
  if (fenced) text = fenced[1].trim();
  if (!text.startsWith('{') || !text.endsWith('}')) return null;
  try {
    const value = JSON.parse(text);
    return isObject(value) ? value : null;
  } catch {
    return null;
  }
}

function looksLikeChain(value) {
  if (!isObject(value)) return false;
  const keys = new Set(Object.keys(value));
  const schema = String(firstValue(value, ['schema_version', 'schema', 'contract'], '')).toLowerCase();
  const signals = [
    'demand_essence', 'demand', 'chain', 'chain_structure', 'upstream', 'midstream', 'downstream',
    'value_capture', 'bottlenecks', 'domestic_substitution', 'company_mapping',
    'companies', 'realization_valuation', 'priorities', 'invalidations',
  ].filter((key) => keys.has(key)).length;
  return schema.includes('chain') || signals >= 2;
}

function findChainObject(payload) {
  if (!isObject(payload)) return null;
  const queue = [payload];
  const seen = new Set();
  while (queue.length) {
    const current = queue.shift();
    if (!isObject(current) || seen.has(current)) continue;
    seen.add(current);
    if (looksLikeChain(current)) return current;
    for (const key of ['structured', 'industry_chain', 'chain_result', 'result', 'data', 'output', 'payload', 'chain']) {
      if (isObject(current[key])) queue.push(current[key]);
    }
  }
  return null;
}

function normalizeCompanies(value) {
  if (Array.isArray(value)) return value.filter(isObject);
  if (!isObject(value)) return [];
  return Object.entries(value).flatMap(([key, item]) => {
    if (Array.isArray(item)) {
      return item.filter(isObject).map((row) => ({ segment: key, ...row }));
    }
    if (isObject(item)) {
      const keyIsCode = /^\d{6}$/.test(key);
      return [{ ...(keyIsCode ? { code: key } : { segment: key }), ...item }];
    }
    return [{ name: key, role: cleanText(item) }];
  });
}

/**
 * 把共享产业链内核的结构化对象归一到前端稳定契约。
 * 兼容 done.structured / done.chain / done.result / done.data，
 * 也兼容旧服务把完整 JSON 当作 Markdown delta 返回。
 */
export function normalizeChainPayload(payload, rawText = '') {
  const candidate = findChainObject(payload) || findChainObject(parseWholeJson(rawText));
  if (!candidate) return null;
  const chain = firstValue(candidate, ['chain', 'segments', 'value_chain', 'chain_structure', '产业链'], {});
  const chainObj = isObject(chain) ? chain : {};
  const chainRows = Array.isArray(chain) ? chain.filter(isObject) : [];
  const stageRows = (stage) => chainRows.filter((row) => {
    const value = cleanText(firstValue(row, ['stage', 'label', 'segment', '环节'], '')).toLowerCase();
    return value.includes(stage.en) || value.includes(stage.cn);
  });
  const evidence = firstValue(candidate, ['evidence', 'provenance', 'data_quality', 'coverage'], {});
  const evidenceObj = isObject(evidence) ? evidence : {};
  const evidenceRows = Array.isArray(evidence) ? evidence.filter(isObject) : [];
  const gaps = firstValue(candidate, ['data_gaps', 'gaps', 'missing', 'evidence_gaps'], []);
  const evidenceGaps = firstValue(evidenceObj, ['gaps', 'missing', 'data_gaps'], []);
  const sources = firstValue(
    evidenceObj,
    ['sources', 'source_list', 'evidence_sources'],
    firstValue(candidate, ['sources'], evidenceRows.map((row) => ({
      name: row.source || row.id,
      status: row.status,
      as_of: row.data_as_of,
    }))),
  );
  const separation = firstValue(candidate, ['research_trade_separation'], {});
  const separationObj = isObject(separation) ? separation : {};

  return {
    schemaVersion: cleanText(firstValue(candidate, ['schema_version', 'schema', 'contract'], 'industry-chain-compatible')),
    industry: cleanText(firstValue(candidate, ['industry', 'topic', 'name'], curIndustry)),
    conclusion: firstValue(candidate, ['conclusion', 'summary', 'industry_conclusion', 'one_line'], ''),
    tradingTiming: firstValue(
      candidate,
      ['trading_timing', 'timing', 'market_timing', 'trade_window'],
      firstValue(separationObj, ['trade_timing', 'timing'], ''),
    ),
    demandEssence: firstValue(candidate, ['demand_essence', 'demand', 'core_demand', '需求本质'], ''),
    upstream: firstValue(chainObj, ['upstream', '上游'], firstValue(candidate, ['upstream'], stageRows({ en: 'upstream', cn: '上游' }))),
    midstream: firstValue(chainObj, ['midstream', 'middle', '中游'], firstValue(candidate, ['midstream', 'middle'], stageRows({ en: 'midstream', cn: '中游' }))),
    downstream: firstValue(chainObj, ['downstream', '下游'], firstValue(candidate, ['downstream'], stageRows({ en: 'downstream', cn: '下游' }))),
    valueCapture: firstValue(candidate, ['value_capture', 'profit_pool', 'value_distribution', '价值捕获'], []),
    bottlenecks: firstValue(candidate, ['bottlenecks', 'constraints', 'key_bottlenecks', '卡点'], []),
    domesticSubstitution: firstValue(candidate, ['domestic_substitution', 'localization', '国产替代'], []),
    companies: normalizeCompanies(firstValue(candidate, ['companies', 'company_mapping', 'a_share_companies', 'company_map'], [])),
    realizationValuation: firstValue(candidate, ['realization_valuation', 'realization_and_valuation', 'earnings_valuation', 'realization', '兑现估值'], []),
    priorities: firstValue(candidate, ['priorities', 'priority', 'research_priorities', 'evidence_verification_queue', '优先级'], []),
    invalidations: firstValue(candidate, ['invalidations', 'invalidation_conditions', 'failure_conditions', '失效条件'], []),
    asOf: cleanText(firstValue(candidate, ['as_of', 'data_time', 'data_date', 'data_as_of'], firstValue(evidenceObj, ['as_of', 'data_time', 'data_date'], ''))),
    coverage: firstValue(evidenceObj, ['coverage', 'coverage_ratio', 'evidence_coverage'], firstValue(candidate, ['evidence_coverage'], null)),
    coverageStatus: cleanText(firstValue(evidenceObj, ['status', 'coverage_status', 'quality_status'], '')),
    sources: toItems(sources),
    gaps: [...toItems(evidenceGaps), ...toItems(gaps)],
  };
}

function fmtTime(ts) {
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function uniqueTextItems(value) {
  const seen = new Set();
  return toItems(value).filter((item) => {
    const text = cleanText(item);
    if (!text || seen.has(text)) return false;
    seen.add(text);
    return true;
  });
}

function itemTitle(item) {
  if (!isObject(item)) return '';
  return cleanText(firstValue(item, ['title', 'name', 'label', 'stage', 'segment', '_label'], ''));
}

function itemDetail(item) {
  if (!isObject(item)) return cleanText(item);
  return cleanText(firstValue(item, ['detail', 'description', 'summary', 'logic', 'role', 'value', 'reason'], ''))
    || cleanText(item);
}

function renderItems(value, { empty = '本轮未提供', ordered = false, compact = false } = {}) {
  const items = uniqueTextItems(value);
  if (!items.length) return `<div class="chain-v2-empty">${escapeHtml(empty)}</div>`;
  const tag = ordered ? 'ol' : 'ul';
  return `<${tag} class="chain-v2-list ${compact ? 'compact' : ''}">${items.map((item) => {
    const title = itemTitle(item);
    const detail = itemDetail(item);
    return `<li>${title && detail !== title ? `<b>${escapeHtml(title)}</b><span>${escapeHtml(detail)}</span>` : escapeHtml(detail)}</li>`;
  }).join('')}</${tag}>`;
}

function renderSegment(title, value, tone) {
  return `<section class="chain-v2-segment ${tone}">
    <div class="chain-v2-segment-head"><span>${escapeHtml(title)}</span><b>${uniqueTextItems(value).length || '—'}</b></div>
    ${renderItems(value, { empty: `${title}结构证据尚未返回`, compact: true })}
  </section>`;
}

function renderCoverage(value) {
  if (value === null || value === undefined || value === '') return '未报告';
  if (typeof value === 'number') {
    const pct = value <= 1 ? value * 100 : value;
    return `${Math.max(0, Math.min(100, pct)).toFixed(0)}%`;
  }
  if (isObject(value)) {
    const received = Number(firstValue(value, ['received', 'covered', 'verified'], NaN));
    const expected = Number(firstValue(value, ['expected', 'total', 'requested'], NaN));
    const ratio = Number(firstValue(value, ['ratio', 'percent', 'coverage'], NaN));
    if (Number.isFinite(received) && Number.isFinite(expected) && expected > 0) {
      return `${received}/${expected}（${(received / expected * 100).toFixed(0)}%）`;
    }
    if (Number.isFinite(ratio)) return `${(ratio <= 1 ? ratio * 100 : ratio).toFixed(0)}%`;
  }
  return cleanText(value) || '未报告';
}

function renderCompanies(companies) {
  if (!companies.length) return `<div class="chain-v2-empty">本轮没有可核验的公司映射；不会用产业常识冒充公司证据。</div>`;
  return `<div class="chain-v2-table-wrap" role="region" aria-label="公司映射，可横向滚动" tabindex="0">
    <table class="tbl chain-v2-table">
      <thead><tr><th class="l">公司</th><th class="l">产业位置</th><th class="l">价值逻辑</th><th class="l">验证状态</th><th class="l">下一步核验</th></tr></thead>
      <tbody>${companies.map((company) => {
        const code = cleanText(firstValue(company, ['code', 'symbol', 'stock_code'], ''));
        const name = cleanText(firstValue(company, ['name', 'company', 'stock_name'], '')) || code || '未命名';
        const segment = cleanText(firstValue(company, ['segment', 'stage', 'position', 'link'], '')) || '—';
        const role = cleanText(firstValue(company, ['role', 'logic', 'value_capture', 'reason'], '')) || '—';
        const verification = cleanText(firstValue(company, ['verification', 'validation_level', 'evidence_level', 'status'], '')) || '待核验';
        const next = cleanText(firstValue(company, ['next_check', 'verification_point', 'evidence', 'gap'], '')) || '—';
        const companyCell = /^\d{6}$/.test(code)
          ? `<button type="button" class="code-link" data-chain-code="${escapeHtml(code)}">${escapeHtml(name)} <span class="num">${escapeHtml(code)}</span></button>`
          : `${escapeHtml(name)}${code ? ` <span class="num">${escapeHtml(code)}</span>` : ''}`;
        return `<tr><td class="l">${companyCell}</td><td class="l">${escapeHtml(segment)}</td><td class="l">${escapeHtml(role)}</td><td class="l"><span class="chain-v2-tag">${escapeHtml(verification)}</span></td><td class="l">${escapeHtml(next)}</td></tr>`;
      }).join('')}</tbody>
    </table>
  </div>`;
}

const MARKDOWN_DIMENSIONS = [
  { id: 'demand', label: '需求本质', terms: ['需求本质', '核心需求', '需求驱动'] },
  { id: 'chain', label: '上中下游', terms: ['产业地图', '上中下游', '上游', '中游', '下游'] },
  { id: 'value', label: '价值捕获', terms: ['价值捕获', '利润池', '议价权'] },
  { id: 'bottleneck', label: '关键卡点', terms: ['关键卡点', '卡点', '瓶颈', '卡脖子'] },
  { id: 'localization', label: '国产替代', terms: ['国产替代', '国产化'] },
  { id: 'companies', label: '公司映射', terms: ['公司映射', 'A股', '标的验证'] },
  { id: 'earnings', label: '兑现/估值', terms: ['兑现', '估值'] },
  { id: 'priority', label: '优先级', terms: ['优先级', '优先研究'] },
  { id: 'invalid', label: '失效条件', terms: ['失效条件', '证伪条件'] },
  { id: 'evidence', label: '证据/缺口', terms: ['证据覆盖', '数据时点', '数据缺口', '缺口'] },
];

export function extractChainMarkdownSections(raw) {
  const text = String(raw || '');
  const heading = /^#{1,4}\s+(.+?)\s*$/gm;
  const matches = [...text.matchAll(heading)];
  return matches.map((match, index) => ({
    title: match[1].replace(/\*\*/g, '').trim(),
    body: text.slice(match.index + match[0].length, matches[index + 1]?.index ?? text.length).trim(),
  }));
}

function markdownSection(sections, terms) {
  return sections.find((section) => terms.some((term) => section.title.includes(term))) || null;
}

function markdownExcerpt(section, fallback) {
  if (!section?.body) return `<div class="chain-v2-empty">${escapeHtml(fallback)}</div>`;
  return `<div class="md-body chain-v2-excerpt">${renderMd(section.body)}</div>`;
}

function lastInlineDecision(content, label) {
  const pattern = new RegExp(`${label}[：:]\\s*([^\\n*]+)`, 'g');
  const matches = [...String(content || '').matchAll(pattern)];
  if (!matches.length) return '';
  return String(matches[matches.length - 1][1] || '')
    .split(/；(?=产业结论|当前交易时点)/, 1)[0]
    .trim();
}

function renderMarkdownReport(content) {
  const sections = extractChainMarkdownSections(content);
  const coverage = MARKDOWN_DIMENSIONS.map((dimension) => ({
    ...dimension,
    present: sections.some((section) => dimension.terms.some((term) => section.title.includes(term)))
      || (dimension.id === 'evidence' && dimension.terms.some((term) => String(content || '').includes(term))),
  }));
  const covered = coverage.filter((item) => item.present).length;
  const conclusion = markdownSection(sections, ['产业结论', '一句话结论', '结论速览', '核心结论'])
    || { body: lastInlineDecision(content, '产业结论') };
  const timing = markdownSection(sections, ['交易时点', '交易节奏', '当前交易时点', '当前时点', '参与时点'])
    || { body: lastInlineDecision(content, '当前交易时点') };
  return `<div class="chain-v2-markdown">
    <div class="chain-v2-provenance" aria-label="Markdown产业链报告章节覆盖">
      <span><b>输出契约</b>结构化 Markdown</span>
      <span><b>章节覆盖</b>${covered}/${coverage.length}</span>
      <span><b>阅读边界</b>产业结构 ≠ 当下买点</span>
    </div>
    <div class="chain-v2-coverage" aria-label="报告维度覆盖">
      ${coverage.map((item) => `<span class="${item.present ? 'ok' : 'missing'}">${item.present ? '✓' : '○'} ${escapeHtml(item.label)}</span>`).join('')}
    </div>
    <div class="chain-v2-dual">
      <section class="chain-v2-decision structural">
        <div class="chain-v2-eyebrow">INDUSTRY STRUCTURE</div>
        <h4>产业结论</h4>
        ${markdownExcerpt(conclusion, '报告未单列产业结论；请在正文中核对结构判断，不能从单日涨跌反推。')}
        <small>回答产业长期结构、利润池与竞争位置；不被单日涨跌改写。</small>
      </section>
      <section class="chain-v2-decision timing">
        <div class="chain-v2-eyebrow">TRADING TIMING</div>
        <h4>交易时点</h4>
        ${markdownExcerpt(timing, '报告未单列交易时点；产业逻辑不能直接视为当前买点。')}
        <small>时点判断必须有当前证据与触发条件，并与个股风险授权分开。</small>
      </section>
    </div>
    <article class="md-body report-body chain-v2-full-report" id="chain-md">${renderMd(content)}</article>
  </div>`;
}

function renderStructured(result) {
  const gaps = uniqueTextItems(result.gaps);
  const sources = uniqueTextItems(result.sources);
  return `<div class="chain-v2-output">
    <div class="chain-v2-provenance" aria-label="产业链数据口径">
      <span><b>数据时点</b>${escapeHtml(result.asOf || '未报告')}</span>
      <span><b>证据覆盖</b>${escapeHtml(renderCoverage(result.coverage))}</span>
      <span><b>质量状态</b>${escapeHtml(result.coverageStatus || (gaps.length ? '存在缺口' : '未报告'))}</span>
      <span><b>证据源</b>${escapeHtml(sources.length ? sources.map(cleanText).join('、') : '未报告')}</span>
    </div>

    <div class="chain-v2-dual">
      <section class="chain-v2-decision structural">
        <div class="chain-v2-eyebrow">INDUSTRY STRUCTURE</div>
        <h4>产业结论</h4>
        <p>${escapeHtml(cleanText(result.conclusion) || '本轮未形成可核验的产业结论')}</p>
        <small>回答产业长期结构、利润池与竞争位置；不被单日涨跌改写。</small>
      </section>
      <section class="chain-v2-decision timing">
        <div class="chain-v2-eyebrow">TRADING TIMING</div>
        <h4>交易时点</h4>
        <div>${renderItems(result.tradingTiming, { empty: '本轮未提供交易时点；不把产业逻辑直接等同于当下买点。', compact: true })}</div>
        <small>仅表达当前阶段/触发与等待条件，不替代个股诊断和风险授权。</small>
      </section>
    </div>

    <section class="chain-v2-section">
      <div class="chain-v2-section-head"><span>01</span><h4>需求本质</h4></div>
      ${renderItems(result.demandEssence, { empty: '尚未取得需求端证据，无法判断增长由什么驱动。' })}
    </section>

    <section class="chain-v2-section">
      <div class="chain-v2-section-head"><span>02</span><h4>上中下游结构</h4></div>
      <div class="chain-v2-segments">
        ${renderSegment('上游', result.upstream, 'upstream')}
        ${renderSegment('中游', result.midstream, 'midstream')}
        ${renderSegment('下游', result.downstream, 'downstream')}
      </div>
    </section>

    <div class="chain-v2-three">
      <section class="chain-v2-section"><div class="chain-v2-section-head"><span>03</span><h4>价值捕获</h4></div>${renderItems(result.valueCapture, { empty: '利润池/议价权证据尚未返回' })}</section>
      <section class="chain-v2-section"><div class="chain-v2-section-head"><span>04</span><h4>关键卡点</h4></div>${renderItems(result.bottlenecks, { empty: '关键约束尚未核验' })}</section>
      <section class="chain-v2-section"><div class="chain-v2-section-head"><span>05</span><h4>国产替代</h4></div>${renderItems(result.domesticSubstitution, { empty: '国产替代环节与验证口径尚未返回' })}</section>
    </div>

    <section class="chain-v2-section">
      <div class="chain-v2-section-head"><span>06</span><h4>公司映射</h4><em>映射不等于推荐</em></div>
      ${renderCompanies(result.companies)}
    </section>

    <div class="chain-v2-two">
      <section class="chain-v2-section"><div class="chain-v2-section-head"><span>07</span><h4>兑现与估值</h4></div>${renderItems(result.realizationValuation, { empty: '订单、业绩兑现或估值证据尚未返回' })}</section>
      <section class="chain-v2-section"><div class="chain-v2-section-head"><span>08</span><h4>研究优先级</h4></div>${renderItems(result.priorities, { empty: '本轮未形成研究优先级', ordered: true })}</section>
    </div>

    <div class="chain-v2-two">
      <section class="chain-v2-section invalid"><div class="chain-v2-section-head"><span>09</span><h4>失效条件</h4></div>${renderItems(result.invalidations, { empty: '未提供失效条件，因此产业结论不能视为闭环。' })}</section>
      <section class="chain-v2-section gaps"><div class="chain-v2-section-head"><span>10</span><h4>证据缺口</h4></div>${renderItems(gaps, { empty: '接口未报告缺口；不等于所有关键维度都已覆盖。' })}</section>
    </div>
  </div>`;
}

function sectionMarkdown(title, value) {
  const items = uniqueTextItems(value);
  if (!items.length) return `## ${title}\n\n本轮未提供。`;
  return `## ${title}\n\n${items.map((item) => `- ${cleanText(item)}`).join('\n')}`;
}

export function structuredChainToMarkdown(result) {
  if (!result) return '';
  const chainLines = [
    ...uniqueTextItems(result.upstream).map((item) => `- 上游：${cleanText(item)}`),
    ...uniqueTextItems(result.midstream).map((item) => `- 中游：${cleanText(item)}`),
    ...uniqueTextItems(result.downstream).map((item) => `- 下游：${cleanText(item)}`),
  ];
  const companyLines = result.companies.map((company) => {
    const name = cleanText(firstValue(company, ['name', 'company'], '未命名'));
    const code = cleanText(firstValue(company, ['code', 'symbol'], ''));
    const segment = cleanText(firstValue(company, ['segment', 'stage', 'position'], '待核'));
    const role = cleanText(firstValue(company, ['role', 'logic', 'reason'], ''));
    const verification = cleanText(firstValue(company, ['verification', 'validation_level', 'status'], '待核验'));
    return `- ${name}${code ? `（${code}）` : ''}｜${segment}｜${role || '逻辑待补'}｜${verification}`;
  });
  return [
    `# 产业链研究：${result.industry || curIndustry}`,
    `> 数据时点：${result.asOf || '未报告'}｜证据覆盖：${renderCoverage(result.coverage)}｜质量：${result.coverageStatus || '未报告'}`,
    sectionMarkdown('产业结论', result.conclusion),
    sectionMarkdown('交易时点（与产业结论分开）', result.tradingTiming),
    sectionMarkdown('需求本质', result.demandEssence),
    `## 上中下游结构\n\n${chainLines.join('\n') || '本轮未提供。'}`,
    sectionMarkdown('价值捕获', result.valueCapture),
    sectionMarkdown('关键卡点', result.bottlenecks),
    sectionMarkdown('国产替代', result.domesticSubstitution),
    `## 公司映射\n\n${companyLines.join('\n') || '本轮未提供。'}`,
    sectionMarkdown('兑现与估值', result.realizationValuation),
    sectionMarkdown('研究优先级', result.priorities),
    sectionMarkdown('失效条件', result.invalidations),
    sectionMarkdown('证据缺口', result.gaps),
  ].join('\n\n');
}

function renderHistory() {
  const el = root?.querySelector('#chain-history');
  if (!el) return;
  if (!history.length) {
    el.innerHTML = `<div class="chain-v2-history-empty">暂无研究记录</div>`;
    return;
  }
  el.innerHTML = history.map((h, i) => `
    <button type="button" class="history-item ${i === activeIdx ? 'active' : ''}" data-i="${i}">
      ${icon('chain')}<span class="chain-v2-history-title">${escapeHtml(h.industry)}</span>
      <span class="chain-v2-history-meta">${h.structured ? '结构化' : 'Markdown'}${h.incomplete ? ' · 未完成' : ''}</span>
      <span class="hi-time num">${fmtTime(h.ts)}</span>
    </button>`).join('');
}

function bindResultActions(el) {
  el.querySelector('#chain-stop')?.addEventListener('click', () => aborter?.abort());
  el.querySelector('#chain-rerun')?.addEventListener('click', () => dig(curIndustry));
  el.querySelector('#chain-save')?.addEventListener('click', saveToKnowledge);
  el.querySelectorAll('[data-chain-code]').forEach((button) => {
    button.addEventListener('click', () => {
      const code = button.dataset.chainCode;
      if (code) location.hash = `#/diagnosis?code=${encodeURIComponent(code)}`;
    });
  });
}

function renderResult({ streaming = false } = {}) {
  const el = root?.querySelector('#chain-result');
  if (!el) return;
  if (!curContent && !curStructured && !streaming) {
    el.innerHTML = curError
      ? emptyState('本次产业链研究未完成', curError, '', 'alert')
      : emptyState(
        '输入一个行业开始研究',
        '先拆需求本质与上中下游，再看价值捕获、卡点、国产替代和公司映射；产业结论与交易时点会分开呈现。',
        '',
        'chain',
      );
    return;
  }
  const resultBody = curStructured
    ? renderStructured(curStructured)
    : streaming
      ? `<div class="chain-v2-stream-note"><b>正在接收产业链研究证据</b><span>生成完成后将按章节检查覆盖，并把产业结论与交易时点分开呈现。</span></div>
        <article class="md-body report-body" id="chain-md">${renderMd(curContent)}<span class="typing-cursor"></span></article>`
      : renderMarkdownReport(curContent);
  el.innerHTML = `
    <div class="chain-v2-result-head">
      <div><div class="chain-v2-eyebrow">INDUSTRY RESEARCH</div><h3>产业链：${escapeHtml(curIndustry)}</h3></div>
      ${streaming
        ? `<span class="gen-indicator" id="chain-stage"><span class="gdot"></span>${escapeHtml(curStage || '建立产业链证据中…')}</span><button class="btn btn-sm btn-danger" id="chain-stop">${icon('stop')}停止</button>`
        : `<div class="chain-v2-actions"><button class="btn btn-sm" id="chain-rerun">${icon('refresh')}重新研究</button><button class="btn btn-sm btn-primary" id="chain-save">${icon('save')}存入知识库</button></div>`}
    </div>
    ${resultBody}`;
  bindResultActions(el);
}

function persistCurrent({ incomplete = false } = {}) {
  if (!curContent && !curStructured) return;
  history.unshift({
    industry: curIndustry,
    ts: Date.now(),
    content: curContent.slice(0, 60000),
    structured: curStructured,
    incomplete,
  });
  history = history.slice(0, MAX_HISTORY);
  lsSet(LS_KEY, history);
  activeIdx = 0;
}

function updateModelState(readiness) {
  const gate = root?.querySelector('#chain-model-state');
  if (!gate) return;
  gate.hidden = false;
  if (readiness?.ready) {
    gate.className = 'model-gate chain-v2-gate ready';
    gate.innerHTML = '<b>结构研究 + AI 增强可用</b><span>公司映射仍以证据覆盖和缺口为边界。</span>';
    return;
  }
  gate.className = 'model-gate chain-v2-gate degraded';
  gate.innerHTML = `<b>确定性产业链证据可用</b><span>${escapeHtml(readiness?.reason || 'AI 增强未连接')} 将优先返回可核验的板块关系与成员，不会凭常识补公司。</span><button type="button" class="btn btn-sm">连接 AI 增强</button>`;
  gate.querySelector('button')?.addEventListener('click', () => { location.hash = '#/settings'; });
}

async function dig(industry) {
  if (running) return;
  industry = (industry || '').trim();
  if (!industry) { toast('请输入行业名称', 'error'); return; }

  const readiness = await aiReadiness();
  if (!root) return;
  updateModelState(readiness);

  running = true;
  curIndustry = industry;
  curContent = '';
  curStructured = null;
  curError = '';
  curStage = readiness.ready ? '调用共享产业链研究内核…' : '读取可核验产业链关系…';
  activeIdx = -1;
  aborter = new AbortController();
  renderHistory();
  renderResult({ streaming: true });
  const mdEl = () => root?.querySelector('#chain-md');

  let rafPending = false;
  let lastPaint = 0;
  const paint = () => {
    rafPending = false;
    lastPaint = performance.now();
    const el = mdEl();
    if (el) el.innerHTML = escapeHtml(curContent).replace(/\n/g, '<br>') + '<span class="typing-cursor"></span>';
  };
  const schedulePaint = () => {
    if (rafPending) return;
    rafPending = true;
    const wait = Math.max(0, STREAM_PAINT_MS - (performance.now() - lastPaint));
    if (wait <= 0) requestAnimationFrame(paint);
    else setTimeout(() => { if (rafPending) requestAnimationFrame(paint); }, wait);
  };

  try {
    const request = buildChainResearchRequest({
      industry,
      history: [],
      images: [],
    });
    await ssePost('/chat', request, {
      signal: aborter.signal,
      onStage(stage) {
        curStage = stage;
        const indicator = root?.querySelector('#chain-stage');
        if (indicator) indicator.innerHTML = `<span class="gdot"></span>${escapeHtml(stage)}`;
      },
      onDelta(text) {
        curContent += text;
        schedulePaint();
      },
      onError(err) {
        curError = String(err || '产业链数据流未完成');
        toast('研究失败：' + curError, 'error');
      },
      onDone(done) {
        if (!curContent && typeof done?.content === 'string') curContent = done.content;
        curStructured = normalizeChainPayload(done, curContent);
        persistCurrent();
        curError = '';
        toast('产业链研究完成', 'success');
      },
    });
  } catch (error) {
    if (error.name === 'AbortError') {
      curStructured = normalizeChainPayload(null, curContent);
      curError = curContent || curStructured ? '' : '本次研究已由用户停止，未形成可保存结果。';
      persistCurrent({ incomplete: true });
      toast('已停止', 'info');
    } else {
      curError = error.message || '产业链研究异常';
      toast('研究异常：' + curError, 'error');
    }
  } finally {
    running = false;
    aborter = null;
    rafPending = false;
    if (root) {
      if (!curStructured) curStructured = normalizeChainPayload(null, curContent);
      renderHistory();
      renderResult();
    }
  }
}

async function saveToKnowledge() {
  const content = curStructured ? structuredChainToMarkdown(curStructured) : curContent;
  if (!content) return;
  try {
    const response = await api.post('/knowledge', {
      category: '复盘笔记',
      title: `产业链研究：${curIndustry}`,
      content,
    });
    if (response && response.ok !== false) toast('已存入知识库（复盘笔记）', 'success');
    else toast('保存失败：' + (response?.error || '未知错误'), 'error');
  } catch (error) {
    toast('保存失败：' + error.message, 'error');
  }
}

function pageStyles() {
  return `<style id="chain-v2-styles">
    #chain-v2{min-width:0}
    #chain-v2 .chain-v2-shell{display:grid;grid-template-columns:220px minmax(0,1fr);gap:16px;align-items:start}
    #chain-v2 .chain-v2-history-card{position:sticky;top:calc(var(--topbar-h) + 16px)}
    #chain-v2 .chain-v2-history-empty{font-size:13px;color:var(--text-3);padding:8px 4px}
    #chain-v2 .history-item{display:grid;grid-template-columns:20px minmax(0,1fr);gap:2px 8px;width:100%;padding:10px 9px;border:1px solid transparent;border-radius:8px;background:transparent;color:var(--text-2);text-align:left;cursor:pointer}
    #chain-v2 .history-item:hover{background:var(--panel-2);color:var(--text)}
    #chain-v2 .history-item.active{border-color:var(--amber-line);background:var(--amber-dim);color:var(--text)}
    #chain-v2 .history-item svg{grid-row:1/3;margin-top:2px}
    #chain-v2 .chain-v2-history-title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:650}
    #chain-v2 .chain-v2-history-meta{font-size:10px;color:var(--text-3)}
    #chain-v2 .history-item .hi-time{grid-column:2;font-size:10px;color:var(--text-3)}
    #chain-v2 .chain-v2-scope{margin:10px 0;padding:8px 10px;border-left:3px solid var(--amber);background:var(--amber-dim);color:var(--text-2);font-size:12px;line-height:1.55}
    #chain-v2 .chain-v2-gate{display:flex;align-items:center;gap:9px;margin-top:12px;padding:9px 10px;border-radius:8px}
    #chain-v2 .chain-v2-gate span{color:var(--text-2);font-size:12px}
    #chain-v2 .chain-v2-gate.ready{border:1px solid color-mix(in srgb,var(--up) 40%,var(--border));background:color-mix(in srgb,var(--up) 8%,transparent)}
    #chain-v2 .chain-v2-gate.degraded{border:1px solid var(--amber-line);background:var(--amber-dim)}
    #chain-v2 .chain-v2-gate .btn{margin-left:auto}
    #chain-v2 .chain-v2-result-card{min-height:520px;min-width:0}
    #chain-v2 .chain-v2-result-head{display:flex;align-items:center;gap:12px;margin-bottom:16px;padding-bottom:13px;border-bottom:1px solid var(--border);flex-wrap:wrap}
    #chain-v2 .chain-v2-result-head h3{font-size:18px;margin:2px 0 0}
    #chain-v2 .chain-v2-eyebrow{font-size:10px;font-weight:800;letter-spacing:.13em;color:var(--amber)}
    #chain-v2 .chain-v2-actions{margin-left:auto;display:flex;gap:8px}
    #chain-v2 #chain-stage{margin-left:auto}
    #chain-v2 .chain-v2-stream-note{display:flex;gap:8px;align-items:center;margin-bottom:12px;padding:8px 10px;border:1px solid var(--amber-line);border-radius:8px;background:var(--amber-dim);font-size:12px}
    #chain-v2 .chain-v2-stream-note span{color:var(--text-3)}
    #chain-v2 .chain-v2-markdown{display:flex;flex-direction:column;gap:14px}
    #chain-v2 .chain-v2-coverage{display:flex;gap:6px;flex-wrap:wrap}
    #chain-v2 .chain-v2-coverage span{padding:4px 7px;border-radius:999px;border:1px solid var(--border);font-size:10px}
    #chain-v2 .chain-v2-coverage .ok{color:var(--up);background:color-mix(in srgb,var(--up) 7%,transparent)}
    #chain-v2 .chain-v2-coverage .missing{color:var(--text-3);border-style:dashed}
    #chain-v2 .chain-v2-excerpt{font-size:13px;line-height:1.6}
    #chain-v2 .chain-v2-excerpt>:first-child{margin-top:0}
    #chain-v2 .chain-v2-excerpt>:last-child{margin-bottom:0}
    #chain-v2 .chain-v2-full-report{padding-top:14px;border-top:1px solid var(--border)}
    #chain-v2 .chain-v2-output{display:flex;flex-direction:column;gap:14px}
    #chain-v2 .chain-v2-provenance{display:flex;gap:8px;flex-wrap:wrap}
    #chain-v2 .chain-v2-provenance span{display:flex;gap:6px;align-items:center;padding:6px 8px;border:1px solid var(--border);border-radius:7px;background:var(--panel-2);color:var(--text-2);font-size:11px}
    #chain-v2 .chain-v2-provenance b{color:var(--text-3);font-weight:600}
    #chain-v2 .chain-v2-dual{display:grid;grid-template-columns:1.2fr 1fr;gap:12px}
    #chain-v2 .chain-v2-decision{padding:15px;border:1px solid var(--border);border-radius:11px;background:var(--panel-2)}
    #chain-v2 .chain-v2-decision.structural{border-left:4px solid var(--amber)}
    #chain-v2 .chain-v2-decision.timing{border-left:4px solid var(--brand1)}
    #chain-v2 .chain-v2-decision h4{font-size:16px;margin:4px 0 8px}
    #chain-v2 .chain-v2-decision p{font-size:14px;line-height:1.7;margin:0;color:var(--text)}
    #chain-v2 .chain-v2-decision small{display:block;margin-top:9px;color:var(--text-3);line-height:1.45}
    #chain-v2 .chain-v2-section{min-width:0;padding:14px;border:1px solid var(--border);border-radius:10px;background:var(--panel)}
    #chain-v2 .chain-v2-section.invalid{border-color:color-mix(in srgb,var(--down) 32%,var(--border))}
    #chain-v2 .chain-v2-section.gaps{border-style:dashed}
    #chain-v2 .chain-v2-section-head{display:flex;align-items:center;gap:8px;margin-bottom:10px}
    #chain-v2 .chain-v2-section-head>span{font:700 11px/1 var(--font-num);color:var(--amber)}
    #chain-v2 .chain-v2-section-head h4{font-size:14px;margin:0}
    #chain-v2 .chain-v2-section-head em{margin-left:auto;color:var(--text-3);font-size:11px;font-style:normal}
    #chain-v2 .chain-v2-segments{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
    #chain-v2 .chain-v2-segment{min-width:0;padding:11px;border:1px solid var(--border);border-radius:9px;background:var(--panel-2)}
    #chain-v2 .chain-v2-segment.upstream{border-top:3px solid #6b8afd}
    #chain-v2 .chain-v2-segment.midstream{border-top:3px solid var(--amber)}
    #chain-v2 .chain-v2-segment.downstream{border-top:3px solid #36a979}
    #chain-v2 .chain-v2-segment-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-weight:750}
    #chain-v2 .chain-v2-segment-head b{display:grid;place-items:center;min-width:22px;height:22px;border-radius:999px;background:var(--hover);font-size:11px;color:var(--text-3)}
    #chain-v2 .chain-v2-three{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
    #chain-v2 .chain-v2-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    #chain-v2 .chain-v2-list{display:flex;flex-direction:column;gap:7px;margin:0;padding-left:19px;color:var(--text-2);font-size:13px;line-height:1.6}
    #chain-v2 .chain-v2-list.compact{gap:5px;font-size:12px}
    #chain-v2 .chain-v2-list li::marker{color:var(--amber)}
    #chain-v2 .chain-v2-list li b{display:block;color:var(--text)}
    #chain-v2 .chain-v2-list li span{display:block;color:var(--text-2)}
    #chain-v2 .chain-v2-empty{padding:8px 9px;border:1px dashed var(--border);border-radius:7px;color:var(--text-3);font-size:12px;line-height:1.5}
    #chain-v2 .chain-v2-table-wrap{max-width:100%;overflow-x:auto}
    #chain-v2 .chain-v2-table{min-width:850px}
    #chain-v2 .chain-v2-table td{vertical-align:top;white-space:normal;line-height:1.5}
    #chain-v2 .chain-v2-table .code-link{appearance:none;border:0;background:transparent;color:var(--brand1);padding:0;text-align:left;cursor:pointer}
    #chain-v2 .chain-v2-tag{display:inline-block;padding:2px 6px;border:1px solid var(--border);border-radius:999px;background:var(--panel-2);font-size:10px;white-space:nowrap}
    @media(max-width:1000px){
      #chain-v2 .chain-v2-shell{grid-template-columns:1fr}
      #chain-v2 .chain-v2-history-card{position:static}
      #chain-v2 #chain-history{display:flex;gap:7px;overflow-x:auto}
      #chain-v2 .history-item{min-width:180px}
      #chain-v2 .chain-v2-three{grid-template-columns:1fr}
    }
    @media(max-width:720px){
      #chain-v2 .chain-form{display:grid;grid-template-columns:1fr}
      #chain-v2 .chain-v2-gate{align-items:flex-start;flex-direction:column}
      #chain-v2 .chain-v2-gate .btn{width:100%;margin-left:0}
      #chain-v2 .chain-v2-dual,#chain-v2 .chain-v2-two,#chain-v2 .chain-v2-segments{grid-template-columns:1fr}
      #chain-v2 .chain-v2-actions{width:100%;margin-left:0}
      #chain-v2 .chain-v2-actions .btn{flex:1}
      #chain-v2 #chain-stage{margin-left:0}
    }
  </style>`;
}

export function mount(container) {
  root = container;
  history = lsGet(LS_KEY, []);
  if (!Array.isArray(history)) history = [];
  activeIdx = -1;
  curIndustry = '';
  curContent = '';
  curStructured = null;
  curError = '';
  curStage = '';

  container.innerHTML = `${pageStyles()}
    <div id="chain-v2" data-research-contract="${escapeHtml(CHAIN_RESEARCH_CONTRACT_VERSION)}">
      <section class="card">
        <div class="chain-hero">
          <div class="chain-v2-eyebrow">SHARED INDUSTRY-CHAIN CORE</div>
          <h2>产业链研究</h2>
          <p>先回答需求从哪来、利润被谁拿走、卡点在哪里，再映射公司与兑现证据。产业长期结论和当前交易时点分开，避免“赛道好”等同于“现在就买”。</p>
          <div class="chain-v2-scope">${escapeHtml(chainResearchScopeHint())}</div>
          <div class="chain-form">
            <input class="input" id="chain-input" placeholder="输入行业 / 产业链，如：光模块" aria-label="行业或产业链名称">
            <button class="btn btn-primary" id="chain-go">${icon('spark')}开始研究</button>
          </div>
          <div class="model-gate chain-v2-gate" id="chain-model-state" role="status" aria-live="polite" hidden></div>
        </div>
      </section>
      <div class="chain-v2-shell" style="margin-top:var(--gap)">
        <aside class="card chain-v2-history-card">
          <div class="card-head"><h3 class="card-title">历史记录</h3>
            <div class="card-extra"><button class="btn btn-sm btn-ghost" id="chain-clear" title="清空历史" aria-label="清空产业链研究历史">${icon('trash')}</button></div>
          </div>
          <div class="card-body" id="chain-history"></div>
        </aside>
        <section class="card chain-v2-result-card">
          <div class="card-body" id="chain-result" aria-live="polite"></div>
        </section>
      </div>
    </div>`;

  const input = container.querySelector('#chain-input');
  const goButton = container.querySelector('#chain-go');
  goButton.addEventListener('click', () => dig(input.value));
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter') dig(input.value); });

  void aiReadiness().then((readiness) => {
    if (root) updateModelState(readiness);
  });

  container.querySelector('#chain-history').addEventListener('click', (event) => {
    const item = event.target.closest('.history-item');
    if (!item || running) return;
    activeIdx = Number(item.dataset.i);
    const saved = history[activeIdx];
    if (!saved) return;
    curIndustry = saved.industry;
    curContent = saved.content || '';
    curStructured = saved.structured || normalizeChainPayload(null, curContent);
    curError = '';
    input.value = saved.industry;
    renderHistory();
    renderResult();
  });

  container.querySelector('#chain-clear').addEventListener('click', async () => {
    if (!history.length) return;
    if (await confirmModal('清空历史', '将删除本地保存的全部产业链研究记录，确定？', { danger: true, okText: '清空' })) {
      history = [];
      lsSet(LS_KEY, history);
      activeIdx = -1;
      renderHistory();
    }
  });

  renderHistory();
  renderResult();
}

export function unmount() {
  if (aborter) {
    try { aborter.abort(); } catch { /* noop */ }
    aborter = null;
  }
  running = false;
  root = null;
}
