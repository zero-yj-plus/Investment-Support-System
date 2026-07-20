#!/usr/bin/env bash
# 数据时效防错闸(出包前必跑)——2026-07-06 事故:扫描时间戳用 time.strftime(本机PDT)把 15:15 标成 00:15。
# 铁律:交易系统所有"给人看/做判断"的时间必须走 now_cn()(北京)。本闸扫共享核心里的裸时钟调用,扫出即拒绝出包。
set -e
cd "$(dirname "$0")/.."
BAD=$(grep -rnE '\btime\.strftime\(|datetime\.now\(\)|datetime\.today\(\)|date\.today\(\)' hermes_trader --include='*.py' \
  | grep -v 'now_cn\|# clock-ok\|monotonic' || true)
if [ -n "$BAD" ]; then
  echo "❌ 裸时钟调用(必须 now_cn(),或确认无害后行尾注 # clock-ok):"
  echo "$BAD"
  exit 1
fi
echo "✓ 时钟检查过(无裸 time.strftime/datetime.now)"
