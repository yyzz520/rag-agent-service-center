const API = "/api/v1";
const state = { token: "", agentConv: null, planConv: null, lastMs: 0 };

const $ = (id) => document.getElementById(id);

function setDebug(obj) {
  $("debug").textContent = JSON.stringify(obj, null, 2);
}

function updateRun(patch) {
  if (patch.user !== undefined) $("runUser").textContent = patch.user;
  if (patch.route !== undefined) $("runRoute").textContent = patch.route || "—";
  if (patch.tools !== undefined) {
    $("runTools").textContent = patch.tools && patch.tools.length
      ? patch.tools.join(" / ") : "—";
  }
  if (patch.status !== undefined) $("runStatus").textContent = patch.status || "—";
}

function setPhase(n) {
  document.querySelectorAll("#flow li").forEach((li) => {
    li.classList.toggle("active", Number(li.dataset.phase) <= n);
  });
}

async function api(path, body, method) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  const start = performance.now();
  const resp = await fetch(API + path, {
    method: method || "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  state.lastMs = Math.round(performance.now() - start);
  $("runMs").textContent = state.lastMs + " ms";
  const trace = resp.headers.get("X-Trace-Id");
  if (trace) {
    $("traceId").textContent = "trace: " + trace;
    $("runTrace").textContent = trace;
  }
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    throw new Error("响应不是 JSON（HTTP " + resp.status + "）");
  }
  if (!resp.ok || data.code !== 0) {
    throw new Error(data.message || "请求失败（HTTP " + resp.status + "）");
  }
  return data.data;
}

function apiGet(path) {
  return api(path, undefined, "GET");
}

async function login() {
  const username = $("username").value.trim();
  const password = $("password").value;
  const data = await api("/auth/login", { username, password });
  state.token = data.token;
  $("userInfo").textContent =
    "已登录 " + data.user.username + " · " + data.user.tenant_id;
  updateRun({
    user: data.user.username + " · " +
      (data.user.role === "admin" ? "管理员" : data.user.role) +
      " · " + data.user.tenant_id,
  });
  loadMiniEval().catch(() => {});
}

function addMsg(container, role, text, meta) {
  const empty = container.querySelector(".empty-state");
  if (empty) empty.remove();
  const wrap = document.createElement("div");
  wrap.className = "msg " + role;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text == null ? "" : String(text);
  wrap.appendChild(bubble);
  if (meta && meta.length) {
    const row = document.createElement("div");
    row.className = "meta";
    meta.forEach((item) => {
      const pill = document.createElement("span");
      pill.className = "pill" + (item.cls ? " " + item.cls : "");
      pill.textContent = item.text;
      row.appendChild(pill);
    });
    wrap.appendChild(row);
  }
  container.appendChild(wrap);
  container.scrollTop = container.scrollHeight;
  return wrap;
}

function renderSources(wrap, sources) {
  const box = document.createElement("div");
  box.className = "sources";
  sources.forEach((s) => {
    const div = document.createElement("div");
    div.className = "source";
    const head = document.createElement("div");
    const cite = document.createElement("span");
    cite.className = "cite";
    cite.textContent = "[" + s.id + "]";
    const src = document.createElement("strong");
    src.textContent = (s.source || "未命名来源") + (s.page ? " · 第 " + s.page + " 页" : "");
    head.appendChild(cite);
    head.appendChild(src);
    const body = document.createElement("div");
    body.className = "content";
    body.textContent = String(s.content || "").slice(0, 420);
    div.appendChild(head);
    div.appendChild(body);
    box.appendChild(div);
  });
  wrap.appendChild(box);
}

// ---- 流式工具 ----
async function streamSSE(path, body, onEvent) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  const start = performance.now();
  const resp = await fetch(API + path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    let message = "HTTP " + resp.status;
    try {
      const d = await resp.json();
      if (d.message) message = d.message;
    } catch (e) { /* ignore */ }
    throw new Error(message);
  }
  const trace = resp.headers.get("X-Trace-Id");
  if (trace) {
    $("traceId").textContent = "trace: " + trace;
    $("runTrace").textContent = trace;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      let data = "";
      block.split("\n").forEach((line) => {
        line = line.replace(/\r$/, "");
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      });
      if (data) {
        let parsed = data;
        try { parsed = JSON.parse(data); } catch (e) { /* keep string */ }
        onEvent(event, parsed);
      }
    }
  }
  state.lastMs = Math.round(performance.now() - start);
  $("runMs").textContent = state.lastMs + " ms";
}

