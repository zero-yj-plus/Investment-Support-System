/* views/knowledge.js — 知识库（分类 + 瀑布流 + 搜索 + 增删） */

import { api } from '../api.js';
import { icon, escapeHtml, toast, emptyState, skeleton, openModal, closeModal, confirmModal } from '../ui.js';

const CATEGORIES = [
  { name: '交易原则', color: '#22d3ee' },
  { name: '亏损禁区', color: '#f0445a' },
  { name: '擅长模式', color: '#2dd4a7' },
  { name: '不擅长模式', color: '#f59e0b' },
  { name: '仓位规则', color: '#8b5cf6' },
  { name: '经验教训', color: '#e879f9' },
  { name: '选股偏好', color: '#60a5fa' },
  { name: '复盘笔记', color: '#a3e635' },
];

let root = null;
let items = [];
let activeCat = '全部';
let activeStatus = '全部';
let keyword = '';

const STATUS_META = {
  approved: { label: '已批准·会进入 AI', color: 'var(--up)' },
  draft: { label: '待审批', color: 'var(--amber)' },
  legacy_unverified: { label: '历史条目·待补证', color: 'var(--amber)' },
  rejected: { label: '已拒绝', color: 'var(--down)' },
  superseded: { label: '已被新版替代', color: 'var(--text-3)' },
};

function catColor(name) { return CATEGORIES.find((c) => c.name === name)?.color || '#8b93a7'; }

function filtered() {
  return items.filter((it) => {
    if (activeCat !== '全部' && it.category !== activeCat) return false;
    if (activeStatus !== '全部' && it.status !== activeStatus) return false;
    if (keyword) {
      const kw = keyword.toLowerCase();
      return String(it.title || '').toLowerCase().includes(kw) || String(it.content || '').toLowerCase().includes(kw);
    }
    return true;
  });
}

function renderCats() {
  if (!root) return;
  const el = root.querySelector('#kn-cats');
  const counts = {};
  items.forEach((it) => { counts[it.category] = (counts[it.category] || 0) + 1; });
  el.innerHTML = [
    `<div class="kn-cat ${activeCat === '全部' ? 'active' : ''}" data-cat="全部">
      <span class="cdot" style="background:var(--brand-grad)"></span>全部<span class="cnt num">${items.length}</span>
    </div>`,
    ...CATEGORIES.map((c) => `
      <div class="kn-cat ${activeCat === c.name ? 'active' : ''}" data-cat="${escapeHtml(c.name)}">
        <span class="cdot" style="background:${c.color}"></span>${escapeHtml(c.name)}
        <span class="cnt num">${counts[c.name] || 0}</span>
      </div>`),
  ].join('');
}

function renderStatuses() {
  if (!root) return;
  const el = root.querySelector('#kn-status');
  if (!el) return;
  const counts = {};
  items.forEach((it) => { counts[it.status] = (counts[it.status] || 0) + 1; });
  const pending = (counts.draft || 0) + (counts.legacy_unverified || 0);
  const tabs = [
    { value: '全部', label: '全部', count: items.length },
    { value: 'approved', label: '已批准', count: counts.approved || 0 },
    { value: 'draft', label: '待审批', count: counts.draft || 0 },
    { value: 'legacy_unverified', label: '待补证', count: counts.legacy_unverified || 0 },
    { value: 'rejected', label: '已拒绝', count: counts.rejected || 0 },
    { value: 'superseded', label: '已替代', count: counts.superseded || 0 },
  ];
  el.innerHTML = `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    ${tabs.map((tab) => `<button type="button" class="chip ${activeStatus === tab.value ? 'active' : ''}" data-status="${escapeHtml(tab.value)}">
      ${escapeHtml(tab.label)} <span class="num">${tab.count}</span>
    </button>`).join('')}
    <span style="margin-left:auto;font-size:12px;color:${pending ? 'var(--amber)' : 'var(--text-3)'}">
      ${pending ? `${pending} 条等待人工决定` : '当前无待审条目'}
    </span>
  </div>`;
}

