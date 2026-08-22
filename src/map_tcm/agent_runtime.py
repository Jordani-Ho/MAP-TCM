from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Sequence

from .agent_knowledge import AgentKnowledgeService
from .agent_policy import (
    AgentRequestDecision,
    validate_agent_request,
)
from .errors import DomainError
from .patient_vault import PatientVaultService


PHYSICIAN_APPRENTICE_INSTRUCTIONS = """
你是某一位现实中医师专属的智能体徒弟，不是独立执业医生。
你的任务是学习、复述和整理老师自己的辨证框架，并把老师看见的人体动态图呈现给老师审阅。

工作顺序：
1. 先检索该老师的已登记知识；区分经典原文、注释、老师观点和 AI 摘要。
2. 接诊任务存在有效临时会诊空间时，可读取当前患者获准范围内的完整资料；这些资料只用于本次任务，不写入老师永久知识库。
3. 用“空间位置、状态关系、时间趋势、可能干预后果”组织材料。
4. 同时列出支持证据、排除证据、冲突、未知和需要老师追问之处。
5. 经典只作为可追溯依据；不得声称某种流派解释是唯一标准。
6. 没有来源就明确说没有来源，不得补造脉象、舌象、病史或经典引文。

绝对边界：
- 不独立诊断、辨证定论、开方、调药、签署病历或冒充老师发言。
- 不把病名直接映射成方剂。
- 不把 AI 草稿写成医生已经确认的结论。
- 涉及急症或危险信号，只提示立即交由真人医生或急救系统处理。
- 只处理与中医、健康、绑定老师或当前获授权照护任务直接相关的请求；不充当通用聊天助手。
- 不执行代码、安装软件、访问未批准网络、递归调用其他智能体或扩大工具权限。
""".strip()


PATIENT_TUTOR_INSTRUCTIONS = """
你是患者个人健康智能体中的辅导老师／课代表，不是医生。
你的成功标准是帮助患者逐步理解并执行老师已经确认的生活教导，减少普通问题对真人医生的反复占用，而不是增加聊天时长或依赖。

工作顺序：
1. 可检索允许患者查看的 teaching_approved/commons 教学内容，以及当前患者本人私域中的资料与健康时间线。
2. 私域中的身份、病历、报告、影像引用和患者观察是来源资料；AI 总结与趋势只是派生草稿，不得冒充原始事实。
3. 把抽象教导转化为少量、可执行、可观察、可复盘的日常行动。
4. 使用复述确认：请患者用自己的话说明理解，不用考试式语言羞辱患者。
5. 明确区分患者陈述、医生确认、外部报告、设备记录和 AI 推测。
6. 发现无法解释的变化、持续加重、药物问题或危险信号时，停止辅导并交回真人医生。

绝对边界：
- 不诊断、不辨证定论、不开方、不建议自行增减或停用药物。
- 不假装知道患者未提供的信息，不替患者同意或陈述。
- 不以打卡、恐惧、内疚或无限提醒制造依赖。
- 只处理与中医、健康、本人资料或 MAP 安全使用直接相关的请求；不充当通用聊天助手。
- 不执行代码、安装软件、访问未批准网络、递归调用其他智能体或扩大工具权限。
""".strip()


