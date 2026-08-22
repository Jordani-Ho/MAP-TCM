# AI 中医病历助手（MAP-TCM）

MAP-TCM 是一个以医生为主导的中医临床记录、动态健康观察与随访辅助底座，也是由医生、患者及其受控智能体组成的去中心化健康学习社区。每个智能体拥有独立知识库；项目不建设共同大脑或人类健康总知识库。共同的是经典理论根系、来源与交换协议、临床安全和权利边界。

首个产品是 **TCM Copilot V8.0 Alpha**：帮助医生整理病历、记录辨证依据、比较历次就诊并追踪疗效；不替代诊断、处方或医生的最终决定。

## 当前交付

仓库已经包含一套可运行的 Alpha 工程骨架：

- 合成患者、同意授权、就诊、观察、医生判断和审计事件的数据模型；
- `draft → doctor_reviewed → signed` 临床记录状态机；
- 医生签署边界与 AI 草稿隔离；
- SQLite 本地存储和浏览器工作台；
- 合成节点内主张/反主张、评审、版本快照，以及点对点知识信封和追加式撤回；
- 面向 2–3 名合作医生和合成患者体验者的中文合成评审门户与千问/豆包提示词；
- 自动化测试及 GitHub Actions CI。
- TCM-SEC-001 合成身份安全状态机：一次性邀请摘要、独立核验、患者接受、字段级同意、会话撤销、服务端角色授权与拒绝审计。
- MAP-AI-001 医师智能体徒弟与患者健康辅导员基础：个性知识节点、追加式框架版本、患者教学访问隔离、AI 草稿与临床权限分离。
- MAP-AI-002 患者健康私域：身份与先天背景、病历与报告、媒体引用、健康时间线及可追溯 AI 派生知识分层保存。
- MAP-AI-003 医师智能体临时会诊空间：接诊期间受控读取当前患者，关闭后失效，只保留无患者原文的最小责任快照。

这只是用于验证产品与工程契约的本地工作台，**禁止输入真实患者资料，也不得用于实际诊疗**。

## 快速开始

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\check.ps1
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\run.ps1
```

然后打开 `http://127.0.0.1:8765`。首次启动会生成一份仅含合成数据的演示病历。

### 智能体合成 Smoke Test

智能体依赖是可选的，不改变零依赖临床骨架：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[agents]"
Copy-Item .env.example .env.local
# 在 .env.local 本地配置 OPENAI_API_KEY，禁止提交
.\.venv\Scripts\python.exe .\scripts\dev\agent_smoke.py
```

该脚本只创建临时数据库与 `SYN-` 合成节点。Gate 0 拒绝真实资料、直接诊断、开方、改药和代签请求。

## 核心设计资产

| 编号 | 文档 | 作用 |
| --- | --- | --- |
| MAP-000 | [项目章程](docs/MAP-000-project-charter.md) | 使命、边界与成功标准 |
| MAP-001 | [Alpha 产品边界](docs/MAP-001-alpha-product-boundary.md) | 首期功能与非功能边界 |
| MAP-002 | [角色与治理](docs/MAP-002-roles-and-governance.md) | 人与 AI 的责任边界 |
| MAP-003 | [临床记录协议](docs/MAP-003-clinical-record-protocol.md) | 病历最小结构与状态 |
| MAP-004 | [身份、同意与授权](docs/MAP-004-identity-consent-authorization.md) | 权限与撤回机制 |
| MAP-005 | [Medical Memory 与证据](docs/MAP-005-medical-memory-and-evidence.md) | 可追溯记忆模型 |
| MAP-006 | [Alpha 系统架构](docs/MAP-006-alpha-system-architecture.md) | 模块、数据流与安全控制 |
| MAP-007 | [临床工作流](docs/MAP-007-clinical-workflows.md) | 初诊、复诊与随访流程 |
| MAP-008 | [独立认知节点与去中心化健康知识社区宪章](docs/MAP-008-independent-cognitive-nodes.md) | 独立大脑、共同经典根系、点对点协作与个人数据主权 |

开发说明见 [本地开发指南](docs/development/LOCAL_DEVELOPMENT.md)，安全边界见 [合成数据政策](docs/security/SYNTHETIC_DATA_ONLY.md)。

## 项目导航

- [V8/V9/V10 Roadmap](ROADMAP.md)
- [Founder Dashboard](FOUNDER_DASHBOARD.md)
- [Founder 三方需求基线与追踪矩阵](docs/product/MAP-PROD-001-founder-requirements-baseline.md)
- [V8 完整工程 Backlog](docs/product/v8/V8_ENGINEERING_BACKLOG.md)
- [身份、邀请、角色、会话与威胁模型](docs/security/TCM-SEC-001-identity-role-session-threat-model.md)
- [中国合作医生临时客户端方案](docs/product/v8/CHINA_DOCTOR_PILOT.md)
- [医患并行合成评审运行手册](docs/pilot/TCM_PILOT_001_REVIEW_RUNBOOK.md)
- [合成评审门户源码](portal/README.md)
- [初诊/复诊字段字典候选版](docs/clinical/TCM_CLIN_001_FIELD_DICTIONARY.md)
- [合作医生评审指南](docs/clinical/TCM_CLIN_001_DOCTOR_REVIEW_GUIDE.md)
- [TCM-CLIN-001 合成病例](docs/clinical/TCM_CLIN_001_SYNTHETIC_CASE.md)
- [历史医家学术研究助手政策](docs/governance/HISTORICAL_MEDICAL_SCHOLAR_AI_POLICY.md)
- [医师徒弟与患者辅导智能体架构](docs/agents/MAP-AI-001-individual-agent-architecture-v0.1.md)
- [患者健康私域与个人知识库](docs/agents/MAP-AI-002-patient-private-vault-v0.1.md)
- [医师智能体临时会诊空间](docs/agents/MAP-AI-003-physician-ephemeral-consultation-v0.1.md)
- [智能体任务范围与资源保护策略](docs/agents/MAP-AI-004-agent-scope-resource-policy-v0.1.md)
- [节点内知识状态机 ADR](docs/architecture/ADR-002-knowledge-commons-state-machine.md)
- [已替代：分布式双域共同大脑 ADR](docs/architecture/ADR-003-federated-dual-domain-knowledge.md)
- [独立认知节点与点对点知识交换 ADR](docs/architecture/ADR-004-independent-cognitive-nodes.md)
