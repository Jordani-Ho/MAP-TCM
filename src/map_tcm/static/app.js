const state = { overview: null, doctorSessionId: null };
const clinicalScope = {
  purpose: "synthetic_clinical_care",
  data_categories: [
    "chief_complaint",
    "observations",
    "assessment",
    "treatment_principle",
    "prescription",
  ],
};

const statusLabels = {
  draft: "草稿",
  doctor_reviewed: "医生已审核",
  signed: "已签署",
  withdrawn: "已撤回",
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.doctorSessionId && options.method === "POST") {
    headers["X-MAP-Session-ID"] = state.doctorSessionId;
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "操作失败");
  return payload;
}

function showMessage(text, isError = false) {
  const node = document.querySelector("#message");
  node.textContent = text;
  node.classList.toggle("error", isError);
}

function currentEncounter() {
  const id = document.querySelector("#encounter-select").value;
  return state.overview.encounters.find((item) => item.id === id);
}

function renderMetrics(counts) {
  const labels = {
    patients: "合成患者",
    active_consents: "有效授权",
    encounters: "就诊记录",
    drafts: "待审核草稿",
    signed: "已签署",
  };
  document.querySelector("#metrics").innerHTML = Object.entries(labels)
    .map(([key, label]) => `<div class="metric"><span>${label}</span><b>${counts[key]}</b></div>`)
    .join("");
}

function renderEncounters(encounters) {
  const list = document.querySelector("#encounters");
  list.innerHTML = encounters.length
    ? encounters.map((item) => `
        <article class="encounter-card ${item.status}">
          <div class="meta"><span class="status">${escapeHtml(statusLabels[item.status] || item.status)}</span><span>${escapeHtml(item.display_code)}</span><span>${escapeHtml(item.encounter_type)}</span></div>
          <h3>${escapeHtml(item.chief_complaint)}</h3>
          <div class="meta"><span>观察 ${escapeHtml(item.observation_count)}</span><span>医生判断 ${escapeHtml(item.assessment_count)}</span><span>版本 ${escapeHtml(item.version)}</span></div>
        </article>`).join("")
    : '<p class="muted">暂无记录</p>';

  const select = document.querySelector("#encounter-select");
  select.innerHTML = encounters.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.display_code)} · ${escapeHtml(statusLabels[item.status] || item.status)} · ${escapeHtml(item.chief_complaint)}</option>`).join("");
}

function renderAudit(events) {
  document.querySelector("#audit").innerHTML = events.map((event) => `
    <div class="audit-event"><code>${escapeHtml(event.event_type)}</code><br>${escapeHtml(event.object_type)} · ${escapeHtml(event.actor_id)}<br><span class="muted">${escapeHtml(event.occurred_at)}</span></div>`).join("");
}

async function refresh() {
  state.overview = await request("/api/v1/overview");
  renderMetrics(state.overview.counts);
  renderEncounters(state.overview.encounters);
  renderAudit(state.overview.audit);
}

async function act(action) {
  try {
    showMessage("处理中……");
    const encounter = currentEncounter();
    if (!encounter) throw new Error("没有可操作的就诊记录");
    await action(encounter);
    await refresh();
    showMessage("操作完成，审计事件已记录。");
  } catch (error) {
    showMessage(error.message, true);
  }
}

document.querySelector("#refresh").addEventListener("click", refresh);
document.querySelector("#add-observation").addEventListener("click", () => act((encounter) => request(`/api/v1/encounters/${encounter.id}/observations`, {
  method: "POST",
  body: JSON.stringify({ category: "随访", content: document.querySelector("#synthetic-note").value, source: "patient", ...clinicalScope }),
})));
document.querySelector("#add-assessment").addEventListener("click", () => act((encounter) => request(`/api/v1/encounters/${encounter.id}/assessments`, {
  method: "POST",
  body: JSON.stringify({ pattern: "合成证候示例", mechanism: "仅用于验证数据结构", rationale: "根据合成观察形成的医生示例判断", treatment_principle: "演示性治则，不构成医疗建议", prescription_summary: "无真实处方", ...clinicalScope }),
})));
document.querySelector("#review-encounter").addEventListener("click", () => act((encounter) => request(`/api/v1/encounters/${encounter.id}/review`, {
  method: "POST", body: JSON.stringify(clinicalScope),
})));
document.querySelector("#sign-encounter").addEventListener("click", () => act((encounter) => request(`/api/v1/encounters/${encounter.id}/sign`, {
  method: "POST", body: JSON.stringify(clinicalScope),
})));

request("/api/v1/synthetic/context")
  .then((context) => {
    state.doctorSessionId = context.doctor_session_id;
    return refresh();
  })
  .catch((error) => showMessage(error.message, true));
