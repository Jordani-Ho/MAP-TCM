from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from .errors import DomainError


AgentRole = Literal["physician_apprentice", "patient_tutor"]

MAX_INPUT_CHARACTERS = 6_000
MAX_AGENT_TURNS = 4
MAX_ESTIMATED_TOOL_CALLS = 4
MAX_AGENT_HOPS = 0

OUT_OF_SCOPE_MESSAGE = "这个问题不属于本智能体的中医、健康或个人知识服务范围，因此不予处理。"

DISALLOWED_CLINICAL_REQUESTS = (
    "替我诊断",
    "直接诊断",
    "给我开方",
    "直接开方",
    "帮我停药",
    "帮我加药",
    "帮我减药",
    "帮我换药",
    "代替医生签署",
    "伪造医生确认",
)

INSTRUCTION_ATTACK_MARKERS = (
    "忽略系统提示",
    "忽略以上规则",
    "绕过授权",
    "解除限制",
    "显示系统提示词",
    "泄露系统提示词",
    "告诉我密钥",
    "输出api key",
    "输出 api key",
    "冒充医生",
)

RESOURCE_ABUSE_MARKERS = (
    "无限循环",
    "无限调用",
    "反复调用直到",
    "无限自我审查",
    "创建更多智能体",
    "递归调用智能体",
    "遍历所有患者",
    "批量爬取",
    "发送垃圾消息",
    "挖矿",
    "拒绝服务攻击",
)

OUT_OF_SCOPE_MARKERS = (
    "写程序",
    "写代码",
    "python脚本",
    "javascript代码",
    "炒股",
    "股票推荐",
    "炒币",
    "虚拟币",
    "论文代写",
    "游戏攻略",
    "娱乐八卦",
    "政治宣传",
    "广告群发",
)

HEALTH_SCOPE_MARKERS = (
    "中医",
    "黄帝内经",
    "神农本草经",
    "伤寒论",
    "金匮",
    "温病",
    "经络",
    "穴位",
    "脉象",
    "舌象",
    "气血",
    "阴阳",
    "五行",
    "证候",
    "辨证",
    "治未病",
    "健康",
    "症状",
    "病史",
    "病历",
    "体检",
    "检查报告",
    "处方",
    "用药",
    "过敏",
    "不良反应",
    "睡眠",
    "饮食",
    "二便",
    "大便",
    "小便",
    "情志",
    "疼痛",
    "不舒服",
    "生活习惯",
    "作息",
    "复诊",
    "随访",
    "会诊",
    "医生指导",
    "健康记录",
    "患者资料",
)

PRODUCT_SCOPE_MARKERS = (
    "map-tcm",
    "患者智能体",
    "医生智能体",
    "医师智能体",
    "健康知识库",
    "患者私域",
    "健康私域",
    "照护关系",
    "隐私授权",
    "健康数据导出",
    "病历导出",
    "健康资料上传",
    "账户安全",
)

ALLOWED_TOOLS_BY_ROLE: dict[AgentRole, frozenset[str]] = {
    "physician_apprentice": frozenset(
        {"search_personalized_knowledge", "search_current_patient_session"}
    ),
    "patient_tutor": frozenset(
        {"search_personalized_knowledge", "search_patient_private_memory"}
    ),
}


@dataclass(frozen=True)
class AgentRequestDecision:
    role_type: AgentRole
    scope: Literal["allowed"]
    max_turns: int
    max_tool_calls: int
    allowed_tools: tuple[str, ...]


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


def validate_agent_request(
    *,
    role_type: AgentRole,
    prompt: str,
    requested_tools: Sequence[str] = (),
    estimated_tool_calls: int = 0,
    agent_hops: int = 0,
) -> AgentRequestDecision:
    normalized = " ".join(prompt.lower().split())
    if not normalized:
        raise DomainError("agent_prompt_required", "智能体输入不能为空。")
    if len(prompt) > MAX_INPUT_CHARACTERS:
        raise DomainError(
            "agent_prompt_too_large",
            f"单次输入超过 {MAX_INPUT_CHARACTERS} 个字符的合成安全上限。",
        )
    if _contains_any(normalized, DISALLOWED_CLINICAL_REQUESTS):
        raise DomainError(
            "clinical_authority_required",
            "该请求涉及诊断、处方、用药或签署，必须交由真人医生处理。",
        )
    if _contains_any(normalized, INSTRUCTION_ATTACK_MARKERS):
        raise DomainError(
            "agent_instruction_attack",
            "该请求试图改变智能体身份、权限或安全规则，已拒绝处理。",
        )
    if _contains_any(normalized, RESOURCE_ABUSE_MARKERS):
        raise DomainError(
            "agent_resource_abuse",
            "该请求包含批量、递归、无限调用或其他资源滥用要求。",
        )
    if agent_hops > MAX_AGENT_HOPS:
        raise DomainError(
            "agent_recursion_blocked",
            "当前智能体不得递归调用其他智能体继续处理任务。",
        )
    if estimated_tool_calls < 0 or estimated_tool_calls > MAX_ESTIMATED_TOOL_CALLS:
        raise DomainError(
            "agent_budget_exceeded",
            "该请求预计超过单次工具调用预算。",
        )

    allowed_tools = ALLOWED_TOOLS_BY_ROLE[role_type]
    unapproved_tools = sorted(set(requested_tools) - allowed_tools)
    if unapproved_tools:
        raise DomainError(
            "agent_tool_not_allowed",
            f"该角色请求了未批准的工具：{', '.join(unapproved_tools)}。",
        )

    if _contains_any(normalized, OUT_OF_SCOPE_MARKERS):
        raise DomainError("agent_request_out_of_scope", OUT_OF_SCOPE_MESSAGE)
    if not (
        _contains_any(normalized, HEALTH_SCOPE_MARKERS)
        or _contains_any(normalized, PRODUCT_SCOPE_MARKERS)
    ):
        raise DomainError(
            "agent_request_ambiguous_scope",
            "请先说明这个问题与中医、健康、本人资料或当前照护任务的直接关系。",
        )

    return AgentRequestDecision(
        role_type=role_type,
        scope="allowed",
        max_turns=MAX_AGENT_TURNS,
        max_tool_calls=MAX_ESTIMATED_TOOL_CALLS,
        allowed_tools=tuple(sorted(allowed_tools)),
    )
