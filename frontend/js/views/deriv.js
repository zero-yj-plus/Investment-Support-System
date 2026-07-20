/* 已下线视图·墓碑占位（期货期权页 2026-06-26 母文档重构退役）
   旧缓存的 app.js 若仍 import 本文件，404 会让整站白屏——墓碑保启动；新版已不引用。 */

export function mount() {
  location.hash = '#/cockpit';
}

export function unmount() {}