function addStreamingMsg(container, text) {
  const wrap = addMsg(container, "assistant", text);
  const bubble = wrap.querySelector(".bubble");
  bubble.classList.add("streaming");
  const meta = document.createElement("div");
  meta.className = "meta";
  wrap.appendChild(meta);
  return { wrap, bubble, meta };
}

function addPill(meta, text, cls) {
  const pill = document.createElement("span");
  pill.className = "pill" + (cls ? " " + cls : "");
  pill.textContent = text;
  meta.appendChild(pill);
}

function scrollBottom(container) {
  container.scrollTop = container.scrollHeight;
}

function hideOverview() {
  const el = $("overview");
  if (el) el.hidden = true;
}

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  document.querySelectorAll(".tab").forEach((s) => {
    s.classList.toggle("active", s.id === "tab-" + name);
  });
  if (name === "eval") loadEval();
}

function showEmpty(container, title, hint) {
  container.innerHTML = "";
  const div = document.createElement("div");
  div.className = "empty-state";
  const strong = document.createElement("strong");
  strong.textContent = title;
  const p = document.createElement("p");
  p.textContent = hint;
  div.appendChild(strong);
  div.appendChild(p);
  container.appendChild(div);
}

// ---- 知识库问答（流式）----
async function sendRag(q) {
  hideOverview();
  const box = $("ragMessages");
  addMsg(box, "user", q);
  setPhase(0);
  updateRun({ route: "rag", tools: [], status: "检索中" });
  const { wrap, bubble } = addStreamingMsg(box, "连接中…");
  let text = "";
  let done = false;
  let failure = null;
  try {
    await streamSSE("/rag/chat/stream", { question: q }, (event, d) => {
      if (event === "content") {
        if (!text) bubble.textContent = "";
        text += d.delta || "";
        bubble.textContent = text;
        scrollBottom(box);
      } else if (event === "done") {
        done = true;
        text = d.answer || text;
        bubble.textContent = text || "(空)";
        if (d.sources && d.sources.length) renderSources(wrap, d.sources);
        setDebug(d);
      } else if (event === "error") {
        failure = d.message || "流式调用失败";
      } else if (event === "meta") {
        setDebug(d);
      }
    });
    if (failure) throw new Error(failure);
    bubble.classList.remove("streaming");
    if (!done) bubble.textContent = text || "(空)";
    setPhase(3);
    updateRun({ route: "rag", tools: [], status: "完成" });
  } catch (e) {
    bubble.classList.remove("streaming");
    wrap.className = "msg error";
    bubble.textContent = "请求失败：" + e.message;
    setDebug({ error: e.message });
  }
}

// ---- 智能体 + 审批（流式）----
async function sendAgent(q) {
  hideOverview();
  const box = $("agentMessages");
  addMsg(box, "user", q);
  setPhase(0);
  updateRun({ route: "—", tools: [], status: "思考中" });
  const { wrap, bubble, meta } = addStreamingMsg(box, "连接中…");
  let text = "";
  let route = "—";
  let tools = [];
  let routePill = false;
  let done = false;
  let awaiting = null;
  let failure = null;
  try {
    const body = { question: q };
    if (state.agentConv) body.conversation_id = state.agentConv;
    await streamSSE("/agent/chat/stream", body, (event, d) => {
      if (event === "meta") {
        if (d.conversation_id) state.agentConv = d.conversation_id;
        if (d.route && !routePill) {
          route = d.route;
          routePill = true;
          addPill(meta, "路由 " + route, "route-" + route);
        }
        updateRun({ route });
        setDebug(d);
      } else if (event === "content") {
        if (!text) bubble.textContent = "";
        text += d.delta || "";
        bubble.textContent = text;
        scrollBottom(box);
      } else if (event === "tool_start") {
        const name = d.name || "tool";
        if (!tools.includes(name)) {
          tools.push(name);
          addPill(meta, "工具 " + name);
        }
        updateRun({ route, tools, status: "执行工具" });
        setDebug(d);
      } else if (event === "tool_end") {
        updateRun({ route, tools, status: "执行工具" });
        setDebug(d);
      } else if (event === "awaiting_approval") {
        awaiting = d;
      } else if (event === "done") {
        done = true;
        text = d.answer || text;
        if (d.route) route = d.route;
        if (d.tools && d.tools.length) tools = d.tools;
        bubble.textContent = text || "(空)";
        setPhase(3);
        updateRun({ route: d.route || route, tools, status: "完成" });
        setDebug(d);
      } else if (event === "error") {
        failure = d.message || "流式调用失败";
      }
    });
    if (failure) throw new Error(failure);
    bubble.classList.remove("streaming");
    if (awaiting) {
      if (!text) wrap.remove();
      if (awaiting.conversation_id) state.agentConv = awaiting.conversation_id;
      setPhase(2);
      updateRun({
        route: awaiting.route || route,
        tools: (awaiting.pending || []).map((p) => p.name),
        status: "等待审批",
      });
      renderApproval(awaiting);
      return;
    }
    if (!done) bubble.textContent = text || "(空)";
    setPhase(3);
    updateRun({ route, tools, status: "完成" });
  } catch (e) {
    bubble.classList.remove("streaming");
    wrap.className = "msg error";
    bubble.textContent = "请求失败：" + e.message;
    setDebug({ error: e.message });
  }
}

