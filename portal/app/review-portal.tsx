"use client";

import { useEffect, useMemo, useState } from "react";

type Role = "doctor" | "patient";
type Decision = "保留" | "修改" | "移动" | "合并" | "删除";
type Requirement = "必填" | "建议" | "条件性" | "可选";

type GroupReview = {
  decision: Decision;
  requirement: Requirement;
  order: number;
  note: string;
};

type DoctorState = {
  participantCode: string;
  acceptedSafety: boolean;
  groupReviews: Record<string, GroupReview>;
  firstQuestion: string;
  followUpTopFive: string;
  aiMayDraft: string;
  aiMustNotDraft: string;
  signatureBoundary: boolean;
  missingnessBoundary: boolean;
  sensitiveBoundary: boolean;
  durationMinutes: string;
  overallNotes: string;
};

type PatientState = {
  participantCode: string;
  acceptedSafety: boolean;
  healthGoal: string;
  chiefConcern: string;
  onsetCourse: string;
  medications: string;
  allergies: string;
  coldHeat: string;
  sleepEnergy: string;
  stoolUrine: string;
  sensitivePreference: string;
  followUpChange: string;
  completionBurden: string;
  trustScore: string;
  confusingFields: string;
  overallNotes: string;
};

const fieldGroups = [
  ["identity", "就诊与授权", "合成标识、就诊类型、时间、有效同意"],
  ["concern", "主诉与患者目标", "最困扰的问题、持续时间、希望改善什么"],
  ["history", "病程与既往资料", "起病变化、诱因、既往诊断、检查和处置"],
  ["safety", "用药、过敏与安全", "当前药物/方药、过敏、不良反应和危险信号"],
  ["inspection", "望诊", "神色形态、舌质、舌苔及采集条件"],
  ["listening", "闻诊", "声音、呼吸及条件性气味观察"],
  ["inquiry", "问诊", "寒热汗、头身胸腹、饮食口渴、二便、睡眠精力"],
  ["sensitive", "条件性敏感问诊", "情志、生殖、月经、妊娠/哺乳；允许拒答"],
  ["palpation", "切诊", "脉诊和实际实施的触诊；远程允许未采集"],
  ["assessment", "医生判断与计划", "病名、证候、病机、治则、干预版本和复诊计划"],
  ["followup", "复诊变化与结局", "新增/改善/加重、不变、执行、疗效和不良事件"],
] as const;

function initialGroupReviews(): Record<string, GroupReview> {
  return Object.fromEntries(
    fieldGroups.map(([id], index) => [
      id,
      {
        decision: "保留" as Decision,
        requirement: (["identity", "concern", "safety", "assessment", "followup"].includes(id)
          ? "必填"
          : id === "sensitive"
            ? "条件性"
            : "建议") as Requirement,
        order: index + 1,
        note: "",
      },
    ]),
  );
}

const defaultDoctor: DoctorState = {
  participantCode: "",
  acceptedSafety: false,
  groupReviews: initialGroupReviews(),
  firstQuestion: "",
  followUpTopFive: "",
  aiMayDraft: "",
  aiMustNotDraft: "诊断、证候、治则、处方、签署",
  signatureBoundary: false,
  missingnessBoundary: false,
  sensitiveBoundary: false,
  durationMinutes: "",
  overallNotes: "",
};

const defaultPatient: PatientState = {
  participantCode: "",
  acceptedSafety: false,
  healthGoal: "",
  chiefConcern: "",
  onsetCourse: "",
  medications: "unknown",
  allergies: "unknown",
  coldHeat: "",
  sleepEnergy: "",
  stoolUrine: "",
  sensitivePreference: "由我决定是否回答，并说明为什么需要",
  followUpChange: "",
  completionBurden: "",
  trustScore: "",
  confusingFields: "",
  overallNotes: "",
};

const steps = ["身份与边界", "合成初诊", "合成复诊", "流程反馈", "导出结果"];

