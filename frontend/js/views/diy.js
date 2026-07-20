/* views/diy.js — DIY 研究工作流（模板管理 + 运行面板 SSE 流式渲染）
   左：模板列表 / 新建 / 删除（全走后端 /api/diy/templates）
   右：选模板 + 按 target_type 填输入 + 运行（/api/diy/run SSE，可中断） */

import { api, ssePost } from '../api.js';
import { icon, escapeHtml, renderMd, toast, emptyState, skeleton, openModal, closeModal, confirmModal } from '../ui.js';

const STREAM_PAINT_MS = 120;

const TARGET_LABELS = { stock: '个股', theme: '题材', free: '自由' };

// 前端展示用示例提示（引导用户，不预置入库）
const SAMPLE_TEMPLATES = [
  { name: '龙头辨识', target_type: 'stock', prompt_template: '分析 {code} 是不是当前题材的真龙头：从连板高度、封单强度、市场情绪认可度、资金合力四个维度判断，并给出明确结论与跟踪要点。' },
  { name: '题材逻辑拆解', target_type: 'theme', prompt_template: '拆解题材「{theme}」的核心逻辑：催化来源、产业链受益环节、龙头与跟风分层、持续性与退潮信号，最后给出参与节奏建议。' },
  { name: '自由复盘', target_type: 'free', prompt_template: '基于今日大盘情绪与赚钱效应，复盘市场主线演绎，并预判明日可能的分歧与机会方向。' },
];

let root = null;
let templates = [];
let selectedId = null;
let curTargetType = 'free';
let streaming = false;
let aborter = null;

/* ---------------- 数据 ---------------- */

async function loadTemplates() {
  const el = root?.querySelector('#diy-tpl-list');
  if (!el) return;
  el.innerHTML = skeleton(4);
  try {
    const d = await api.get('/diy/templates');
    templates = (d && Array.isArray(d.templates)) ? d.templates : [];
  } catch (e) {
    el.innerHTML = emptyState('模板加载失败', e.message, '', 'alert');
    templates = [];
    return;
  }
  renderTemplates();
}

function renderTemplates() {
  const el = root?.querySelector('#diy-tpl-list');
  if (!el) return;
  if (!templates.length) {
    el.innerHTML = emptyState('还没有保存的模板', '新建一个研究模板，或直接在右侧编写 prompt 运行', '', 'diy');
    return;
  }
  el.innerHTML = templates.map((t) => `
    <div class="diy-tpl ${t.id === selectedId ? 'active' : ''}" data-id="${escapeHtml(t.id || '')}">
      <div class="diy-tpl-main">
        <div class="diy-tpl-name">${escapeHtml(t.name || '（未命名）')}</div>
        <span class="diy-tt ${escapeHtml(t.target_type || 'free')}">${TARGET_LABELS[t.target_type] || t.target_type || '自由'}</span>
      </div>
      <button class="icon-btn diy-tpl-del" data-del="${escapeHtml(t.id || '')}" title="删除模板" style="width:26px;height:26px">${icon('trash')}</button>
    </div>`).join('');
}

/* ---------------- 运行面板 ---------------- */

function selectTemplate(id) {
  const t = templates.find((x) => x.id === id);
  if (!t) return;
  selectedId = id;
  curTargetType = t.target_type || 'free';
  renderTemplates();
  root.querySelector('#diy-prompt').value = t.prompt_template || '';
  const sel = root.querySelector('#diy-type-sel');
  if (sel) sel.value = curTargetType;
  renderTargetInput();
}

function renderTargetInput() {
  const el = root?.querySelector('#diy-target');
  if (!el) return;
  if (curTargetType === 'stock') {
    el.innerHTML = `<label class="field-label">个股代码（6位）</label>
      <input class="input" id="diy-code" maxlength="6" inputmode="numeric" placeholder="如 002131" style="width:180px;font-family:var(--mono)">`;
  } else if (curTargetType === 'theme') {
    el.innerHTML = `<label class="field-label">题材名称</label>
      <input class="input" id="diy-theme" placeholder="如 光模块" style="width:220px">`;
  } else {
    el.innerHTML = `<span class="ctx-hint">自由模式：无需指定标的，后端将注入今日大盘上下文</span>`;
  }
}

function setRunBtn(stop) {
  const btn = root?.querySelector('#diy-run');
  if (!btn) return;
  btn.classList.toggle('stop', stop);
  btn.innerHTML = stop ? `${icon('stop')}停止` : `${icon('play')}运行`;
}

