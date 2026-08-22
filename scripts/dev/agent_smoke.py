from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from map_tcm.agent_knowledge import AgentKnowledgeService  # noqa: E402
from map_tcm.agent_runtime import load_local_env, run_agent_once  # noqa: E402
from map_tcm.store import SQLiteStore  # noqa: E402


async def main() -> None:
    load_local_env(ROOT / ".env.local")
    with tempfile.TemporaryDirectory() as tempdir:
        store = SQLiteStore(Path(tempdir) / "agent-smoke.sqlite3")
        store.initialize()
        knowledge = AgentKnowledgeService(store)
        node = knowledge.create_node(
            node_type="physician_apprentice",
            owner_subject_code="SYN-DOC-001",
            teacher_subject_code="SYN-DOC-001",
            actor_id="SYN-DOC-001",
        )
        item = knowledge.add_knowledge_item(
            agent_node_id=node["id"],
            knowledge_domain="physician_framework",
            title="合成老师的趋势辨证原则",
            content="同一病名不能直接决定方子；先看当下脉证、趋势和正邪关系。",
            source_ref="synthetic://teacher/framework/trend-1",
            source_layer="physician_view",
            access_level="private",
            rights_status="owned",
            status="teacher_confirmed",
            created_by="SYN-DOC-001",
        )
        result = await run_agent_once(
            role_type="physician_apprentice",
            agent_node_id=node["id"],
            prompt=(
                "这是合成工程测试，不是患者咨询。请检索老师关于‘趋势’的知识，"
                "只用两句话说明徒弟应该如何向老师呈现材料，并明确不作诊断或处方。"
            ),
            is_synthetic=True,
            knowledge=knowledge,
            model="gpt-5.4-mini",
        )
        output = str(result.final_output)
        if not output.strip():
            raise RuntimeError("Agent smoke test returned an empty response.")
        knowledge.record_ai_draft(
            agent_node_id=node["id"],
            role_type="physician_apprentice",
            input_summary="合成趋势原则 smoke test",
            output={"text": output},
            cited_item_ids=[item["id"]],
        )
        print(output)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        code = getattr(exc, "code", None)
        message = str(exc)
        if code in {"insufficient_quota", "credit_balance_exhausted"} or "no credits remaining" in message:
            print(
                "OpenAI API 已认证，但当前项目没有可用额度。请在 Platform Billing 添加额度后重试。",
                file=sys.stderr,
            )
            raise SystemExit(3) from None
        raise