function handleAgent(d) {
  setDebug(d);
  if (d.status === "awaiting_approval") {
    setPhase(2);
    updateRun({ route: d.route || "tool", tools: d.tools || [], status: "等待审批" });
    renderApproval(d);
    return;
  }
  setPhase(3);
  updateRun({ route: d.route || "tool", tools: d.tools || [], status: "完成" });
  const meta = [];
  if (d.route) meta.push({ text: "路由 " + d.route, cls: "route-" + d.route });
  if (d.tools && d.tools.length) meta.push({ text: "工具 " + d.tools.join(" / ") });
  addMsg($("agentMessages"), "assistant", d.answer || "(空)", meta);
}

function renderApproval(d) {
  const box = $("agentMessages");
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  const card = document.createElement("div");
  card.className = "approval";
  const h = document.createElement("h3");
  h.textContent = "需要人工审批（外部工具执行前暂停）";
  card.appendChild(h);
  (d.pending || []).forEach((p) => {
    const line = document.createElement("div");
    line.className = "tool";
    line.textContent = p.name + "  " + JSON.stringify(p.args || {});
    card.appendChild(line);
  });
  const actions = document.createElement("div");
  actions.className = "actions";
  const approve = document.createElement("button");
  approve.className = "ok";
  approve.textContent = "批准";
  const reject = document.createElement("button");
  reject.className = "danger";
  reject.textContent = "拒绝";
  approve.onclick = () => decideAgent(d, "approve", [approve, reject]);
  reject.onclick = () => decideAgent(d, "reject", [approve, reject]);
  actions.appendChild(approve);
  actions.appendChild(reject);
  card.appendChild(actions);
  wrap.appendChild(card);
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
}

async function decideAgent(d, type, buttons) {
  buttons.forEach((b) => (b.disabled = true));
  const box = $("agentMessages");
  const loading = addMsg(box, "assistant", "处理中…");
  try {
    const decisions = (d.pending || []).map((p) => ({
      tool_call_id: p.tool_call_id,
      type,
    }));
    const r = await api("/agent/approve", {
      conversation_id: d.conversation_id,
      decisions,
    });
    loading.remove();
    handleAgent(r);
  } catch (e) {
    loading.remove();
    addMsg(box, "error", e.message);
    buttons.forEach((b) => (b.disabled = false));
  }
}

// ---- 计划任务 ----
async function sendPlan(q) {
  hideOverview();
  const box = $("planMessages");
  addMsg(box, "user", q);
  setPhase(0);
  updateRun({ route: "plan", tools: [], status: "规划中" });
  const loading = addMsg(box, "assistant", "生成计划…");
  try {
    const body = { question: q };
    if (state.planConv) body.conversation_id = state.planConv;
    const d = await api("/agent/plan", body);
    loading.remove();
    if (d.conversation_id) state.planConv = d.conversation_id;
    handlePlan(d);
  } catch (e) {
    loading.remove();
    addMsg(box, "error", e.message);
  }
}

function handlePlan(d) {
  setDebug(d);
  const steps = d.plan || d.steps || [];
  const tools = steps.map((s) => s.tool).filter(Boolean);
  if (d.status === "awaiting_plan_approval") {
    setPhase(2);
    updateRun({ route: "plan", tools, status: "计划待审批" });
    renderPlan(d);
    return;
  }
  setPhase(3);
  updateRun({ route: "plan", tools, status: "完成" });
  const box = $("planMessages");
  const wrap = addMsg(box, "assistant", d.answer || "(空)");
  if (d.steps && d.steps.length) renderSteps(wrap, d.steps);
}

