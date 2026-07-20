/* views/macro.js — 宏观风险偏好（替代早期「期货期权」过度发挥）
   ------------------------------------------------------------------
   母文档定位（§8.3 / §4.2）：原油 / 黄金 / 美债 / 美元指数 = A股【风险偏好】辅助信号，
   **不是交易标的**。本页只把这些宏观品种当作 A股环境的「温度计」，给客观常识映射，绝不预测点位。

   ★ 数据真实性铁律（不可破）：
     · 商品风险卡 = 真实期货行情（GET /api/deriv/futures 的 commodity：原油 SC0/黄金 AU0/白银 AG0），
       盘中实时 / 盘后取上一收盘，全部由后端确定性抓取注入，本页只展示，绝不生成/改写任何数字。
     · 美债收益率 / 美元指数 **当前无数据源** → 明确标「未接入数据源」，
       只给方向性常识文字，**不显示任何点位/收益率数字**，绝不编造。
     · 取不到 /api/deriv/futures → 友好空状态/标缺，不堆假数据。
     · 单位约定：价/成交额/市值=元，涨跌幅/换手=%（商品单位以后端 unit 字段为准，如 元/桶、元/克）。
   并行纪律：本视图仅新建本文件，不引图表库、不改共享模块；样式带 id 幂等注入。
   交互：宏观非个股 → 卡片点击不跳诊断。 */

import { api, fmtPrice, fmtPct, pctClass } from '../api.js';
import { escapeHtml, emptyState, skeleton, dataStamp } from '../ui.js';

let root = null;
let timers = [];
// 挂载代号：主题切换 / 重路由会复用容器重挂载，旧挂载的在途 fetch 须作废，避免回填到已脱离 DOM 的旧节点（alive 守卫）。
let gen = 0;

/* 关注的三个商品（母文档 §8.3 指名的宏观辅助品种里有数据源的部分）。
   按 symbol 锁定，避免后端商品列表里其它合约（螺纹/铜/豆粕等）混进本页——本页只谈宏观风险偏好。 */
const WATCH = [
  {
    symbol: 'SC0', label: '原油', tag: '再通胀 / 成本',
    // 客观常识映射（有事实依据的宏观链路，措辞不预测点位、不给买卖建议）：
    up: '原油走强 → 再通胀 / 成本上行预期，关注 A股 资源(石油石化)、煤炭、化工链；通胀预期升温对成长股估值偏压制。',
    down: '原油走弱 → 需求担忧 / 通缩压力，利好下游成本敏感行业（交运、部分制造），但常伴随经济需求转弱信号。',
    flat: '原油横盘 → 通胀/成本预期暂稳，对 A股 风险偏好影响中性。',
  },
  {
    symbol: 'AU0', label: '黄金', tag: '避险 / 实际利率',
    up: '黄金走强 → 避险情绪升温或实际利率走低，A股 风险偏好通常受抑；黄金股相对受益，顺周期承压。',
    down: '黄金走弱 → 避险降温 / 实际利率回升，风险资产偏好回暖，对 A股 顺周期相对友好。',
    flat: '黄金横盘 → 避险情绪与实际利率预期平稳，对 A股 风险偏好影响中性。',
  },
  {
    symbol: 'AG0', label: '白银', tag: '避险 + 工业需求',
    up: '白银走强 → 兼具避险与工业(光伏/电子)属性，若伴随金价同步多为避险驱动，若独立走强多反映工业需求改善。',
    down: '白银走弱 → 避险降温或工业需求预期转弱，需结合黄金方向区分驱动。',
    flat: '白银横盘 → 避险与工业需求预期暂稳，信号偏中性。',
  },
];

