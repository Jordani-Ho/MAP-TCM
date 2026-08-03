# AGENTS.md

## 项目边界

MAP-TCM 是医生主导的中医临床记录、长期观察和随访辅助项目。AI 可以整理、比较、检索和生成草稿，但不得独立诊断、处方或替代医生最终决定。

## 工作规则

- 先阅读 `README.md`、`STATUS.md`、`BACKLOG.md`、`DECISIONS.md` 及相关 `docs/` 协议。
- 修改前检查 Git 状态；不得删除用途未确认的文件。
- 不覆盖 `MAP-000` 至 `MAP-007`，除非任务明确要求且完成差异审查。
- 只使用合成或充分去标识化的测试数据。
- 不把真实、可识别患者资料写入代码、日志、测试、Issue、提交或 PR。
- 医生判断与 AI 草稿必须使用不同记录类型；AI 不得签署病历。
- 每次持久化写入必须形成审计事件。
- 完成任务后更新 `STATUS.md`、`CHANGELOG.md`，重大取舍写入 `DECISIONS.md`。

## 常用命令

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\check.ps1
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\run.ps1
```

## 验证要求

- 文档使用 UTF-8，链接可正常读取。
- 代码至少通过编译检查、单元测试和 localhost 启动探测。
- 脚本默认不删除、不上传患者资料、不监听公网地址。
