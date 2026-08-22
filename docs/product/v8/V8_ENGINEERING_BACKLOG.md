# TCM Copilot V8.0 Canonical Engineering Backlog

版本：0.6
状态：Active
更新日期：2026-08-22

本文件恢复并统一旧对话中提到但未完整落库的 12 项 V8 工程任务。它取代先前只有两项任务的精简 Sprint 001 清单，成为 V8 工程任务的规范来源。

## 十二项核心任务

| ID | 状态 | 任务 | 关键验收 |
| --- | --- | --- | --- |
| TCM-ENG-001 | Under Review | 仓库治理与开发基线 | UTF-8、开发命令、CI、安全规则、状态文件 |
| TCM-ENG-002 | Under Review | 可运行应用骨架 | SQLite、服务边界、HTTP API、工作台、测试 |
| TCM-ENG-003 | Candidate Registry Under Review | 结构化初诊与字段来源 | 47 项候选字段已服务端版本化；来源、作者、时间、未知/未采集可校验，仍待医生批准 |
| TCM-ENG-004 | Synthetic Patient Idempotency Under Review | 复诊差异与疗效追踪 | 已投影执行、反馈、变化与不良事件，并实现患者本人会话授权和同事务幂等；生产认证、离线同步和医生可用性待审 |
| TCM-ENG-005 | Structured Timeline Under Review | 动态健康时间线与四时环境 | 饮食、睡眠、二便、情志、活动、穿衣冷热、身体感受、气候、季节、节气和地点抽象已追加式实现；等待医生词汇审查及生产数据源/授权 |
| TCM-ENG-006 | Pending | 辨证、干预与版本关系 | 医生判断、治则、方药/非药物干预、调整理由、历史版本 |
| TCM-ENG-007 | Pending | 医生审核后的患者摘要 | 只引用已签署且授权内容；确定性模板先行 |
| TCM-ENG-008 | Pending | 患者每日最小记录体验 | 少量变化驱动问题；不形成填写负担；紧急信息人工升级 |
| TCM-PILOT-001 | Synthetic Review Ready | 中国医生/患者合成评审门户 | 中文移动端、千问/豆包提示词、合成病例、匿名反馈和本地 JSON 导出已实现；邀请登录与签署待后续安全门禁 |
| TCM-SEC-001 | Architecture Baseline | 身份、邀请、角色与会话威胁模型 | 0.1 文档与机器可读合同已建立；待实现服务端身份、关系授权、会话与负向集成测试 |
| TCM-DATA-001 | Pending | 迁移、导出、备份与恢复 | 版本化迁移；最小导出；恢复演练；无静默丢失 |
| TCM-QA-001 | Synthetic E2E Completed | V8 合成病例端到端验收 | 无 AI 初诊至复诊再签署闭环已通过；真实临床与医生可用性评审仍关闭 |

## 前置与治理任务

| ID | 状态 | 任务 | 解除条件 |
| --- | --- | --- | --- |
| MAP-PMO-002 | Completed | 统一 Roadmap、Dashboard 与 Backlog；审查 PR #1 | 文档、测试、PR 审查完成 |
| MAP-PROD-001 | Baseline Established | Founder 医生/患者/运营三方需求与追踪矩阵 | 已映射 V8/V9/V10；临床字段仍由合作医生确认 |
| MAP-GOV-008 | Completed | 独立认知节点与去中心化健康知识社区宪章 | Founder Approved / Independent Cognitive Node Baseline 0.3 |
| MAP-KNOW-002 | Migration Required | 单节点主张、争议、审查、本地快照与撤回 | 现有代码/API/测试需移除中央贡献和社区共识语义 |
| MAP-KNOW-003 | Superseded | 原分布式双域共同大脑协议 | `commons_node` 与人类健康总知识体停止推进；保留追加式与来源规则供迁移 |
| MAP-KNOW-004 | Architecture Baseline | 独立节点与点对点知识信封 | 用多个合成节点验证归属、发送、拒绝、接收、本地派生、过期和撤回 |
| TCM-CLIN-001 | Ready for Doctor Review | 0.1.0 候选字段合同、合成病例与医生评审 | 合作医生确认字段、顺序、必填与可跳过项，固化 0.2.0 |
| TCM-UX-001 | Founder Review Frozen | 中医 AI 社区对话入口与最小交互 | 新版已发布且旧评审入口已封闭；Founder 两天审核期内冻结界面，离线 0.2 继续等待合伙人反馈 |
| TCM-AI-001 | Gated | 国内模型可替换 AI 草稿适配器 | ENG-003/004 稳定；评测集、安全审查和引用协议完成 |
| MAP-AI-004 | Synthetic Guard Implemented / Under Review | 智能体任务范围与资源保护 | 合成前置门禁已实现；生产速率/费用熔断、并发取消、重复检测、文件安全与真人安全审查完成后解除 |
| TCM-KNOW-001 | Gated Research | 历史医家学术资料研究助手 | 权利、来源、非冒充、AI 标识和临床安全审查通过 |

## 推荐执行顺序

```text
MAP-PMO-002
→ PR #1 Ready / Merge decision
→ MAP-GOV-008 v0.3 + ADR-004
→ MAP-KNOW-002 local-node migration
→ MAP-KNOW-004 point-to-point envelope contract
→ TCM-PILOT-001 synthetic review + TCM-CLIN-001 + TCM-UX-001
→ MAP-PROD-001 requirements baseline
→ TCM-ENG-003
→ TCM-ENG-004
→ TCM-ENG-005 + TCM-ENG-006
→ TCM-ENG-007 + TCM-ENG-008
→ TCM-SEC-001 implementation + TCM-DATA-001
→ TCM-PILOT-001 controlled pilot hardening
→ TCM-QA-001
→ MAP-AI-004 production resource controls
→ TCM-AI-001 evaluation decision
```

## 完成定义

任务只有在存在可检查交付物、测试证据、更新状态文件并通过对应临床/安全审查后才能标记 `Completed`。已实现但尚未合并的工作使用 `Under Review`。
