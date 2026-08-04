# AI 中医病历助手（MAP-TCM）

MAP-TCM 是一个以医生为主导的中医临床记录、动态健康观察与随访辅助底座，也是由医生、患者及其受控智能体共同建设的健康知识公地。代码只是实现载体；共同知识可追溯、可争议并持续演进，个人身份与原始健康资料仍由本人及其授权范围控制。

首个产品是 **TCM Copilot V8.0 Alpha**：帮助医生整理病历、记录辨证依据、比较历次就诊并追踪疗效；不替代诊断、处方或医生的最终决定。

## 当前交付

仓库已经包含一套可运行的 Alpha 工程骨架：

- 合成患者、同意授权、就诊、观察、医生判断和审计事件的数据模型；
- `draft → doctor_reviewed → signed` 临床记录状态机；
- 医生签署边界与 AI 草稿隔离；
- SQLite 本地存储和浏览器工作台；
- 合成知识贡献、主张/反主张、医生评审、共识快照和追加式撤回；
- 自动化测试及 GitHub Actions CI。

这只是用于验证产品与工程契约的本地工作台，**禁止输入真实患者资料，也不得用于实际诊疗**。

## 快速开始

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\check.ps1
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\run.ps1
```

然后打开 `http://127.0.0.1:8765`。首次启动会生成一份仅含合成数据的演示病历。

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
| MAP-008 | [健康知识公地与共同大脑宪章](docs/MAP-008-health-knowledge-commons.md) | 医患智能体共同知识、个人数据主权与开放治理 |

开发说明见 [本地开发指南](docs/development/LOCAL_DEVELOPMENT.md)，安全边界见 [合成数据政策](docs/security/SYNTHETIC_DATA_ONLY.md)。

## 项目导航

- [V8/V9/V10 Roadmap](ROADMAP.md)
- [Founder Dashboard](FOUNDER_DASHBOARD.md)
- [V8 完整工程 Backlog](docs/product/v8/V8_ENGINEERING_BACKLOG.md)
- [中国合作医生临时客户端方案](docs/product/v8/CHINA_DOCTOR_PILOT.md)
- [历史医家学术研究助手政策](docs/governance/HISTORICAL_MEDICAL_SCHOLAR_AI_POLICY.md)
- [合成知识公地状态机 ADR](docs/architecture/ADR-002-knowledge-commons-state-machine.md)
