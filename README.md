# AI中医病历助手（MAP）

MAP（Medical Agent Protocol）是一个以医生为主导的中医临床记录与随访辅助项目。首个产品为 **MAP Clinical Apprentice Alpha**：帮助医生整理病历、记录辨证依据、追踪处方与疗效；不替代诊断、处方或医生的最终决定。

## 当前范围

- 使用去标识化、模拟或已取得明确授权的资料。
- 医生审核后才可形成正式病历或患者摘要。
- 记录原始事实、医生判断、AI 草稿与外部证据的来源和版本。
- 不使用患者数据训练公共模型；不支持自主诊疗。

## 首批设计资产

| 编号 | 文档 | 作用 |
| --- | --- | --- |
| MAP-000 | [项目宪章](docs/MAP-000-project-charter.md) | 使命、边界与成功标准 |
| MAP-001 | [Alpha 产品边界](docs/MAP-001-alpha-product-boundary.md) | 首期功能与非功能 |
| MAP-002 | [角色与治理](docs/MAP-002-roles-and-governance.md) | 人与 AI 的责任边界 |
| MAP-003 | [临床记录协议](docs/MAP-003-clinical-record-protocol.md) | 中医病历的最小结构 |
| MAP-004 | [身份、同意与授权](docs/MAP-004-identity-consent-authorization.md) | 权限与撤回机制 |
| MAP-005 | [医疗记忆与证据](docs/MAP-005-medical-memory-and-evidence.md) | 可追溯知识与记忆模型 |
| MAP-006 | [Alpha 系统架构](docs/MAP-006-alpha-system-architecture.md) | 模块、数据流与安全控制 |
| MAP-007 | [临床工作流](docs/MAP-007-clinical-workflows.md) | 初诊、复诊与随访流程 |

下一项待办：与首位合作医生以一份去标识化病例验证 MAP-003 与 MAP-007，并根据真实流程修订字段与审核节点。
