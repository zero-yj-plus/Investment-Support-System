/* swr.js — stale-while-revalidate 读取层
 *
 * 解决「每次打开页面都空白 loading 等待」：先把上次成功的数据从 localStorage 同步秒出
 * （不显 skeleton），同时后台 api.get 拉新；新数据到位无缝替换，失败则保留旧数据不覆盖。
 *
 * 设计与既有约定一致：
 *  - 复用 api.js 的 api.get / lsGet / lsSet，不另起一套 fetch / 存储逻辑。
 *  - 失败绝不覆盖好数据——与 dashboard.js 的 serve-last-good（staleGuard）同一信条：
 *    宁可显示稍旧的真实数据，也不把已渲染的好内容刷成空/错。本模块负责「跨刷新」的旧数据，
 *    视图内的 staleGuard 负责「同一会话内后台刷新」的归零防护，两者叠加、互不冲突。
 *  - 纯读取缓存，不缓存写操作（POST/PUT/DELETE 不经此处）。
 *
 * 缓存信封：{ d: <原始响应>, t: <写入时刻 epoch ms> }，key = 'swr:' + path。
 * 存 t 是为了 opts.maxAge 判定「太旧的缓存视为无」（首屏仍走 skeleton，不秒出过期数据）。
 */

import { api, lsGet, lsSet } from './api.js';

const PREFIX = 'swr:';
const keyOf = (path) => PREFIX + path;

/**
 * 同步取缓存信封（含写入时刻）。无缓存返回 null。
 * @returns {{ data:any, ts:number, age:number }|null}
 */
function peekEnvelope(path) {
  const env = lsGet(keyOf(path), null);
  // 仅认带 d 字段的新信封；老格式 / 脏数据一律当无缓存（绝不把残形当好数据秒出）
  if (!env || typeof env !== 'object' || !('d' in env)) return null;
  const ts = Number(env.t) || 0;
  return { data: env.d, ts, age: ts ? Date.now() - ts : Infinity };
}

/**
 * 同步取上次成功数据（仅数据本体，无则返回 fallback）。供视图首屏「先铺旧数据再后台刷新」。
 * @param {string} path  与 api.get 相同的接口路径，如 '/sentiment/today'
 * @param {any} [fallback=null]
 */
export function swrPeek(path, fallback = null) {
  const env = peekEnvelope(path);
  return env ? env.data : fallback;
}

/**
 * 手动写入缓存（成功数据）。swrGet 内部成功时会自动调用；也可在视图里手动落地一份。
 */
export function swrSet(path, data) {
  lsSet(keyOf(path), { d: data, t: Date.now() });
}

/**
 * stale-while-revalidate 读取。
 *
 * 行为：
 *  ① 立即从 localStorage 读上次成功数据 —— 有且未过期(maxAge)则同步回调 onData(data,{stale:true})，
 *     视图据此秒渲染、不显 loading（age 同时给出，便于标「x 秒前 · 更新中」）。
 *  ② 同时 api.get(path)：
 *       成功 → 写回缓存 + onData(fresh,{stale:false})（无缝替换旧内容）。
 *       失败 → 保留旧缓存、不覆盖；onError(err,{hadCache}) 通知（视图可据此决定首屏是否报错）。
 *
 * @param {string} path  接口路径
 * @param {(data:any, meta:{stale:boolean, age:number, fromCache:boolean})=>void} onData
 *        stale=true 为缓存命中的旧数据；stale=false 为本次拉到的新数据。
 *        age = 数据写入至今的毫秒数（新数据为 0）；fromCache 标记本帧是否来自缓存。
 * @param {object} [opts]
 * @param {number} [opts.maxAge=Infinity]  缓存最大有效毫秒数；超龄缓存视为无，首屏仍走 skeleton。
 * @param {(err:Error, meta:{hadCache:boolean})=>void} [opts.onError]
 *        拉取失败回调；hadCache 标记此前是否已用缓存秒出（true 时视图通常静默保留旧数据，不报错）。
 * @param {boolean} [opts.revalidate=true]  置 false 则只吃缓存、不发后台请求（极少用）。
 * @returns {Promise<{ ok:boolean, data?:any, stale?:boolean, error?:Error, hadCache:boolean }>}
 *          resolve 时附带最终结果；调用方一般无需 await（onData 已驱动渲染）。
 */
export function swrGet(path, onData, opts = {}) {
  const { maxAge = Infinity, onError, revalidate = true } = opts;

  // ① 同步吃缓存：未过期才回调，过期缓存当无（让视图照常显 skeleton，不秒出陈旧数据）
  const cached = peekEnvelope(path);
  const hadFresh = !!cached && cached.age <= maxAge;
  if (hadFresh && typeof onData === 'function') {
    try { onData(cached.data, { stale: true, age: cached.age, fromCache: true }); } catch { /* 渲染异常不阻断后台刷新 */ }
  }

  if (!revalidate) {
    return Promise.resolve(hadFresh
      ? { ok: true, data: cached.data, stale: true, hadCache: true }
      : { ok: false, hadCache: false });
  }

  // ② 后台拉新：成功写回 + 回调新数据；失败保留旧数据不覆盖
  return api.get(path).then(
    (fresh) => {
      swrSet(path, fresh);
      if (typeof onData === 'function') {
        try { onData(fresh, { stale: false, age: 0, fromCache: false }); } catch { /* noop */ }
      }
      return { ok: true, data: fresh, stale: false, hadCache: hadFresh };
    },
    (err) => {
      // 关键：失败时绝不动缓存、绝不回调空数据——旧的好数据继续留在屏上
      if (typeof onError === 'function') {
        try { onError(err, { hadCache: hadFresh }); } catch { /* noop */ }
      }
      return { ok: false, error: err, hadCache: hadFresh };
    },
  );
}
