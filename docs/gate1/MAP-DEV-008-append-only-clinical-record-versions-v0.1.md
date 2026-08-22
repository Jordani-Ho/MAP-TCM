# MAP-DEV-008｜追加式临床记录版本 0.1

- 状态：Synthetic Implementation Complete / Human Review Pending
- 日期：2026-08-13
- 范围：Gate 1 P0-2
- 前置：MAP-DEV-007 服务端会话授权
- 数据边界：仅 `SYN-` 合成患者、合成医生和 `synthetic://` 来源

> 2026-08-13 后续状态：本文件记录的显式患者安全主体键与 SourceObject/SourceAnchor 缺口已由 MAP-DEV-009 在合成运行时完成；统一授权证据、临床写入和 Outbox 事务已由 MAP-DEV-010 完成。生产 KMS、加密媒体、数据库并发、幂等和 Outbox 投递器仍未完成。

## 1. 实现结果

每次就诊创建一个 `ClinicalRecordVersion` 草稿。主诉、观察和医生判断同时写入不可原位修改的 `ClinicalFieldValue`，并记录字段编号、值哈希、来源类型、来源引用、来源锚点和服务端作者。

审核只允许 `draft → doctor_reviewed`；签署只允许 `doctor_reviewed → signed`。一旦签署，SQLite 触发器禁止更新或删除版本；字段值从创建开始即禁止更新和删除。

## 2. 修订与撤回

- 修订不会重新打开旧签署版本，而是创建新的草稿版本；
- 新版本通过 `supersedes_version_id` 指向上一签署版本；
- 继承字段通过 `supersedes_field_value_id` 指向上一版本字段；
- 撤回只追加 `WithdrawalEvent`，签署版本仍保持 `signed`；
- 当前 `encounters`、`observations` 和 `assessments` 继续作为兼容投影，追加式版本链是新审计依据。

## 3. 最小责任快照

签署事务生成不可变 `ResponsibilitySnapshot`。快照只包含字段值编号、字段编号、值哈希、来源类型、来源引用、来源锚点和被替代字段编号，并记录签署医生和清单哈希。`raw_source_content_persisted` 固定为 `0`，不复制患者原文。

## 4. 机器门禁

数据库层拒绝：

- 删除任一临床版本；
- 修改或删除任一字段值；
- 修改或删除责任快照；
- 修改或删除撤回事件；
- 对签署版本进行任何更新；
- 绕过 `draft → doctor_reviewed → signed` 的状态变化。

对应机器合同：`schemas/gate1/clinical-record-version-v0.1.json`。

## 5. 尚未完成

授权判定与临床写入仍是相邻但独立的 SQLite 事务，尚未形成统一授权证据快照和 Outbox；合成患者记录与安全主体暂按合成显示码映射；当前字段来源只覆盖文本和结构化表单，尚未实现加密媒体对象锚点；兼容投影仍会更新，但不改变追加式签署版本。

因此真实患者数据、真实医生签署和生产发布继续禁止。

## 6. 真人技术合伙人审查点

1. 是否批准追加式版本链作为临床审计事实源、旧表作为兼容投影；
2. 是否批准签署版本和字段值在数据库层不可修改/删除；
3. 是否批准撤回追加事件、不改变原签署事实；
4. 是否批准修订复制上一版字段并逐项建立 supersedes 链；
5. P0-3 前是否先加入显式 PatientRecord–SecuritySubject 外键；
6. P0-4 统一事务采用 SQLite Outbox 还是提前迁移 PostgreSQL。
