/* views/settings.js — 系统设置（任务 / 数据 / 交付 / 安全与版本） */

import { api, appState } from '../api.js';
import { icon, escapeHtml, toast, skeleton, emptyState } from '../ui.js';

let root = null;
let activeSection = 'tasks';
let editingModelConnectionId;

const SCHED_LABELS = {
  morning: '早盘内参', midday: '尾盘草案', tail: '尾盘定稿',
  close: '收盘复盘', evening: '次日策略', archive: '每日存档',
};

const JOB_LABELS = Object.freeze({
  morning: '早盘内参', midday: '尾盘决策草案', tail: '尾盘决策定稿',
  close: '收盘复盘', evening: '次日策略与趋势票', archive: '晚间自动存档',
  capital_snapshot: '资金快照', daily_plan_finalize: '盘前计划定稿',
  data_sentinel_am: '开盘前数据体检', data_sentinel_pm: '收盘后数据体检',
  pdf_postmarket: '盘后 PDF', pdf_premarket: '盘前 PDF', pm_brief_gen: '盘前简报草案',
  quant_pick_daily: '每日量化选股', quant_pick_fill: '开盘价回填', prewarm_tick: '数据预热',
});

const DELIVERY_BLOCKER_LABELS = Object.freeze({
  scheduled_push_disabled: '自动发送尚未开启',
  push_channel_unconfigured: '还没有设置报告接收渠道',
  push_target_unconfigured: '还没有选择报告接收目标',
  delivery_manual_review_required: '有发送记录需要人工确认',
  preflight_failed: '发送渠道检查失败',
});

const DELIVERY_SUCCESS = new Set(['accepted', 'delivered', 'acknowledged']);
const DELIVERY_REVIEW = new Set(['sending', 'unknown', 'partial']);
const DELIVERY_FAILED = new Set(['failed', 'failed_retryable', 'failed_terminal']);

function deliveryBlockerLabel(code) {
  const key = String(code || '').trim();
  return DELIVERY_BLOCKER_LABELS[key] || '自动发送配置还需要完善';
}

function targetChannel(value) {
  const text = String(value || '').toLowerCase();
  if (/feishu|lark/.test(text)) return 'feishu';
  if (/weixin|wechat|wecom/.test(text)) return 'wechat';
  return '';
}

function receiptProjection(receipt, channel) {
  if (!receipt || typeof receipt !== 'object') return null;
  const contentId = String(receipt.content_id || '');
  if (channel === 'pdf') {
    if (!/(^|:)pdf(?:_|:)/i.test(contentId)) return null;
    return { ...receipt, projected_status: String(receipt.status || 'unknown') };
  }
  const targetRows = (Array.isArray(receipt.target_results) ? receipt.target_results : [])
    .filter((row) => targetChannel(row?.target) === channel);
  if (targetRows.length) {
    const statuses = targetRows.map((row) => String(row?.status || 'unknown'));
    const successes = statuses.filter((status) => DELIVERY_SUCCESS.has(status)).length;
    const projected = successes === statuses.length ? 'accepted'
      : (successes ? 'partial' : (statuses.some((status) => DELIVERY_REVIEW.has(status)) ? 'unknown' : 'failed_terminal'));
    return {
      ...receipt,
      projected_status: projected,
      projected_error: targetRows.map((row) => row?.error || row?.provider_code).filter(Boolean).join('；'),
    };
  }
  if (targetChannel(receipt.channel) === channel) {
    return { ...receipt, projected_status: String(receipt.status || 'unknown') };
  }
  return null;
}

function latestChannelReceipt(receipts, channel) {
  for (const receipt of Array.isArray(receipts) ? receipts : []) {
    const projected = receiptProjection(receipt, channel);
    if (projected) return projected;
  }
  return null;
}

