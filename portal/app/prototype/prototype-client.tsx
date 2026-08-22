"use client";

import { useState } from "react";

type View = "patient" | "doctor";
type ConsultationStatus = "idle" | "active" | "closed";
type FactStatus = "pending" | "confirmed" | "edited" | "declined";
type ActionStatus = "pending" | "active" | "completed";
type ChatMessage = { id: number; author: "person" | "agent"; text: string };

const initialMessages: ChatMessage[] = [
  { id: 1, author: "person", text: "合成示例：这几天入睡稍慢，午后容易疲倦。" },
  { id: 2, author: "agent", text: "收到。我先把它整理成一张待确认的健康事实卡，不会自动判断病因。" },
];

export default function GateOneConversationPrototype() {
  const [view, setView] = useState<View>("patient");
  const [selectedPatient, setSelectedPatient] = useState("SYN-PAT-001");
  const [consultationStatus, setConsultationStatus] = useState<ConsultationStatus>("idle");
  const [factStatus, setFactStatus] = useState<FactStatus>("pending");
  const [actionStatus, setActionStatus] = useState<ActionStatus>("pending");
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [doctorReviewed, setDoctorReviewed] = useState(false);
  const [composer, setComposer] = useState("");
  const [messages, setMessages] = useState(initialMessages);
  const [notice, setNotice] = useState("当前原型不保存、不上传，也不读取真实文件。");

  const patientEligible = selectedPatient === "SYN-PAT-001";

  function changeView(next: View) {
    setView(next);
    setNotice(next === "patient" ? "已进入患者健康辅导员会话。" : "已进入医师智能体徒弟会话。");
  }

  function sendMessage() {
    const text = composer.trim();
    if (!text) return;
    const seed = Date.now();
    setMessages((current) => [
      ...current,
      { id: seed, author: "person", text },
      {
        id: seed + 1,
        author: "agent",
        text: "已收到这条合成消息。我会先保留原意和来源，整理结果仍需你确认；医生判断必须由真人医生完成。",
      },
    ]);
    setComposer("");
  }

  function simulateInput(kind: string) {
    setNotice(`${kind}入口目前只做合成本地演示：没有调用设备、没有读取内容、没有上传。`);
  }

  function startConsultation() {
    if (!patientEligible) return;
    setConsultationStatus("active");
    setNotice("合成临时会诊已打开；关闭后立即解除患者资料挂载。");
  }

  function closeConsultation() {
    setConsultationStatus("closed");
    setNotice("会诊已关闭，患者原文和临时上下文不进入医生永久知识库。");
  }

  return (
    <main className="conversation-page">
      <header className="conversation-topbar">
        <a className="brand" href="/" aria-label="返回对话首页">
          <span className="brand-mark">MAP</span>
          <span><b>治未病 AI 社区</b><small>对话入口 · Gate 1 P0-6</small></span>
        </a>
        <div className="conversation-role-switch" aria-label="切换合成视角">
          <button className={view === "patient" ? "active" : ""} onClick={() => changeView("patient")}>患者视角</button>
          <button className={view === "doctor" ? "active" : ""} onClick={() => changeView("doctor")}>医生视角</button>
        </div>
        <span className="conversation-synthetic">仅限合成数据</span>
      </header>

      <div className="conversation-shell">
        <aside className="conversation-sidebar">
          <div className="sidebar-title"><span>对话</span><button aria-label="新建合成对话">＋</button></div>
          <button className="conversation-entry active">
            <i>{view === "patient" ? "辅" : "徒"}</i>
            <span>
              <b>{view === "patient" ? "我的健康辅导员" : "医师智能体徒弟"}</b>
              <small>{view === "patient" ? "行动与复诊回顾" : "当前患者临时会诊"}</small>
            </span>
          </button>
          <button className="conversation-entry">
            <i>共</i><span><b>共同健康知识体</b><small>只读 · 来源可追溯</small></span>
          </button>
          <div className="scope-panel">
            <b>当前作用域</b>
            <p>{view === "patient" ? "只使用 SYN-PAT-001 的合成私域" : "只使用当前会诊患者获准资料"}</p>
            <span>不进入公开社区</span>
          </div>
        </aside>

        <section className="conversation-workspace">
          <div className="conversation-header">
            <div className="agent-avatar">{view === "patient" ? "辅" : "徒"}</div>
            <div>
              <small>当前交流对象</small>
              <h1>{view === "patient" ? "患者健康辅导员（AI）" : "医师智能体徒弟（AI）"}</h1>
              <p>{view === "patient" ? "帮助记录、学习、执行和复盘，不诊断、不处方" : "整理来源与差异，不替代老师判断、不代签"}</p>
            </div>
            <span className="online-state">合成演示</span>
          </div>

          <div className="conversation-boundary" role="note">
            <b>能力边界：</b> AI 只起草，医生逐项审阅；聊天不能替代病历，医生确认后才能签署。
          </div>

          <div className="message-thread" aria-live="polite">
            {view === "patient" ? (
              <PatientConversation
                messages={messages}
                factStatus={factStatus}
                setFactStatus={setFactStatus}
                actionStatus={actionStatus}
                setActionStatus={setActionStatus}
                feedbackSubmitted={feedbackSubmitted}
                setFeedbackSubmitted={setFeedbackSubmitted}
              />
            ) : (
              <DoctorConversation
                patient={selectedPatient}
                setPatient={(patient) => {
                  setSelectedPatient(patient);
                  setConsultationStatus("idle");
                  setDoctorReviewed(false);
                }}
                eligible={patientEligible}
                status={consultationStatus}
                onStart={startConsultation}
                onClose={closeConsultation}
                reviewed={doctorReviewed}
                setReviewed={setDoctorReviewed}
              />
            )}
          </div>

          <div className="composer-shell">
            <div className="input-tools" aria-label="合成多模态入口">
              {[["语", "语音"], ["照", "照片"], ["视", "视频"], ["件", "文件"]].map(([icon, label]) => (
                <button key={label} onClick={() => simulateInput(label)}><i>{icon}</i>{label}</button>
              ))}
            </div>
            <div className="composer-row">
              <textarea
                aria-label="输入合成消息"
                value={composer}
                onChange={(event) => setComposer(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    sendMessage();
                  }
                }}
                placeholder={view === "patient" ? "说说今天身体和生活有什么变化……" : "记录追问、观察或医生自己的比较……"}
              />
              <button className="send-button" onClick={sendMessage} disabled={!composer.trim()}>发送</button>
            </div>
            <small>{notice}</small>
          </div>
        </section>
      </div>
    </main>
  );
}

