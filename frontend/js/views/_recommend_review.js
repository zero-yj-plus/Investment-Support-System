/* Shared recommendation-review v2 semantics.
   Keep this module data-only so every surface uses the same contract without
   inventing prices, percentages, or review states in the browser. */

export const REVIEW_WINDOWS = Object.freeze(['T0', 'T1', 'T2', 'T5']);

const PENDING_DETAIL = Object.freeze({
  window_not_due: '目标交易日尚未到期',
  target_trade_day_unknown: '目标交易日尚未确认',
  trigger_unknown: '入场条件缺少可核验证据',
  target_close_missing: '目标日收盘价缺失',
});

export function reviewStateMeta(review) {
  if (!review) {
    return { key: 'waiting', tone: 'waiting', label: '待生成', detail: '尚无该窗口回验记录' };
  }

  const state = String(review.evaluation_state || '');
  const verdict = String(review.verdict || '');
  const pendingReason = String(review.pending_reason || '');
  const detail = PENDING_DETAIL[pendingReason] || '';

  if (state === 'waiting') {
    const label = pendingReason === 'target_trade_day_unknown' ? '目标日待确认' : '待到期';
    return { key: 'waiting', tone: 'waiting', label, detail: detail || '等待目标交易日和收盘数据' };
  }
  if (state === 'untriggered' || verdict === 'not_triggered') {
    return { key: 'untriggered', tone: 'neutral', label: '未触发', detail: '入场条件未触发，不计输赢' };
  }
  if (state === 'incomparable' || verdict === 'insufficient_evidence') {
    if (verdict === 'insufficient_evidence' || pendingReason === 'trigger_unknown') {
      return { key: 'incomparable', tone: 'warning', label: '证据不足', detail: detail || '缺少完成回验所需的证据，不计输赢' };
    }
    return { key: 'incomparable', tone: 'warning', label: '不可比', detail: detail || '当前证据不足以形成可比结果' };
  }
  if (verdict === 'hit') {
    return { key: 'hit', tone: 'positive', label: '方向命中', detail: '方向调整后收益为正' };
  }
  if (verdict === 'partial_hit') {
    return { key: 'partial_hit', tone: 'neutral', label: '部分命中', detail: '方向调整后收益为 0' };
  }
  if (verdict === 'invalidated') {
    return { key: 'invalidated', tone: 'negative', label: '方向失效', detail: review.error_reason || '未达预期方向' };
  }

  // Compatibility for old append-only rows. New v2 rows should never need it.
  if (review.hit_status === 'hit') return { key: 'hit', tone: 'positive', label: '方向命中', detail: '历史回验记录' };
  if (review.hit_status === 'miss') return { key: 'invalidated', tone: 'negative', label: '方向失效', detail: review.error_reason || '历史回验记录' };
  return { key: 'incomparable', tone: 'warning', label: '证据不足', detail: '无法识别该历史回验的评价口径' };
}

export function reviewWindowRows(rec) {
  const supplied = rec?.review_windows && typeof rec.review_windows === 'object'
    ? rec.review_windows : {};
  const reviews = Array.isArray(rec?.reviews) ? rec.reviews : [];
  return REVIEW_WINDOWS.map((window) => {
    const candidates = reviews.filter((item) => item?.window === window);
    const fallback = candidates.length ? candidates[candidates.length - 1] : null;
    const review = supplied[window] || fallback;
    return { window, review, meta: reviewStateMeta(review) };
  });
}

export function latestReview(rec) {
  if (rec?.review) return rec.review;
  const rows = reviewWindowRows(rec).map((item) => item.review).filter(Boolean);
  return rows.length ? rows[rows.length - 1] : null;
}

// stats v2 percentages are already 0–100. Reject unknown units instead of
// silently guessing whether 0.62 means 0.62% or 62%.
export function statsPercent(stats, key) {
  if (!stats || stats.win_rate_unit !== 'percent' || stats[key] == null) return null;
  const value = Number(stats[key]);
  return Number.isFinite(value) ? value : null;
}

export function reviewCoverage(review) {
  const coverage = review?.coverage && typeof review.coverage === 'object' ? review.coverage : {};
  return {
    observed: coverage.bars_observed ?? null,
    expected: coverage.bars_expected ?? null,
    complete: coverage.path_complete === true,
  };
}