function renderCards() {
  if (!root) return;
  const el = root.querySelector('#kn-cards');
  const list = filtered();
  if (!list.length) {
    el.innerHTML = emptyState(
      keyword ? '没有匹配的条目' : '这个分类还是空的',
      keyword ? '换个关键词试试' : '把交易铁律、踩过的坑、擅长的模式沉淀下来；通过审批后，当前模型助手才会在对话中参考',
      `<button class="btn btn-primary" id="kn-add-empty" style="margin-top:6px">${icon('plus')}新建条目</button>`,
      'knowledge'
    );
    el.querySelector('#kn-add-empty')?.addEventListener('click', openEditor);
    return;
  }
  el.innerHTML = `<div class="kn-masonry">${list.map((it) => {
    const color = catColor(it.category);
    const content = String(it.content || '');
    const status = STATUS_META[it.status] || STATUS_META.legacy_unverified;
    const pending = ['draft', 'legacy_unverified'].includes(it.status);
    const sources = [...(it.source_report_ids || []), ...(it.evidence_ids || [])];
    return `<div class="card hoverable kn-card" data-id="${escapeHtml(it.id || '')}" style="cursor:pointer" title="点击看全文">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
          <span class="cat-tag" style="color:${color};border-color:${color}55;background:${color}14">${escapeHtml(it.category || '未分类')}</span>
          <span class="cat-tag" style="color:${status.color};border-color:color-mix(in srgb,${status.color} 45%,transparent)">${escapeHtml(status.label)}</span>
        </div>
        ${pending ? `<div style="display:flex;gap:6px">
          <button class="btn btn-sm" data-approve="${escapeHtml(it.id || '')}">${icon('check')}批准</button>
          <button class="btn btn-sm" data-reject="${escapeHtml(it.id || '')}">拒绝</button>
        </div>` : `<button class="row-del" data-del="${escapeHtml(it.id || '')}" title="删除该条知识" style="font-size:12px;padding:2px 8px;white-space:nowrap;color:var(--down);border:1px solid var(--down);border-radius:4px;background:transparent">${icon('trash')} 删除</button>`}
      </div>
      <h4>${escapeHtml(it.title || '（无标题）')}</h4>
      <div class="kn-content ${content.length < 180 ? 'short' : ''}">${escapeHtml(content.slice(0, 600))}${content.length > 600 ? '<span style="color:var(--accent)">… 点击看全文</span>' : ''}</div>
      <div class="kn-foot" style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <span class="num">v${Number(it.version || 1)} · ${escapeHtml(it.created_at || '')}</span>
        <span>样本 ${Number(it.sample_count || 0)} · ${sources.length ? `来源 ${sources.length}` : '未绑定来源'}</span>
      </div>
    </div>`;
  }).join('')}</div>`;
}

async function load() {
  // 新建条目模态框挂在全局 #modal-root、不随路由卸载，保存回调可能在视图卸载后才触发 load
  if (!root) return;
  const el = root.querySelector('#kn-cards');
  el.innerHTML = skeleton(5);
  try {
    const d = await api.get('/knowledge');
    items = (d && Array.isArray(d.items)) ? d.items : [];
  } catch (e) {
    el.innerHTML = emptyState('知识库加载失败', e.message, '', 'alert');
    items = [];
    renderCats();
    renderStatuses();
    return;
  }
  renderCats();
  renderStatuses();
  renderCards();
}

function openEditor() {
  openModal({
    title: '新建知识条目',
    body: `
      <div class="field">
        <label class="field-label">分类</label>
        <select class="select" id="kn-f-cat">
          ${CATEGORIES.map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('')}
        </select>
      </div>
      <div class="field">
        <label class="field-label">标题</label>
        <input class="input" id="kn-f-title" placeholder="一句话概括，如：缩量阴跌不抄底">
      </div>
      <div class="field">
        <label class="field-label">内容</label>
        <textarea class="textarea" id="kn-f-content" rows="6" placeholder="规则细节、触发条件、历史案例…"></textarea>
      </div>`,
    footer: `
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" id="kn-f-save">${icon('save')}保存</button>`,
    onMount(modal) {
      modal.querySelector('#kn-f-title').focus();
      modal.querySelector('#kn-f-save').addEventListener('click', async () => {
        const category = modal.querySelector('#kn-f-cat').value;
        const title = modal.querySelector('#kn-f-title').value.trim();
        const content = modal.querySelector('#kn-f-content').value.trim();
        if (!title) { toast('请填写标题', 'error'); return; }
        try {
          const r = await api.post('/knowledge', { category, title, content });
          if (r && r.ok !== false) { toast('已保存', 'success'); closeModal(); load(); }
          else toast('保存失败：' + (r?.error || '未知错误'), 'error');
        } catch (e) { toast('保存失败：' + e.message, 'error'); }
      });
    },
  });
}