function formatDuration(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const ms = Math.max(0, Number(value));
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} 秒`;
  return `${(ms / 60000).toFixed(1)} 分`;
}

function jobOutputObjects(job) {
  const nested = job?.last_result?.output_objects;
  if (Array.isArray(nested)) return nested.filter((row) => row && typeof row === 'object');
  return Array.isArray(job?.last_output_objects)
    ? job.last_output_objects.filter((row) => row && typeof row === 'object') : [];
}

function jobIdentity(job) {
  const id = String(job?.id || '').trim();
  const name = String(job?.name || '').trim();
  const raw = `${id} ${name}`.toLowerCase();
  if (raw.includes('register.<locals>') || raw.includes('prewarm_tick')) return { key: 'prewarm_tick', technical: 'prewarm_tick' };
  if (raw.includes('build_scheduler.<locals>') || raw.includes('capital_snapshot')) return { key: 'capital_snapshot', technical: 'capital_snapshot' };
  const clean = id.replace(/^ext_/, '') || name.replace(/^ext_/, '');
  return { key: clean, technical: id || clean || '未提供' };
}

function jobDisplay(job) {
  const identity = jobIdentity(job);
  const rawName = String(job?.name || '');
  const safeName = /<locals>|build_scheduler|register\./i.test(rawName) ? '' : rawName;
  return {
    label: JOB_LABELS[identity.key] || SCHED_LABELS[identity.key] || safeName || '系统任务',
    technical: identity.technical,
  };
}

function injectStyle() {
  if (document.getElementById('settings-v2-style')) return;
  const css = `
    .set-tabs{display:flex;gap:4px;overflow-x:auto;margin-bottom:16px;padding:4px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel)}
    .set-tab{display:inline-flex;align-items:center;gap:8px;min-height:40px;padding:8px 14px;border:0;border-radius:var(--radius-sm);background:transparent;color:var(--text-2);font-size:14px;font-weight:650;white-space:nowrap;cursor:pointer}
    .set-tab:hover{color:var(--text);background:var(--hover)}
    .set-tab.active{color:var(--amber);background:var(--amber-dim);box-shadow:inset 0 0 0 1px var(--amber-line)}
    .set-tab-state{width:7px;height:7px;border-radius:50%;background:var(--text-3)}
    .set-tab-state.ok{background:#22c55e}.set-tab-state.warn{background:var(--warn)}.set-tab-state.bad{background:var(--up)}
    .set-ok{color:#16834d}.set-warn{color:var(--warn)}.set-bad{color:var(--up)}.set-neutral{color:var(--text-2)}
    #set-wrap,.set-panel{min-width:0;max-width:100%}.set-panel[hidden]{display:none}
    .set-overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
    .set-overview-item{border:1px solid var(--border);border-left:3px solid var(--text-3);border-radius:var(--radius);background:var(--panel);padding:12px 14px;min-width:0}
    .set-overview-item.ok{border-left-color:#22c55e}.set-overview-item.warn{border-left-color:var(--warn)}.set-overview-item.bad{border-left-color:var(--up)}
    .set-overview-item .k{font-size:12px;color:var(--text-3);margin-bottom:4px}.set-overview-item .v{font-size:15px;font-weight:750;color:var(--text)}
    .set-overview-item .s{font-size:12px;color:var(--text-3);margin-top:4px;line-height:1.5}
    .set-section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin:0 0 12px}
    .set-section-head h3{font-size:18px;line-height:1.4}.set-section-head p{font-size:13px;color:var(--text-3);margin-top:3px}
    .set-section-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;min-width:0;width:100%}
    .set-section-grid>*{min-width:0;max-width:100%}.set-section-grid .card-body{min-width:0;max-width:100%}
    .set-section-grid>.full{grid-column:1/-1}
    .set-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
    .set-advanced{border:1px solid var(--border);border-radius:var(--radius);background:var(--panel)}
    .set-advanced>summary{cursor:pointer;list-style:none;padding:14px 16px;font-size:14px;font-weight:700;color:var(--text-2)}
    .set-advanced>summary::-webkit-details-marker{display:none}.set-advanced>summary::after{content:'展开';float:right;color:var(--text-3);font-size:12px;font-weight:500}
    .set-advanced[open]>summary{border-bottom:1px solid var(--border)}.set-advanced[open]>summary::after{content:'收起'}
    .set-advanced-body{padding:16px}
    .set-model-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.set-model-actions .btn-danger{color:var(--up);border-color:color-mix(in srgb,var(--up) 35%,var(--border))}
    .set-model-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:9px;margin-bottom:16px}
    .set-model-item{border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 12px;background:var(--bg);min-width:0}
    .set-model-item.active{border-color:color-mix(in srgb,var(--up) 45%,var(--border))}.set-model-item-head{display:flex;align-items:center;gap:8px}.set-model-item-head b{color:var(--text);overflow-wrap:anywhere}
    .set-model-item-head span{margin-left:auto;font-size:11px}.set-model-item-meta{font-size:11px;color:var(--text-3);margin-top:5px;line-height:1.5;overflow-wrap:anywhere}
    .set-status-note{margin-top:10px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text-2);line-height:1.65}
    .set-job-meta{margin-top:3px}.set-job-meta summary{cursor:pointer;color:var(--text-3);font-size:11px;list-style:none}.set-job-meta summary::-webkit-details-marker{display:none}
    .set-job-meta code{display:block;margin-top:4px;color:var(--text-3);font-size:11px;overflow-wrap:anywhere}
    .set-job-outputs{display:flex;flex-direction:column;gap:6px}.set-job-output{display:flex;flex-direction:column;gap:2px;padding:5px 7px;border:1px solid var(--border);border-radius:6px;background:var(--bg)}
    .set-job-output b{font-size:11px;color:var(--text-2)}.set-job-output code{font-size:11px;color:var(--text);overflow-wrap:anywhere}.set-job-output small{font-size:10px;color:var(--text-3);line-height:1.4}
    @media(max-width:960px){.set-overview{grid-template-columns:repeat(2,minmax(0,1fr))}.set-section-grid{grid-template-columns:1fr}}
    @media(max-width:640px){.set-overview{grid-template-columns:1fr}.set-tab{min-height:44px}.set-section-head{display:block}.set-actions{justify-content:stretch}.set-actions .btn{flex:1}}
  `;
  const tag = document.createElement('style');
  tag.id = 'settings-v2-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}

async function load() {
  const wrap = root.querySelector('#set-wrap');
  wrap.innerHTML = `<div class="settings-grid">${[0, 1, 2].map(() => `<div class="card" style="padding:18px">${skeleton(5)}</div>`).join('')}</div>`;

  let settings = null, status = null, sourceReport = null, deliveryReport = null, deliveryGate = null, deliveryOutbox = null, agentRunReport = null, modelConnectionsReport = null;
  const [sRes, stRes, dsRes, drRes, dgRes, doRes, arRes, mcRes] = await Promise.allSettled([
    api.get('/settings'), api.get('/status'), api.get('/datasources'),
    api.get('/delivery/receipts?limit=30'),
    api.get('/delivery/readiness'),
    api.get('/delivery/outbox?limit=30'),
    api.get('/agent/runs?limit=12'),
    api.get('/settings/model-connections'),
  ]);
  if (sRes.status === 'fulfilled' && sRes.value?.ok !== false) settings = sRes.value.settings || {};
  if (stRes.status === 'fulfilled') { status = stRes.value; appState.status = status; }
  if (dsRes.status === 'fulfilled' && dsRes.value?.ok !== false) sourceReport = dsRes.value;
  if (drRes.status === 'fulfilled' && drRes.value?.ok !== false) deliveryReport = drRes.value;
  if (dgRes.status === 'fulfilled' && dgRes.value?.ok !== false) deliveryGate = dgRes.value;
  if (doRes.status === 'fulfilled' && doRes.value?.ok !== false) deliveryOutbox = doRes.value;
  if (arRes.status === 'fulfilled' && arRes.value?.ok !== false) agentRunReport = arRes.value;
  if (mcRes.status === 'fulfilled' && mcRes.value?.ok !== false) modelConnectionsReport = mcRes.value;

  if (!settings) {
    wrap.innerHTML = `<div class="card" style="padding:8px">${emptyState('设置加载失败', '无法读取 /api/settings，请确认后端已启动', '', 'alert')}</div>`;
    return;
  }
  const sch = settings.schedule || {};
  const hermes = status?.hermes || {};
  const feishu = status?.feishu || {};
  const pushVia = (settings.push_via || 'feishu').toLowerCase();
  const jobs = status?.task_center?.jobs || status?.jobs || [];
  const receipts = deliveryReport?.receipts || [];
  const sourceRows = sourceReport?.sources || [];
  const fieldContract = sourceReport?.field_contract || {};
  const fieldContractRows = Object.entries(fieldContract.fields || {});
  const deployment = sourceReport?.deployment || {};
  const deploymentProfileLabel = ({
    cn_home: '国内家庭机',
    cn_server: '国内服务器',
    overseas_server: '海外服务器',
  })[deployment.profile] || '未识别';
  const deploymentRouteLabel = ({
    direct_low_concurrency: '境内低并发直连',
    direct: '境内直连',
    relay: '境内中继',
    disabled: 'IP 敏感公开源停用',
  })[deployment.china_public_route] || '未声明';
  const newsObservation = sourceReport?.news_observation || {};
  const newsCheckpointHealth = newsObservation.checkpoint_health || {};
  const universeAttempts = sourceReport?.universe?.source_attempts || {};
  const universeAttemptText = ['sse', 'szse', 'bse', 'tdx_api'].map((key) => {
    const item = universeAttempts[key];
    if (!item) return '';
    const label = ({ sse: '上交所', szse: '深交所', bse: '北交所', tdx_api: 'TDX API' })[key] || key;
    const state = item.status === 'ok' ? '成功' : (
      item.status === 'degraded' && Number(item.count || 0) > 0 ? '部分' : '失败/未返回'
    );
    return `${label}${state}${Number(item.count || 0) ? `(${Number(item.count).toLocaleString('zh-CN')})` : ''}`;
  }).filter(Boolean).join(' · ');
  const coreSourceKeys = new Set(['tdx_api', 'tencent', 'universe']);
  const downSources = sourceRows.filter((source) => source.status === 'down');
  const coreDownSources = downSources.filter((source) => coreSourceKeys.has(String(source.key || '')));
  const enhancementDownSources = downSources.filter((source) => !coreSourceKeys.has(String(source.key || '')));
  const degradedSources = sourceRows.filter((source) => source.status === 'degraded');
  const idleSources = sourceRows.filter((source) => !['ok', 'down'].includes(String(source.status || '')));
  const slowSources = sourceRows.filter((source) => Number(source.latency_ms || 0) >= 5000);
  const sourceImpact = (source) => coreSourceKeys.has(String(source?.key || '')) ? '基础行情' : '增强数据';
  const sourceState = (source) => {
    if (source.key === 'universe') {
      if (source.status === 'ok') return '<span class="set-ok">已就绪</span>';
      if (source.status === 'degraded') return '<span class="set-warn">可用但待刷新</span>';
      if (source.status === 'down') return '<span class="set-bad">当前不可用</span>';
      return '<span style="color:var(--text-3)">尚未构建</span>';
    }
    if (source.status === 'ok') return '<span class="set-ok">最近调用成功</span>';
    if (source.status === 'degraded') return '<span class="set-warn">部分可用</span>';
    if (source.status === 'down') return `<span class="set-bad">当前调用失败</span><div style="font-size:11px;color:var(--text-3)">影响：${sourceImpact(source)}</div>`;
    return '<span style="color:var(--text-3)">尚未调用</span>';
  };
  const runtimeSourceState = (sourceId) => {
    const state = sourceReport?.evidence_health?.sources?.[sourceId] || {};
    const breaker = state.breaker || {};
    const requests = state.requests || {};
    if (!breaker.state || breaker.state === 'unmanaged') return '<span style="color:var(--text-3)">未装配监控</span>';
    if (!Number(requests.attempted || 0)) return '<span style="color:var(--text-3)">尚未探测</span>';
    const cls = breaker.state === 'open' ? 'set-bad' : (breaker.state === 'half_open' || requests.last_result !== 'success' ? 'set-warn' : 'set-ok');
    const label = breaker.state === 'open' ? '已熔断'
      : (breaker.state === 'half_open' ? '半开探测'
        : (requests.last_result === 'failure' ? '最近失败·未熔断'
          : (requests.last_result === 'no_data' ? '源可达·该代码无数据' : '可用')));
    return `<span class="${cls}">${label}</span><div style="font-size:11px;color:var(--text-3)">
      请求 ${Number(requests.attempted || 0)} · 成功 ${Number(requests.succeeded || 0)} · 失败 ${Number(requests.failed || 0)}
    </div>`;
  };
  const sourceCard = sourceReport ? `
      <section class="card hoverable" style="grid-column:1/-1">
        <div class="card-head"><h3 class="card-title">数据源与缺口</h3>
          <div class="card-extra"><span style="font-size:12px;color:var(--text-3)">正式策略 ${escapeHtml(sourceReport.active_policy || '未知')} · ${escapeHtml(sourceReport.source_contract || 'field-route')}</span></div>
        </div>
        <div class="card-body" style="overflow:auto">
          <div class="set-overview" style="grid-template-columns:repeat(4,minmax(150px,1fr));margin-bottom:12px">
            <div class="set-overview-item ${coreDownSources.length ? 'bad' : 'ok'}"><div class="k">基础行情</div><div class="v">${coreDownSources.length ? `${coreDownSources.length} 个主链路失败` : '主链路可用'}</div><div class="s">TDX API / 腾讯 / 股票池</div></div>
            <div class="set-overview-item ${enhancementDownSources.length ? 'warn' : 'ok'}"><div class="k">增强数据</div><div class="v">${enhancementDownSources.length ? `${enhancementDownSources.length} 个源降级` : '未发现失败'}</div><div class="s">${enhancementDownSources.length ? enhancementDownSources.slice(0, 2).map((source) => escapeHtml(source.name)).join('、') : '行业、资金、财务等'}</div></div>
            <div class="set-overview-item ${slowSources.length ? 'warn' : 'ok'}"><div class="k">慢源</div><div class="v">${slowSources.length ? `${slowSources.length} 个 ≥ 5 秒` : '未发现慢源'}</div><div class="s">${slowSources.length ? slowSources.slice(0, 2).map((source) => `${escapeHtml(source.name)} ${formatDuration(source.latency_ms)}`).join('、') : '最近调用耗时'}</div></div>
            <div class="set-overview-item ${idleSources.length ? 'warn' : 'ok'}"><div class="k">未采集</div><div class="v">${idleSources.length} 个链路</div><div class="s">未调用不等于不可用</div></div>
          </div>
          <div class="field-hint" style="line-height:1.7;margin:0 0 12px">
            当前使用客户授权 TDX API 与免费公开源组成字段级主备。“当前调用失败”与“尚未调用”严格区分；增强源失败只降级对应模块，不会把股票误判为不存在。盘后可复用当日成功快照，界面会标明数据日期，不冒充实时行情。
            ${sourceReport.infra?.relay?.enabled ? `当前中继：${escapeHtml(sourceReport.infra.relay.transport || '已配置')}` : '当前未启用数据中继，东财链路仅在本机直连可用时参与。'}
          </div>
          ${fieldContractRows.length ? `<details style="margin:0 0 12px">
            <summary style="cursor:pointer;font-size:13px;font-weight:700">
              标准字段契约 · ${escapeHtml(fieldContract.schema_version || 'field-contract')} · ${fieldContractRows.length} 个字段
            </summary>
            <div style="overflow:auto;margin-top:8px">
              <table class="data-table" style="min-width:980px;width:100%">
                <thead><tr><th>字段</th><th>业务含义</th><th>单位/值语义</th><th>主备路由</th><th>新鲜度门槛</th><th>批次范围</th></tr></thead>
                <tbody>${fieldContractRows.map(([field, meta]) => `<tr>
                  <td><code>${escapeHtml(field)}</code></td>
                  <td style="max-width:320px">${escapeHtml(meta.meaning || '未声明')}</td>
                  <td>${escapeHtml(meta.unit || '—')}<div style="font-size:11px;color:var(--text-3)">${escapeHtml(meta.value_semantics || '—')}</div></td>
                  <td>${(meta.route || []).map((source) => `<code>${escapeHtml(source)}</code>`).join(' → ') || '—'}</td>
                  <td>实时 ≤ ${Number(meta.live_max_age_seconds || 0)}s<div style="font-size:11px;color:var(--text-3)">延迟 ≤ ${Number(meta.delayed_max_age_seconds || 0)}s</div></td>
                  <td>${escapeHtml(meta.batch_scope || '—')}</td>
                </tr>`).join('')}</tbody>
              </table>
            </div>
          </details>` : ''}
          ${deployment.profile ? `<div class="set-row" style="margin:0 0 12px;padding:10px 12px;border:1px solid var(--line);border-radius:9px">
            <span class="sr-label">部署地域与源路由<br><small style="color:var(--text-3)">配置 ${escapeHtml(deployment.configured || 'auto')} · 实际 ${escapeHtml(deployment.profile)}</small></span>
            <span class="sr-val">
              <b>${escapeHtml(deploymentProfileLabel)}</b> · ${escapeHtml(deploymentRouteLabel)}<br>
              <small style="color:var(--text-3)">${escapeHtml(deployment.public_note || '未提供路由说明')}</small>
            </span>
          </div>` : ''}
          ${sourceReport.universe?.count ? `<div class="set-row" style="margin:0 0 12px;padding:10px 12px;border:1px solid var(--line);border-radius:9px">
            <span class="sr-label">全 A 股股票池<br><small style="color:var(--text-3)">${escapeHtml(sourceReport.universe.source || '免费公开源')} · as_of ${escapeHtml(sourceReport.universe.as_of || '—')}</small>${universeAttemptText ? `<br><small style="color:var(--text-3)">本轮支路：${escapeHtml(universeAttemptText)}</small>` : ''}</span>
            <span class="sr-val ${sourceReport.universe.stale || sourceReport.universe.refresh_required ? 'set-warn' : 'set-ok'}">
              ${Number(sourceReport.universe.count || 0).toLocaleString('zh-CN')} 只 · ${sourceReport.universe.stale ? '陈旧' : '当前'}
              ${sourceReport.universe.refresh_required ? ` · 待刷新${(sourceReport.universe.missing_exchanges || []).length ? `（缺 ${(sourceReport.universe.missing_exchanges || []).map((x) => escapeHtml(String(x).toUpperCase())).join('/')}）` : ''}` : ''}
            </span>
          </div>` : `<div class="field-hint" style="margin:0 0 12px">股票池尚未采集，首次全市场筛选时会按免费交易所/TDX/last-good 顺序构建。</div>`}
          ${newsObservation.enabled ? `<div class="set-row" style="margin:0 0 12px;padding:10px 12px;border:1px solid var(--line);border-radius:9px">
            <span class="sr-label">新闻事件观察账本<br><small style="color:var(--text-3)">最近任务 ${escapeHtml(newsObservation.last_finished || newsObservation.last_run || '—')} · 任务成功与数据完整分开判定</small></span>
            <span class="sr-val ${newsObservation.last_status === 'warn' || Number(newsCheckpointHealth.terminal_missing_unique || 0) ? 'set-warn' : 'set-ok'}">
              跟踪 ${Number(newsObservation.tracked || 0)} 条 · 已完成 ${Number(newsObservation.observed || 0)} · 观察中 ${Number(newsObservation.observing || 0)} · 不可比 ${Number(newsObservation.incomparable || 0)}<br>
              检查点成功 ${Number(newsCheckpointHealth.recorded_unique || 0)} / ${Number(newsCheckpointHealth.attempted_unique || 0)}
              ${Number(newsCheckpointHealth.terminal_missing_unique || 0) ? ` · 永久缺失 ${Number(newsCheckpointHealth.terminal_missing_unique || 0)}` : ''}
              ${newsObservation.last_error ? `<br><small>${escapeHtml(newsObservation.last_error)}</small>` : ''}
            </span>
          </div>` : ''}
          <table class="data-table" style="min-width:1080px;width:100%">
            <thead><tr><th>字段链路</th><th>供应商</th><th>用途</th><th>角色</th><th>状态</th><th>最近耗时</th><th>最近记录</th></tr></thead>
            <tbody>${(sourceReport.sources || []).map((source) => `<tr>
              <td><b>${escapeHtml(source.name || '—')}</b></td>
              <td>${escapeHtml(source.provider || '—')}</td>
              <td style="max-width:280px">${escapeHtml(source.feeds || '—')}</td>
              <td>${escapeHtml(source.role || '—')}</td>
              <td>${sourceState(source)}${source.note ? `<div style="font-size:11px;color:var(--text-3);max-width:240px">${escapeHtml(source.note)}</div>` : ''}</td>
              <td class="num ${Number(source.latency_ms || 0) >= 5000 ? 'set-warn' : ''}">${formatDuration(source.latency_ms)}</td>
              <td class="num">${escapeHtml(source.last_ts || '—')}</td>
            </tr>`).join('')}</tbody>
          </table>
          ${(sourceReport.evidence_registry || []).length ? `
            <div style="display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin:18px 0 8px">
              <b style="font-size:13px">字段级证据登记</b>
              <span style="font-size:11px;color:var(--text-3)">
                已存 ${Number(sourceReport.evidence_stats?.evidence_points || 0)} 条证据 · 原始载荷 ${Number(sourceReport.evidence_stats?.raw_payloads || 0)} 份
              </span>
            </div>
            <table class="data-table" style="min-width:980px;width:100%">
              <thead><tr><th>source_id</th><th>Tier</th><th>运行态</th><th>标准字段</th><th>成本</th><th>授权/条款</th><th>已知风险</th></tr></thead>
              <tbody>${sourceReport.evidence_registry.map((source) => `<tr>
                <td><b>${escapeHtml(source.source_id || '—')}</b><div style="font-size:11px;color:var(--text-3)">${escapeHtml(source.provider || '')}</div></td>
                <td class="num">T${escapeHtml(source.tier ?? '—')}</td>
                <td>${runtimeSourceState(source.source_id)}</td>
                <td style="max-width:360px">${(source.fields || []).map((f) => `<code>${escapeHtml(f)}</code>`).join(' ') || '—'}</td>
                <td>${escapeHtml(source.cost || '—')}</td>
                <td style="max-width:240px">${escapeHtml(source.license || '—')}</td>
                <td style="max-width:260px;color:var(--text-3)">${escapeHtml(source.known_risks || '—')}</td>
              </tr>`).join('')}</tbody>
            </table>` : ''}
        </div>
      </section>` : `
      <section class="card hoverable" style="grid-column:1/-1">
        <div class="card-head"><h3 class="card-title">数据源与缺口</h3></div>
        <div class="card-body" style="color:var(--text-3);font-size:13px">无法读取数据源健康信息；请确认后端已启动后刷新。本页不会把未知状态显示为“正常”。</div>
      </section>`;

  const jobState = (j) => {
    const state = String(j?.last_result?.status || j?.last_status || '');
    const runDate = String(j?.last_run || j?.last_result?.started_at || '').slice(0, 10);
    const isHistory = Boolean(runDate && status?.date && runDate !== String(status.date));
    if (j?.enabled === false) return '<span class="set-neutral">未启用</span>';
    if (state === 'error') return `<span class="${isHistory ? 'set-neutral' : 'set-bad'}">${isHistory ? '上次失败（历史）' : '今日运行失败'}</span>`;
    if (state === 'interrupted') return `<span class="${isHistory ? 'set-neutral' : 'set-bad'}">${isHistory ? '上次中断（历史）' : '今日运行中断'}</span>`;
    if (state === 'blocked') return `<span class="${isHistory ? 'set-neutral' : 'set-warn'}">${isHistory ? '上次阻止（历史）' : '今日已阻止'}</span>`;
    if (state === 'warn') return `<span class="${isHistory ? 'set-neutral' : 'set-warn'}">${isHistory ? '上次告警（历史）' : '今日需要关注'}</span>`;
    if (state === 'running') return '<span class="set-warn">运行中</span>';
    if (state === 'ok') return `<span class="${isHistory ? 'set-neutral' : 'set-ok'}">${isHistory ? '上次成功（历史）' : '今日已成功'}</span>`;
    return '<span style="color:var(--text-3)">尚未运行</span>';
  };

  // 自定义连接列表来自专用 API；GET settings 的同名字段只作读取失败时的同契约备用。
  const modelConnections = Array.isArray(modelConnectionsReport?.connections)
    ? modelConnectionsReport.connections
    : (Array.isArray(settings.model_connections) ? settings.model_connections : []);
  const activeModelConnectionId = modelConnectionsReport?.active_connection_id
    ?? settings.active_model_connection_id ?? null;
  const activeModelConnection = modelConnections.find((row) => row.id === activeModelConnectionId) || null;
  if (editingModelConnectionId === undefined) editingModelConnectionId = activeModelConnectionId || null;
  if (editingModelConnectionId && !modelConnections.some((row) => row.id === editingModelConnectionId)) {
    editingModelConnectionId = activeModelConnectionId || null;
  }
  const editingModelConnection = modelConnections.find((row) => row.id === editingModelConnectionId) || null;
  const modelCustom = Boolean(activeModelConnectionId && activeModelConnection);
  const keyPh = editingModelConnection?.api_key_set ? '已安全保存——留空表示沿用' : '公网模型服务请填写 API Key';
  const keyHint = editingModelConnection?.api_key_set
    ? '<div class="field-hint">密钥不会回显；输入新值可更换，留空保持不变。</div>' : '';
  // 飞书签名密钥同理（仅 feishu 推送模式用）
  const secretPh = settings.feishu_secret_set ? '已配置——留空表示不修改' : '留空表示不加签';
  const secretHint = settings.feishu_secret_set ? '<div class="field-hint">当前已配置签名密钥；输入新值可覆盖，留空保持不变</div>' : '';
  const webhookPh = settings.feishu_webhook_set ? '已配置——留空表示不修改' : 'https://open.feishu.cn/open-apis/bot/v2/hook/…';
  const webhookHint = settings.feishu_webhook_set ? '<div class="field-hint">Webhook 已安全隐藏；输入新地址可覆盖，留空保持不变</div>' : '';
  const manualDeliveries = (deliveryOutbox?.items || []).filter((item) =>
    ['sending', 'unknown', 'partial', 'failed_terminal'].includes(item.status));
  const agentCapability = hermes.agent_capability || {};
  const agentRuns = agentRunReport?.items || [];
  const hermesRuntimeMode = String(agentCapability.runtime_mode || '');
  const hermesIntegrationActive = Boolean(
    hermes.connected === true
    && hermesRuntimeMode
    && hermesRuntimeMode !== 'direct_model_api'
  );
  const runStatusLabel = (row) => {
    const statusName = String(row.status || 'unknown');
    if (row.safe_to_use) return '<span class="set-ok">完整可用</span>';
    if (row.partial_output || statusName === 'partial') return '<span class="set-bad">部分输出·不可用</span>';
    if (statusName === 'blocked') return '<span class="set-bad">权限阻断</span>';
    if (statusName === 'failed') return '<span class="set-bad">模型运行失败</span>';
    if (statusName === 'cancelled') return '<span class="set-warn">已取消·不可用</span>';
    if (['unknown', 'interrupted'].includes(statusName)) return '<span class="set-warn">需人工复核</span>';
    return `<span style="color:var(--text-3)">${escapeHtml(statusName)}</span>`;
  };
  const unexpectedToolsets = !hermesIntegrationActive
    ? '未启用可选集成'
    : (!agentCapability.ready
      ? '未知·权限探测未通过'
    : ((agentCapability.unexpected_toolsets || []).length
      ? escapeHtml(agentCapability.unexpected_toolsets.join('、')) : '无'));
  const agentHeadline = !hermesIntegrationActive
    ? { color: 'var(--text-3)', label: '未启用·可选' }
    : (agentCapability.ready === true
      ? { color: 'var(--down)', label: '权限已收敛' }
      : (agentCapability.ready === false
        ? { color: 'var(--up)', label: '已接入但权限未通过' }
        : { color: 'var(--warn)', label: '已接入·权限待核验' }));
  const agentCard = `
      <section class="card hoverable" style="grid-column:1/-1">
        <div class="card-head"><h3 class="card-title">Agent 权限与运行账本</h3>
          <div class="card-extra"><span style="color:${agentHeadline.color}">${agentHeadline.label}</span></div>
        </div>
        <div class="card-body">
          <div class="field-hint" style="line-height:1.7;margin-bottom:12px">
            模型只负责证据解释；动作、置信度、仓位和交付由确定性规则与中央风险闸决定。
            运行账本只保存哈希、字节数和终态，不保存 prompt、工具参数或模型原文。
          </div>
          <div class="settings-grid" style="margin-bottom:12px">
            <div class="set-row"><span class="sr-label">运行模式</span><span class="sr-val">${escapeHtml(agentCapability.runtime_mode || '未知')}</span></div>
            <div class="set-row"><span class="sr-label">项目 profile</span><span class="sr-val">${escapeHtml(agentCapability.profile_attestation || (agentCapability.runtime_mode === 'direct_model_api' ? '直连模型' : '未核验'))}</span></div>
            <div class="set-row"><span class="sr-label">进程证明</span><span class="sr-val">${escapeHtml(agentCapability.process_attestation || (agentCapability.runtime_mode === 'direct_model_api' ? '无需代理证明' : '未绑定'))}</span></div>
            <div class="set-row"><span class="sr-label">权限结论</span><span class="sr-val">${escapeHtml(agentCapability.reason || '无法读取')}</span></div>
            <div class="set-row"><span class="sr-label">未授权工具集</span><span class="sr-val">${unexpectedToolsets}</span></div>
          </div>
          ${agentRunReport ? `<div style="overflow:auto"><table class="data-table" style="min-width:980px;width:100%">
            <thead><tr><th>运行 ID</th><th>开始时间</th><th>用途</th><th>模型</th><th>权限快照</th><th>尝试</th><th>工具事件</th><th>终态</th></tr></thead>
            <tbody>${agentRuns.map((row) => `<tr>
              <td><code>${escapeHtml(String(row.run_id || '—').slice(0, 8))}</code></td>
              <td class="num">${escapeHtml(row.started_at || '—')}</td>
              <td>${escapeHtml(row.purpose || '—')}</td>
              <td>${escapeHtml(row.model || '—')}</td>
              <td>${escapeHtml(row.capability_status || '—')}</td>
              <td class="num">${Number(row.attempts || 0)}</td>
              <td class="num">${Number(row.tool_events || 0)}</td>
              <td>${runStatusLabel(row)}<div style="font-size:11px;color:var(--text-3)">${escapeHtml(row.error_code || row.finish_reason || '—')}</div></td>
            </tr>`).join('') || '<tr><td colspan="8" style="color:var(--text-3)">尚无 Agent 运行记录</td></tr>'}</tbody>
          </table></div>` : '<div style="color:var(--down);font-size:13px">Agent 运行账本加载失败，不会伪装成空记录。</div>'}
        </div>
      </section>`;

  // 推送卡：gateway 模式 = 默认走网关（不在此配渠道）；feishu 模式 = 保留 webhook 配置
  const pushCard = pushVia === 'gateway' ? `
      <section class="card hoverable">
        <div class="card-head"><h3 class="card-title">接收设置</h3>
          <div class="card-extra"><span class="status-dot ${deliveryGate?.configured ? 'on' : ''}"><span class="dot"></span>${deliveryGate?.configured ? '已接入可选集成网关' : '网关未配置'}</span></div>
        </div>
        <div class="card-body">
          <div class="set-row"><span class="sr-label">推送方式</span><span class="sr-val">经可选集成网关投递</span></div>
          <div class="field-hint" style="margin:2px 0 12px">自动识别集成网关已连渠道（微信 / 企业微信 / 飞书…）。渠道由集成网关单独管理。</div>
          <div class="set-row">
            <span class="sr-label">自动推送报告</span>
            <label class="switch"><input type="checkbox" id="f-push" ${settings.push_enabled ? 'checked' : ''}><span class="track"></span></label>
          </div>
          <button class="btn btn-sm" id="f-test" style="margin-top:10px">${icon('fly')}发送测试消息</button>
        </div>
      </section>` : `
      <section class="card hoverable">
        <div class="card-head"><h3 class="card-title">接收设置（飞书）</h3>
          <div class="card-extra"><span class="status-dot ${feishu.configured ? 'on' : ''}"><span class="dot"></span>${feishu.configured ? '已配置' : '未配置'}</span></div>
        </div>
        <div class="card-body">
          <div class="field">
            <label class="field-label">Webhook 地址</label>
            <input class="input" id="f-webhook" value="" placeholder="${webhookPh}">
            ${webhookHint}
          </div>
          <div class="field">
            <label class="field-label">签名密钥（可选）</label>
            <input class="input" id="f-secret" type="password" value="" placeholder="${secretPh}">
            ${secretHint}
          </div>
          <div class="set-row">
            <span class="sr-label">自动推送报告</span>
            <label class="switch"><input type="checkbox" id="f-push" ${settings.push_enabled ? 'checked' : ''}><span class="track"></span></label>
          </div>
          <button class="btn btn-sm" id="f-test" style="margin-top:10px">${icon('fly')}发送测试消息</button>
        </div>
      </section>`;

  const jobStatusValue = (job) => String(job?.last_result?.status || job?.last_status || '');
  const jobRunDate = (job) => String(job?.last_run || job?.last_result?.started_at || '').slice(0, 10);
  const isTodayJob = (job) => Boolean(status?.date && jobRunDate(job) === String(status.date));
  const failedJobsAll = jobs.filter((job) => ['error', 'interrupted'].includes(jobStatusValue(job)));
  const blockedJobsAll = jobs.filter((job) => ['blocked', 'warn'].includes(jobStatusValue(job)));
  const failedJobs = failedJobsAll.filter(isTodayJob);
  const blockedJobs = blockedJobsAll.filter(isTodayJob);
  const historicalFailedJobs = failedJobsAll.filter((job) => !isTodayJob(job));
  const historicalBlockedJobs = blockedJobsAll.filter((job) => !isTodayJob(job));
  const pendingJobs = jobs.filter((job) => !jobStatusValue(job) || jobStatusValue(job) === 'never');
  const autoSendEnabled = settings.push_enabled === true;
  const autoSendReady = autoSendEnabled && deliveryGate?.launch_ready === true;
  const autoSendNeedsSetup = autoSendEnabled && !autoSendReady;
  const deliveryIssues = (deliveryGate?.blockers || []).filter((code) => code !== 'scheduled_push_disabled');
  const deliveryIssueLabels = deliveryIssues.map(deliveryBlockerLabel);
  const newestJob = (rows) => (rows || []).slice().sort((a, b) => {
    const at = String(a?.last_result?.finished_at || a?.last_ok || a?.last_run || '');
    const bt = String(b?.last_result?.finished_at || b?.last_ok || b?.last_run || '');
    return bt.localeCompare(at);
  })[0] || null;
  const reportJobKeys = new Set(['morning', 'midday', 'tail', 'close', 'evening']);
  const webJobs = jobs.filter((job) => reportJobKeys.has(jobIdentity(job).key));
  const pdfJobs = jobs.filter((job) => ['pdf_premarket', 'pdf_postmarket'].includes(jobIdentity(job).key));
  const latestWebJob = newestJob(webJobs);
  const latestPdfJob = newestJob(pdfJobs);
  const channelContracts = new Map(
    (Array.isArray(deliveryGate?.channels) ? deliveryGate.channels : [])
      .filter((row) => row?.key).map((row) => [String(row.key), row])
  );
  const modelConnectionsUnavailable = !modelConnectionsReport && !Array.isArray(settings.model_connections);
  const severity = (bad, warn) => bad ? 'bad' : (warn ? 'warn' : 'ok');
  const taskSeverity = severity(failedJobs.length, blockedJobs.length || pendingJobs.length || historicalFailedJobs.length || historicalBlockedJobs.length);
  const dataSeverity = severity(coreDownSources.length, !sourceReport || enhancementDownSources.length || degradedSources.length || idleSources.length || slowSources.length);
  const deliverySeverity = severity(autoSendNeedsSetup, false);
  const securitySeverity = severity(false, modelConnectionsUnavailable);
  const overview = [
    { k: '任务运行', level: taskSeverity, v: failedJobs.length ? `今日 ${failedJobs.length} 个任务失败` : (blockedJobs.length ? `今日 ${blockedJobs.length} 个任务需关注` : '今日尚无失败'), s: historicalFailedJobs.length || historicalBlockedJobs.length ? `另有 ${historicalFailedJobs.length + historicalBlockedJobs.length} 条上一交易日异常，已标为历史` : `待首次运行 ${pendingJobs.length} 个` },
    { k: '数据健康', level: dataSeverity, v: coreDownSources.length ? `${coreDownSources.length} 个基础链路失败` : (enhancementDownSources.length ? `基础行情可用 · ${enhancementDownSources.length} 个增强源降级` : (sourceReport ? '基础行情可用' : '健康信息不可用')), s: sourceReport ? `${sourceRows.length} 条链路 · ${slowSources.length} 个慢源 · ${idleSources.length} 个未采集` : '需确认后端数据服务' },
    { k: '自动发送', level: deliverySeverity, v: !autoSendEnabled ? '未开启' : (autoSendReady ? '已就绪' : '需要完善配置'), s: !autoSendEnabled ? '报告保留在系统内，不会自动对外发送' : (autoSendReady ? '接收渠道、目标和回执检查已通过' : escapeHtml(deliveryIssueLabels[0] || '请完善接收渠道')) },
    { k: '模型连接', level: securitySeverity, v: modelConnectionsUnavailable ? '连接状态暂不可读' : (!modelCustom ? '未启用自定义连接' : '已启用自定义连接'), s: !modelCustom ? '核心规则功能仍可使用；Hermes 为可选集成' : '可在“安全与版本”中测试、更换或删除' },
  ];

  const deliveryStateLabel = !autoSendEnabled ? '未开启' : (autoSendReady ? '已就绪' : '需要配置');
  const deliveryStateColor = !autoSendEnabled ? 'var(--text-2)' : (autoSendReady ? 'var(--down)' : 'var(--up)');
  const deliveryCta = !autoSendEnabled ? '开启自动发送' : (
    deliveryIssues.includes('delivery_manual_review_required') ? '查看待确认记录' : '配置接收渠道'
  );
  const receiptTime = (row) => row?.sent_at || row?.attempted_at || row?.updated_at || '—';
  const receiptStatus = (row) => {
    const value = String(row?.projected_status || row?.status || '');
    if (DELIVERY_SUCCESS.has(value)) return { cls: 'set-ok', label: '最近成功' };
    if (DELIVERY_REVIEW.has(value)) return { cls: 'set-warn', label: '结果待核验' };
    if (DELIVERY_FAILED.has(value)) return { cls: 'set-bad', label: '最近失败' };
    return { cls: 'set-neutral', label: '尚无发送记录' };
  };
  const channelRows = [
    { key: 'web', fallbackLabel: 'Web 页面', fallbackRole: 'internal_view', job: latestWebJob },
    { key: 'pdf', fallbackLabel: 'PDF 文件', fallbackRole: 'generated_file', job: latestPdfJob },
    { key: 'feishu', fallbackLabel: '飞书', fallbackRole: 'external_message', job: null },
    { key: 'wechat', fallbackLabel: '微信', fallbackRole: 'external_message', job: null },
  ].map((base) => {
    const contract = channelContracts.get(base.key) || {};
    return {
      ...base,
      ...contract,
      label: contract.label || base.fallbackLabel,
      role: contract.role || contract.kind || base.fallbackRole,
      state: contract.state || contract.status || 'unknown',
      lastSuccess: contract.last_success_at || contract.last_success || null,
      receipt: latestChannelReceipt(receipts, base.key),
    };
  });
  const channelConfigLabel = (row) => {
    if (!channelContracts.has(row.key)) return '<span class="set-neutral">状态未返回</span>';
    if (row.configured !== true) return '<span class="set-neutral">未配置</span>';
    if (['web', 'pdf'].includes(row.key)) return '<span class="set-ok">已配置·内置</span>';
    return row.enabled === true ? '<span class="set-ok">已配置·已开启</span>' : '<span class="set-neutral">已配置·未开启</span>';
  };
  const channelLatest = (row) => {
    if (!channelContracts.has(row.key)) return { cls: 'set-neutral', label: '接口未返回' };
    if (row.receipt) {
      const result = receiptStatus(row.receipt);
      if (result.label !== '尚无发送记录') return result;
    }
    if (row.state === 'unconfigured') return { cls: 'set-neutral', label: '未配置' };
    if (row.state === 'disabled') return { cls: 'set-neutral', label: '能力可用·自动发送未开启' };
    if (row.state === 'needs_attention') return { cls: 'set-warn', label: '需要检查配置' };
    if (['web', 'pdf'].includes(row.key) && !row.lastSuccess) return { cls: 'set-neutral', label: '能力可用·待首次生成' };
    if (row.state === 'ready' && row.lastSuccess) return { cls: 'set-ok', label: '最近成功' };
    if (row.state === 'ready') return { cls: 'set-neutral', label: '能力可用·待首次发送' };
    return { cls: 'set-neutral', label: '状态未知' };
  };
  const channelError = (row) => row.receipt
    ? (row.receipt.projected_error || row.receipt.error || row.receipt.error_code || row.latest_receipt?.error_code || '—')
    : (row.latest_receipt?.error_code || '—');
  const channelRetry = (row) => {
    const projectedState = String(row.receipt?.projected_status || row.receipt?.status || '');
    const deliveryState = String(row.receipt?.status || projectedState);
    if (row.receipt?.delivery_id && !DELIVERY_SUCCESS.has(projectedState)) {
      return `<button class="btn btn-sm btn-ghost" data-delivery-id="${escapeHtml(row.receipt.delivery_id)}" data-delivery-status="${escapeHtml(deliveryState)}">核验后重试</button>`;
    }
    const retryableJob = row.job?.retry?.eligible === true || ['error', 'blocked'].includes(String(row.job?.last_status || ''));
    if (retryableJob && row.job?.id) return `<button class="btn btn-sm btn-ghost" data-job-run="${escapeHtml(row.job.id)}">重试任务</button>`;
    return '<span class="set-neutral">无待重试项</span>';
  };
  const deliveryChannelsCard = `
    <section class="card hoverable full"><div class="card-head"><h3 class="card-title">分渠道自动发送状态</h3>
      <div class="card-extra"><span style="font-size:12px;color:var(--text-3)">配置状态 ≠ 已送达</span></div></div>
      <div class="card-body" style="overflow:auto"><table class="data-table" style="min-width:900px;width:100%">
        <thead><tr><th>渠道</th><th>配置</th><th>最近状态</th><th>最近成功</th><th>失败 / 待核验原因</th><th>可重试</th></tr></thead>
        <tbody>${channelRows.map((row) => {
          const latest = channelLatest(row);
          const roleLabel = { internal_view: '系统内置展示', generated_file: '系统生成文件', external_message: '外部消息渠道' }[row.role] || row.role || '—';
          return `<tr><td><b>${escapeHtml(row.label)}</b><div style="font-size:11px;color:var(--text-3)">${escapeHtml(roleLabel)}</div></td>
            <td>${channelConfigLabel(row)}</td><td><span class="${latest.cls}">${latest.label}</span></td>
            <td class="num">${escapeHtml(row.lastSuccess || (['web', 'pdf'].includes(row.key) ? '待首次生成' : '尚无成功记录'))}</td><td style="max-width:260px">${escapeHtml(channelError(row))}</td><td>${channelRetry(row)}</td></tr>`;
        }).join('')}</tbody></table>
        ${channelContracts.size ? '' : '<div class="set-status-note">当前自动发送接口未返回 Web/PDF/飞书/微信的独立状态，本页不作推断。</div>'}
      </div></section>`;
  const deliveryGateCard = `
    <section class="card hoverable">
      <div class="card-head"><h3 class="card-title">自动发送状态</h3>
        <div class="card-extra"><span style="color:${deliveryStateColor}">${deliveryStateLabel}</span></div></div>
      <div class="card-body">${deliveryGate ? `
        <div class="set-row"><span class="sr-label">自动发送</span><span class="sr-val">${autoSendEnabled ? '<span class="set-ok">已开启</span>' : '<span class="set-neutral">未开启</span>'}</span></div>
        <div class="set-row"><span class="sr-label">接收渠道 / 目标</span><span class="sr-val">${escapeHtml(deliveryGate.channel || '尚未设置')} · ${escapeHtml((deliveryGate.targets || []).join('、') || '尚未选择')}</span></div>
        <div class="set-row"><span class="sr-label">待人工确认</span><span class="sr-val" style="color:${Number(deliveryGate.manual_review_count || 0) ? 'var(--up)' : 'var(--text-2)'}">${Number(deliveryGate.manual_review_count || 0)} 条</span></div>
        ${!autoSendEnabled ? '<div class="set-status-note">当前报告只保留在系统内，不会自动发给外部接收人。这是正常的关闭状态。</div>' : (deliveryIssueLabels.length ? `<div class="set-status-note set-bad">需处理：${deliveryIssueLabels.map(escapeHtml).join('、')}</div>` : '<div class="set-status-note set-ok">接收渠道、目标和发送记录检查均通过。</div>')}
        ${(!autoSendReady) ? `<button type="button" class="btn btn-sm" data-delivery-config style="margin-top:10px">${deliveryCta}</button>` : ''}
        ${manualDeliveries.length ? `<div style="overflow:auto;margin-top:12px"><table class="data-table" style="min-width:920px;width:100%"><thead><tr><th>待核验内容</th><th>状态</th><th>尝试</th><th>更新时间</th><th>人工处置</th></tr></thead><tbody>${manualDeliveries.map((item) => `<tr>
          <td><b>${escapeHtml(item.content_id || item.delivery_id)}</b></td><td style="color:var(--up)">${escapeHtml(item.status || '—')}</td><td class="num">#${escapeHtml(item.attempts ?? '—')}</td><td class="num">${escapeHtml(item.updated_at || '—')}</td>
          <td style="white-space:nowrap">${['sending', 'unknown', 'partial'].includes(item.status) ? `<button class="btn btn-sm btn-ghost" data-delivery-ack="${escapeHtml(item.delivery_id)}">确认外部已接收</button>` : ''}<button class="btn btn-sm btn-ghost" data-delivery-id="${escapeHtml(item.delivery_id)}" data-delivery-status="${escapeHtml(item.status || '')}">核验后重试</button></td></tr>`).join('')}</tbody></table></div>` : ''}`
        : '<div class="set-bad">自动发送状态暂时无法读取，本次不会自动发送。</div>'}</div>
    </section>`;

  const receiptsCard = `
    <section class="card hoverable full"><div class="card-head"><h3 class="card-title">交付回执</h3><div class="card-extra"><span style="font-size:12px;color:var(--text-3)">生成成功 ≠ 已送达</span></div></div>
      <div class="card-body" style="overflow:auto">${(deliveryReport?.receipts || []).length ? `<table class="data-table" style="min-width:960px;width:100%"><thead><tr><th>内容</th><th>渠道</th><th>尝试</th><th>状态</th><th>发送时间</th><th>错误</th><th></th></tr></thead><tbody>${deliveryReport.receipts.map((receipt) => {
        const ok = ['accepted', 'delivered', 'acknowledged'].includes(receipt.status);
        const partial = receipt.status === 'partial';
        const statusColor = ok ? 'var(--down)' : (partial ? 'var(--text-2)' : 'var(--up)');
        return `<tr><td><b>${escapeHtml(receipt.content_id || '—')}</b>${receipt.plan_id ? `<div style="font-size:11px;color:var(--text-3)">${escapeHtml(receipt.plan_id)} · v${escapeHtml(receipt.version ?? '—')}</div>` : ''}</td><td>${escapeHtml(receipt.channel || '—')}</td><td class="num">#${escapeHtml(receipt.attempt ?? '—')}</td><td><span style="color:${statusColor}">${ok ? (receipt.status === 'acknowledged' ? '已确认' : (receipt.status === 'accepted' ? '平台已接受' : '已送达')) : (partial ? '部分目标成功' : (receipt.status === 'unknown' ? '结果未知·待核验' : '失败'))}</span></td><td class="num">${escapeHtml(receipt.sent_at || receipt.attempted_at || '—')}</td><td class="${receipt.error || receipt.error_code ? 'set-bad' : ''}" style="max-width:280px">${escapeHtml(receipt.error || receipt.error_code || '—')}</td><td>${!ok && receipt.delivery_id ? `<button class="btn btn-sm btn-ghost" data-delivery-id="${escapeHtml(receipt.delivery_id)}" data-delivery-status="${escapeHtml(receipt.status || '')}">重试</button>` : ''}</td></tr>`;
      }).join('')}</tbody></table>` : '<div style="font-size:13px;color:var(--text-3)">暂无交付尝试记录。首份报告推送后显示送达结果与失败原因。</div>'}</div>
    </section>`;

  const renderJobOutputs = (job) => {
    const outputs = jobOutputObjects(job);
    if (!outputs.length) return '<span class="set-neutral">尚无产物</span>';
    return `<div class="set-job-outputs">${outputs.map((row) => {
      const kind = row.kind || row.report_type || '任务产物';
      const contentId = row.content_id || '—';
      const details = [
        row.trade_day ? `交易日 ${row.trade_day}` : '',
        row.version != null ? `v${row.version}` : '',
        row.content_hash ? `hash ${String(row.content_hash).slice(0, 12)}` : '',
        Array.isArray(row.report_types) && row.report_types.length ? `包含 ${row.report_types.join('、')}` : '',
      ].filter(Boolean).join(' · ');
      return `<div class="set-job-output"><b>${escapeHtml(kind)}</b><code>${escapeHtml(contentId)}</code>${details ? `<small>${escapeHtml(details)}</small>` : ''}</div>`;
    }).join('')}</div>`;
  };
  const taskRows = jobs.map((j) => {
    const info = jobDisplay(j);
    const result = j.last_result || {};
    const retryable = j.retry?.eligible === true;
    const runnable = j.manual_run?.enabled !== false && j.enabled !== false;
    const error = result.error || result.error_code || j.last_error || j.last_error_code;
    return `<tr><td><b title="任务标识：${escapeHtml(info.technical)}">${escapeHtml(info.label)}</b><details class="set-job-meta"><summary>技术信息</summary><code>${escapeHtml(info.technical)}</code></details></td>
      <td>${j.kind === 'extension' ? '客户扩展' : (j.kind === 'system' ? '系统' : '核心')}</td>
      <td>${jobState(j)}</td><td class="num">${escapeHtml(j.next_run_at || j.next_run || '—')}</td>
      <td class="num">${escapeHtml(j.last_ok || '—')}</td><td class="num">${formatDuration(result.duration_ms ?? j.last_duration_ms)}</td>
      <td style="max-width:220px" class="${error ? 'set-bad' : 'set-neutral'}">${escapeHtml(error || '—')}</td>
      <td style="min-width:240px">${renderJobOutputs(j)}</td><td>${runnable ? `<button class="btn btn-sm btn-ghost" data-job-run="${escapeHtml(j.id)}">${retryable ? '重试' : '手动执行'}</button>` : '<span class="set-neutral">不可手动执行</span>'}</td></tr>`;
  }).join('');

  const tab = (id, label, level) => `<button type="button" class="set-tab${activeSection === id ? ' active' : ''}" role="tab" aria-selected="${activeSection === id}" aria-controls="set-panel-${id}" tabindex="${activeSection === id ? '0' : '-1'}" data-set-tab="${id}"><span class="set-tab-state ${level}" aria-hidden="true"></span>${label}</button>`;
  wrap.innerHTML = `
    <div class="set-tabs" role="tablist" aria-label="系统设置分类">${tab('tasks', '任务中心', taskSeverity)}${tab('data', '数据健康', dataSeverity)}${tab('delivery', '交付中心', deliverySeverity)}${tab('security', '安全与版本', securitySeverity)}</div>

    <section class="set-panel" id="set-panel-tasks" role="tabpanel" data-set-panel="tasks" ${activeSection === 'tasks' ? '' : 'hidden'}>
      <div class="set-section-head"><div><h3>运行概览</h3><p>先看需处理的红黄问题，再管理任务。</p></div></div>
      <div class="set-overview">${overview.map((item) => `<div class="set-overview-item ${item.level}"><div class="k">${item.k}</div><div class="v">${item.v}</div><div class="s">${item.s}</div></div>`).join('')}</div>
      <div class="set-section-grid">
        <section class="card hoverable"><div class="card-head"><h3 class="card-title">定时任务时间</h3></div><div class="card-body"><div class="sched-grid">${Object.entries(SCHED_LABELS).map(([k, label]) => `<div class="field" style="margin-bottom:4px"><label class="field-label" for="sched-${k}">${label}</label><input id="sched-${k}" type="time" class="input" data-sched="${k}" value="${escapeHtml(sch[k] ?? '')}"></div>`).join('')}</div><div class="field-hint" style="margin-top:8px">保存后热生效，无需重启。</div></div></section>
        <section class="card hoverable"><div class="card-head"><h3 class="card-title">任务结论</h3></div><div class="card-body"><div class="set-row"><span class="sr-label">今日运行失败</span><span class="sr-val ${failedJobs.length ? 'set-bad' : 'set-ok'}">${failedJobs.length} 个</span></div><div class="set-row"><span class="sr-label">今日需关注 / 已阻止</span><span class="sr-val ${blockedJobs.length ? 'set-warn' : 'set-ok'}">${blockedJobs.length} 个</span></div><div class="set-row"><span class="sr-label">历史异常（不等于当前失败）</span><span class="sr-val ${historicalFailedJobs.length + historicalBlockedJobs.length ? 'set-warn' : 'set-ok'}">${historicalFailedJobs.length + historicalBlockedJobs.length} 个</span></div><div class="set-row"><span class="sr-label">待首次运行</span><span class="sr-val ${pendingJobs.length ? 'set-warn' : 'set-ok'}">${pendingJobs.length} 个</span></div><div class="set-row"><span class="sr-label">已登记任务</span><span class="sr-val">${jobs.length} 个</span></div></div></section>
        <section class="card hoverable full"><div class="card-head"><h3 class="card-title">任务运行状态</h3><div class="card-extra"><span style="font-size:12px;color:var(--text-3)">核心、扩展与系统任务</span></div></div><div class="card-body" style="overflow:auto">${jobs.length ? `<table class="data-table" style="min-width:1380px;width:100%"><thead><tr><th>任务</th><th>类型</th><th>运行状态</th><th>下次运行</th><th>最近成功</th><th>耗时</th><th>错误</th><th>最近产物（实际输出对象）</th><th>操作</th></tr></thead><tbody>${taskRows}</tbody></table>` : '<div style="color:var(--text-3);font-size:13px">暂无任务状态</div>'}</div></section>
      </div><div class="set-actions"><button type="button" class="btn btn-primary" data-settings-save>${icon('save')}保存任务时间</button></div>
    </section>

    <section class="set-panel" id="set-panel-data" role="tabpanel" data-set-panel="data" ${activeSection === 'data' ? '' : 'hidden'}>
      <div class="set-section-head"><div><h3>数据健康</h3><p>查看字段级主备源、当前失败、慢源与影响范围。</p></div><span class="set-${dataSeverity}">● ${dataSeverity === 'ok' ? '无已知失败' : (dataSeverity === 'bad' ? '基础行情存在失败' : '基础可用，部分数据降级')}</span></div>${sourceCard}
    </section>

    <section class="set-panel" id="set-panel-delivery" role="tabpanel" data-set-panel="delivery" ${activeSection === 'delivery' ? '' : 'hidden'}>
      <div class="set-section-head"><div><h3>交付中心</h3><p>设置报告接收渠道，查看自动发送状态和历史回执。</p></div><span class="status-dot ${autoSendReady ? 'on' : (autoSendNeedsSetup ? 'off' : '')}"><span class="dot"></span>${deliveryStateLabel}</span></div><div class="set-section-grid">${pushCard}${deliveryGateCard}${deliveryChannelsCard}${receiptsCard}</div><div class="set-actions"><button type="button" class="btn btn-primary" data-settings-save>${icon('save')}保存发送设置</button></div>
    </section>

    <section class="set-panel" id="set-panel-security" role="tabpanel" data-set-panel="security" ${activeSection === 'security' ? '' : 'hidden'}>
      <div class="set-section-head"><div><h3>安全与版本</h3><p>管理自定义模型连接；Hermes 集成和 Agent 诊断收进高级区域。</p></div><span class="set-${securitySeverity}">● ${modelConnectionsUnavailable ? '连接状态暂不可读' : (modelCustom ? '自定义连接已启用' : '模型连接未启用（可选）')}</span></div>
      <div class="set-section-grid">
        <section class="card hoverable"><div class="card-head"><h3 class="card-title">系统信息</h3></div><div class="card-body">${status ? `${status.ui?.hide_client_info ? '' : `<div class="set-row"><span class="sr-label">客户</span><span class="sr-val">${escapeHtml(status.client?.name ?? '--')}</span></div>`}<div class="set-row"><span class="sr-label">版本</span><span class="sr-val num">${escapeHtml(status.version ?? '--')}</span></div><div class="set-row"><span class="sr-label">数据策略</span><span class="sr-val">${escapeHtml(status.data_source ?? '--')}</span></div><div class="set-row"><span class="sr-label">系统日期</span><span class="sr-val num">${escapeHtml(status.date ?? '--')}</span></div>` : '<div style="color:var(--text-3)">系统信息暂不可用</div>'}</div></section>
        <section class="card hoverable"><div class="card-head"><h3 class="card-title">自定义模型连接</h3><div class="card-extra"><span class="status-dot ${modelCustom ? 'on' : ''}"><span class="dot"></span>${modelCustom ? '已启用·请按需测试' : '未启用'}</span></div></div><div class="card-body"><div class="set-row"><span class="sr-label">核心决策</span><span class="sr-val set-ok">不依赖模型连接</span></div><div class="set-row"><span class="sr-label">当前连接</span><span class="sr-val">${activeModelConnection ? `${escapeHtml(activeModelConnection.name || '未命名')} · ${escapeHtml(activeModelConnection.provider || 'custom')} / ${escapeHtml(activeModelConnection.model || '—')}` : '未启用，需要时再开启'}</span></div><div class="field-hint" style="margin-top:10px">连接的可达性以“测试当前填写”结果为准；Hermes 状态不用来判定模型连接。未配置模型不会影响行情、风险和今日决策的确定性规则。</div></div></section>
        <section class="card hoverable full"><div class="card-head"><h3 class="card-title">自定义连接管理</h3><div class="card-extra"><button type="button" class="btn btn-sm btn-ghost" data-model-new>${icon('plus')}新建连接</button></div></div><div class="card-body">
          ${modelConnectionsReport ? '' : '<div class="set-status-note set-warn">专用连接 API 暂不可用，当前仅显示 GET settings 返回的同契约非敏感字段。</div>'}
          <div class="set-model-list">${modelConnections.length ? modelConnections.map((row) => `<div class="set-model-item ${row.active ? 'active' : ''}"><div class="set-model-item-head"><b>${escapeHtml(row.name || '未命名连接')}</b><span class="${row.active ? 'set-ok' : 'set-neutral'}">${row.active ? '当前使用' : '已保存'}</span></div><div class="set-model-item-meta">${escapeHtml(row.provider || 'custom')} · ${escapeHtml(row.model || '—')}<br>${escapeHtml(row.base_url || '—')}<br>API Key ${row.api_key_set ? '已安全保存' : '未保存'}</div><div class="set-model-actions"><button type="button" class="btn btn-sm btn-ghost" data-model-edit="${escapeHtml(row.id)}">编辑</button>${row.active ? '' : `<button type="button" class="btn btn-sm" data-model-activate="${escapeHtml(row.id)}">设为当前</button>`}<button type="button" class="btn btn-sm btn-danger" data-model-delete="${escapeHtml(row.id)}">删除</button></div></div>`).join('') : '<div class="set-status-note">尚未保存自定义连接。下方填写后可保存并设为当前连接。</div>'}</div>
          <div class="set-status-note" style="margin-bottom:12px"><b>${editingModelConnection ? `编辑：${escapeHtml(editingModelConnection.name || editingModelConnection.id)}` : '新建连接'}</b> · 密钥不回显，也不会在连接列表中传回。</div>
          <div class="field"><label class="field-label" for="h-name">连接名称</label><input class="input" id="h-name" value="${escapeHtml(editingModelConnection?.name || '')}" placeholder="例如 生产 DeepSeek"></div>
          <div class="field"><label class="field-label" for="h-provider">Provider</label><input class="input" id="h-provider" value="${escapeHtml(editingModelConnection?.provider || 'custom')}" placeholder="custom / ollama / vllm"></div>
          <div class="field"><label class="field-label" for="h-base">接口地址</label><input class="input" id="h-base" value="${escapeHtml(editingModelConnection?.base_url || '')}" placeholder="https://api.example.com/v1"><div class="field-hint">支持公网 HTTPS /v1；本机模型可使用 http://localhost:端口/v1。</div></div>
          <div class="field"><label class="field-label" for="h-key">API Key</label><input class="input" id="h-key" type="password" value="" autocomplete="new-password" placeholder="${keyPh}">${keyHint}</div>
          <div class="field"><label class="field-label" for="h-model">模型名</label><input class="input" id="h-model" value="${escapeHtml(editingModelConnection?.model || '')}" placeholder="例如 gpt-5.4-mini"></div>
          <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-2)"><input type="checkbox" id="h-activate" ${!editingModelConnection || editingModelConnection.active ? 'checked' : ''}>保存后设为当前连接</label>
          <div class="set-model-actions"><button type="button" class="btn btn-sm" id="h-test">${icon('refresh')}测试当前填写</button><button type="button" class="btn btn-sm btn-primary" id="h-save">${icon('save')}${editingModelConnection ? '保存更换' : '保存连接'}</button></div>
        </div></section>
        <details class="set-advanced full"><summary>可选集成 / 高级诊断（Hermes）</summary><div class="set-advanced-body"><div class="set-status-note">Hermes 是可选的 Agent 集成，不是普通功能的必经网关。未启用时是正常状态，不会标记为系统故障。</div><div class="settings-grid" style="margin:12px 0"><div class="set-row"><span class="sr-label">Agent 运行模式</span><span class="sr-val">${escapeHtml(hermesRuntimeMode || '未返回')}</span></div><div class="set-row"><span class="sr-label">Hermes 集成</span><span class="sr-val ${hermesIntegrationActive ? 'set-ok' : 'set-neutral'}">${hermesIntegrationActive ? '已接入' : '未启用（可选）'}</span></div></div><div class="field-hint" style="margin-bottom:12px">该结论只使用 /status.hermes.agent_capability.runtime_mode 与 connected；不会把自定义模型地址冒充为独立 Hermes 集成配置。</div>${agentCard}</div></details>
      </div>
    </section>`;

  const activateSection = (id, focus = false) => {
    activeSection = id;
    wrap.querySelectorAll('[data-set-tab]').forEach((button) => {
      const selected = button.dataset.setTab === id;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    });
    wrap.querySelectorAll('[data-set-panel]').forEach((panel) => { panel.hidden = panel.dataset.setPanel !== id; });
  };
  wrap.querySelector('.set-tabs').addEventListener('click', (event) => {
    const button = event.target.closest('[data-set-tab]');
    if (button) activateSection(button.dataset.setTab);
  });
  wrap.querySelector('.set-tabs').addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const buttons = Array.from(wrap.querySelectorAll('[data-set-tab]'));
    const at = buttons.indexOf(document.activeElement);
    if (at < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (at + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    activateSection(buttons[next].dataset.setTab, true);
  });
  wrap.querySelectorAll('[data-settings-save]').forEach((button) => button.addEventListener('click', save));
  const tBtn = wrap.querySelector('#f-test');
  if (tBtn) tBtn.addEventListener('click', testPush);
  wrap.querySelector('#h-test')?.addEventListener('click', testModel);
  wrap.querySelector('#h-save')?.addEventListener('click', saveModel);
  wrap.querySelector('[data-model-new]')?.addEventListener('click', () => {
    editingModelConnectionId = null;
    load();
  });
  wrap.querySelectorAll('[data-model-edit]').forEach((button) => button.addEventListener('click', () => {
    editingModelConnectionId = button.dataset.modelEdit || null;
    load();
  }));
  wrap.querySelectorAll('[data-model-activate]').forEach((button) => button.addEventListener('click', activateModel));
  wrap.querySelectorAll('[data-model-delete]').forEach((button) => button.addEventListener('click', deleteModel));
  wrap.querySelector('[data-delivery-config]')?.addEventListener('click', () => {
    const target = root.querySelector('#f-webhook') || root.querySelector('#f-push');
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target?.focus();
  });
  wrap.querySelectorAll('[data-job-run]').forEach((btn) => btn.addEventListener('click', runTask));
  wrap.querySelectorAll('[data-delivery-id]').forEach((btn) => btn.addEventListener('click', retryDelivery));
  wrap.querySelectorAll('[data-delivery-ack]').forEach((btn) => btn.addEventListener('click', acknowledgeDelivery));
}