function AgentMessage({ children }: { children: React.ReactNode }) {
  return <div className="chat-line agent-line"><div className="chat-avatar">AI</div><div className="chat-bubble">{children}</div></div>;
}

function PersonMessage({ text }: { text: string }) {
  return <div className="chat-line person-line"><div className="chat-bubble">{text}</div><div className="chat-avatar">我</div></div>;
}

function PatientConversation({
  messages,
  factStatus,
  setFactStatus,
  actionStatus,
  setActionStatus,
  feedbackSubmitted,
  setFeedbackSubmitted,
}: {
  messages: ChatMessage[];
  factStatus: FactStatus;
  setFactStatus: (status: FactStatus) => void;
  actionStatus: ActionStatus;
  setActionStatus: (status: ActionStatus) => void;
  feedbackSubmitted: boolean;
  setFeedbackSubmitted: (value: boolean) => void;
}) {
  return (
    <>
      <div className="conversation-day">今天 · 合成健康观察</div>
      <AgentMessage>
        <p>你好，我是你的患者健康辅导员。我可以帮你记录身体和生活变化、理解医生已经确认的指导，并提醒你复盘。</p>
        <p className="bubble-muted">我不会诊断、开方或替医生作决定。以下所有信息都是合成示例。</p>
      </AgentMessage>
      {messages.map((message) => message.author === "person"
        ? <PersonMessage key={message.id} text={message.text} />
        : <AgentMessage key={message.id}><p>{message.text}</p></AgentMessage>)}
      <AgentMessage>
        <FactConfirmationCard status={factStatus} onChange={setFactStatus} />
      </AgentMessage>
      <AgentMessage>
        <p>这是一份已经引用医生签署记录的行动卡。你可以开始、完成或说明没有执行；系统不会把“未执行”写成“无效”。</p>
        <ActionCard status={actionStatus} onChange={setActionStatus} />
      </AgentMessage>
      <AgentMessage>
        <p>到了复诊前，我们只比较真实变化、实际执行和不良事件。</p>
        <FeedbackCard submitted={feedbackSubmitted} onSubmit={() => setFeedbackSubmitted(true)} />
      </AgentMessage>
      {feedbackSubmitted ? <AgentMessage><FollowUpDifferenceCard /></AgentMessage> : null}
    </>
  );
}

