"""问股多视角白名单与提示词协议。

这些配置是从用户提供的公开材料型 perspective Skill 中提炼出的分析框架，
不是 Codex Skill 运行时，也不执行原包中的脚本、网络请求或角色扮演指令。
所有视角共享问股服务端已经冻结的同一份事实与风险结论。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


MAX_SELECTED_PERSPECTIVES = 4
PERSPECTIVE_VERSION = "2026-08-06"


class PerspectiveSelectionError(ValueError):
    """前端提交的视角选择不符合白名单契约。"""


@dataclass(frozen=True)
class StockPerspective:
    id: str
    name: str
    category: str
    description: str
    tags: tuple[str, ...]
    instruction: str
    version: str = PERSPECTIVE_VERSION

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "tags": list(self.tags),
            "version": self.version,
        }


_SHORT_TERM = "A股短线与游资"
_OTHER = "宏观与趋势交易"


PERSPECTIVES: tuple[StockPerspective, ...] = (
    StockPerspective(
        "92kobe", "92科比", _SHORT_TERM,
        "从情绪周期、题材时机、人气与换手判断短线位置。",
        ("情绪周期", "题材时机", "换手"),
        "优先判断情绪处于启动、发酵、高潮还是退潮；只讨论绝对高位核心或绝对低位新机会，警惕中位股；检查题材出现的时机、模仿扩散、人气形成的流动性溢价以及充分换手。",
    ),
    StockPerspective(
        "aizaibingchuan", "爱在冰川", _SHORT_TERM,
        "强调情绪圆、存量博弈、预期差和逻辑潜伏。",
        ("情绪圆", "预期差", "存量博弈"),
        "沿冰点、升温、高潮、退潮定位当前阶段；检查新热点是否抽走旧热点资金、一致性是否透支、是否存在可验证的预期差；用买入驱动、博弈节点、盈利目标、失效条件审视计划，鱼尾或看不懂时以克制为先。",
    ),
    StockPerspective(
        "chaoguyangjia", "炒股养家", _SHORT_TERM,
        "用赚钱效应、分歧一致和龙头分层评估赢面。",
        ("赚钱效应", "分歧一致", "龙头分层"),
        "先看市场天气和赚钱亏钱效应，再区分龙头、中军、跟风与补涨；判断机会是否产生于分歧、风险是否来自过度一致；采用预判、试错、确认的递进思路，只评价证据支持的赢面，不按用户成本价锚定结论。",
    ),
    StockPerspective(
        "chenxiaoqun", "陈小群", _SHORT_TERM,
        "围绕主线龙头、资金合力、预期差与空仓纪律。",
        ("主线龙头", "资金合力", "预期差"),
        "先判断是否应该参与，再看个股；核对主线、龙头地位和资金合力，重点识别该弱不弱或该强不强的预期差；市场退潮、标的非核心或合力不足时明确指出空仓/等待理由，不把主观预判置于市场反馈之上。",
    ),
    StockPerspective(
        "geweidong", "葛卫东", _OTHER,
        "从主要矛盾、长期方向、赔率和生存优先审视交易。",
        ("主要矛盾", "趋势", "赔率"),
        "把市场视为概率系统而非确定预测；区分长期基本方向与短期情绪摆动，寻找当前主要矛盾和阻力最小方向；只有基本面方向与技术时机共振、风险收益具有足够赔率时才提高评价，否则强调宁缺毋滥和活下来。",
    ),
    StockPerspective(
        "huadongdandaodan", "华东大导弹", _SHORT_TERM,
        "用审美过滤、竞价信号、小周期补涨和快速认错判断机会。",
        ("审美过滤", "小周期", "快速认错"),
        "先用高标准过滤模式外机会；重点检查主线中的补涨、身位和高低切，小周期是否成立、竞价和午后确认是否一致；不固守立场，亏损或证据变化视为转向信号，并检查标的是否具备历史涨停与辨识度基础。",
    ),
    StockPerspective(
        "ningbogansidui", "宁波敢死队", _SHORT_TERM,
        "以势、量、板和龙头唯一性观察强势机会。",
        ("势量板", "龙头", "快进快出"),
        "依次检查大盘与板块之势、板块第一强者、量价是否极端且相互确认，再讨论时机；先写清失效和退路，不追孤立跟风；明确这是早期市场方法学，旧参数不能机械套用到注册制和量化环境。",
    ),
    StockPerspective(
        "renzeping", "任泽平", _OTHER,
        "从总量、周期、流动性到资产映射提供宏观背景。",
        ("宏观周期", "流动性", "资产映射"),
        "按总量位置、长中短周期叠加和流动性传导分析宏观背景，再说明它如何影响行业与风险偏好；宏观只能作为环境变量，不得替代个股证据，也不得把传播性判断写成确定事实。",
    ),
    StockPerspective(
        "tuixuechaogu", "退学炒股", _SHORT_TERM,
        "强调主线、确定性、回撤约束和识别情绪化的“小明”。",
        ("自我纪律", "回撤", "确定性"),
        "检查本次判断是否属于既定模式、是否偏离主线、是否受到踏空、连续盈利或大亏后的冲动驱动；把操作质量与单次盈亏分开，优先杜绝大回撤和模式外交易；旧打板环境结论必须按当前证据校准。",
    ),
    StockPerspective(
        "yishunliuguang", "一瞬流光", _SHORT_TERM,
        "以等待、板块阶段、流动性和有限亏损寻找高效机会。",
        ("等待", "板块阶段", "流动性"),
        "先判断板块处于上升、震荡还是衰退阶段；上升期看核心强度，震荡与衰退期强调等待；检查封单、换手、新高基因和辨识度，排除无板块支撑或筹码断层标的；评价重点放在选择是否正确和最大可承受损失。",
    ),
    StockPerspective(
        "zhaolao-ge", "赵老哥", _SHORT_TERM,
        "聚焦二板确认、新题材、龙头纯粹性与铁律止损。",
        ("二板确认", "新题材", "龙头"),
        "区分首板噪声与二板后的资金确认，只研究主线龙头或退潮后的唯一活口；新题材出现时评估旧题材是否失去资金凝聚；不补仓摊错，热点退潮或买入逻辑失效时明确退出边界。该框架公开一手材料稀少，结论置信度需保守。",
    ),
    StockPerspective(
        "asking", "Asking", _SHORT_TERM,
        "按等待、发现、跟随的顺序，只做最强并尊重弱市。",
        ("等待跟随", "最强", "弱市不做"),
        "不创造和预测机会，先等待，再发现市场已经形成的势，最后才讨论跟随；关注人气、领涨和龙头地位，弱市或非核心标的直接降低评价；把心态控制和执行一致性置于具体技巧之上。",
    ),
    StockPerspective(
        "beijing-chaogu", "北京炒家", _SHORT_TERM,
        "以概率、分仓、首板亮点和知行一致评估交易。",
        ("概率", "分仓", "首板"),
        "把单次交易视为概率样本，不要求每笔正确；检查买入是否有清晰亮点、是否属于简单可重复的机会以及大势是否支持；强调分仓降低单票冲击、永不补仓、连续亏损后降速，不用事后叙事替代盘面事实。",
    ),
    StockPerspective(
        "huanle-haian", "欢乐海岸", _SHORT_TERM,
        "专注迷茫期妖股、分歧造妖、气质与龙头多条命。",
        ("妖股", "分歧", "龙回头"),
        "判断市场是否处于迷茫和亏钱效应后的情绪窗口，标的是普通强势股还是真正带动市场的核心；检查分歧换手、筹码连续性、辨识度和主升空间，不碰跟风；对缩量加速、筹码断层和过早预判龙回头保持警惕。",
    ),
    StockPerspective(
        "livermore", "利弗莫尔", _OTHER,
        "以趋势、关键点、试探仓递增和失败纪律观察大行情。",
        ("趋势", "关键点", "递增确认"),
        "只沿主要趋势和阻力最小方向评估，忽略无意义的小波动；关注关键点突破是否得到后续确认，判断正确后才允许逐步增加暴露，错误则快速退出；突出失败案例和纪律，不把历史固定点数规则机械映射到当前价格。",
    ),
    StockPerspective(
        "qiao-bangzhu", "乔帮主", _SHORT_TERM,
        "通过题材、龙头、换手、均线漏斗寻找恐慌低吸。",
        ("低吸", "四级漏斗", "承接"),
        "按题材持续性、龙头地位、回调换手和均线/结构承接逐层过滤；区分动态跟随与主观猜底，只有恐慌后出现真实承接和止跌证据才评价低吸机会；强调集中选择必须配套严格失效条件，空仓也是主动选择。",
    ),
    StockPerspective(
        "zhang-mengzhu", "章盟主", _SHORT_TERM,
        "围绕龙头、趋势共振、逻辑止损和二波分歧。",
        ("龙头", "趋势共振", "逻辑止损"),
        "先确认板块和个股是否处于上升通道并具备灵魂股地位；关注龙头首轮休整后的第一次有效分歧，而非跟风杂毛；止损依据是买入逻辑和结构是否破坏，方向不清时保留资金，避免把短期波动误写成长期确定性。",
    ),
)


PERSPECTIVE_BY_ID = {item.id: item for item in PERSPECTIVES}


def public_perspectives() -> list[dict]:
    return [item.public_dict() for item in PERSPECTIVES]


def resolve_perspective_ids(raw: object) -> tuple[str, ...]:
    """校验、去重并保持用户选择顺序。空值表示使用默认问股分析。"""
    if raw in (None, ""):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise PerspectiveSelectionError("perspective_ids 必须是数组")
    selected: list[str] = []
    for value in raw:
        perspective_id = str(value or "").strip()
        if not perspective_id or perspective_id in selected:
            continue
        if perspective_id not in PERSPECTIVE_BY_ID:
            raise PerspectiveSelectionError(f"未知分析视角：{perspective_id}")
        selected.append(perspective_id)
    if len(selected) > MAX_SELECTED_PERSPECTIVES:
        raise PerspectiveSelectionError(
            f"每轮最多选择 {MAX_SELECTED_PERSPECTIVES} 个分析视角"
        )
    return tuple(selected)


def selected_metadata(ids: Iterable[str]) -> list[dict]:
    return [PERSPECTIVE_BY_ID[item].public_dict() for item in ids]


def build_perspective_prompt(ids: Iterable[str]) -> str:
    resolved = tuple(ids)
    if not resolved:
        return ""
    profiles = [PERSPECTIVE_BY_ID[item] for item in resolved]
    sections = []
    for profile in profiles:
        sections.append(
            f"#### {profile.name}视角\n"
            f"关注框架：{profile.instruction}"
        )
    names = "、".join(item.name for item in profiles)
    return (
        "\n\n【多视角会诊·服从问股最高治理约束】\n"
        f"本轮用户选择了 {names}，请把它们作为并列分析镜头，不是可改写事实的角色。\n"
        "- 不得声称自己是这些人物，不模仿本人发言，不把输出表述为本人观点。\n"
        "- 所有视角只使用统一分析资料包；不得自行联网、调用工具、补造数字或引入资料包外事件。\n"
        "- 每个视角用一个同名四级标题和 2—3 句独立判断，指出最关键的支持证据、主要风险或失效信号；"
        "不得重复抄写整套行情。\n"
        "- 各视角允许有分歧，但都无权覆盖 frozen_decision。若其历史方法与冻结动作冲突，"
        "明确写该框架在本轮不适用，不得修改动作、仓位、价位或缺失语义。\n"
        "- 各视角之后必须输出“### 多视角共识与分歧”，分别说明共识、分歧及当前证据更支持哪一种观察方式；"
        "最终仍以“### 执行边界”收束。\n\n"
        + "\n\n".join(sections)
    )
