from __future__ import annotations

import json
import re
import sqlite3
from typing import Any
from uuid import uuid4

from .errors import DomainError
from .store import SQLiteStore, utc_now


SYNTHETIC_SUBJECT_CODE = re.compile(r"^SYN-[A-Z0-9-]{3,}$")
CONTRIBUTOR_ROLES = {"patient", "doctor", "researcher", "ai"}
ACCESS_LEVELS = {"private", "restricted", "commons_candidate"}
REVIEW_VERDICTS = {"support", "oppose", "needs_evidence"}
CONSENSUS_STATES = {"provisional_knowledge", "validated_snapshot", "disputed"}


class KnowledgeService:
    """Synthetic-only, append-only knowledge commons workflow."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:16]}"

    def create_contribution(
        self,
        *,
        subject_code: str,
        is_synthetic: bool,
        contributor_id: str,
        contributor_role: str,
        content: str,
        source_ref: str,
        access_level: str,
        withdrawal_rule: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if not is_synthetic or not SYNTHETIC_SUBJECT_CODE.fullmatch(subject_code):
            raise DomainError(
                "synthetic_knowledge_only",
                "知识公地 Alpha 只接受以 SYN- 开头的合成主题编号。",
            )
        if not source_ref.startswith("synthetic://"):
            raise DomainError(
                "synthetic_source_required",
                "知识公地 Alpha 的来源引用必须使用 synthetic://。",
            )
        if contributor_role not in CONTRIBUTOR_ROLES:
            raise DomainError("invalid_contributor_role", "贡献者角色无效。")
        if access_level not in ACCESS_LEVELS:
            raise DomainError("invalid_access_level", "知识贡献访问级别无效。")
        if actor_id != contributor_id:
            raise DomainError("contributor_required", "草稿必须由贡献者本人创建。", status=403)
        values = [content, source_ref, withdrawal_rule]
        if any(not value.strip() for value in values):
            raise DomainError("invalid_contribution", "内容、来源和撤回规则不能为空。")

        contribution_id = self.new_id("kcontrib")
        created_at = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_contributions (
                    id, subject_code, is_synthetic, contributor_id, contributor_role,
                    content, source_ref, access_level, withdrawal_rule, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contribution_id,
                    subject_code,
                    contributor_id,
                    contributor_role,
                    content.strip(),
                    source_ref.strip(),
                    access_level,
                    withdrawal_rule.strip(),
                    created_at,
                ),
            )
            self._append_contribution_state(
                connection,
                contribution_id=contribution_id,
                state="private_draft",
                actor_id=actor_id,
                actor_role=contributor_role,
                metadata={"access_level": access_level},
            )
            self.store.audit(
                connection,
                event_type="knowledge.contribution.created",
                actor_id=actor_id,
                object_type="knowledge_contribution",
                object_id=contribution_id,
                metadata={
                    "subject_code": subject_code,
                    "synthetic": True,
                    "state": "private_draft",
                },
            )
        return {
            "id": contribution_id,
            "subject_code": subject_code,
            "state": "private_draft",
            "access_level": access_level,
        }

    def consent_contribution(
        self,
        *,
        contribution_id: str,
        consent_reference: str,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        if not consent_reference.strip():
            raise DomainError("consent_reference_required", "贡献授权必须有可追溯引用。")
        with self.store.connect() as connection:
            contribution = self._get_contribution(connection, contribution_id)
            if actor_role == "ai":
                raise DomainError("human_consent_required", "AI 不能代表贡献者授权。", status=403)
            if actor_id != contribution["contributor_id"] or actor_role != contribution["contributor_role"]:
                raise DomainError("contributor_required", "只有贡献者本人可以授权该贡献。", status=403)
            current_state = self._current_contribution_state(connection, contribution_id)
            if current_state != "private_draft":
                raise DomainError(
                    "invalid_contribution_transition",
                    f"不能把贡献从 {current_state} 变更为 consented_contribution。",
                )
            self._append_contribution_state(
                connection,
                contribution_id=contribution_id,
                state="consented_contribution",
                actor_id=actor_id,
                actor_role=actor_role,
                metadata={"consent_reference": consent_reference.strip()},
            )
            self.store.audit(
                connection,
                event_type="knowledge.contribution.consented",
                actor_id=actor_id,
                object_type="knowledge_contribution",
                object_id=contribution_id,
                metadata={"consent_reference": consent_reference.strip()},
            )
        return {"id": contribution_id, "state": "consented_contribution"}

    def withdraw_contribution(
        self,
        *,
        contribution_id: str,
        reason: str,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise DomainError("withdrawal_reason_required", "撤回必须记录原因。")
        with self.store.connect() as connection:
            contribution = self._get_contribution(connection, contribution_id)
            is_contributor = (
                actor_id == contribution["contributor_id"]
                and actor_role == contribution["contributor_role"]
                and actor_role != "ai"
            )
            if not is_contributor and actor_role != "data_steward":
                raise DomainError(
                    "withdrawal_not_authorized",
                    "只有贡献者本人或数据权利维护者可以撤回贡献。",
                    status=403,
                )
            current_state = self._current_contribution_state(connection, contribution_id)
            if current_state == "withdrawn":
                raise DomainError("already_withdrawn", "该贡献已经撤回。")
            self._append_contribution_state(
                connection,
                contribution_id=contribution_id,
                state="withdrawn",
                actor_id=actor_id,
                actor_role=actor_role,
                metadata={"from": current_state, "reason": reason.strip()},
            )
            self.store.audit(
                connection,
                event_type="knowledge.contribution.withdrawn",
                actor_id=actor_id,
                object_type="knowledge_contribution",
                object_id=contribution_id,
                metadata={"from": current_state, "reason": reason.strip()},
            )

            claim_rows = connection.execute(
                "SELECT id FROM knowledge_claims WHERE contribution_id = ?",
                (contribution_id,),
            ).fetchall()
            retracted_count = 0
            for claim_row in claim_rows:
                claim_id = claim_row["id"]
                claim_state = self._current_claim_state(connection, claim_id)
                if claim_state == "retracted":
                    continue
                self._append_claim_state(
                    connection,
                    claim_id=claim_id,
                    from_state=claim_state,
                    to_state="retracted",
                    actor_id=actor_id,
                    actor_role=actor_role,
                    metadata={"reason": "source_contribution_withdrawn"},
                )
                self.store.audit(
                    connection,
                    event_type="knowledge.claim.retracted",
                    actor_id=actor_id,
                    object_type="knowledge_claim",
                    object_id=claim_id,
                    metadata={"contribution_id": contribution_id},
                )
                retracted_count += 1
        return {
            "id": contribution_id,
            "state": "withdrawn",
            "retracted_claims": retracted_count,
        }

    def create_claim(
        self,
        *,
        contribution_id: str,
        claim_kind: str,
        statement: str,
        source_ref: str,
        author_id: str,
        author_role: str,
        parent_claim_id: str | None = None,
    ) -> dict[str, Any]:
        if claim_kind not in {"claim", "counterclaim"}:
            raise DomainError("invalid_claim_kind", "知识主张类型无效。")
        if author_role not in CONTRIBUTOR_ROLES:
            raise DomainError("invalid_author_role", "知识主张作者角色无效。")
        if not statement.strip() or not source_ref.strip():
            raise DomainError("invalid_claim", "知识主张及其来源引用不能为空。")
        if not source_ref.startswith("synthetic://"):
            raise DomainError(
                "synthetic_source_required",
                "知识公地 Alpha 的主张来源必须使用 synthetic://。",
            )
        if claim_kind == "claim" and parent_claim_id is not None:
            raise DomainError("unexpected_parent_claim", "普通主张不能指定父主张。")
        if claim_kind == "counterclaim" and not parent_claim_id:
            raise DomainError("parent_claim_required", "反主张必须指向被反驳的主张。")

        claim_id = self.new_id("kclaim")
        with self.store.connect() as connection:
            contribution = self._get_contribution(connection, contribution_id)
            contribution_state = self._current_contribution_state(connection, contribution_id)
            if contribution_state != "consented_contribution":
                raise DomainError(
                    "contribution_consent_required",
                    "只有已明确授权的贡献才能形成社区知识候选。",
                    status=403,
                )
            if contribution["access_level"] != "commons_candidate":
                raise DomainError(
                    "commons_access_required",
                    "该贡献未授权进入共同知识候选层。",
                    status=403,
                )
            if parent_claim_id:
                parent = self._get_claim(connection, parent_claim_id)
                if self._current_claim_state(connection, parent["id"]) == "retracted":
                    raise DomainError("parent_claim_retracted", "不能反驳已撤回的主张。")
            try:
                connection.execute(
                    """
                    INSERT INTO knowledge_claims (
                        id, contribution_id, claim_kind, parent_claim_id, statement,
                        source_ref, author_id, author_role, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id,
                        contribution_id,
                        claim_kind,
                        parent_claim_id,
                        statement.strip(),
                        source_ref.strip(),
                        author_id,
                        author_role,
                        utc_now(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise DomainError("invalid_claim_relation", "知识主张关系无效。") from error
            self._append_claim_state(
                connection,
                claim_id=claim_id,
                from_state=None,
                to_state="deidentified_candidate",
                actor_id=author_id,
                actor_role=author_role,
                metadata={"claim_kind": claim_kind},
            )
            self.store.audit(
                connection,
                event_type="knowledge.claim.created",
                actor_id=author_id,
                object_type="knowledge_claim",
                object_id=claim_id,
                metadata={
                    "contribution_id": contribution_id,
                    "claim_kind": claim_kind,
                    "parent_claim_id": parent_claim_id,
                },
            )
        return {
            "id": claim_id,
            "claim_kind": claim_kind,
            "state": "deidentified_candidate",
            "parent_claim_id": parent_claim_id,
        }

    def submit_claim_for_review(
        self,
        *,
        claim_id: str,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        if actor_role not in {"doctor", "knowledge_steward"}:
            raise DomainError(
                "knowledge_steward_required",
                "只有医生或知识维护者可以提交同行评审。",
                status=403,
            )
        with self.store.connect() as connection:
            self._get_claim(connection, claim_id)
            current_state = self._current_claim_state(connection, claim_id)
            if current_state != "deidentified_candidate":
                raise DomainError(
                    "invalid_claim_transition",
                    f"不能把主张从 {current_state} 变更为 peer_review。",
                )
            self._append_claim_state(
                connection,
                claim_id=claim_id,
                from_state=current_state,
                to_state="peer_review",
                actor_id=actor_id,
                actor_role=actor_role,
                metadata={},
            )
            self.store.audit(
                connection,
                event_type="knowledge.claim.submitted_for_review",
                actor_id=actor_id,
                object_type="knowledge_claim",
                object_id=claim_id,
                metadata={"from": current_state, "to": "peer_review"},
            )
        return {"id": claim_id, "state": "peer_review"}

    def add_review(
        self,
        *,
        claim_id: str,
        verdict: str,
        rationale: str,
        reviewer_id: str,
        reviewer_role: str,
    ) -> dict[str, Any]:
        if reviewer_role not in CONTRIBUTOR_ROLES:
            raise DomainError("invalid_reviewer_role", "评审者角色无效。")
        if verdict not in REVIEW_VERDICTS:
            raise DomainError("invalid_review_verdict", "评审结论无效。")
        if not rationale.strip():
            raise DomainError("review_rationale_required", "评审必须记录依据。")
        review_id = self.new_id("kreview")
        is_advisory = reviewer_role == "ai"
        with self.store.connect() as connection:
            self._get_claim(connection, claim_id)
            current_state = self._current_claim_state(connection, claim_id)
            if current_state not in {
                "peer_review",
                "provisional_knowledge",
                "validated_snapshot",
                "disputed",
            }:
                raise DomainError("claim_not_reviewable", "该知识主张当前不能接受评审。")
            connection.execute(
                """
                INSERT INTO knowledge_reviews (
                    id, claim_id, reviewer_id, reviewer_role, verdict, rationale,
                    is_advisory, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    claim_id,
                    reviewer_id,
                    reviewer_role,
                    verdict,
                    rationale.strip(),
                    1 if is_advisory else 0,
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="knowledge.review.created",
                actor_id=reviewer_id,
                object_type="knowledge_review",
                object_id=review_id,
                metadata={
                    "claim_id": claim_id,
                    "verdict": verdict,
                    "advisory": is_advisory,
                },
            )
        return {"id": review_id, "verdict": verdict, "advisory": is_advisory}

    def create_consensus_snapshot(
        self,
        *,
        claim_id: str,
        target_state: str,
        summary: str,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        if target_state not in CONSENSUS_STATES:
            raise DomainError("invalid_consensus_state", "共识快照状态无效。")
        if not summary.strip():
            raise DomainError("consensus_summary_required", "共识快照必须记录边界摘要。")
        if actor_role not in {"doctor", "knowledge_steward"}:
            raise DomainError(
                "consensus_steward_required",
                "AI 或普通贡献者不能发布共识快照。",
                status=403,
            )
        if target_state == "validated_snapshot" and actor_role != "doctor":
            raise DomainError(
                "doctor_validation_required",
                "验证级共识必须由医生发布。",
                status=403,
            )

        snapshot_id = self.new_id("ksnapshot")
        with self.store.connect() as connection:
            self._get_claim(connection, claim_id)
            current_state = self._current_claim_state(connection, claim_id)
            metrics = self._claim_metrics(connection, claim_id)
            self._validate_consensus_transition(
                current_state=current_state,
                target_state=target_state,
                metrics=metrics,
            )
            self._append_claim_state(
                connection,
                claim_id=claim_id,
                from_state=current_state,
                to_state=target_state,
                actor_id=actor_id,
                actor_role=actor_role,
                metadata=metrics,
            )
            self.store.audit(
                connection,
                event_type="knowledge.claim.state_changed",
                actor_id=actor_id,
                object_type="knowledge_claim",
                object_id=claim_id,
                metadata={"from": current_state, "to": target_state, **metrics},
            )
            connection.execute(
                """
                INSERT INTO consensus_snapshots (
                    id, claim_id, state, summary, review_count, doctor_support_count,
                    doctor_oppose_count, active_counterclaim_count, created_by,
                    created_by_role, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    claim_id,
                    target_state,
                    summary.strip(),
                    metrics["review_count"],
                    metrics["doctor_support_count"],
                    metrics["doctor_oppose_count"],
                    metrics["active_counterclaim_count"],
                    actor_id,
                    actor_role,
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="knowledge.snapshot.created",
                actor_id=actor_id,
                object_type="consensus_snapshot",
                object_id=snapshot_id,
                metadata={"claim_id": claim_id, "state": target_state},
            )
        return {"id": snapshot_id, "claim_id": claim_id, "state": target_state, **metrics}

    def overview(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            contribution_rows = connection.execute(
                """
                SELECT c.*,
                       (SELECT e.state FROM knowledge_contribution_events e
                        WHERE e.contribution_id = c.id ORDER BY e.id DESC LIMIT 1) AS state
                FROM knowledge_contributions c
                ORDER BY c.created_at DESC
                LIMIT 50
                """
            ).fetchall()
            claim_rows = connection.execute(
                """
                SELECT c.*,
                       (SELECT e.to_state FROM knowledge_claim_state_events e
                        WHERE e.claim_id = c.id ORDER BY e.id DESC LIMIT 1) AS state,
                       (SELECT COUNT(*) FROM knowledge_reviews r WHERE r.claim_id = c.id)
                           AS review_count,
                       (SELECT COUNT(*) FROM knowledge_claims child
                        WHERE child.parent_claim_id = c.id) AS counterclaim_count
                FROM knowledge_claims c
                ORDER BY c.created_at DESC
                LIMIT 50
                """
            ).fetchall()
            snapshot_rows = connection.execute(
                """
                SELECT * FROM consensus_snapshots
                ORDER BY created_at DESC
                LIMIT 25
                """
            ).fetchall()

        contributions = self.store.rows_to_dicts(contribution_rows)
        claims = self.store.rows_to_dicts(claim_rows)
        snapshots = self.store.rows_to_dicts(snapshot_rows)
        claim_states = [claim["state"] for claim in claims]
        counts = {
            "contributions": len(contributions),
            "consented_contributions": sum(
                item["state"] == "consented_contribution" for item in contributions
            ),
            "claims": len(claims),
            "peer_review": claim_states.count("peer_review"),
            "provisional_knowledge": claim_states.count("provisional_knowledge"),
            "validated_snapshot": claim_states.count("validated_snapshot"),
            "disputed": claim_states.count("disputed"),
            "retracted": claim_states.count("retracted"),
        }
        return {
            "mode": "synthetic_only",
            "counts": counts,
            "contributions": contributions,
            "claims": claims,
            "snapshots": snapshots,
        }

    @staticmethod
    def _append_contribution_state(
        connection: sqlite3.Connection,
        *,
        contribution_id: str,
        state: str,
        actor_id: str,
        actor_role: str,
        metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_contribution_events (
                contribution_id, state, actor_id, actor_role, occurred_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                contribution_id,
                state,
                actor_id,
                actor_role,
                utc_now(),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ),
        )

    @staticmethod
    def _append_claim_state(
        connection: sqlite3.Connection,
        *,
        claim_id: str,
        from_state: str | None,
        to_state: str,
        actor_id: str,
        actor_role: str,
        metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_claim_state_events (
                claim_id, from_state, to_state, actor_id, actor_role,
                occurred_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_id,
                from_state,
                to_state,
                actor_id,
                actor_role,
                utc_now(),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ),
        )

    @staticmethod
    def _get_contribution(
        connection: sqlite3.Connection, contribution_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM knowledge_contributions WHERE id = ?",
            (contribution_id,),
        ).fetchone()
        if row is None:
            raise DomainError("contribution_not_found", "知识贡献不存在。", status=404)
        return row

    @staticmethod
    def _current_contribution_state(connection: sqlite3.Connection, contribution_id: str) -> str:
        row = connection.execute(
            """
            SELECT state FROM knowledge_contribution_events
            WHERE contribution_id = ? ORDER BY id DESC LIMIT 1
            """,
            (contribution_id,),
        ).fetchone()
        if row is None:
            raise DomainError("contribution_state_missing", "知识贡献状态缺失。")
        return str(row["state"])

    @staticmethod
    def _get_claim(connection: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM knowledge_claims WHERE id = ?",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise DomainError("claim_not_found", "知识主张不存在。", status=404)
        return row

    @staticmethod
    def _current_claim_state(connection: sqlite3.Connection, claim_id: str) -> str:
        row = connection.execute(
            """
            SELECT to_state FROM knowledge_claim_state_events
            WHERE claim_id = ? ORDER BY id DESC LIMIT 1
            """,
            (claim_id,),
        ).fetchone()
        if row is None:
            raise DomainError("claim_state_missing", "知识主张状态缺失。")
        return str(row["to_state"])

    def _claim_metrics(self, connection: sqlite3.Connection, claim_id: str) -> dict[str, int]:
        review_count = connection.execute(
            "SELECT COUNT(*) FROM knowledge_reviews WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()[0]
        doctor_support_count = connection.execute(
            """
            SELECT COUNT(DISTINCT reviewer_id) FROM knowledge_reviews
            WHERE claim_id = ? AND reviewer_role = 'doctor' AND verdict = 'support'
            """,
            (claim_id,),
        ).fetchone()[0]
        doctor_oppose_count = connection.execute(
            """
            SELECT COUNT(DISTINCT reviewer_id) FROM knowledge_reviews
            WHERE claim_id = ? AND reviewer_role = 'doctor' AND verdict = 'oppose'
            """,
            (claim_id,),
        ).fetchone()[0]
        child_rows = connection.execute(
            "SELECT id FROM knowledge_claims WHERE parent_claim_id = ?",
            (claim_id,),
        ).fetchall()
        active_counterclaim_count = sum(
            self._current_claim_state(connection, row["id"]) != "retracted" for row in child_rows
        )
        return {
            "review_count": int(review_count),
            "doctor_support_count": int(doctor_support_count),
            "doctor_oppose_count": int(doctor_oppose_count),
            "active_counterclaim_count": int(active_counterclaim_count),
        }

    @staticmethod
    def _validate_consensus_transition(
        *,
        current_state: str,
        target_state: str,
        metrics: dict[str, int],
    ) -> None:
        if target_state == "provisional_knowledge":
            if current_state != "peer_review":
                raise DomainError(
                    "invalid_claim_transition",
                    f"不能把主张从 {current_state} 变更为 provisional_knowledge。",
                )
            if metrics["doctor_support_count"] < 1:
                raise DomainError("doctor_support_required", "临时知识至少需要一名医生支持。")
            if metrics["doctor_oppose_count"] or metrics["active_counterclaim_count"]:
                raise DomainError("unresolved_dispute", "存在医生反对或有效反主张，不能形成临时知识。")
            return

        if target_state == "validated_snapshot":
            if current_state != "provisional_knowledge":
                raise DomainError(
                    "invalid_claim_transition",
                    f"不能把主张从 {current_state} 变更为 validated_snapshot。",
                )
            if metrics["doctor_support_count"] < 2:
                raise DomainError("independent_review_required", "验证级共识需要两名不同医生支持。")
            if metrics["doctor_oppose_count"] or metrics["active_counterclaim_count"]:
                raise DomainError("unresolved_dispute", "存在医生反对或有效反主张，不能形成验证快照。")
            return

        if current_state not in {"peer_review", "provisional_knowledge", "validated_snapshot"}:
            raise DomainError(
                "invalid_claim_transition",
                f"不能把主张从 {current_state} 变更为 disputed。",
            )
        if not metrics["doctor_oppose_count"] and not metrics["active_counterclaim_count"]:
            raise DomainError("dispute_evidence_required", "进入争议状态需要医生反对或有效反主张。")
