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

test("renders the MAP-TCM synthetic review portal", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>MAP-TCM 合成评审门户<\/title>/i);
  assert.match(html, /我是合作中医师/);
  assert.match(html, /我是患者体验者/);
  assert.match(html, /仅限合成数据/);
  assert.match(html, /<b>0<\/b>\s*条真实病历/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/);
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