async function approveItem(item) {
  const hasSource = (item.source_report_ids || []).length || (item.evidence_ids || []).length;
  const hasSample = Number(item.sample_count || 0) >= 1;
  if (hasSource && hasSample) {
    try {
      const r = await api.post(`/knowledge/${encodeURIComponent(item.id)}/approve`, {});
      if (r?.ok !== false) { toast('已批准，后续 AI 只会参考已批准规则', 'success'); await load(); }
      else toast('批准失败：' + (r?.error || '未知错误'), 'error');
    } catch (e) { toast('批准失败：' + e.message, 'error'); }
    return;
  }
  openModal({
    title: '补充来源后批准',
    body: `<div class="field-hint" style="margin-bottom:12px">历史条目没有可核验来源，不会自动进入 AI。请至少绑定一个报告 ID 或证据 ID，并如实填写样本数。</div>
      <div class="field"><label class="field-label">来源报告 ID</label><input class="input" id="kn-approve-report" placeholder="report:close:2026-07-13"></div>
      <div class="field"><label class="field-label">证据 ID</label><input class="input" id="kn-approve-evidence" placeholder="可选，evidence UUID"></div>
      <div class="field"><label class="field-label">真实样本数</label><input class="input" id="kn-approve-sample" type="number" min="1" step="1" value="1"></div>`,
    footer: `<button class="btn btn-ghost" data-close>取消</button><button class="btn btn-primary" id="kn-approve-save">${icon('check')}批准</button>`,
    onMount(modal) {
      modal.querySelector('#kn-approve-save').addEventListener('click', async () => {
        const report = modal.querySelector('#kn-approve-report').value.trim();
        const evidence = modal.querySelector('#kn-approve-evidence').value.trim();
        const sample = Number(modal.querySelector('#kn-approve-sample').value);
        if ((!report && !evidence) || !Number.isInteger(sample) || sample < 1) {
          toast('请绑定来源并填写大于等于 1 的样本数', 'error'); return;
        }
        try {
          const r = await api.post(`/knowledge/${encodeURIComponent(item.id)}/approve`, {
            source_report_ids: report ? [report] : [], evidence_ids: evidence ? [evidence] : [], sample_count: sample,
          });
          if (r?.ok !== false) { closeModal(); toast('已批准', 'success'); await load(); }
          else toast('批准失败：' + (r?.error || '未知错误'), 'error');
        } catch (e) { toast('批准失败：' + e.message, 'error'); }
      });
    },
  });
}

function rejectItem(item) {
  openModal({
    title: '拒绝待审知识',
    body: `<div class="field"><label class="field-label">拒绝原因</label><textarea class="textarea" id="kn-reject-reason" rows="4" placeholder="例如：样本不足，无法泛化"></textarea></div>`,
    footer: `<button class="btn btn-ghost" data-close>取消</button><button class="btn btn-primary" id="kn-reject-save">确认拒绝</button>`,
    onMount(modal) {
      modal.querySelector('#kn-reject-reason').focus();
      modal.querySelector('#kn-reject-save').addEventListener('click', async () => {
        const reason = modal.querySelector('#kn-reject-reason').value.trim();
        if (!reason) { toast('请填写拒绝原因', 'error'); return; }
        try {
          const r = await api.post(`/knowledge/${encodeURIComponent(item.id)}/reject`, { reason });
          if (r?.ok !== false) { closeModal(); toast('已拒绝，不会进入 AI', 'success'); await load(); }
          else toast('拒绝失败：' + (r?.error || '未知错误'), 'error');
        } catch (e) { toast('拒绝失败：' + e.message, 'error'); }
      });
    },
  });
}