function downloadJson(filename: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function ReviewPortal() {
  const [role, setRole] = useState<Role | null>(null);
  const [step, setStep] = useState(0);
  const [doctor, setDoctor] = useState<DoctorState>(defaultDoctor);
  const [patient, setPatient] = useState<PatientState>(defaultPatient);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const saved = window.localStorage.getItem("map-tcm-synthetic-review-v1");
    if (!saved) return;
    try {
      const parsed = JSON.parse(saved);
      if (parsed.doctor) setDoctor(parsed.doctor);
      if (parsed.patient) setPatient(parsed.patient);
    } catch {
      window.localStorage.removeItem("map-tcm-synthetic-review-v1");
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(
      "map-tcm-synthetic-review-v1",
      JSON.stringify({ doctor, patient }),
    );
  }, [doctor, patient]);

  const progress = useMemo(() => `${Math.round(((step + 1) / steps.length) * 100)}%`, [step]);

  function chooseRole(nextRole: Role) {
    setRole(nextRole);
    setStep(0);
    setMessage("");
  }

  function validParticipantCode() {
    if (!role) return false;
    const value = role === "doctor" ? doctor.participantCode : patient.participantCode;
    return new RegExp(`^SYN-${role === "doctor" ? "DOC" : "PAT"}-\\d{3}$`).test(value.trim());
  }

  function canContinue() {
    if (step !== 0 || !role) return true;
    const accepted = role === "doctor" ? doctor.acceptedSafety : patient.acceptedSafety;
    return validParticipantCode() && accepted;
  }

  function next() {
    if (!canContinue()) {
      setMessage(`请输入 ${role === "doctor" ? "SYN-DOC-001" : "SYN-PAT-001"} 格式的合成编号，并确认安全边界。`);
      return;
    }
    setMessage("");
    setStep((value) => Math.min(value + 1, steps.length - 1));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function exportResult() {
    if (!role || !validParticipantCode()) {
      setMessage("请先填写有效的合成参与者编号。");
      return;
    }
    const participantCode = role === "doctor" ? doctor.participantCode : patient.participantCode;
    const payload = {
      contract_id: "TCM-PILOT-001-REVIEW",
      contract_version: "0.1.0",
      synthetic_only: true,
      role,
      participant_code: participantCode,
      exported_at: new Date().toISOString(),
      local_storage_only: true,
      responses: role === "doctor" ? doctor : patient,
    };
    downloadJson(`MAP-TCM-${participantCode}-review.json`, payload);
    setMessage("评审结果已下载到本设备。请由您主动发送给项目 Founder；网页没有自动上传任何内容。");
  }

  async function copyPrompt() {
    if (!role) return;
    const response = await fetch(`/prompts/${role}-review-prompt.txt`);
    await navigator.clipboard.writeText(await response.text());
    setMessage("提示词已复制，可以粘贴到千问或豆包。只允许使用其中的合成病例。");
  }

  function updateGroup(id: string, patch: Partial<GroupReview>) {
    setDoctor((current) => ({
      ...current,
      groupReviews: {
        ...current.groupReviews,
        [id]: { ...current.groupReviews[id], ...patch },
      },
    }));
  }

  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="返回首页">
          <span className="brand-mark">MAP</span>
          <span><b>中医协作评审</b><small>医生 × 患者 × 共同大脑</small></span>
        </a>
        <span className="synthetic-pill">仅限合成数据</span>
      </header>

      <div id="top" className="safety-ribbon" role="note">
        <b>这不是诊疗工具。</b> 不输入真实姓名、电话、地址、照片、检查或处方；AI 不诊断、不处方、不签署。
      </div>

      {!role ? (
        <section className="hero-shell">
          <div className="hero-copy">
            <p className="kicker">TCM-PILOT-001 · 合成联合评审</p>
            <h1>用一次真实的体验，<br />共同决定未来的中医健康记录。</h1>
            <p className="hero-lede">医生评审问诊与辨证工作流，患者评审表达、隐私与长期趋势体验。所有内容只保存在当前设备，完成后由参与者主动导出。</p>
            <div className="scope-row" aria-label="本轮规模">
              <span><b>2–3</b> 位合作医生</span>
              <span><b>1+</b> 位患者体验者</span>
              <span><b>0</b> 条真实病历</span>
            </div>
          </div>

          <div className="role-grid" aria-label="选择评审入口">
            <button className="role-card doctor-card" onClick={() => chooseRole("doctor")}>
              <span className="role-index">01</span>
              <span className="role-icon" aria-hidden="true">医</span>
              <strong>我是合作中医师</strong>
              <small>评审初诊/复诊顺序、字段负担、临床边界和 AI 草稿范围</small>
              <span className="role-action">进入医生评审 →</span>
            </button>
            <button className="role-card patient-card" onClick={() => chooseRole("patient")}>
              <span className="role-index">02</span>
              <span className="role-icon" aria-hidden="true">患</span>
              <strong>我是患者体验者</strong>
              <small>评审健康目标、症状表达、敏感问题选择和复诊变化体验</small>
              <span className="role-action">进入患者评审 →</span>
            </button>
          </div>

          <aside className="trust-strip">
            <span>设备本地暂存</span><i />
            <span>明确未知与拒答</span><i />
            <span>医生最终决定</span><i />
            <span>结果自主导出</span>
          </aside>
        </section>
      ) : (
        <section className="review-shell">
          <aside className="review-sidebar">
            <button className="back-link" onClick={() => setRole(null)}>← 返回角色选择</button>
            <p className="kicker">{role === "doctor" ? "医生评审入口" : "患者体验入口"}</p>
            <h2>{role === "doctor" ? "验证临床工作流" : "验证表达与信任"}</h2>
            <ol className="step-list">
              {steps.map((label, index) => (
                <li key={label} className={index === step ? "active" : index < step ? "done" : ""}>
                  <button onClick={() => setStep(index)}><span>{index < step ? "✓" : index + 1}</span>{label}</button>
                </li>
              ))}
            </ol>
            <div className="progress-track"><span style={{ width: progress }} /></div>
            <small>{progress} · 自动保存在本设备</small>
          </aside>

          <div className="review-content">
            {step === 0 && (
              <section className="form-section">
                <p className="kicker">Step 1</p>
                <h2>身份与安全边界</h2>
                <p className="section-lede">使用合成编号参与。编号用于比较 2–3 位医生或多位患者的评审结果，不对应真实身份。</p>
                <label>合成参与者编号</label>
                <input
                  value={role === "doctor" ? doctor.participantCode : patient.participantCode}
                  onChange={(event) => role === "doctor"
                    ? setDoctor({ ...doctor, participantCode: event.target.value.toUpperCase() })
                    : setPatient({ ...patient, participantCode: event.target.value.toUpperCase() })}
                  placeholder={role === "doctor" ? "SYN-DOC-001" : "SYN-PAT-001"}
                  autoComplete="off"
                />
                <div className="boundary-card">
                  <b>本轮只评审产品，不进行诊疗</b>
                  <ul>
                    <li>只使用网页提供的虚构病例；</li>
                    <li>不粘贴任何真实患者聊天、照片、检查、病历或处方；</li>
                    <li>医生意见是产品设计反馈，不是对合成角色的真实诊疗建议；</li>
                    <li>网页没有云端提交按钮，结果需主动下载。</li>
                  </ul>
                </div>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={role === "doctor" ? doctor.acceptedSafety : patient.acceptedSafety}
                    onChange={(event) => role === "doctor"
                      ? setDoctor({ ...doctor, acceptedSafety: event.target.checked })
                      : setPatient({ ...patient, acceptedSafety: event.target.checked })}
                  />
                  <span>我理解并同意只使用合成信息完成本次产品评审。</span>
                </label>
              </section>
            )}

            {step === 1 && role === "doctor" && (
              <section className="form-section wide-section">
                <p className="kicker">Step 2 · SYN-CLIN-001</p>
                <h2>合成初诊：调整字段与顺序</h2>
                <div className="case-card"><b>虚构主诉</b><p>近三周入睡变慢，偶有夜醒，晨起精神不足；工作节奏变化后更明显，休息日稍缓解。药物与过敏信息均待确认。</p></div>
                <label>您通常会用什么问题开始？</label>
                <textarea value={doctor.firstQuestion} onChange={(e) => setDoctor({ ...doctor, firstQuestion: e.target.value })} placeholder="请写下您最自然的第一问及原因" />
                <div className="field-review-list">
                  {fieldGroups.map(([id, label, description]) => {
                    const item = doctor.groupReviews[id];
                    return (
                      <article className="field-review" key={id}>
                        <div><b>{label}</b><small>{description}</small></div>
                        <label><span>决定</span><select value={item.decision} onChange={(e) => updateGroup(id, { decision: e.target.value as Decision })}>{["保留", "修改", "移动", "合并", "删除"].map((v) => <option key={v}>{v}</option>)}</select></label>
                        <label><span>要求</span><select value={item.requirement} onChange={(e) => updateGroup(id, { requirement: e.target.value as Requirement })}>{["必填", "建议", "条件性", "可选"].map((v) => <option key={v}>{v}</option>)}</select></label>
                        <label><span>顺序</span><input type="number" min="1" max="20" value={item.order} onChange={(e) => updateGroup(id, { order: Number(e.target.value) })} /></label>
                        <label className="note-field"><span>意见</span><input value={item.note} onChange={(e) => updateGroup(id, { note: e.target.value })} placeholder="遗漏、合并对象或跳过条件" /></label>
                      </article>
                    );
                  })}
                </div>
              </section>
            )}

            {step === 1 && role === "patient" && (
              <section className="form-section">
                <p className="kicker">Step 2 · 合成初诊体验</p>
                <h2>您是否愿意这样描述健康问题？</h2>
                <p className="section-lede">请继续使用虚构内容。可以直接使用页面提示，不要改成自己的真实病史。</p>
                <label>希望改善什么？</label><input value={patient.healthGoal} onChange={(e) => setPatient({ ...patient, healthGoal: e.target.value })} placeholder="合成示例：希望更容易入睡，白天更有精神" />
                <label>本次最困扰的问题</label><textarea value={patient.chiefConcern} onChange={(e) => setPatient({ ...patient, chiefConcern: e.target.value })} placeholder="合成示例：近三周入睡变慢，偶有夜醒" />
                <label>起病和变化过程</label><textarea value={patient.onsetCourse} onChange={(e) => setPatient({ ...patient, onsetCourse: e.target.value })} placeholder="合成示例：工作节奏变化后更明显，休息日稍缓解" />
                <div className="two-column">
                  <label>当前药物/方药<select value={patient.medications} onChange={(e) => setPatient({ ...patient, medications: e.target.value })}><option value="unknown">不清楚 / 待确认</option><option value="none_reported">合成陈述：未报告</option><option value="declined">暂不回答</option></select></label>
                  <label>过敏/不良反应<select value={patient.allergies} onChange={(e) => setPatient({ ...patient, allergies: e.target.value })}><option value="unknown">不清楚 / 待确认</option><option value="none_reported">合成陈述：未报告</option><option value="declined">暂不回答</option></select></label>
                </div>
                <label>寒热、口渴等感受</label><textarea value={patient.coldHeat} onChange={(e) => setPatient({ ...patient, coldHeat: e.target.value })} placeholder="合成示例：近几天口渴感略多，其他说不清" />
                <label>睡眠与精力</label><textarea value={patient.sleepEnergy} onChange={(e) => setPatient({ ...patient, sleepEnergy: e.target.value })} placeholder="合成示例：入睡较慢，偶有夜醒，晨起精神不足" />
                <label>二便相关问题</label><textarea value={patient.stoolUrine} onChange={(e) => setPatient({ ...patient, stoolUrine: e.target.value })} placeholder="可以写“正常”“说不清”“暂不回答”或提出更合适的问法" />
                <label>敏感问题的理想处理方式</label><select value={patient.sensitivePreference} onChange={(e) => setPatient({ ...patient, sensitivePreference: e.target.value })}><option>由我决定是否回答，并说明为什么需要</option><option>只在医生确认相关后再询问</option><option>我希望直接跳过</option><option>其他，请在最终意见说明</option></select>
              </section>
            )}

            {step === 2 && role === "doctor" && (
              <section className="form-section">
                <p className="kicker">Step 3 · 合成复诊</p>
                <h2>哪些变化决定下一步？</h2>
                <div className="case-card"><b>一周后的虚构变化</b><p>入睡时间略缩短，夜醒次数说不清；白天精力变化不明显；完成睡眠记录五天、中断两天；无可确认方药暴露；新增治疗待追问。</p></div>
                <label>复诊最先比较的五项</label><textarea value={doctor.followUpTopFive} onChange={(e) => setDoctor({ ...doctor, followUpTopFive: e.target.value })} placeholder="请按优先顺序填写，并指出哪些需要与上次签署记录并列" />
                <label>AI 可以起草哪些内容？</label><textarea value={doctor.aiMayDraft} onChange={(e) => setDoctor({ ...doctor, aiMayDraft: e.target.value })} placeholder="例如：患者陈述摘要、前后差异候选；仍需医生核对" />
                <label>AI 绝不能代填哪些内容？</label><textarea value={doctor.aiMustNotDraft} onChange={(e) => setDoctor({ ...doctor, aiMustNotDraft: e.target.value })} />
              </section>
            )}

            {step === 2 && role === "patient" && (
              <section className="form-section">
                <p className="kicker">Step 3 · 合成复诊</p>
                <h2>怎样表达“有变化，但不确定”？</h2>
                <div className="case-card"><b>一周后的虚构变化</b><p>入睡时间略缩短，夜醒次数说不清；白天精力变化不明显；睡眠记录完成五天、中断两天。</p></div>
                <label>用您觉得自然的方式概括变化</label><textarea value={patient.followUpChange} onChange={(e) => setPatient({ ...patient, followUpChange: e.target.value })} placeholder="可以保留“略有改善”“没有明显变化”“说不清”等原话" />
                <div className="choice-grid">
                  {["明显改善", "略有改善", "没有明显变化", "有所加重", "说不清"].map((item) => <button type="button" key={item} onClick={() => setPatient({ ...patient, followUpChange: item })}>{item}</button>)}
                </div>
              </section>
            )}

            {step === 3 && role === "doctor" && (
              <section className="form-section">
                <p className="kicker">Step 4</p><h2>确认临床边界与整体反馈</h2>
                <label className="check-row"><input type="checkbox" checked={doctor.signatureBoundary} onChange={(e) => setDoctor({ ...doctor, signatureBoundary: e.target.checked })} /><span>诊断、证候、病机、治则及干预只能由医生确认和签署。</span></label>
                <label className="check-row"><input type="checkbox" checked={doctor.missingnessBoundary} onChange={(e) => setDoctor({ ...doctor, missingnessBoundary: e.target.checked })} /><span>未知、未采集、不适用和拒答必须可见，不允许 AI 静默补全。</span></label>
                <label className="check-row"><input type="checkbox" checked={doctor.sensitiveBoundary} onChange={(e) => setDoctor({ ...doctor, sensitiveBoundary: e.target.checked })} /><span>敏感问题只在适用、相关且患者愿意时采集。</span></label>
                <label>本次评审大约用时（分钟）</label><input type="number" min="1" max="180" value={doctor.durationMinutes} onChange={(e) => setDoctor({ ...doctor, durationMinutes: e.target.value })} />
                <label>整体意见与最优先修改项</label><textarea value={doctor.overallNotes} onChange={(e) => setDoctor({ ...doctor, overallNotes: e.target.value })} placeholder="请优先写会阻止您使用本产品的问题" />
              </section>
            )}

            {step === 3 && role === "patient" && (
              <section className="form-section">
                <p className="kicker">Step 4</p><h2>填写负担、理解与信任</h2>
                <label>填写负担</label><select value={patient.completionBurden} onChange={(e) => setPatient({ ...patient, completionBurden: e.target.value })}><option value="">请选择</option><option>很轻松</option><option>可以接受</option><option>有些繁琐</option><option>无法完成</option></select>
                <label>如果正式使用，您对这种记录方式的信任程度</label><select value={patient.trustScore} onChange={(e) => setPatient({ ...patient, trustScore: e.target.value })}><option value="">请选择</option>{[1,2,3,4,5].map((n) => <option key={n} value={String(n)}>{n} / 5</option>)}</select>
                <label>哪些问题不清楚、太敏感或不想每次回答？</label><textarea value={patient.confusingFields} onChange={(e) => setPatient({ ...patient, confusingFields: e.target.value })} />
                <label>整体意见与最优先修改项</label><textarea value={patient.overallNotes} onChange={(e) => setPatient({ ...patient, overallNotes: e.target.value })} />
              </section>
            )}

            {step === 4 && (
              <section className="form-section export-section">
                <p className="kicker">Step 5</p><h2>导出并发送评审结果</h2>
                <p className="section-lede">网页不会自动上传内容。请下载 JSON 文件，再通过您与 Founder 约定的渠道发送。发送前可用文本编辑器检查文件。</p>
                <div className="export-card"><span className="export-icon">↓</span><div><b>{role === "doctor" ? doctor.participantCode || "SYN-DOC-___" : patient.participantCode || "SYN-PAT-___"}</b><small>合成评审 · JSON · 不含真实身份字段</small></div><button className="primary" onClick={exportResult}>下载评审结果</button></div>
                <div className="prompt-card"><div><b>千问 / 豆包提示词评审包</b><small>可复制到医生或患者熟悉的国内 AI，仅用于同一合成病例。</small></div><button onClick={copyPrompt}>复制提示词</button><a href={`/prompts/${role}-review-prompt.txt`} download>下载 TXT</a></div>
                <div className="final-boundary"><b>发送前请再次确认</b><p>文件中只有 `SYN-` 合成编号和产品意见，没有真实患者资料、真实诊断、处方、照片或联系方式。</p></div>
              </section>
            )}

            {message && <div className="message" role="status" aria-live="polite">{message}</div>}
            <nav className="form-nav">
              <button onClick={() => setStep((value) => Math.max(0, value - 1))} disabled={step === 0}>上一步</button>
              {step < steps.length - 1 ? <button className="primary" onClick={next}>保存并继续</button> : <button onClick={() => setRole(null)}>完成并返回首页</button>}
            </nav>
          </div>
        </section>
      )}

      <footer><b>MAP-TCM</b><span>共同的大脑，不是共同暴露的硬盘。</span><span>版本 0.1 · 合成联合评审</span></footer>
    </main>
  );
}
