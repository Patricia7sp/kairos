import { ControlAttempt, cleanExport } from "./collector.js";
import { ChatChannel, jsonRequest } from "./channel.js";

const scenarios = ["ocioso", "conversa_painel", "auditoria", "execucao_isolada", "recuperacao"];
const labels = { ocioso: "Ocioso", conversa_painel: "Conversa e painel", auditoria: "Auditoria sintética",
  execucao_isolada: "Execução isolada", recuperacao: "Recuperação" };
const byId = (id) => document.getElementById(id);
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const hex = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
const newSession = () => `e03-synthetic-${hex()}`;
const samples = [];
let manifest, report, current, cancelled = false;
const status = (text) => { byId("status").textContent = text; };
const progress = (text) => { byId("progress").textContent += `${text}\n`; };

async function patchSession(sessionId, stage) {
  const response = await fetch(`/api/sessions/${sessionId}`, { method: "PATCH", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      display_name: `e03-synthetic:${manifest.run_id}:${stage}`, tags: ["e03-synthetic"] }) });
  if (!response.ok) throw new Error("session_marker_failed");
}
async function publicArtifact(name) {
  const response = await fetch(`/ui/e03-run/${name}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error("artifact_unavailable");
  return response.json();
}


async function persisted(sessionId, callId, signal) {
  while (!signal.aborted) {
    const response = await jsonRequest(`/api/sessions/${sessionId}/messages`, { signal });
    if (response.status !== 200 || response.body.session_id !== sessionId) throw new Error("persist_read_failed");
    const rows = response.body.messages.filter((row) => row.tool_call_id === callId);
    if (rows.length > 1) throw new Error("duplicate_receipt");
    if (rows.length === 1) return rows[0];
    await pause(25);
  }
  throw new Error("persist_timeout");
}
function emptyFailure(scenario, sessionId, outcome = "erro", kind = "transport") {
  return cleanExport({ scenario, outcome, session_id: sessionId, error_kind: kind });
}
async function control(channel, scenario, sessionId) {
  const approval = channel.wait("tool_approval_request");
  const ended = channel.wait("turn_end").then(() => true, () => false);
  channel.send(sessionId, `e03-synthetic:${manifest.run_id}:${scenario}:controle`, true);
  let event;
  try { event = await approval; } catch { return emptyFailure(scenario, sessionId, "timeout", "deadline"); }
  let attempt;
  try {
    attempt = new ControlAttempt({ scenario, sessionId, approvalId: event.approval_id, callId: event.tool_call?.id,
      postDeny: (signal) => jsonRequest(`/api/chat/sessions/${sessionId}/tool-approvals/${event.approval_id}`, { body: { decision: "deny" }, signal }),
      readPersisted: (signal) => persisted(sessionId, event.tool_call.id, signal), root: byId("result") });
  } catch { return emptyFailure(scenario, sessionId, "erro", "correlation"); }
  channel.attempt = attempt;
  current = attempt;
  const row = await attempt.start();
  const finished = await ended;
  current = null; channel.attempt = null;
  return finished ? row : cleanExport({ ...row, outcome: "erro", error_kind: "transport" });
}

async function waitStage(scenario) {
  const deadline = performance.now() + 120_000;
  while (!cancelled && performance.now() < deadline) {
    const value = await publicArtifact("status.json");
    if (value.run_id !== manifest.run_id || value.state === "failed") throw new Error("workload_failed");
    if (value.active_scenario === scenario && value.state === "running") return;
    await pause(250);
  }
  throw new Error("workload_timeout");
}
async function pressure(signal) {
  const channel = await ChatChannel.open(() => {
    byId("panel").textContent = `Conversa sintética concorrente: ${performance.now().toFixed(0)} ms`;
  });
  const sessionId = newSession();
  try {
    while (!signal.aborted) {
      await channel.plain(sessionId, "e03-synthetic:painel-concorrente");
      await pause(100);
    }
  } finally { channel.close(); }
}
function clientMetadata() {
  return { user_agent: navigator.userAgent.slice(0, 512), platform: navigator.platform.slice(0, 64),
    protocol: location.protocol, secure_context: globalThis.isSecureContext, origin: location.origin,
    clock: "browser_performance_now", capture: "actual_user_browser", includes_network: true };
}
async function submitReport() {
  const channel = await ChatChannel.open();
  const reportSession = newSession();
  try {
    const raw = { schema_version: 1, run_id: manifest.run_id, client: clientMetadata(), samples: samples.map(cleanExport) };
    await channel.plain(reportSession, `e03-synthetic:report:${JSON.stringify(raw)}`);
    await patchSession(reportSession, "report");
  } finally { channel.close(); }
  const deadline = performance.now() + 30_000;
  while (performance.now() < deadline) {
    try {
      const value = await publicArtifact("report.json");
      if (value.run_id === manifest.run_id) return value;
    } catch { /* o launcher ainda não publicou o artefato; o prazo continua valendo */ }
    await pause(500);
  }
  throw new Error("report_not_ready");
}

async function run() {
  cancelled = false; samples.length = 0; report = null;
  byId("start").disabled = true; byId("stop").disabled = false; byId("download").disabled = true;
  byId("progress").textContent = "";
  try {
    for (const scenario of scenarios) {
      if (cancelled) break;
      status(`Preparando ${labels[scenario]}…`);
      const sessionId = newSession();
      let channel, load, loadController;
      try {
        channel = await ChatChannel.open();
        await channel.plain(sessionId, `e03-synthetic:${manifest.run_id}:${scenario}:preparação`);
        await patchSession(sessionId, scenario);
        await waitStage(scenario);
        if (scenario === "conversa_painel") {
          loadController = new AbortController();
          load = pressure(loadController.signal).then(() => true, () => false);
        }
        for (let index = 0; index < 30 && !cancelled; index++) {
          status(`${labels[scenario]} — controle ${index + 1}/30`);
          byId("result").scrollIntoView({ block: "center" });
          const row = await control(channel, scenario, sessionId);
          samples.push(row);
          progress(`${labels[scenario]} ${index + 1}: ${row.outcome}${row.http_status ? ` (HTTP ${row.http_status})` : ""}`);
          if (row.outcome !== "denied") { cancelled = true; break; }
          await pause(150);
        }
      } catch {
        samples.push(emptyFailure(scenario, sessionId, "erro", "workload"));
        cancelled = true;
      } finally {
        loadController?.abort();
        if (load && !await load) cancelled = true;
        channel?.close();
      }
    }
    status("Consolidando amostras e recursos…");
    report = await submitReport();
    byId("summary").textContent = JSON.stringify({ scope: report.scope, gate: report.gate,
      scenarios: report.scenarios, resource_state: report.resource_state }, null, 2);
    status(cancelled ? "Coleta parcial preservada. Baixe o relatório para revisão." : "Coleta encerrada. Baixe o relatório para revisão do E0.");
  } catch {
    report = { schema_version: 1, run_id: manifest.run_id, client: clientMetadata(),
      samples: samples.map(cleanExport), scope: "partial_client_only", gate: "inconclusive" };
    status("Não foi possível consolidar no servidor. As amostras locais estão disponíveis para baixar.");
  } finally {
    current?.fail("erro", "cancelled"); current = null;
    byId("stop").disabled = true; byId("download").disabled = false;
  }
}

byId("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("secret");
  try {
    if (!manifest) throw new Error("profile_unavailable");
    const token = input.value; input.value = "";
    const response = await jsonRequest("/api/auth/login", { body: { token }, signal: AbortSignal.timeout(10_000) });
    if (response.status !== 200 || response.body.authenticated !== true) throw new Error("login_failed");
    byId("login").hidden = true; byId("start").disabled = false;
    status("Acesso confirmado. Mantenha esta aba visível e inicie a coleta.");
  } catch { status("Acesso recusado. Confira a credencial própria da temporária."); }
});
byId("start").addEventListener("click", () => { void run(); });
byId("stop").addEventListener("click", () => { cancelled = true; current?.fail("erro", "cancelled"); });
byId("download").addEventListener("click", () => {
  if (!report) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = `e03-${manifest.run_id}.json`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
try {
  manifest = await publicArtifact("manifest.json");
  if (manifest.profile !== "e03-synthetic" || !/^[a-f0-9]{32}$/.test(manifest.run_id)
      || manifest.origin !== location.origin) throw new Error("invalid_profile");
  status("Instância temporária identificada. Informe sua credencial de acesso.");
} catch { manifest = null; status("Identidade da instância indisponível. Coleta bloqueada."); }