async function retryDelivery(e) {
  const btn = e.currentTarget;
  const deliveryId = btn.dataset.deliveryId;
  const status = btn.dataset.deliveryStatus;
  if (!deliveryId) return;
  const ambiguous = ['sending', 'unknown', 'partial'].includes(status);
  if (ambiguous && !window.confirm('该交付可能已有部分内容送达，重试可能产生重复消息。确认已人工核对并继续？')) return;
  const reviewNote = ambiguous ? window.prompt('请填写人工核验说明（至少 3 个字符）', '已核对外部平台，确认允许重试') : '';
  if (ambiguous && (!reviewNote || reviewNote.trim().length < 3)) return;
  btn.disabled = true;
  btn.textContent = '重试中…';
  try {
    const result = await api.post(`/delivery/${encodeURIComponent(deliveryId)}/retry`, {
      confirm_possible_duplicate: ambiguous,
      review_note: reviewNote || '',
    });
    if (result?.ok) toast('交付重试已被平台接受', 'success');
    else toast('重试失败：' + (result?.error || '未知错误'), 'error');
  } catch (err) {
    toast('重试失败：' + err.message, 'error');
  } finally {
    load();
  }
}

async function acknowledgeDelivery(e) {
  const btn = e.currentTarget;
  const deliveryId = btn.dataset.deliveryAck;
  if (!deliveryId) return;
  if (!window.confirm('仅当你已在外部平台确认内容确实被接收时使用。继续？')) return;
  const reviewNote = window.prompt('请填写核验依据（至少 3 个字符）', '已在外部平台核对到该消息');
  if (!reviewNote || reviewNote.trim().length < 3) return;
  btn.disabled = true;
  btn.textContent = '确认中…';
  try {
    const result = await api.post(`/delivery/${encodeURIComponent(deliveryId)}/acknowledge`, {
      review_note: reviewNote.trim(),
    });
    if (result?.ok) toast('已记录人工确认，交付阻断已解除', 'success');
    else toast('确认失败：' + (result?.error || '未知错误'), 'error');
  } catch (err) {
    toast('确认失败：' + err.message, 'error');
  } finally {
    load();
  }
}

