/* live.js — 盘中数字跳动
   拿到真实新数据、且值确实变了时：把数字从旧值「滚动」到新值 + 红涨绿跌闪一下。
   铁律（数据真实）：只有真实新值变化才动；值没变就不动，更不会合成假跳动来"显得实时"。
   用法：
     import { tickNum, flash, seed } from '../live.js';
     tickNum(el, 3218.44, (n) => fmtNum(n, 2));   // el 是承载数字的 DOM 节点
     flash(pctEl, 'up');                          // 只闪不滚（用于带 +/- 号的百分比/盈亏）
     seed(el, 3210.11);                           // 整块重建后播种基准，避免首次误闪/乱滚 */

const ROLL_MS = 420;   // 滚动时长
const FLASH_MS = 620;  // 闪烁时长，需与 app.css 的 @keyframes lvUp/lvDown 对齐

/** 数字就地跳动：el._lv 记上次真实值；首帧只落值不动画，之后每次真变化滚动+闪。 */
export function tickNum(el, val, fmt) {
  if (!el || val == null || !isFinite(Number(val))) return;
  const v = Number(val);
  const f = typeof fmt === 'function' ? fmt : ((n) => Number(n).toFixed(2));
  const prev = el._lv;
  if (prev == null || !isFinite(prev)) { el.textContent = f(v); el._lv = v; return; }
  if (v === prev) return;                    // 没变 → 不动（诚实：不制造假跳动）
  const from = prev, to = v, t0 = performance.now();
  if (el._raf) cancelAnimationFrame(el._raf);
  const step = (now) => {
    const k = Math.min(1, (now - t0) / ROLL_MS);
    el.textContent = f(from + (to - from) * k);
    if (k < 1) { el._raf = requestAnimationFrame(step); }
    else { el.textContent = f(to); el._lv = to; el._raf = 0; }
  };
  el._raf = requestAnimationFrame(step);
  flash(el, to >= from ? 'up' : 'down');     // 逐笔红涨绿跌：本次相对上次的方向
}

/** 只闪一下：用于已有 +/- 文本、不便数值插值的节点（涨跌幅、盈亏额）。dir: 'up'|'down' */
export function flash(el, dir) {
  if (!el) return;
  const cls = dir === 'down' ? 'lv-down' : 'lv-up';
  el.classList.remove('lv-up', 'lv-down');
  void el.offsetWidth;                       // 强制重排 → 重新触发同名动画
  el.classList.add(cls);
  clearTimeout(el._lvt);
  el._lvt = setTimeout(() => el.classList.remove(cls), FLASH_MS);
}

/** 播种基准值：整块 innerHTML 重建后调用，让「第一次真变化」从正确基准滚动、且不误闪。 */
export function seed(el, val) {
  if (el && val != null && isFinite(Number(val))) el._lv = Number(val);
}