async function run() {
  if (streaming) { aborter?.abort(); return; }
  const prompt = root.querySelector('#diy-prompt').value.trim();
  if (!prompt) { toast('请先选择模板或编写 prompt', 'error'); return; }

  const payload = { target_type: curTargetType };
  if (selectedId) payload.template_id = selectedId;
  // 同时带上 prompt_template：若用户在文本框改过内容，以文本框为准（无 template_id 时必需）
  payload.prompt_template = prompt;

  if (curTargetType === 'stock') {
    const code = root.querySelector('#diy-code')?.value.trim() || '';
    if (!/^\d{6}$/.test(code)) { toast('请输入 6 位股票代码', 'error'); root.querySelector('#diy-code')?.focus(); return; }
    payload.code = code;
  } else if (curTargetType === 'theme') {
    const theme = root.querySelector('#diy-theme')?.value.trim() || '';
    if (!theme) { toast('请输入题材名称', 'error'); root.querySelector('#diy-theme')?.focus(); return; }
    payload.theme = theme;
  }

  const out = root.querySelector('#diy-output');
  out.innerHTML = '<div class="md-body report-body"><span class="typing-cursor"></span></div>';
  const bubble = out.querySelector('.md-body');

  streaming = true;
  aborter = new AbortController();
  setRunBtn(true);
  let acc = '';
  let rafPending = false;
  let lastPaint = 0;
  const paint = () => {
    rafPending = false;
    lastPaint = performance.now();
    if (bubble.isConnected) bubble.innerHTML = escapeHtml(acc).replace(/\n/g, '<br>') + '<span class="typing-cursor"></span>';
  };
  const schedulePaint = () => {
    if (rafPending) return;
    rafPending = true;
    const wait = Math.max(0, STREAM_PAINT_MS - (performance.now() - lastPaint));
    if (wait <= 0) requestAnimationFrame(paint);
    else setTimeout(() => { if (rafPending) requestAnimationFrame(paint); }, wait);
  };
  const finish = (content, errMsg) => {
    rafPending = false;
    if (!bubble.isConnected) return;
    if (content) {
      bubble.innerHTML = renderMd(content);
      if (errMsg) bubble.insertAdjacentHTML('beforeend', `<div class="msg system" style="margin-top:10px"><div class="msg-bubble">${escapeHtml(errMsg)}</div></div>`);
    } else {
      out.innerHTML = `<div style="padding:8px">${emptyState(errMsg ? '运行失败' : '无内容返回', errMsg || '', '', 'alert')}</div>`;
    }
  };

  try {
    await ssePost('/diy/run', payload, {
      signal: aborter.signal,
      onDelta(t) { acc += t; schedulePaint(); },
      onError(err) { finish(acc, err); },
      onDone() { finish(acc); },
    });
  } catch (e) {
    if (e.name === 'AbortError') finish(acc || '', acc ? '（已中断）' : '运行已中断');
    else finish(acc, '请求失败：' + e.message);
  } finally {
    streaming = false;
    aborter = null;
    setRunBtn(false);
  }
}

/* ---------------- 新建模板 ---------------- */

function openEditor() {
  openModal({
    title: '新建研究模板',
    body: `
      <div class="field">
        <label class="field-label">模板名称</label>
        <input class="input" id="diy-f-name" placeholder="如：龙头辨识">
      </div>
      <div class="field">
        <label class="field-label">作用对象</label>
        <select class="select" id="diy-f-type">
          <option value="stock">个股（运行时填 6 位代码，prompt 用 {code}）</option>
          <option value="theme">题材（运行时填题材名，prompt 用 {theme}）</option>
          <option value="free" selected>自由（无需标的）</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">Prompt 模板</label>
        <textarea class="textarea" id="diy-f-prompt" rows="6" placeholder="可用占位符 {code} {theme}，运行时自动替换；真实数据由后端注入"></textarea>
        <div class="field-hint">提示：占位符 {code} 表示个股代码，{theme} 表示题材名称</div>
      </div>`,
    footer: `
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" id="diy-f-save">${icon('save')}保存</button>`,
    onMount(modal) {
      modal.querySelector('#diy-f-name').focus();
      modal.querySelector('#diy-f-save').addEventListener('click', async () => {
        const name = modal.querySelector('#diy-f-name').value.trim();
        const target_type = modal.querySelector('#diy-f-type').value;
        const prompt_template = modal.querySelector('#diy-f-prompt').value.trim();
        if (!name) { toast('请填写模板名称', 'error'); return; }
        if (!prompt_template) { toast('请填写 Prompt 模板', 'error'); return; }
        try {
          const r = await api.post('/diy/templates', { name, target_type, prompt_template });
          if (r && r.ok !== false) {
            toast('模板已保存', 'success');
            closeModal();
            await loadTemplates();
            if (r.id) selectTemplate(r.id);
          } else toast('保存失败：' + (r?.error || '未知错误'), 'error');
        } catch (e) { toast('保存失败：' + e.message, 'error'); }
      });
    },
  });
}

