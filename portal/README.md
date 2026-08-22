# MAP-TCM 合成评审门户

供 2–3 位合作中医师和患者体验者评审 TCM-CLIN-001 初诊、复诊、隐私与 AI 边界的中文移动端门户。

## 当前边界

- 只使用 `SYN-` 合成编号和网页内置虚构病例；
- 不采集、上传或持久化真实患者资料；
- 进度仅保存在当前设备的 `localStorage`；
- 结果由参与者主动下载为 JSON，再发送给 Founder；
- AI 提示词只作产品评审记录，不诊断、不辨证、不处方、不签署。

## 入口

- 医生入口：评审 11 个字段组、初诊/复诊顺序、必填规则和 AI 草稿边界；
- 患者入口：评审表达负担、敏感问题处理、未知/拒答与复诊变化体验；
- 提示词包：`public/prompts/doctor-review-prompt.txt` 与 `patient-review-prompt.txt`。

## 本地验证

```powershell
pnpm run build
node --test tests/rendered-html.test.mjs
```

当前版本不是互联网诊疗客户端，也不授权真实患者试点。