def load_local_env(path: str | Path) -> None:
    """Load a narrowly scoped local env file without logging values."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in {"OPENAI_API_KEY", "MAP_TCM_AGENT_MODEL"}:
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def validate_agent_input(
    *,
    prompt: str,
    is_synthetic: bool,
    role_type: Literal["physician_apprentice", "patient_tutor"] = "patient_tutor",
    requested_tools: Sequence[str] = (),
    estimated_tool_calls: int = 0,
    agent_hops: int = 0,
) -> AgentRequestDecision:
    if not is_synthetic:
        raise DomainError(
            "synthetic_agent_input_only",
            "Gate 0 智能体只接受明确标记的合成工程输入。",
        )
    return validate_agent_request(
        role_type=role_type,
        prompt=prompt,
        requested_tools=requested_tools,
        estimated_tool_calls=estimated_tool_calls,
        agent_hops=agent_hops,
    )


def build_agent(
    *,
    role_type: Literal["physician_apprentice", "patient_tutor"],
    agent_node_id: str,
    knowledge: AgentKnowledgeService,
    patient_vault: PatientVaultService | None = None,
    physician_access_session_id: str | None = None,
    model: str | None = None,
):
    """Build one focused Agents SDK agent; imports stay optional for the core Alpha."""
    try:
        from agents import Agent, function_tool
    except ImportError as exc:  # pragma: no cover - exercised by CLI install check
        raise RuntimeError("请安装可选依赖：pip install -e .[agents]") from exc

    audience = "physician" if role_type == "physician_apprentice" else "patient"

    @function_tool
    def search_personalized_knowledge(query: str) -> str:
        """Search the current agent's permitted knowledge and return source-labelled excerpts."""
        hits = knowledge.search(
            agent_node_id=agent_node_id,
            query=query,
            audience=audience,
        )
        return json.dumps(
            [
                {
                    "id": hit.id,
                    "title": hit.title,
                    "excerpt": hit.content[:800],
                    "source_ref": hit.source_ref,
                    "source_layer": hit.source_layer,
                    "knowledge_domain": hit.knowledge_domain,
                    "status": hit.status,
                }
                for hit in hits
            ],
            ensure_ascii=False,
        )

    tools = [search_personalized_knowledge]

    if (
        role_type == "physician_apprentice"
        and patient_vault is not None
        and physician_access_session_id is not None
    ):

        @function_tool
        def search_current_patient_session(query: str) -> str:
            """Search the current authorized patient consultation without retaining a copy."""
            hits = patient_vault.search_for_physician_session(
                access_session_id=physician_access_session_id,
                query=query,
            )
            return json.dumps(
                [
                    {
                        "id": hit.id,
                        "memory_type": hit.memory_type,
                        "title": hit.title,
                        "content": hit.content[:800],
                        "occurred_at": hit.occurred_at,
                        "source_ref": hit.source_ref,
                        "status": hit.status,
                    }
                    for hit in hits
                ],
                ensure_ascii=False,
            )

        tools.append(search_current_patient_session)

    if role_type == "patient_tutor" and patient_vault is not None:

        @function_tool
        def search_patient_private_memory(query: str) -> str:
            """Search only the current synthetic patient's own private vault and timeline sources."""
            hits = patient_vault.search_for_patient_agent(
                agent_node_id=agent_node_id,
                query=query,
            )
            return json.dumps(
                [
                    {
                        "id": hit.id,
                        "memory_type": hit.memory_type,
                        "title": hit.title,
                        "content": hit.content[:800],
                        "occurred_at": hit.occurred_at,
                        "source_ref": hit.source_ref,
                        "status": hit.status,
                    }
                    for hit in hits
                ],
                ensure_ascii=False,
            )

        tools.append(search_patient_private_memory)

    instructions = (
        PHYSICIAN_APPRENTICE_INSTRUCTIONS
        if role_type == "physician_apprentice"
        else PATIENT_TUTOR_INSTRUCTIONS
    )
    return Agent(
        name="医师智能体徒弟" if role_type == "physician_apprentice" else "患者健康辅导员",
        instructions=instructions,
        model=model or os.environ.get("MAP_TCM_AGENT_MODEL", "gpt-5.4-mini"),
        tools=tools,
    )


async def run_agent_once(
    *,
    role_type: Literal["physician_apprentice", "patient_tutor"],
    agent_node_id: str,
    prompt: str,
    is_synthetic: bool,
    knowledge: AgentKnowledgeService,
    patient_vault: PatientVaultService | None = None,
    physician_access_session_id: str | None = None,
    model: str | None = None,
    agent_hops: int = 0,
) -> Any:
    from agents import Runner

    requested_tools = ["search_personalized_knowledge"]
    if (
        role_type == "physician_apprentice"
        and patient_vault is not None
        and physician_access_session_id is not None
    ):
        requested_tools.append("search_current_patient_session")
    if role_type == "patient_tutor" and patient_vault is not None:
        requested_tools.append("search_patient_private_memory")

    decision = validate_agent_input(
        prompt=prompt,
        is_synthetic=is_synthetic,
        role_type=role_type,
        requested_tools=requested_tools,
        estimated_tool_calls=len(requested_tools),
        agent_hops=agent_hops,
    )

    agent = build_agent(
        role_type=role_type,
        agent_node_id=agent_node_id,
        knowledge=knowledge,
        patient_vault=patient_vault,
        physician_access_session_id=physician_access_session_id,
        model=model,
    )
    return await Runner.run(agent, prompt, max_turns=decision.max_turns)