async function runTask(e) {
  const btn = e.currentTarget;
  const jobId = btn.dataset.jobRun;
  if (!jobId) return;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = '已触发…';
  try {
    const r = await api.post(`/jobs/${encodeURIComponent(jobId)}/run`);
    if (r?.ok) {
      toast(`已触发任务：${jobDisplay({ id: jobId }).label}`, 'success');
      setTimeout(() => load(), 800);
    } else {
      toast('触发失败：' + (r?.error || '未知错误'), 'error');
      btn.disabled = false;
      btn.textContent = original;
    }
  } catch (err) {
    toast('触发失败：' + err.message, 'error');
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function save(event) {
  const btn = event?.currentTarget || root.querySelector('[data-settings-save]');
  if (!btn) return;
  btn.disabled = true;
  const schedule = {};
  root.querySelectorAll('[data-sched]').forEach((i) => { if (i.value) schedule[i.dataset.sched] = i.value; });
  const $ = (id) => root.querySelector(id);
  const payload = {
    push_enabled: $('#f-push') ? $('#f-push').checked : undefined,
    schedule,
  };
  // 飞书推送模式才有 webhook/secret 字段
  if ($('#f-webhook')) payload.feishu_webhook = $('#f-webhook').value.trim();
  if ($('#f-secret')) payload.feishu_secret = $('#f-secret').value.trim();
  try {
    const r = await api.put('/settings', payload);
    if (r && r.ok !== false) { toast('设置已保存并热生效', 'success'); load(); }
    else toast('保存失败：' + (r?.error || '未知错误'), 'error');
  } catch (e) {
    toast('保存失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function testModel() {
  const btn = root.querySelector('#h-test');
  btn.disabled = true;
  const orig = btn.innerHTML;
  btn.innerHTML = `<span class="btn-spin"></span>测试中`;
  try {
    const h = await api.post('/settings/model/test', modelFormPayload());
    toast(`连接测试通过：${h.model || '--'} @ ${h.base_url || '--'}`, 'success');
  } catch (e) {
    toast('测试失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

function modelFormPayload() {
  return {
    connection_id: editingModelConnectionId || '',
    name: root.querySelector('#h-name')?.value.trim() || '',
    provider: root.querySelector('#h-provider')?.value.trim() || 'custom',
    base_url: root.querySelector('#h-base')?.value.trim() || '',
    api_key: root.querySelector('#h-key')?.value.trim() || '',
    model: root.querySelector('#h-model')?.value.trim() || '',
    activate: root.querySelector('#h-activate')?.checked === true,
  };
}

async function saveModel() {
  const btn = root.querySelector('#h-save');
  if (!btn) return;
  const payload = modelFormPayload();
  if (!payload.name || !payload.provider || !payload.base_url || !payload.model) {
    toast('请完整填写连接名称、Provider、接口地址和模型名', 'error');
    return;
  }
  btn.disabled = true;
  const original = btn.innerHTML;
  btn.innerHTML = '<span class="btn-spin"></span>保存中';
  try {
    const body = {
      name: payload.name,
      provider: payload.provider,
      base_url: payload.base_url,
      model: payload.model,
      activate: payload.activate,
      ...(payload.api_key ? { api_key: payload.api_key } : {}),
    };
    const result = payload.connection_id
      ? await api.put(`/settings/model-connections/${encodeURIComponent(payload.connection_id)}`, body)
      : await api.post('/settings/model-connections', body);
    if (result?.ok === false) throw new Error(result.error || '保存失败');
    editingModelConnectionId = result?.connection?.id || payload.connection_id || undefined;
    toast(payload.connection_id ? '自定义模型连接已更新' : '自定义模型连接已新建', 'success');
    load();
  } catch (error) {
    toast('保存失败：' + error.message, 'error');
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

async function activateModel(event) {
  const btn = event.currentTarget;
  const connectionId = btn.dataset.modelActivate;
  if (!connectionId) return;
  btn.disabled = true;
  try {
    const result = await api.post(`/settings/model-connections/${encodeURIComponent(connectionId)}/activate`, {});
    if (result?.ok === false) throw new Error(result.error || '切换失败');
    editingModelConnectionId = connectionId;
    toast('已切换当前模型连接', 'success');
    load();
  } catch (error) {
    toast('切换失败：' + error.message, 'error');
    btn.disabled = false;
  }
}

async function deleteModel(event) {
  const btn = event.currentTarget;
  const connectionId = btn.dataset.modelDelete;
  if (!connectionId) return;
  if (!window.confirm('确定删除这条自定义模型连接？行情和确定性决策功能不受影响。')) return;
  if (btn) btn.disabled = true;
  try {
    const result = await api.del(`/settings/model-connections/${encodeURIComponent(connectionId)}`);
    if (result?.ok === false) throw new Error(result.error || '删除失败');
    if (editingModelConnectionId === connectionId) editingModelConnectionId = undefined;
    toast('自定义连接已删除；行情、风险与确定性决策继续可用', 'success');
    load();
  } catch (error) {
    toast('删除失败：' + error.message, 'error');
    if (btn) btn.disabled = false;
  }
}

async function testPush() {
  const btn = root.querySelector('#f-test');
  btn.disabled = true;
  btn.innerHTML = `<span class="btn-spin"></span>发送中`;
  try {
    const r = await api.post('/push/test');
    if (r && r.ok) toast('测试消息已发送' + (r.msg ? '（' + r.msg + '）' : '，请到已配置的通讯软件查收'), 'success');
    else toast('发送失败：' + (r?.error || '未知错误'), 'error');
  } catch (e) {
    toast('发送失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `${icon('fly')}发送测试消息`;
  }
}

export function mount(container) {
  root = container;
  activeSection = 'tasks';
  editingModelConnectionId = undefined;
  injectStyle();
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">系统设置</h2>
      <span class="page-sub">任务 · 数据 · 交付 · 安全与版本</span>
    </div>
    <div id="set-wrap"></div>`;
  load();
}

export function unmount() {
  root = null;
  editingModelConnectionId = undefined;
}
