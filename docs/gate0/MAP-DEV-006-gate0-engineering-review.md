# MAP-DEV-006｜Gate 0 工程预审报告

- 版本：0.1.0
- 状态：Engineering Pre-Review Complete / Human Approval Pending
- 日期：2026-08-10
- 评审角色：墨衡（架构方案与技术预审）
- 评审范围：文档、Schema、现有合成运行时兼容性；未修改客户端代码

## 1. 预审结论

**结论：Gate 0 文档/Schema制品可进入 Aster 产品预审和真人技术合伙人架构批准；不得据此进入真实数据或生产临床使用。**

本轮完成：

1. `MAP-DEV-003` 临床数据 Schema 文档；
2. 机器可读 `clinical-schema-v0.1.json`；
3. `MAP-DEV-004` 身份、同意、授权与审计详细设计；
4. 本工程预审报告。

方案保持现有模块化单体/SQLite合成骨架和四状态临床流程，采用增加式兼容，不要求 Gate 0 改动现有客户端或运行时代码。

## 2. 输入基线

已完整读取并用于评审：

- `AGENTS.md`
- `README.md`
- `STATUS.md`
- `BACKLOG.md`
- `DECISIONS.md`
- `docs/MAP-003-clinical-record-protocol.md`
- `docs/MAP-004-identity-consent-authorization.md`
- `docs/MAP-005-medical-memory-and-evidence.md`
- `docs/MAP-006-alpha-system-architecture.md`
- `docs/MAP-007-clinical-workflows.md`
- `docs/clinical/TCM_CLIN_001_FIELD_DICTIONARY.md`
- `docs/security/TCM-SEC-001-identity-role-session-threat-model.md`
- `schemas/tcm_clinical_fields_v0.1.json`
- `schemas/tcm_sec_identity_access_contract_v0.1.json`
- `src/map_tcm/service.py`
- `src/map_tcm/identity.py`
- `src/map_tcm/store.py`
- 相关临床与身份合同/服务测试。

所有示例和合同继续使用 `SYN-` 合成标识，无真实患者资料。

## 3. 已确认的现有实现能力

| 能力 | 证据 | 评价 |
|---|---|---|
| 合成数据门禁 | `patients.is_synthetic = 1`、`SYN-`合同测试 | 适合当前仓库；不能替代生产隐私控制 |
| 临床状态机 | `draft / doctor_reviewed / signed / withdrawn` | 与 MAP-003 兼容 |
| 来源分层雏形 | `observations.source`、`assessments`独立表 | 方向正确，尚缺字段级来源锚点 |
| 医生判断边界 | 服务层 `_require_doctor`、签发前assessment检查 | 合成演示有效；生产不能信任客户端 `actor_role` |
| 追加审计雏形 | `audit_events`和各服务写入 | 适合合成原型；尚缺完整决策外壳和防篡改归档 |
| 身份安全状态机 | 一次性邀请摘要、核验、会话、关系、同意、step-up | 安全顺序明确，负向测试覆盖良好 |
| 服务端授权原型 | `IdentityAccessService.authorize`忽略客户端角色声明 | 可作为生产设计基础，尚未接入临床写 API |
| 字段合同 | 47个初诊/复诊候选字段、未知状态与医生专属判断 | 机器可测，仍待真人医生确认 |

## 4. 主要差距与风险

### 4.1 生产阻断项

