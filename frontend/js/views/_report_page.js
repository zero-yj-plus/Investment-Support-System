/* views/_report_page.js — 交易节奏报告页共享组件（尾盘策略/收盘复盘/次日策略复用）
   ------------------------------------------------------------------
   母文档 §24/§25/§26：这三页不是再造行情，而是把已有的 AI 报告(rtype=tail/close/evening)
   做成「那个时点该干什么」的工作站——报告正文 + 该时点的真实结构化面板(风险闸/推荐验证/观察池)。

   设计：machinery(取报告/SSE生成/推送/PDF) 收敛在此，与「报告中心」reports.js 同款已验证逻辑；
   每页通过 topRender/bottomRender 钩子注入自己的结构化面板（数据各自取真实端点，本组件不碰）。
   铁律：报告由后端 AI 引擎按注入的真实数据生成；本组件只渲染，不编任何数字。 */

import { api, aiReadiness, ssePost, todayStr, cnNow } from '../api.js';
import { icon, escapeHtml, renderMd, toast, emptyState, skeleton } from '../ui.js';
import { downloadPdf } from '../export.js';

const STAGE_FALLBACK = '处理中…';

function reportVersionLabel(report) {
  const version = report?.plan_version || report?.report_version || 1;
  const hash = String(report?.content_hash || '').slice(0, 12);
  return hash ? `已定稿 v${version} · ${hash}` : `历史定稿 v${version} · 未记录指纹`;
}

/**
 * 造一个交易节奏报告页视图（返回 {mount, unmount}，供 app.js 直接当 view 用）。
 * @param {{rtype:string, title:string, sub?:string,
 *          topRender?:(host:HTMLElement, ctx:{reload:Function, generate:Function, isAlive:()=>boolean})=>void,
 *          bottomRender?:(host:HTMLElement, ctx:{reload:Function, isAlive:()=>boolean})=>void}} cfg
 */