export function mount(container) {
  root = container;
  activeCat = '全部';
  activeStatus = '全部';
  keyword = '';
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">知识库</h2>
      <span class="page-sub">个人交易体系沉淀 · 只有已批准规则会进入 AI 上下文</span>
      <div class="ph-extra">
        <div style="position:relative">
          <input class="input" id="kn-search" placeholder="搜索标题或内容…" style="width:220px;padding-left:32px">
          <span style="position:absolute;left:9px;top:50%;transform:translateY(-50%);width:15px;height:15px;color:var(--text-3)">${icon('search')}</span>
        </div>
        <button class="btn btn-primary" id="kn-add">${icon('plus')}新建条目</button>
      </div>
    </div>
    <section class="card" id="kn-status" style="padding:10px 12px;margin-bottom:var(--gap)"></section>
    <div class="kn-layout">
      <section class="card" style="padding:10px;align-self:start" id="kn-cats"></section>
      <div id="kn-cards"></div>
    </div>`;

  container.querySelector('#kn-add').addEventListener('click', openEditor);
  container.querySelector('#kn-search').addEventListener('input', (e) => {
    keyword = e.target.value.trim();
    renderCards();
  });
  container.querySelector('#kn-cats').addEventListener('click', (e) => {
    const cat = e.target.closest('.kn-cat');
    if (!cat) return;
    activeCat = cat.dataset.cat;
    renderCats();
    renderCards();
  });
  container.querySelector('#kn-status').addEventListener('click', (e) => {
    const tab = e.target.closest('[data-status]');
    if (!tab) return;
    activeStatus = tab.dataset.status;
    renderStatuses();
    renderCards();
  });
  container.querySelector('#kn-cards').addEventListener('click', async (e) => {
    const approve = e.target.closest('[data-approve]');
    if (approve) {
      const item = items.find((x) => String(x.id) === String(approve.dataset.approve));
      if (item) await approveItem(item);
      return;
    }
    const reject = e.target.closest('[data-reject]');
    if (reject) {
      const item = items.find((x) => String(x.id) === String(reject.dataset.reject));
      if (item) rejectItem(item);
      return;
    }
    const del = e.target.closest('[data-del]');
    if (del) {
      const id = del.dataset.del;
      if (!id) return;
      if (!(await confirmModal('删除条目', '删除后不可恢复，确定删除该知识条目？', { danger: true, okText: '删除' }))) return;
      try {
        const r = await api.del(`/knowledge/${encodeURIComponent(id)}`);
        if (r && r.ok !== false) { toast('已删除', 'success'); load(); }
        else toast('删除失败：' + (r?.error || '未知错误'), 'error');
      } catch (err) { toast('删除失败：' + err.message, 'error'); }
      return;
    }
    // 点卡片本体 → 弹窗看全文（#9 修：此前卡片不可点、长内容还被截 600 字，客户点击"打不开"）
    const card = e.target.closest('.kn-card');
    if (!card) return;
    const it = items.find((x) => String(x.id) === String(card.dataset.id));
    if (!it) return;
    openModal({
      title: `${it.category || '未分类'} · ${it.title || '（无标题）'}`,
      body: `<div style="white-space:pre-wrap;line-height:1.75;font-size:14px;max-height:60vh;overflow:auto">${escapeHtml(String(it.content || '（无内容）'))}</div>
             <div style="margin-top:12px;font-size:12px;color:var(--text-3);line-height:1.7">
               ${escapeHtml((STATUS_META[it.status] || STATUS_META.legacy_unverified).label)} · v${Number(it.version || 1)} · 样本 ${Number(it.sample_count || 0)}<br>
               来源：${escapeHtml([...(it.source_report_ids || []), ...(it.evidence_ids || [])].join('、') || '未绑定')}<br>
               ${escapeHtml(it.created_at || '')}
             </div>`,
    });
  });

  load();
}

export function unmount() {
  root = null;
}