| ID | 等级 | 发现 | 影响 | 必需处理 |
|---|---|---|---|---|
| G0-F01 | Critical for production | 临床服务仍接受 `actor_role` 参数并用字符串判断医生 | 客户端可伪造角色；现有API只能保持合成演示 | Gate 1 将临床写接口改为服务端 session context，并运行端到端越权测试 |
| G0-F02 | Critical for production | 现有 `encounters/observations/assessments`以更新当前行表达审核/签发，缺独立不可变 RecordVersion | 已签发历史、修订链和来源清单不足 | 新增 RecordVersion/FieldValue/Snapshot；签发后只追加新版本 |
| G0-F03 | High | 尚无 Patient/Doctor Vault、KMS、对象存储控制域 | 不能承载真实身份、病历和媒体 | 真人批准 Vault/KMS设计；实现每对象授权、短时访问、密钥恢复 |
| G0-F04 | High | `consents` 与 `security_consents`并存，临床创建只检查粗粒度同意 | 目的/字段/关系可能在临床路径失联 | 统一为版本化 ConsentGrant并由临床应用服务强制调用授权引擎 |
| G0-F05 | High | 无 SourceObject/SourceAnchor/字段级来源 | AI草稿或结构化字段无法回到原音频/页/区域 | Gate 1先实现人工字段来源；Gate 2再接AI流水线 |
| G0-F06 | High | AI草稿尚无独立持久化对象与禁止签发状态机 | 未来接AI时容易污染事实和医生判断 | 按MAP-DEV-003实现AiDraftVersion/AiReviewDecision独立存储 |
| G0-F07 | High | 审计事件缺server role、capability、purpose、decision、reason、session/request关联；SQLite无WORM | 无法完整解释高敏访问，防篡改不足 | 扩展审计合同；同事务/Outbox；生产归档隔离和完整性链 |
| G0-F08 | High | 无生产备份、恢复、保留、删除和责任快照规则 | 撤回/事故/履责无法可靠处理 | 法务/隐私裁决 + 恢复演练 + Snapshot最小化 |
| G0-F09 | High | 生产IdP、真实医生资质、安全Cookie/CSRF未实现 | 不能真实登录或签发 | 保持真实数据Gate，单独实现并审查 |

### 4.2 非阻断但需在 Gate 1 管理

| ID | 发现 | 建议 |
|---|---|---|
| G0-F10 | 字段字典临床顺序和粒度未获医生确认 | 保持0.1 Candidate；实现配置/版本，不硬编码流派唯一顺序 |
| G0-F11 | 历法/节气、媒体、治疗执行和复诊差异未进入统一Schema | 在字段注册表之上增加版本化对象，不改变已签发基础事实 |
| G0-F12 | SQLite零依赖骨架与目标PostgreSQL差异较大 | Gate 1可继续合成SQLite；生产迁移单独ADR和双读验证 |
| G0-F13 | Vault“本地私域”可能被误解为纯端侧 | 对外表述为个人控制域；真正端侧另案，不能夸大当前实现 |
| G0-F14 | 审计写入由业务方法手动调用 | 建立应用服务/仓储统一事务边界和审计缺失测试 |

## 5. 兼容性判断

### 5.1 无破坏兼容

新合同保留：

- 现有临床状态和值；
- `initial / follow_up / check_in / review` Encounter类型；
- `patient / doctor / file / ai / system`来源映射；
- 现有 `SYN-`门禁；
- 已实现身份角色、关系、同意、会话与原因码；
- 现有测试无需为Gate 0文档而改变。

### 5.2 后续增加式对象

| 新对象 | 兼容策略 |
|---|---|
| `Vault` | 为现有合成患者/医生创建一对一合成Vault，不改变display code |
| `ClinicalRecordVersion` | 首先镜像现有Encounter版本；旧界面继续读兼容投影 |
| `ClinicalFieldValue` | 从observations/assessments映射；新字段走统一envelope |
| `SourceObject/Anchor` | 先为文本和现有合成观察生成object_version锚点 |
| `AiDraftVersion/ReviewDecision` | 新表独立，不写现有assessments |
| `ResponsibilitySnapshot` | 签发事务新增；现有合成记录可通过迁移脚本生成清单 |
| 扩展审计 | 保留旧列并增加规范metadata或新表；读端可兼容 |

## 6. 架构不变量预审

| 不变量 | Schema | 身份/授权设计 | 当前实现 | 结论 |
|---|---:|---:|---:|---|
| 只使用合成数据 | 是 | 是 | 是 | Gate 0通过 |
| Patient/Doctor Vault分离 | 是 | 是 | 否 | Gate 1待实现 |
| 关系和同意双重约束 | 是 | 是 | 身份原型有，临床路径未集成 | Gate 1阻断 |
| 字段来源可定位 | 是 | 是 | 只有粗粒度source | Gate 1待实现 |
| AI草稿与医生判断隔离 | 是 | 是 | AI尚未接入 | Gate 2前必须实现 |
| 只有医生可签发 | 是 | 是 | 合成字符串门禁 | 生产阻断 |
| 签发版本不可变 | 是 | 是 | 当前同一行更新 | Gate 1阻断 |
| 每个写入有审计 | 是 | 是 | 大部分服务手动审计 | 需自动化证明 |
| 管理员默认无正文 | 是 | 是 | 安全合同有，临床API未生产集成 | 生产阻断 |

## 7. 建议的 Gate 1 任务包顺序

