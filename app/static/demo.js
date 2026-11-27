const API = "/api/v1";
const state = { token: "", agentConv: null, planConv: null };

const $ = (id) => document.getElementById(id);

function setDebug(obj) {
  $("debug").textContent = JSON.stringify(obj, null, 2);
}

async function api(path, body) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  const resp = await fetch(API + path, {
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const trace = resp.headers.get("X-Trace-Id");
  if (trace) $("traceId").textContent = "trace: " + trace;
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

async function login() {
  const username = $("username").value.trim();
  const password = $("password").value;
  const data = await api("/auth/login", { username, password });
  state.token = data.token;
  $("userInfo").textContent =
    "已登录 " + data.user.username + " · " + data.user.tenant_id +
    " · " + (data.user.org_id || "无组织") + " L" + (data.user.level || 1);
}

function addMsg(container, role, text, meta) {
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

// ---- 知识库问答 ----
async function sendRag(q) {
  const box = $("ragMessages");
  addMsg(box, "user", q);
  const loading = addMsg(box, "assistant", "检索中…");
  try {
    const d = await api("/rag/chat", { question: q });
    loading.remove();
    const wrap = addMsg(box, "assistant", d.answer || "(空)");
    if (d.sources && d.sources.length) renderSources(wrap, d.sources);
    setDebug(d);
  } catch (e) {
    loading.remove();
    addMsg(box, "error", e.message);
  }
}

// ---- 智能体 + 审批 ----
async function sendAgent(q) {
  const box = $("agentMessages");
  addMsg(box, "user", q);
  const loading = addMsg(box, "assistant", "思考中…");
  try {
    const body = { question: q };
    if (state.agentConv) body.conversation_id = state.agentConv;
    const d = await api("/agent/chat", body);
    loading.remove();
    if (d.conversation_id) state.agentConv = d.conversation_id;
    handleAgent(d);
  } catch (e) {
    loading.remove();
    addMsg(box, "error", e.message);
  }
}

function handleAgent(d) {
  setDebug(d);
  if (d.status === "awaiting_approval") {
    renderApproval(d);
    return;
  }
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
  const box = $("planMessages");
  addMsg(box, "user", q);
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
  if (d.status === "awaiting_plan_approval") {
    renderPlan(d);
    return;
  }
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

// ---- 交互绑定 ----
function wire() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((s) => s.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
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
    $("agentMessages").innerHTML = "";
  };
  $("newPlan").onclick = () => {
    state.planConv = null;
    $("planMessages").innerHTML = "";
  };
}

window.addEventListener("DOMContentLoaded", () => {
  wire();
  login().catch((e) => {
    $("userInfo").textContent = "登录失败：" + e.message;
  });
});
