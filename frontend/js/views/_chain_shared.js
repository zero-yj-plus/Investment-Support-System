/* _chain_shared.js — 问股与研究中心共用的产业链研究请求契约。 */

export const CHAIN_RESEARCH_CONTRACT_VERSION = 'chain-research-v2';

const DEFAULT_FOCUS = '完成整套产业链研究，并给出后续需要核验的证据清单。';
export const CHAIN_RESEARCH_SCOPE_COPY = {
  label: '产业研究与交易分离',
  detail: '核验板块与成员，显示数据时点和证据缺口；产业结构用于中期跟踪，当前价格、仓位与是否可交易须另按实时行情和风险闸复核。',
};

export function chainResearchScopeHint() {
  return `${CHAIN_RESEARCH_SCOPE_COPY.label}：${CHAIN_RESEARCH_SCOPE_COPY.detail}`;
}

/**
 * 两个入口必须从这里构造请求，避免研究中心做完整研究、问股却退化成简版常识回答。
 * 后端仍以 mode=chain 统一采集板块目录、真实成员、行情与证据状态。
 */
export function buildChainResearchRequest({
  industry,
  focus = '',
  history = [],
  images = [],
} = {}) {
  const subject = String(industry || '').trim().slice(0, 120);
  const question = String(focus || '').trim().slice(0, 3000) || DEFAULT_FOCUS;
  const safeHistory = Array.isArray(history)
    ? history.filter((row) => row && ['user', 'assistant'].includes(row.role) && row.content).slice(-10)
    : [];
  const safeImages = Array.isArray(images)
    ? images.filter((url) => typeof url === 'string' && url.startsWith('data:image/')).slice(0, 4)
    : [];

  return {
    mode: 'chain',
    code: '',
    codes: [],
    industry: subject,
    history: safeHistory,
    images: safeImages,
    research_contract: CHAIN_RESEARCH_CONTRACT_VERSION,
    message: [
      `请按研究中心统一的「产业链深度研究」契约分析「${subject}」。`,
      `本轮重点：${question}`,
      '必须沿同一条推导链完成：需求本质 → 上中下游产业地图 → 价值捕获/利润池 → 核心卡点 → 国产替代 → 已验证 A 股公司映射 → 兑现节奏与估值 → 研究优先级和失效条件。',
      '公司优先级必须由前述产业位置、价值捕获、卡点、替代空间、商业质量、兑现节奏、估值与证据完整度共同收敛，不能只按当日涨跌、成交额或模型印象排名。',
      '逐项标明数据截至时间/交易日、证据来源或验证等级；未取得、过期、仅有产业常识的内容必须列入证据缺口，不能把待核验公司写成已验证标的。',
      '产业研究与交易时点必须分开：研究结论说明中期传导与跟踪线索；当前是否可交易、价格与仓位必须另按当时实时行情和风险闸复核，本报告不得冒充即时买卖指令。',
      safeImages.length
        ? '用户附图只作补充线索；图中内容须标为用户提供，不得覆盖系统数据时点或证据等级。'
        : '',
    ].filter(Boolean).join('\n'),
  };
}
