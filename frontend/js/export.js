/* export.js — 把页面/报告导出为 PDF：html2canvas 截图 → jsPDF 按 A4 分页 → 下载 / 推送到已连通讯软件。
 * 客户端生成，适用于任意页面（仪表盘各板块、报告等）；推送复用后端 /api/export/push（网关 MEDIA: 发附件）。 */

import { toast } from './ui.js';

let exportLibrariesPromise = null;

function assetVersion() {
  return document.querySelector('meta[name="wudi-asset-version"]')?.content?.trim() || '';
}

function vendorUrl(filename) {
  const version = assetVersion();
  return `/vendor/${filename}${version ? `?v=${encodeURIComponent(version)}` : ''}`;
}

function loadVendorScript(filename, ready) {
  if (ready()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const selector = `script[data-export-vendor="${filename}"]`;
    let script = document.querySelector(selector);
    const fail = () => {
      script?.remove();
      reject(new Error(`导出组件加载失败（vendor/${filename}）`));
    };
    const done = () => {
      if (ready()) resolve();
      else fail();
    };
    if (script) {
      script.addEventListener('load', done, { once: true });
      script.addEventListener('error', fail, { once: true });
      return;
    }
    script = document.createElement('script');
    script.dataset.exportVendor = filename;
    script.src = vendorUrl(filename);
    script.async = true;
    script.addEventListener('load', done, { once: true });
    script.addEventListener('error', fail, { once: true });
    document.head.appendChild(script);
  });
}

async function ensureExportLibraries() {
  if (!exportLibrariesPromise) {
    exportLibrariesPromise = Promise.all([
      loadVendorScript('html2canvas.min.js', () => typeof window.html2canvas === 'function'),
      loadVendorScript('jspdf.umd.min.js', () => Boolean(window.jspdf?.jsPDF)),
    ]).catch((error) => {
      exportLibrariesPromise = null;
      throw error;
    });
  }
  await exportLibrariesPromise;
}

/** 时间戳 YYYYMMDD-HHMM（用于默认文件名） */
function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

/** 页面背景色：暗色主题下避免 html2canvas 截出白底 */
function pageBg() {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue('--bg0').trim();
    return v || '#0b0d11';
  } catch { return '#0b0d11'; }
}

function safeName(name) {
  return String(name || 'export').replace(/[\\/:*?"<>|\s]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '') || 'export';
}

/** 截取元素 → 返回 jsPDF 文档（A4 纵向，长内容自动分页切片，避免重复嵌图导致体积膨胀） */
async function capturePdf(el) {
  await ensureExportLibraries();
  if (typeof window.html2canvas !== 'function') throw new Error('截图组件未加载（vendor/html2canvas.min.js）');
  const jsPDFCtor = window.jspdf && window.jspdf.jsPDF;
  if (!jsPDFCtor) throw new Error('PDF 组件未加载（vendor/jspdf.umd.min.js）');

  const bg = pageBg();
  const canvas = await window.html2canvas(el, {
    backgroundColor: bg,
    scale: Math.min(2, Math.max(1.5, window.devicePixelRatio || 1.5)),
    useCORS: true,
    logging: false,
    scrollX: 0,
    scrollY: 0,
    windowWidth: el.scrollWidth,
    windowHeight: el.scrollHeight,
  });

  const pdf = new jsPDFCtor({ unit: 'pt', format: 'a4', compress: true });
  const pw = pdf.internal.pageSize.getWidth();
  const ph = pdf.internal.pageSize.getHeight();
  // 每个 PDF 页对应的源画布像素高度
  const pxPerPage = Math.floor((canvas.width * ph) / pw);

  const slice = document.createElement('canvas');
  const ctx = slice.getContext('2d');
  slice.width = canvas.width;

  let sy = 0;
  let first = true;
  while (sy < canvas.height) {
    const sliceH = Math.min(pxPerPage, canvas.height - sy);
    slice.height = sliceH;
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, slice.width, sliceH);
    ctx.drawImage(canvas, 0, sy, canvas.width, sliceH, 0, 0, canvas.width, sliceH);
    const img = slice.toDataURL('image/jpeg', 0.92);
    const drawH = (sliceH * pw) / canvas.width;
    if (!first) pdf.addPage();
    pdf.addImage(img, 'JPEG', 0, 0, pw, drawH);
    sy += sliceH;
    first = false;
  }
  return pdf;
}

/** 生成并下载 PDF */
export async function downloadPdf(el, filename) {
  if (!el) { toast('没有可导出的内容', 'error'); return { ok: false, error: '没有可导出的内容' }; }
  toast('正在生成 PDF…', 'info', 2500);
  try {
    const pdf = await capturePdf(el);
    pdf.save(safeName(filename) + '.pdf');
    toast('PDF 已生成，下载已开始', 'success', 5000);
    return { ok: true };
  } catch (e) {
    toast('生成 PDF 失败：' + e.message, 'error');
    return { ok: false, error: e.message };
  }
}

/** 生成 PDF 并经后端推送到已连通讯软件（微信等）。caption 作为随附文字。 */
export async function pushPdf(el, filename, caption = '') {
  if (!el) { toast('没有可导出的内容', 'error'); return; }
  toast('正在生成并推送 PDF…', 'info', 2500);
  try {
    const pdf = await capturePdf(el);
    const blob = pdf.output('blob');
    const fd = new FormData();
    fd.append('file', blob, safeName(filename) + '.pdf');
    if (caption) fd.append('caption', caption);
    const res = await fetch('/api/export/push', { method: 'POST', body: fd });
    const r = await res.json().catch(() => ({}));
    if (res.ok && r.ok) toast('已推送' + (r.msg ? '（' + r.msg + '）' : ''), 'success');
    else toast('推送失败：' + (r.error || r.msg || ('HTTP ' + res.status)), 'error');
  } catch (e) {
    toast('推送失败：' + e.message, 'error');
  }
}