export function makeReportView(cfg) {
  const { rtype, title, sub, topRender, bottomRender, notBefore } = cfg;
  let root = null;
  let generating = false;
  let aborter = null;
  let date = todayStr();
  let lastReport = null;   // 最近一次拿到的报告记录（含时点锁定的 cockpit 快照），供 topRender 渲染横幅

  const isAlive = () => !!root;
  const label = () => title;
  const beforeGenerationTime = () => {
    if (!notBefore || date !== todayStr()) return false;
    const match = /^(\d{2}):(\d{2})$/.exec(String(notBefore));
    if (!match) return false;
    const now = cnNow();
    return now.hh * 60 + now.mm < Number(match[1]) * 60 + Number(match[2]);
  };

  function mount(container) {
    root = container;
    date = todayStr();
    container.innerHTML = `
      <div class="page-head">
        <h2 class="page-title">${escapeHtml(title)}</h2>
        ${sub ? `<span class="page-sub">${escapeHtml(sub)}</span>` : ''}
      </div>
      <div id="rp-top"></div>
      <section class="card" id="rp-main" style="min-height:360px"></section>
      <div id="rp-bottom" style="margin-top:var(--gap)"></div>`;
    // 上方结构化面板（风险闸）先占位骨架；真实内容在报告数据到手后由 renderTop() 用【报告内快照】渲染
    // （时点锁定 T9 §0-1：盘前定格 08:50、尾盘定格 14:45，不再读实时 /cockpit）
    if (typeof topRender === 'function') {
      const th = container.querySelector('#rp-top');
      if (th) th.innerHTML = `<div class="rp-top-skel">${skeleton(2, [40, 18])}</div>`;
    }
    load();
    // 下方结构化面板（如收盘「昨日推荐验证」、次日「观察池排序」）
    if (typeof bottomRender === 'function') {
      try { bottomRender(container.querySelector('#rp-bottom'), { reload: load, isAlive }); } catch (e) { /* noop */ }
    }
  }

  async function load() {
    if (generating || !root) return;
    const main = root.querySelector('#rp-main');
    if (!main) return;
    main.innerHTML = `<div class="card-body">${skeleton(6, [26, 14, 14, 14, 14, 14])}</div>`;
    let d;
    try { d = await api.get(`/reports/${rtype}?date=${encodeURIComponent(date)}`); } catch (e) {
      if (!root) return;
      lastReport = null; renderTop();
      main.innerHTML = `<div class="card-body">${emptyState('报告加载失败', e.message, '', 'alert')}</div>`;
      return;
    }
    if (!root) return;
    lastReport = d;
    renderTop();   // 风险闸横幅用报告内快照渲染（时点锁定，不读实时 /cockpit）
    if (!d || d.ok === false || !d.content_md) {
      const waitingForScheduledTime = beforeGenerationTime();
      const readiness = waitingForScheduledTime ? { ready: true } : await aiReadiness();
      if (!root) return;
      const isScheduledCatchup = Boolean(notBefore && date === todayStr() && !waitingForScheduledTime);
      const missingDetail = waitingForScheduledTime
        ? `计划在北京时间 ${notBefore} 后生成；当前不提前定稿，避免用盘中数据冒充收盘结论。到时刷新即可。`
        : readiness.ready
          ? (isScheduledCatchup
              ? `今日预定 ${notBefore} 产物缺失。可以按当前时点补生成，报告会保留实际生成时间，不冒充 ${notBefore} 准时产物；也可先查看历史报告。`
              : (d?.error || '该报告尚未生成。可手动生成，生成时间会如实记录。'))
          : `${isScheduledCatchup ? `今日预定 ${notBefore} 产物缺失。` : ''}${readiness.reason}可先查看历史报告；当前不会发起注定失败的生成请求。`;
      const action = waitingForScheduledTime
        ? `<button class="btn btn-lg" id="rp-refresh" style="margin-top:8px">${icon('refresh')}刷新状态</button>`
        : `${readiness.ready
            ? `<button class="btn btn-primary btn-lg" id="rp-gen" style="margin-top:8px">${icon('spark')}${isScheduledCatchup ? '补生成今日报告' : '立即生成'}</button>`
            : `<button class="btn btn-lg" id="rp-model-settings" style="margin-top:8px">去系统设置</button>`}
          <button class="btn btn-lg" id="rp-history" style="margin-top:8px">查看历史报告</button>`;
      main.innerHTML = `<div class="card-body">${emptyState(
        waitingForScheduledTime
          ? `${label()}尚未到生成时点`
          : `${date} 暂无「${label()}」`,
        missingDetail,
        action,
        'reports',
      )}</div>`;
      main.querySelector('#rp-gen')?.addEventListener('click', generate);
      main.querySelector('#rp-refresh')?.addEventListener('click', load);
      main.querySelector('#rp-model-settings')?.addEventListener('click', () => { location.hash = '#/settings'; });
      main.querySelector('#rp-history')?.addEventListener('click', () => { location.hash = '#/history?tab=reports'; });
      return;
    }
    main.innerHTML = `
      <div class="card-body">
        <div class="report-meta" style="margin-bottom:14px">
          <h2 style="font-size:18px;color:var(--text)">${escapeHtml(d.title || label())}</h2>
          <span class="num">${escapeHtml(d.created_at || '')}</span>
          ${d.pushed ? '<span class="pushed-badge">已推送</span>' : ''}
          <span style="margin-left:auto;display:flex;gap:8px;align-items:center">
            <span style="font-size:12px;color:var(--text-2)" title="每日报告当日首版即定稿；页面、推送与 PDF 用此内容指纹对账">${escapeHtml(reportVersionLabel(d))}</span>
            <button class="btn btn-sm" id="rp-pdf">${icon('save')}下载 PDF</button>
            <button class="btn btn-sm btn-primary" id="rp-push">${icon('fly')}推送</button>
          </span>
        </div>
        <article class="md-body report-body">${renderMd(d.content_md)}</article>
      </div>`;
    main.querySelector('#rp-pdf')?.addEventListener('click', downloadReportPdf);
    main.querySelector('#rp-push')?.addEventListener('click', push);
  }

  // 用【报告内 cockpit 快照】渲染上方风险闸（时点锁定）——topRender 收 ctx.report，不再自取实时 /cockpit。
  function renderTop() {
    if (typeof topRender !== 'function' || !root) return;
    const th = root.querySelector('#rp-top');
    if (!th) return;
    try { topRender(th, { reload: load, generate, isAlive, report: lastReport }); } catch (e) { /* 面板失败不拖垮报告 */ }
  }

  async function downloadReportPdf() {
    const art = root?.querySelector('.report-body');
    if (!art) { toast('请先生成报告', 'error'); return; }
    await downloadPdf(art, `${label()}-${date}`);
  }

  async function generate() {
    if (generating || !root) return;
    const readiness = await aiReadiness({ force: true });
    if (!readiness.ready) {
      toast(readiness.reason, 'error', 7000);
      load();
      return;
    }
    generating = true;
    aborter = new AbortController();
    const main = root.querySelector('#rp-main');
    main.innerHTML = `
      <div class="card-body">
        <div class="report-meta" style="margin-bottom:14px">
          <h2 style="font-size:18px;color:var(--text)">${escapeHtml(label())}</h2>
          <span class="gen-indicator"><span class="gdot"></span><span class="gen-stage">${escapeHtml(STAGE_FALLBACK)}</span> <span class="gen-secs num">0s</span></span>
          <span style="margin-left:auto"><button class="btn btn-sm btn-danger" id="rp-stop">${icon('stop')}停止</button></span>
        </div>
        <article class="md-body report-body" id="rp-genbody"><span class="typing-cursor"></span></article>
      </div>`;
    const body = main.querySelector('#rp-genbody');
    main.querySelector('#rp-stop').addEventListener('click', () => aborter?.abort());

    let waitSecs = 0, stageText = '', firstDelta = false, stageTimer = null;
    const indicator = () => root?.querySelector('.gen-indicator');
    const renderStageWait = () => {
      if (firstDelta) return;
      const el = indicator(); if (!el) return;
      const t = el.querySelector('.gen-stage'), s = el.querySelector('.gen-secs');
      if (t) t.textContent = stageText || STAGE_FALLBACK;
      if (s) s.textContent = waitSecs + 's';
    };
    const clearStageTimer = () => { if (stageTimer) { clearInterval(stageTimer); stageTimer = null; } };
    stageTimer = setInterval(() => { waitSecs += 1; renderStageWait(); }, 1000);

    let acc = '', rafPending = false;
    const paint = () => { rafPending = false; if (body?.isConnected) body.innerHTML = renderMd(acc) + '<span class="typing-cursor"></span>'; };

    try {
      await ssePost(`/reports/${rtype}/generate`, {}, {
        signal: aborter.signal,
        onStage(s) { if (firstDelta) return; stageText = String(s || ''); renderStageWait(); },
        onDelta(t) {
          if (!firstDelta) { firstDelta = true; clearStageTimer(); const el = indicator(); if (el) el.innerHTML = '<span class="gdot"></span>当前模型正在生成…'; }
          acc += t;
          if (!rafPending) { rafPending = true; requestAnimationFrame(paint); }
        },
        onError(err) {
          clearStageTimer();
          acc = '';
          if (body?.isConnected) body.innerHTML = '<div class="empty-desc" style="color:var(--up)">本次输出未完整，已隔离，不保存也不可推送。</div>';
          toast('生成失败：' + err, 'error');
        },
        onDone(obj) {
          clearStageTimer();
          if (obj && obj.truncated) toast('⚠️ 报告因长度被截断，建议重新生成', 'error', 6000);
          else if (obj && obj.saved) toast('报告已生成并保存', 'success');
          else if (acc) toast('生成完成', 'success');
          date = (obj && obj.date) || todayStr();
        },
      });
    } catch (e) {
      if (e.name === 'AbortError') toast('已停止生成', 'info');
      else toast('生成异常：' + e.message, 'error');
    } finally {
      clearStageTimer();
      generating = false;
      aborter = null;
      // 生成前可能缓存了“尚无报告”；生成完成后必须废弃旧结果，
      // 否则 SWR 会先返回旧空态，让用户误以为没有生成成功。
      api.invalidate(`/reports/${rtype}`);
      if (root) load();
    }
  }

  async function push() {
    const btn = root?.querySelector('#rp-push');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="btn-spin"></span>推送中'; }
    try {
      const r = await api.post(`/reports/${rtype}/push`, { date });
      if (r && r.ok) { toast('已推送' + (r.msg ? '（' + r.msg + '）' : ''), 'success'); load(); }
      else toast('推送失败：' + (r?.error || '未知错误'), 'error');
    } catch (e) {
      toast('推送失败：' + e.message, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = `${icon('fly')}推送`; }
    }
  }

  function unmount() {
    if (aborter) { try { aborter.abort(); } catch { /* noop */ } aborter = null; }
    generating = false;
    root = null;
  }

  return { mount, unmount };
}