function renderPlan(d) {
  const box = $("planMessages");
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  const card = document.createElement("div");
  card.className = "plan-card";
  const h = document.createElement("h3");
  h.textContent = "计划待审批（批准后逐步执行）";
  card.appendChild(h);
  (d.plan || d.steps || []).forEach((step, i) => {
    const row = document.createElement("div");
    row.className = "plan-step";
    const idx = document.createElement("div");
    idx.className = "idx";
    idx.textContent = String(i + 1);
    const main = document.createElement("div");
    const goal = document.createElement("div");
    goal.className = "goal";
    goal.textContent = step.goal || "步骤 " + (i + 1);
    const tool = document.createElement("div");
    tool.className = "tool";
    tool.textContent = step.tool + "  " + JSON.stringify(step.args || {});
    main.appendChild(goal);
    main.appendChild(tool);
    row.appendChild(idx);
    row.appendChild(main);
    card.appendChild(row);
  });
  const actions = document.createElement("div");
  actions.className = "actions";
  const approve = document.createElement("button");
  approve.className = "ok";
  approve.textContent = "批准计划";
  const reject = document.createElement("button");
  reject.className = "danger";
  reject.textContent = "拒绝计划";
  approve.onclick = () => decidePlan(d, "approve", [approve, reject]);
  reject.onclick = () => decidePlan(d, "reject", [approve, reject]);
  actions.appendChild(approve);
  actions.appendChild(reject);
  card.appendChild(actions);
  wrap.appendChild(card);
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
}

async function decidePlan(d, decision, buttons) {
  buttons.forEach((b) => (b.disabled = true));
  const box = $("planMessages");
  const loading = addMsg(box, "assistant", "执行中…");
  try {
    const r = await api("/agent/plan/approve", {
      conversation_id: d.conversation_id,
      decision,
    });
    loading.remove();
    handlePlan(r);
  } catch (e) {
    loading.remove();
    addMsg(box, "error", e.message);
    buttons.forEach((b) => (b.disabled = false));
  }
}

function renderSteps(wrap, steps) {
  const box = document.createElement("div");
  box.className = "sources";
  steps.forEach((s) => {
    const div = document.createElement("div");
    div.className = "source";
    const head = document.createElement("div");
    head.textContent = "第 " + s.step + " 步 · " + (s.tool || "");
    const body = document.createElement("div");
    body.className = "content";
    body.textContent = String(s.result || "").slice(0, 520);
    div.appendChild(head);
    div.appendChild(body);
    box.appendChild(div);
  });
  wrap.appendChild(box);
}

// ---- 评测数据 ----
async function fetchEval() {
  return Promise.all([
    apiGet("/eval/retrieval"),
    apiGet("/eval/generation"),
    apiGet("/eval/agent"),
    apiGet("/eval/router"),
    apiGet("/eval/plan"),
  ]);
}

function fmt(v) {
  return v === undefined || v === null ? "—" : Number(v).toFixed(3);
}

function metricOf(res, key) {
  return res && res.latest && res.latest.metrics ? res.latest.metrics[key] : undefined;
}

function updateMini(data) {
  const [ret, gen, router, plan] = data;
  $("miniMrr").textContent = fmt(metricOf(ret, "mrr"));
  $("miniCorrect").textContent = fmt(metricOf(gen, "correct_rate"));
  $("miniRouterAcc").textContent = fmt(metricOf(router, "accuracy"));
  $("miniPlanSuccess").textContent = fmt(metricOf(plan, "success_rate"));
}

async function loadMiniEval() {
  try {
    updateMini(await fetchEval());
  } catch (e) {
    ["miniMrr", "miniCorrect", "miniRouterAcc", "miniPlanSuccess"].forEach((id) => {
      $(id).textContent = "—";
    });
  }
}