function FactConfirmationCard({ status, onChange }: { status: FactStatus; onChange: (status: FactStatus) => void }) {
  const labels: Record<FactStatus, string> = {
    pending: "等待本人确认",
    confirmed: "本人已确认",
    edited: "本人修改后确认",
    declined: "本人选择不记录",
  };
  return (
    <article className="chat-card fact-card">
      <div className="card-heading"><span>健康事实确认卡</span><b className={`card-state ${status}`}>{labels[status]}</b></div>
      <dl>
        <div><dt>睡眠</dt><dd>近几日入睡稍慢</dd></div>
        <div><dt>精力</dt><dd>午后容易疲倦</dd></div>
        <div><dt>来源锚点</dt><dd>患者合成自述 · 消息 SYN-MSG-014 · 版本 1</dd></div>
      </dl>
      <p className="card-note">AI 只做整理；原话、来源和你的确认状态分别保存。未知不会被写成正常。</p>
      <div className="card-actions">
        <button onClick={() => onChange("confirmed")}>确认无误</button>
        <button onClick={() => onChange("edited")}>修改后确认</button>
        <button className="quiet" onClick={() => onChange("declined")}>不记录</button>
      </div>
    </article>
  );
}

function ActionCard({ status, onChange }: { status: ActionStatus; onChange: (status: ActionStatus) => void }) {
  const statusText = { pending: "待开始", active: "执行中", completed: "已完成" }[status];
  return (
    <article className="chat-card action-card">
      <div className="card-heading"><span>医生确认的行动卡</span><b className={`card-state ${status}`}>{statusText}</b></div>
      <h3>连续 3 天记录入睡时间</h3>
      <p>目的：观察作息与午后精力之间的变化。不是治疗承诺，也不要求追求“完美执行”。</p>
      <div className="source-strip">签署记录 SYN-REC-001 · 计划版本 1 · 医生 SYN-DOC-001</div>
      <div className="card-actions">
        {status === "pending" ? <button onClick={() => onChange("active")}>开始执行</button> : null}
        {status === "active" ? <button onClick={() => onChange("completed")}>标记今天已完成</button> : null}
        {status === "completed" ? <button onClick={() => onChange("active")}>继续下一天</button> : null}
        <button className="quiet">记录中断或偏差</button>
      </div>
    </article>
  );
}

function FeedbackCard({ submitted, onSubmit }: { submitted: boolean; onSubmit: () => void }) {
  const [response, setResponse] = useState("略有改善");
  const [adverse, setAdverse] = useState(false);
  return (
    <article className="chat-card feedback-card">
      <div className="card-heading"><span>患者反馈卡</span><b className={`card-state ${submitted ? "confirmed" : "pending"}`}>{submitted ? "已提交（防重复）" : "等待反馈"}</b></div>
      <label>相比上次，最重要的变化</label>
      <div className="feedback-scale">
        {["加重", "无变化", "略有改善", "明显改善", "不确定"].map((item) => <button className={response === item ? "active" : ""} key={item} onClick={() => setResponse(item)}>{item}</button>)}
      </div>
      <label className="adverse-check"><input type="checkbox" checked={adverse} onChange={(event) => setAdverse(event.target.checked)} /> 出现新的不适或疑似不良事件，需要医生查看</label>
      <textarea defaultValue="合成示例：入睡稍快，午后精力仍有波动。" aria-label="合成患者反馈" />
      <div className="card-actions"><button onClick={onSubmit}>{submitted ? "相同内容已安全识别，不会重复写入" : "提交本次反馈"}</button></div>
    </article>
  );
}

function FollowUpDifferenceCard() {
  return (
    <article className="chat-card difference-card">
      <div className="card-heading"><span>复诊差异卡</span><b className="card-state draft">等待医生比较</b></div>
      <div className="difference-grid">
        <div><small>上次签署记录</small><b>SYN-REC-001 · 版本 1</b></div>
        <div><small>实际执行</small><b>开始 1 次 · 完成 1 次</b></div>
        <div><small>患者体验</small><b>略有改善</b></div>
        <div><small>不良事件</small><b>本人未报告</b></div>
      </div>
      <p className="card-note">这只是确定性汇总，没有 AI 判断。医生必须结合本次四诊重新辨证。</p>
    </article>
  );
}

