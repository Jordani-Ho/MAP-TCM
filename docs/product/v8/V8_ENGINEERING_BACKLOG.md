# TCM Copilot V8.0 Canonical Engineering Backlog

版本：0.2
状态：Active
更新日期：2026-08-03

本文件恢复并统一旧对话中提到但未完整落库的 12 项 V8 工程任务。它取代先前只有两项任务的精简 Sprint 001 清单，成为 V8 工程任务的规范来源。

## 十二项核心任务

| ID | 状态 | 任务 | 关键验收 |
| --- | --- | --- | --- |
| TCM-ENG-001 | Under Review | 仓库治理与开发基线 | UTF-8、开发命令、CI、安全规则、状态文件 |
| TCM-ENG-002 | Under Review | 可运行应用骨架 | SQLite、服务边界、HTTP API、工作台、测试 |
| TCM-ENG-003 | Ready after CLIN-001 | 结构化初诊与字段来源 | 四诊和病史字段；来源、作者、时间、未知/未采集 |
| TCM-ENG-004 | Pending | 复诊差异与疗效追踪 | 与上次签署记录比较；新增、改善、加重、未知 |
| TCM-ENG-005 | Pending | 动态健康时间线与四时环境 | 饮食、睡眠、排泄、情志、活动、气候、节气和地点抽象 |
| TCM-ENG-006 | Pending | 辨证、干预与版本关系 | 医生判断、治则、方药/非药物干预、调整理由、历史版本 |
| TCM-ENG-007 | Pending | 医生审核后的患者摘要 | 只引用已签署且授权内容；确定性模板先行 |
| TCM-ENG-008 | Pending | 患者每日最小记录体验 | 少量变化驱动问题；不形成填写负担；紧急信息人工升级 |
| TCM-PILOT-001 | Proposed | 中国医生临时 Web/PWA | 中文移动端、邀请登录、合成病例、审核签署、操作反馈 |
| TCM-SEC-001 | Pending | 身份、角色与会话威胁模型 | 医生身份、照护关系、最小权限、会话、审计和滥用测试 |
| TCM-DATA-001 | Pending | 迁移、导出、备份与恢复 | 版本化迁移；最小导出；恢复演练；无静默丢失 |
| TCM-QA-001 | Pending | V8 合成病例端到端验收 | 建档至摘要闭环；安全负向测试；医生可用性评审 |

## 前置与治理任务

| ID | 状态 | 任务 | 解除条件 |
| --- | --- | --- | --- |
| MAP-PMO-002 | In Progress | 统一 Roadmap、Dashboard 与 Backlog；审查 PR #1 | 文档、测试、PR 审查完成 |
| TCM-CLIN-001 | Ready | 初诊/复诊字段字典与医生评审 | 合作医生确认字段、顺序、必填与可跳过项 |
| TCM-UX-001 | Pending | 医生问诊顺序与最小交互 | 完成一次合成病例桌面演练 |
| TCM-AI-001 | Gated | 国内模型可替换 AI 草稿适配器 | ENG-003/004 稳定；评测集、安全审查和引用协议完成 |
| TCM-KNOW-001 | Gated Research | 历史医家学术资料研究助手 | 权利、来源、非冒充、AI 标识和临床安全审查通过 |

## 推荐执行顺序

```text
MAP-PMO-002
→ PR #1 Ready / Merge decision
→ TCM-CLIN-001 + TCM-UX-001
→ TCM-ENG-003
→ TCM-ENG-004
→ TCM-ENG-005 + TCM-ENG-006
→ TCM-ENG-007 + TCM-ENG-008
→ TCM-SEC-001 + TCM-DATA-001
→ TCM-PILOT-001
→ TCM-QA-001
→ TCM-AI-001 evaluation decision
```

## 完成定义

任务只有在存在可检查交付物、测试证据、更新状态文件并通过对应临床/安全审查后才能标记 `Completed`。已实现但尚未合并的工作使用 `Under Review`。
