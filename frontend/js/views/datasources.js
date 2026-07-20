/* 已下线视图·墓碑占位（数据源页退役）
   旧缓存的 app.js 若仍 import 本文件，404 会让整站白屏——墓碑保启动；新版已不引用。 */

export function mount() {
  location.hash = '#/cockpit';
}

export function unmount() {}
