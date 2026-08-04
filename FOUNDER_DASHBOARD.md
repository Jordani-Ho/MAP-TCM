# Founder Dashboard

更新时间：2026-08-03
当前版本：TCM Copilot V8.0 Alpha
当前阶段：Foundation Ready for Review

## 一、总体状态

| 领域 | 状态 | 证据 / 下一出口 |
| --- | --- | --- |
| 项目恢复 | Completed | H 盘备份、Git 基线、私有 GitHub |
| MAP-000 至 MAP-007 | Draft Baseline | 八份核心协议已保存且未被工程提交改写 |
| MAP-008 共同大脑宪章 | Founder Approved | 健康知识公地、个人数据主权、经典完整性与防御性开放 |
| MAP-KNOW-002 合成状态机 | Under Review | 贡献、授权、主张/反主张、医生门控共识、撤回与审计 |
| 仓库治理 | Under Review | PR #1，CI 已通过 |
| 可运行工程骨架 | Under Review | 本地启动、10 项测试、GitHub Actions |
| 临床字段体系 | Ready | 等待第一位合作医生评审字段和顺序 |
| 复诊与疗效追踪 | Pending | 依赖结构化初诊和字段来源 |
| Founder 合成试点 | Approved | 使用 `SYN-FOUNDER-001` |
| 中国医生 Web 客户端 | Proposed | 依赖临床字段和身份威胁模型 |
| 真实患者数据 | Blocked | 需要 L1 临床信任空间与合规验收 |
| AI 草稿适配器 | Gated | 需要稳定工作流、评测集和安全审查 |
| 历史医家研究助手 | Gated Research | 需要权利、来源与非冒充审查 |
| V9 智能学徒 | Planned | 依赖 V8 使用数据和医生授权 |
| V10 MAP 节点 | Roadmap | 依赖 V8/V9 验证 |

## 二、当前 GitHub 状态

- 仓库：私有 `MAP-TCM`
- 基线：`main` @ `8545755`
- 工作分支：`agent/tcm-alpha-skeleton`
- PR：#1 `feat: establish governed runnable MAP-TCM Alpha`
- 当前审查结论：本地复验与 GitHub Actions 均通过；PR 可合并，已转为 Ready for review。

## 三、刚完成

- REC-001 至 REC-007：项目恢复与 GitHub 基线；
- TCM-ENG-001：仓库治理与开发基线实现；
- TCM-ENG-002：可运行 Alpha 骨架实现；
- 合成数据、同意门禁、医生签署和审计测试；
- V8/V9/V10 路线重新统一。
- MAP-PMO-002 完成，PR #1 已完成 Ready 审查。
- MAP-008 完成 Founder Approved / Draft Baseline。
- MAP-KNOW-002 已实现合成知识状态机并通过本地测试，等待 PR 合并。

## 四、当前最高优先级

1. 完成 TCM-CLIN-001 临床字段字典；
2. 让第一位合作医生用合成病例审查初诊流程；
3. 实现 TCM-ENG-003 结构化初诊；
4. 实现 TCM-ENG-004 复诊差异比较；
5. 在临床字段与安全门禁稳定后启动中国医生 Web/PWA 原型。
6. 在真实贡献前完成许可栈、贡献协议和退出语义审查。

## 五、Founder 已决定

- 技术路线在 Charter 和临床安全边界内由 CTO 负责；
- Founder 本人作为首位患者角色；
- 工程阶段只使用合成映射，不将真实健康资料写入仓库；
- 为中国医生设计不依赖 ChatGPT 的临时客户端；
- 探索历史名医学术智能体，但接受权利、安全和非冒充边界。
- MAP 是面向医生、患者及其智能体的健康知识公地，不是以代码开发者为中心的开源项目；
- 经典内容不可覆盖，社区解释和验证允许持续演进；
- 共同知识归社区治理，原始身份和健康资料仍由个人控制。

## 六、需要 Founder 后续协调

- 确认第一位合作中医师及其可参与的评审时间；
- 获得其使用合成病例参与产品设计的明确同意；
- 指定首批需要评审的现有问诊表或临床资料；
- 若推进特定历史医家研究助手，协助联系家属、机构或内容权利人。

## 七、停止条件

出现以下任何情况立即停止相关工作并升级：真实身份资料进入 GitHub；无授权的临床资料进入模型；AI 被表达为真实医生；自动生成正式诊断或处方；来源或权利状态无法追溯。