async function loadEval() {
  const box = $("evalCards");
  box.textContent = "加载中…";
  try {
    const data = await fetchEval();
    const [ret, gen, agent, router, plan] = data;
    updateMini(data);
    const cards = [
      metricCard("检索（最近）", ret.latest && ret.latest.metrics, [
        ["recall@1", "recall@1"], ["recall@5", "recall@5"],
        ["MRR", "mrr"], ["NDCG@16", "ndcg@16"],
      ]),
      metricCard("生成（最近）", gen.latest && gen.latest.metrics, [
        ["正确率", "correct_rate"], ["引用率", "citation_rate"],
        ["拒答率", "refusal_rate"],
      ]),
      metricCard("Agent（最近）", agent.latest && agent.latest.metrics, [
        ["工具路由", "tool_route_accuracy"], ["完成率", "completion_rate"],
        ["平均步数", "avg_tool_steps"], ["平均 token", "avg_tokens"],
      ]),
      metricCard("路由（最近）", router.latest && router.latest.metrics, [
        ["准确率", "accuracy"], ["题数", "total"],
      ]),
      metricCard("计划执行（最近）", plan.latest && plan.latest.metrics, [
        ["成功率", "success_rate"], ["平均步数", "avg_steps"],
        ["平均 token", "avg_tokens"], ["平均耗时 ms", "avg_ms"],
      ]),
    ];
    box.innerHTML = "";
    cards.forEach((c) => box.appendChild(c));
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.textContent = "数据来自最近一次评测报告；完整历史见 /eval/* 接口。";
    box.appendChild(hint);
  } catch (e) {
    box.textContent = "加载失败：" + e.message + "（评测数据需要管理员账号）";
  }
}

function metricCard(title, metrics, rows) {
  const el = document.createElement("div");
  el.className = "card";
  const h = document.createElement("h3");
  h.textContent = title;
  el.appendChild(h);
  if (!metrics) {
    const p = document.createElement("div");
    p.className = "hint";
    p.textContent = "暂无数据（先跑一次评测）";
    el.appendChild(p);
    return el;
  }
  rows.forEach(([label, key]) => {
    if (metrics[key] === undefined || metrics[key] === null) return;
    const row = document.createElement("div");
    row.className = "metric";
    const span = document.createElement("span");
    span.textContent = label;
    const b = document.createElement("b");
    b.textContent = String(metrics[key]);
    row.appendChild(span);
    row.appendChild(b);
    el.appendChild(row);
  });
  return el;
}

// ---- 交互绑定 ----
function wire() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.onclick = () => switchTab(btn.dataset.tab);
  });

  document.querySelectorAll(".scenario").forEach((btn) => {
    btn.onclick = () => {
      const s = btn.dataset.scenario;
      hideOverview();
      switchTab(s);
      if (s === "rag") sendRag("冷藏库的温控要求是什么？");
      else if (s === "agent") sendAgent("批号 QS-2024-9527 的库区、批记录和放行状态");
      else sendPlan("查一下批号 QS-2024-9527 的库区和放行状态，再查知识库里冷藏库的温控要求，判断存放是否合规");
    };
  });

  document.querySelectorAll(".chips button").forEach((btn) => {
    btn.onclick = () => {
      const tab = btn.closest(".tab").id;
      const q = btn.dataset.q;
      if (tab === "tab-rag") sendRag(q);
      else if (tab === "tab-agent") sendAgent(q);
      else sendPlan(q);
    };
  });

  $("loginBtn").onclick = () => login().catch((e) => {
    $("userInfo").textContent = "登录失败：" + e.message;
  });

  $("ragForm").onsubmit = (e) => {
    e.preventDefault();
    const q = $("ragInput").value.trim();
    if (!q) return;
    $("ragInput").value = "";
    sendRag(q);
  };

  $("agentForm").onsubmit = (e) => {
    e.preventDefault();
    const q = $("agentInput").value.trim();
    if (!q) return;
    $("agentInput").value = "";
    sendAgent(q);
  };

  $("planForm").onsubmit = (e) => {
    e.preventDefault();
    const q = $("planInput").value.trim();
    if (!q) return;
    $("planInput").value = "";
    sendPlan(q);
  };

  $("newAgent").onclick = () => {
    state.agentConv = null;
    showEmpty($("agentMessages"), "还没有对话", "试试查批号，或发起一次需要审批的放行操作。");
  };
  $("newPlan").onclick = () => {
    state.planConv = null;
    showEmpty($("planMessages"), "还没有计划", "描述一个多步任务，先生成计划，再批准执行。");
  };
  $("loadEval").onclick = () => loadEval();
}

window.addEventListener("DOMContentLoaded", () => {
  wire();
  login().catch((e) => {
    $("userInfo").textContent = "登录失败：" + e.message;
  });
});
