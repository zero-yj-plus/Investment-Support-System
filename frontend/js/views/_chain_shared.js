/* _chain_shared.js — 问股与研究中心共用的产业链研究请求契约。 */

export const CHAIN_RESEARCH_CONTRACT_VERSION = 'chain-research-v2';

const DEFAULT_FOCUS = '完成整套产业链研究，并给出后续需要核验的证据清单。';
export const CHAIN_RESEARCH_SCOPE_COPY = {
  label: '上下游思维优先',
  detail: '先拆需求、产业地图、利润池、卡点和国产替代；公司与数据只作辅助线索，交易判断另行处理。',
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
      '必须沿同一条推导链完成：核心需求与驱动力 → 上中下游全景 → 利润池与价值分配 → 核心卡点与竞争壁垒 → 国产替代路径 → A股映射思路 → 兑现节奏与观察指标 → 风险与失效条件。',
      '重点解释产业为什么成立、价值如何沿上下游传导、利润为什么集中、瓶颈如何形成；不要把报告写成数据清单。',
      '不要逐段输出依据边界、待公告核验、数据不全或数值占位；真实性边界只在报告末尾集中说明一次。',
      '产业研究与交易时点必须分开；本报告不得生成目标价、仓位或即时买卖指令。',
      safeImages.length
        ? '用户附图只作补充线索；图中内容须标为用户提供，不得覆盖系统数据时点或证据等级。'
        : '',
    ].filter(Boolean).join('\n'),
  };
}