### P0-1｜临床会话授权集成

- 临床应用服务只接收服务端session context；
- 接入active关系、ConsentGrant、字段/目的、状态和step-up；
- 移除生产路径对客户端`actor_role`的信任；
- 跨患者、管理员、Agent、撤回、过期和step-up负向E2E。

### P0-2｜追加式临床版本

- RecordVersion/FieldValue/Supersedes；
- 签发事务、不可变检查、ResponsibilitySnapshot；
- 修订/撤回事件；
- 兼容投影和迁移核对。

### P0-3｜Vault 与对象来源骨架

- 合成Patient/Doctor Vault；
- SourceObject/Anchor，先支持文本/表单；
- 对象级授权引用；
- 不引入真实媒体或生产密钥。

### P0-4｜审计强化

- 统一AuditEvent envelope；
- 授权allow/deny和所有持久化写入证明；
- 同事务/Outbox失败测试；
- 日志脱敏和审计查询。

### P0-5｜医生字段与无AI闭环

- 将TCM-CLIN-001字段注册表驱动到服务端；
- 医生评审差异可配置/版本化；
- 初诊→签发→执行→反馈→复诊完整合成E2E；
- AI关闭时全部通过。

### Gate 2前置｜AI隔离存储

- AiDraftVersion、EvidenceItem/SourceAnchor、ReviewDecision；
- Agent/Worker最小身份和任务授权；
- 患者出口、跨患者、无来源和高风险字段负向测试。

## 8. 真人批准请求

真人技术合伙人需要明确批准或退回：

1. Gate 0采用增加式Schema、现有客户端不改的兼容策略；
2. Patient/Doctor Vault与共同照护域的边界；
3. 临床服务端会话授权作为Gate 1第一优先；
4. 签发不可变版本与最小ResponsibilitySnapshot；
5. 审计同事务/Outbox和生产完整性归档方向；
6. AI工程Agent无生产凭证、无真实病历全库访问；
7. 在上述生产阻断项关闭前继续`synthetic_only`。

真人医生需要确认：

1. TCM-CLIN-001 47个候选字段、顺序和必填性；
2. Patient fact / Doctor observation / Doctor judgment分层是否符合临床工作；
3. unknown / not_collected / not_applicable / declined的实际用法；
4. 签发、修订、撤回和患者摘要语义；
5. 危险信号和患者出口文案。

## 9. Gate 0 验证计划

```powershell
# JSON解析
Get-Content .\schemas\gate0\clinical-schema-v0.1.json -Raw -Encoding UTF8 |
  ConvertFrom-Json | Out-Null

# 合成数据/敏感标识扫描（人工复核命中语义说明）
rg -n "真实姓名|身份证|手机号|患者姓名|BEGIN (RSA|OPENSSH) PRIVATE KEY" `
  docs\gate0 schemas\gate0

# 现有工程回归
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\check.ps1

# Git范围
git status --short
git diff --check
```

## 10. 预审裁决

| 项目 | 结果 |
|---|---|
| 文档与机器合同内部一致 | 通过：JSON标准解析成功，11个实体合同、9项全局不变量 |
| 现有合成运行时兼容 | 通过：增加式，无代码变更 |
| 产品边界 | 通过：无自主诊断、处方、签发或真实数据 |
| 隐私与Vault边界 | 通过设计预审，生产实现待真人批准 |
| 身份/授权/审计 | 通过设计预审，临床API集成为Gate 1阻断项 |
| 临床语义 | 待真人医生评审 |
| 生产安全/密钥/发布 | 待真人技术合伙人批准与实施 |
| 真实试点 | **不批准** |

### 10.1 实际验证记录

- `clinical-schema-v0.1.json`：PowerShell `ConvertFrom-Json` 解析成功；`contract_id=MAP-DEV-003`、`synthetic_only=true`、`production_ready=false`；
- 合成/秘密扫描：未发现身份证号、手机号值、患者姓名值或私钥头；
- `git diff --check`：通过；共享工作树中的客户端改动不属于本任务，本轮未触碰；
- `scripts/dev/check.ps1`：通过；Python编译检查和 **56项自动化测试全部通过**；
- Git：未提交、未推送。

---

**墨衡意见：** 批准以上四份制品进入产品/技术真人审查；在 G0-F01 至 G0-F09 关闭并完成对应门禁前，不得输入真实患者资料、连接真实临床流程或宣称生产可用。