function DoctorConversation({
  patient,
  setPatient,
  eligible,
  status,
  onStart,
  onClose,
  reviewed,
  setReviewed,
}: {
  patient: string;
  setPatient: (patient: string) => void;
  eligible: boolean;
  status: ConsultationStatus;
  onStart: () => void;
  onClose: () => void;
  reviewed: boolean;
  setReviewed: (value: boolean) => void;
}) {
  if (status === "closed") return <ResponsibilitySnapshot patient={patient} onStart={onStart} />;
  if (status !== "active") {
    return (
      <AgentMessage>
        <article className="chat-card consultation-launch-card">
          <div className="card-heading"><span>临时会诊门禁</span><b className={`card-state ${eligible ? "confirmed" : "declined"}`}>{eligible ? "关系有效" : "门禁拒绝"}</b></div>
          <h3>开始接诊前，系统自动核对当前患者</h3>
          <p>案件编号、医生编号和授权依据由服务端生成，不让医生手工填写格式化表格。</p>
          <label>合成患者<select value={patient} onChange={(event) => setPatient(event.target.value)}><option value="SYN-PAT-001">SYN-PAT-001 · 本轮可接诊</option><option value="SYN-PAT-002">SYN-PAT-002 · 无有效照护关系</option></select></label>
          <div className="source-strip">{eligible ? "医生身份、照护关系、目的、范围与时限检查通过" : "无有效照护关系，患者资料不可读取"}</div>
          <div className="card-actions"><button disabled={!eligible} onClick={onStart}>开始接诊并临时挂载资料</button></div>
        </article>
      </AgentMessage>
    );
  }
  return (
    <>
      <div className="consultation-session-bar"><span><b>当前患者：{patient}</b> · 临时读取本次获准资料</span><button onClick={onClose}>结束接诊并关闭</button></div>
      <PersonMessage text="请把患者最近执行与反馈的变化放到上次签署记录旁边。" />
      <AgentMessage><FollowUpDifferenceCard /></AgentMessage>
      <AgentMessage>
        <article className="chat-card doctor-review-card">
          <div className="card-heading"><span>医生审核卡</span><b className={`card-state ${reviewed ? "confirmed" : "pending"}`}>{reviewed ? "医生已逐项审核" : "等待医生审核"}</b></div>
          <h3>候选字段与来源检查</h3>
          <div className="review-line"><span>患者事实</span><b>5 项</b><small>均有患者来源或明确未采集</small></div>
          <div className="review-line"><span>上次签署记录</span><b>1 项</b><small>SYN-REC-001 · 版本 1</small></div>
          <div className="review-line warning"><span>候选字段缺口</span><b>3 项</b><small>脉诊、舌象采集条件、当前过敏史</small></div>
          <div className="review-line"><span>AI 内容</span><b>草稿区</b><small>未进入正式字段</small></div>
          <label className="doctor-comparison">医生前后比较<textarea defaultValue="合成示例：执行后睡眠自述略有改善，但脉诊与舌象尚未采集，暂不改变原判断。" /></label>
          <p className="card-note">TCM-CLIN-001 仍是待真人医生评审的候选字段合同，不是临床标准。</p>
          <div className="card-actions"><button onClick={() => setReviewed(true)}>逐项确认并保存医生比较</button><button disabled>签署功能未启用</button></div>
        </article>
      </AgentMessage>
    </>
  );
}

function ResponsibilitySnapshot({ patient, onStart }: { patient: string; onStart: () => void }) {
  return (
    <AgentMessage>
      <article className="chat-card responsibility-card-v2">
        <div className="snapshot-mark">✓</div>
        <div>
          <div className="card-heading"><span>最小责任快照</span><b className="card-state confirmed">会诊已关闭</b></div>
          <h3>{patient} 的临时资料已经解除挂载</h3>
          <p>没有把患者原文、检索词或临时模型上下文写入医生永久知识库。</p>
          <dl><div><dt>来源编号/版本/哈希</dt><dd>7 项</dd></div><div><dt>患者原文副本</dt><dd>0 份</dd></div><div><dt>模型临时上下文</dt><dd>已丢弃</dd></div></dl>
          <div className="card-actions"><button onClick={onStart}>重新开始一次合成会诊</button></div>
        </div>
      </article>
    </AgentMessage>
  );
}