/* ---------------- 挂载 ---------------- */

export function mount(container) {
  root = container;
  templates = [];
  selectedId = null;
  curTargetType = 'free';
  streaming = false;

  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">DIY 研究工作流</h2>
      <span class="page-sub">自定义研究模板 · 注入真实数据 · 当前模型流式生成</span>
    </div>
    <div class="diy-layout">
      <section class="card diy-left">
        <div class="card-head"><span class="card-title">研究模板 · TEMPLATES</span>
          <span class="card-extra"><button class="btn btn-sm btn-primary" id="diy-add">${icon('plus')}新建</button></span>
        </div>
        <div class="card-body" id="diy-tpl-list">${skeleton(4)}</div>
        <div class="diy-samples">
          <div class="ft-sub-title">示例模板（点击套用，不入库）</div>
          ${SAMPLE_TEMPLATES.map((s, i) => `<div class="diy-sample" data-sample="${i}">
            <span class="diy-sample-name">${escapeHtml(s.name)}</span>
            <span class="diy-tt ${s.target_type}">${TARGET_LABELS[s.target_type]}</span>
          </div>`).join('')}
        </div>
      </section>

      <section class="card diy-right">
        <div class="card-head"><span class="card-title">运行面板 · RUN</span>
          <span class="card-extra">
            <select class="select" id="diy-type-sel" style="width:120px;padding:4px 9px;font-size:15px">
              <option value="stock">个股</option>
              <option value="theme">题材</option>
              <option value="free">自由</option>
            </select>
          </span>
        </div>
        <div class="card-body diy-run-body">
          <div class="field">
            <label class="field-label">Prompt（选模板后自动填充，可直接编辑）</label>
            <textarea class="textarea" id="diy-prompt" rows="4" placeholder="选择左侧模板，或直接在此编写 prompt 模板（可用 {code} {theme}）"></textarea>
          </div>
          <div class="diy-run-row">
            <div class="field diy-target-field" id="diy-target"></div>
            <button class="btn btn-primary diy-run-btn" id="diy-run">${icon('play')}运行</button>
          </div>
          <div class="diy-output-wrap">
            <div id="diy-output">${emptyState('运行后在此查看当前模型的流式分析', '选择模板 / 编写 prompt，按对象类型填入标的后点击「运行」', '', 'diy')}</div>
          </div>
        </div>
      </section>
    </div>`;

  container.querySelector('#diy-add').addEventListener('click', openEditor);

  // 模板列表点击：选中 / 删除
  container.querySelector('#diy-tpl-list').addEventListener('click', async (e) => {
    const del = e.target.closest('[data-del]');
    if (del) {
      e.stopPropagation();
      const id = del.dataset.del;
      if (!id) return;
      if (!(await confirmModal('删除模板', '删除后不可恢复，确定删除该模板？', { danger: true, okText: '删除' }))) return;
      try {
        const r = await api.del(`/diy/templates/${encodeURIComponent(id)}`);
        if (r && r.ok !== false) {
          toast('模板已删除', 'success');
          if (selectedId === id) selectedId = null;
          loadTemplates();
        } else toast('删除失败：' + (r?.error || '未知错误'), 'error');
      } catch (err) { toast('删除失败：' + err.message, 'error'); }
      return;
    }
    const tpl = e.target.closest('.diy-tpl');
    if (tpl && tpl.dataset.id) selectTemplate(tpl.dataset.id);
  });

  // 示例模板套用
  container.querySelector('.diy-samples').addEventListener('click', (e) => {
    const s = e.target.closest('[data-sample]');
    if (!s) return;
    const sample = SAMPLE_TEMPLATES[Number(s.dataset.sample)];
    if (!sample) return;
    selectedId = null;
    renderTemplates();
    curTargetType = sample.target_type;
    container.querySelector('#diy-prompt').value = sample.prompt_template;
    container.querySelector('#diy-type-sel').value = curTargetType;
    renderTargetInput();
  });

  // 对象类型下拉（手动切换）
  container.querySelector('#diy-type-sel').addEventListener('change', (e) => {
    curTargetType = e.target.value;
    renderTargetInput();
  });

  container.querySelector('#diy-run').addEventListener('click', run);

  renderTargetInput();
  container.querySelector('#diy-type-sel').value = curTargetType;
  loadTemplates();
}

export function unmount() {
  if (aborter) { try { aborter.abort(); } catch { /* noop */ } aborter = null; }
  streaming = false;
  root = null;
}
