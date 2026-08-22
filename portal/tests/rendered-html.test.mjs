import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("root route renders only the Gate 1 conversation client", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>MAP 治未病 AI 社区<\/title>/i);
  assert.match(html, /患者健康辅导员（AI）/);
  assert.match(html, /医师智能体徒弟/);
  assert.match(html, /仅限合成数据/);
  assert.match(html, /健康事实确认卡/);
  assert.doesNotMatch(html, /我是合作中医师|我是患者体验者|进入医生评审|进入患者评审/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/);
});

test("legacy review portal is sealed off from all active routes", async () => {
  const [home, prototype] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/prototype/page.tsx", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(home, /review-portal|ReviewPortal/);
  assert.match(home, /prototype\/prototype-client/);
  assert.match(prototype, /prototype-client/);
});

test("ships both prompt packs and keeps hosting state logical", async () => {
  const [doctorPrompt, patientPrompt, hosting] = await Promise.all([
    readFile(new URL("../public/prompts/doctor-review-prompt.txt", import.meta.url), "utf8"),
    readFile(new URL("../public/prompts/patient-review-prompt.txt", import.meta.url), "utf8"),
    readFile(new URL("../.openai/hosting.json", import.meta.url), "utf8"),
  ]);
  assert.match(doctorPrompt, /SYN-DOC-001/);
  assert.match(doctorPrompt, /不诊断、不辨证、不处方/);
  assert.match(patientPrompt, /SYN-PAT-001/);
  assert.match(patientPrompt, /不得询问参与者自己的真实病史/);
  const hostingConfig = JSON.parse(hosting);
  assert.match(hostingConfig.project_id, /^appgprj_/);
  assert.equal(hostingConfig.d1, null);
  assert.equal(hostingConfig.r2, null);
  await assert.rejects(access(new URL("../app/_sites-preview", import.meta.url)));
});

test("client export contract remains synthetic and local-only", async () => {
  const source = await readFile(new URL("../app/review-portal.tsx", import.meta.url), "utf8");
  assert.match(source, /synthetic_only:\s*true/);
  assert.match(source, /local_storage_only:\s*true/);
  assert.match(source, /SYN-\$\{role/);
  assert.match(source, /window\.localStorage/);
  assert.match(source, /downloadJson/);
  assert.doesNotMatch(source, /fetch\([^)]*api\//i);
});

test("Gate 0 prototype keeps clinical responsibility and data boundaries explicit", async () => {
  const source = await readFile(new URL("../app/prototype/prototype-client.tsx", import.meta.url), "utf8");
  assert.match(source, /仅限合成数据/);
  assert.match(source, /原型不保存|不保存/);
  assert.match(source, /AI 只起草，医生逐项审阅/);
  assert.match(source, /签署功能未启用|签署门禁未开放/);
  assert.match(source, /SYN-PAT-001/);
  assert.match(source, /SYN-DOC-001/);
  assert.doesNotMatch(source, /fetch\([^)]*api\//i);
});

test("doctor prototype mounts one patient temporarily and closes without a copy", async () => {
  const source = await readFile(new URL("../app/prototype/prototype-client.tsx", import.meta.url), "utf8");
  assert.match(source, /type ConsultationStatus = "idle" \| "active" \| "closed"/);
  assert.match(source, /开始接诊并临时挂载资料/);
  assert.match(source, /无有效照护关系/);
  assert.match(source, /结束接诊并关闭/);
  assert.match(source, /患者原文副本<\/dt><dd>0 份/);
  assert.match(source, /最小责任快照/);
  assert.doesNotMatch(source, /fetch\([^)]*api\//i);
});

test("Gate 1 prototype is conversation-first and carries the no-AI care loop as cards", async () => {
  const source = await readFile(new URL("../app/prototype/prototype-client.tsx", import.meta.url), "utf8");
  assert.match(source, /当前交流对象/);
  assert.match(source, /患者健康辅导员（AI）/);
  assert.match(source, /医师智能体徒弟（AI）/);
  assert.match(source, /健康事实确认卡/);
  assert.match(source, /来源锚点/);
  assert.match(source, /医生确认的行动卡/);
  assert.match(source, /患者反馈卡/);
  assert.match(source, /复诊差异卡/);
  assert.match(source, /上次签署记录/);
  assert.match(source, /医生审核卡/);
  assert.match(source, /候选字段缺口/);
  assert.match(source, /语音/);
  assert.match(source, /照片/);
  assert.match(source, /视频/);
  assert.match(source, /文件/);
  assert.match(source, /没有调用设备、没有读取内容、没有上传/);
  assert.doesNotMatch(source, /fetch\([^)]*api\//i);
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
});