function injectStyle() {
  if (document.getElementById('macro-style')) return;
  const css = `
  .mc-intro{border:1px solid var(--border);border-left:3px solid var(--amber);border-radius:var(--radius-sm);
    background:var(--panel-2);padding:11px 15px;margin-bottom:var(--gap);font-size:13px;line-height:1.65;color:var(--text-2)}
  .mc-intro b{color:var(--text)}
  .mc-sec-title{display:flex;align-items:baseline;gap:9px;margin:0 0 11px;font-size:13px;font-weight:700;
    color:var(--text-2);letter-spacing:.3px}
  .mc-sec-title .sub{font-size:12px;font-weight:400;color:var(--text-3)}

  .mc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:var(--gap);margin-bottom:var(--gap)}
  .mc-card{border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);padding:15px 16px;
    box-shadow:var(--shadow-soft);display:flex;flex-direction:column}
  .mc-card.up{border-top:2px solid var(--up)} .mc-card.down{border-top:2px solid var(--down)}
  .mc-card.flat{border-top:2px solid var(--flat)}
  .mc-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:9px}
  .mc-name{font-size:16px;font-weight:700;color:var(--text)}
  .mc-tag{font-size:11px;color:var(--text-3);border:1px solid var(--border);border-radius:10px;padding:1px 8px;white-space:nowrap}
  .mc-px{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin-bottom:4px}
  .mc-px b{font-size:25px;font-weight:800;font-family:var(--mono);font-variant-numeric:tabular-nums;line-height:1.1}
  .mc-px .unit{font-size:12px;color:var(--text-3)}
  .mc-chg{font-size:14px;font-weight:700;font-family:var(--mono);font-variant-numeric:tabular-nums}
  .mc-meta{display:flex;flex-wrap:wrap;gap:4px 12px;margin:8px 0 11px;font-size:12px;color:var(--text-3)}
  .mc-meta b{color:var(--text-2);font-family:var(--mono);font-weight:600}
  .mc-live{display:inline-flex;align-items:center;gap:4px;font-size:11px}
  .mc-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--flat)}
  .mc-dot.on{background:var(--up);box-shadow:0 0 5px var(--up-line)}
  .mc-imp{margin-top:auto;font-size:12.5px;line-height:1.6;color:var(--text-2);
    border-top:1px dashed var(--border);padding-top:9px}
  .mc-imp .lab{font-size:11px;font-weight:700;letter-spacing:.3px;color:var(--text-3);display:block;margin-bottom:3px}

  /* 占位卡（美债/美元指数：无数据源，绝不编数字） */
  .mc-card.placeholder{border-style:dashed;background:var(--panel-2);box-shadow:none}
  .mc-na-badge{font-size:11px;font-weight:700;color:var(--amber);border:1px solid var(--amber-line);
    background:var(--amber-dim);border-radius:10px;padding:1px 9px;white-space:nowrap}
  .mc-na-px{font-size:22px;font-weight:800;font-family:var(--mono);color:var(--text-3);letter-spacing:1px;margin:6px 0 4px}
  .mc-na-note{font-size:12px;color:var(--text-3);line-height:1.55;margin-bottom:11px}

  .mc-foot{font-size:12px;color:var(--text-3);line-height:1.6;margin-top:2px}
  .mc-foot b{color:var(--text-2)}
  .mc-progress{padding:11px 13px;margin-bottom:12px;border:1px solid var(--amber-line);border-radius:var(--radius-sm);background:var(--amber-dim);line-height:1.5}
  .mc-progress b{display:block;color:var(--text);font-size:13px}.mc-progress span{display:block;color:var(--text-3);font-size:12px}

  @media (max-width:760px){
    .mc-grid{grid-template-columns:1fr}
    .mc-px b{font-size:22px}
  }`;
  const tag = document.createElement('style');
  tag.id = 'macro-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}

export function mount(el) {
  gen += 1;
  root = el;
  injectStyle();
  root.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">宏观风险</h2>
      <span class="page-sub">宏观风险偏好 → A股 · 原油/黄金/白银期货 · 红涨绿跌 · 仅辅助判断不构成建议</span>
    </div>

    <div class="mc-intro">
      <b>宏观风险偏好 → A股</b>：下列宏观品种是判断 A股 <b>风险偏好</b>的辅助信号，<b>不是交易标的</b>。
      商品价格为真实期货行情（盘中实时 / 盘后取上一收盘）；风险含义为<b>客观常识映射</b>，基于真实涨跌方向，
      不预测点位、不构成买卖建议。
    </div>

    <h3 class="mc-sec-title">商品风险温度计<span class="sub">原油 / 黄金 / 白银 · 数据源 新浪期货主力连续</span></h3>
    <div id="mc-com">
      <div class="mc-progress" id="mc-progress" role="status" aria-live="polite">
        <b>正在读取商品期货行情</b><span>首次联网可能较慢，最多等待 12 秒，超时后会明确说明。</span>
      </div>
      ${skeleton(3, [120, 120, 120])}
    </div>

    <div class="mc-foot">
      口径说明：商品涨跌幅由<b>同源</b>最后两根日线计算（绝不跨源拼价）；
      <span class="mc-live"><span class="mc-dot on"></span>盘中实时</span> /
      <span class="mc-live"><span class="mc-dot"></span>上一交易日收盘</span>。
    </div>`;

  loadCommodities();

  timers.push(setTimeout(() => {
    const p = root?.querySelector('#mc-progress');
    if (p) p.innerHTML = '<b>正在等待商品期货上游</b><span>原油、黄金、白银三个品种会合并校验，不用旧日数据冒充实时。</span>';
  }, 4000));
  timers.push(setTimeout(() => {
    const p = root?.querySelector('#mc-progress');
    if (p) p.innerHTML = '<b>仍在校验行情时效</b><span>若 12 秒内无响应，本轮会自动停止并保留已有成功数据。</span>';
  }, 8000));

  // 交易时段自动刷新（商品期货随盘动；失败/空不覆盖已渲染内容，见 loadCommodities 内 gen + 空值保护）。
  // 海外利率/汇率为静态占位卡，无需刷新。
  timers.push(setInterval(() => loadCommodities(true), 30000));
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  root = null;
}

/* ================= 商品风险温度计（真实期货行情） ================= */

async function loadCommodities(bg = false) {
  const host = root?.querySelector('#mc-com');
  if (!host) return;
  const g = gen;
  let d = null;
  let loadError = null;
  try { d = await api.get('/deriv/futures'); } catch (e) { loadError = e; }
  if (g !== gen || !root) return;                 // alive 守卫：旧挂载/已脱离 DOM → 丢弃在途响应
  // 后台刷新(bg) serve-last-good：取不到/空时直接返回，保留已渲染的真实卡片。
  // 开发机中继瞬断(ProxyError)高发，否则一次网络抖动就把真卡刷成「暂无数据」空态、下周期才恢复。
  if (bg && (!d || d.ok === false || !(d.commodity || []).some((r) => r && r.symbol))) return;

  // 后端 commodity 含多个合约，本页只取母文档指名的宏观品种（按 symbol 锁定，不混入其它商品）。
  const all = (d?.commodity || []).filter((r) => r && r.symbol);
  const bySym = new Map(all.map((r) => [r.symbol, r]));
  const rows = WATCH.map((w) => ({ meta: w, q: bySym.get(w.symbol) || null }));
  const hasAny = rows.some((r) => r.q);

  if (!hasAny) {
    // 取不到任何关注品种 → 友好空状态/标缺，绝不堆假数据。
    const miss = (d?.missing || []).filter((m) => /原油|黄金|白银|SC0|AU0|AG0/.test(String(m)));
    host.innerHTML = emptyState(
      '暂无商品期货数据',
      loadError?.message || (miss.length ? miss.join(' · ') : '非交易时段或期货数据源暂不可用，稍后自动重试'),
    );
    return;
  }

  const stamp = rows.find((r) => r.q)?.q?.data_date;
  host.innerHTML = `
    ${stamp ? `<div style="text-align:right;margin-bottom:6px">${dataStamp(stamp, d?.missing)}</div>` : ''}
    <div class="mc-grid">${rows.map(commodityCard).join('')}</div>`;
}

function commodityCard({ meta, q }) {
  if (!q) {
    // 单个品种缺：明确标缺，不补零、不留空数字。
    return `<div class="mc-card flat">
      <div class="mc-head">
        <span class="mc-name">${escapeHtml(meta.label)}</span>
        <span class="mc-tag">${escapeHtml(meta.tag)}</span>
      </div>
      <div class="mc-na-px">未取到</div>
      <div class="mc-na-note">该品种期货行情暂不可用（非交易时段或数据源未就绪），不展示推断值。</div>
      <div class="mc-imp"><span class="lab">宏观含义</span>${escapeHtml(meta.flat)}</div>
    </div>`;
  }
  const cls = pctClass(q.change_pct);
  // 风险含义按真实涨跌方向取（up/down/flat），纯客观常识映射，不预测、不建议。
  const imp = cls === 'up' ? meta.up : cls === 'down' ? meta.down : meta.flat;
  const live = q.live
    ? '<span class="mc-live"><span class="mc-dot on"></span>盘中实时</span>'
    : '<span class="mc-live"><span class="mc-dot"></span>上一交易日收盘</span>';
  return `<div class="mc-card ${cls}">
    <div class="mc-head">
      <span class="mc-name">${escapeHtml(meta.label)}</span>
      <span class="mc-tag">${escapeHtml(meta.tag)}</span>
    </div>
    <div class="mc-px">
      <b class="${cls}">${fmtPrice(q.price)}</b>
      <span class="unit">${escapeHtml(q.unit || '')}</span>
      <span class="mc-chg ${cls}">${q.change_pct != null ? fmtPct(q.change_pct) : '--'}</span>
    </div>
    <div class="mc-meta">
      <span>数据日期 <b>${escapeHtml(q.data_date || '--')}</b></span>
      <span>${live}</span>
    </div>
    <div class="mc-imp"><span class="lab">宏观含义（客观常识，非预测）</span>${escapeHtml(imp)}</div>
  </div>`;
}

/* 海外利率/汇率占位卡已删除（2026-07-03 用户裁定）：美债/美元当前无确定性数据源，
   两块「未接入数据源」空卡无信息量；待接入真实源后再以数据卡形式回归。 */
