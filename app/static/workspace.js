const API = "/api/v1";
const state = { token: "", agentConv: null, planConv: null, lastMs: 0, docId: null, docPage: null, docBlobUrl: null, chunkItems: [] };

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
  listDocs().catch(() => {});
}

function authHeaders(json) {
  const headers = {};
  if (state.token) headers.Authorization = "Bearer " + state.token;
  if (json) headers["Content-Type"] = "application/json";
  return headers;
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
    if (s.doc_id) {
      const btn = document.createElement("button");
      btn.className = "ghost";
      btn.textContent = "查看链路";
      btn.style.marginTop = "6px";
      btn.onclick = () => openTrace(s);
      div.appendChild(btn);
    }
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
  hideOverview();
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


// ---- 文档入库 / 溯源 ----
async function listDocs() {
  const box = $("docList");
  box.textContent = "加载中…";
  try {
    const d = await apiGet("/docs");
    renderDocList(d.items || []);
  } catch (e) {
    box.textContent = "加载失败：" + e.message;
  }
}

function renderDocList(items) {
  const box = $("docList");
  box.innerHTML = "";
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "还没有文档，先上传一份（samples/medical 里有 md/docx/xlsx/pdf 示例）。";
    box.appendChild(p);
    return;
  }
  items.forEach((doc) => {
    const btn = document.createElement("button");
    btn.className = "doc-item";
    btn.dataset.id = doc.doc_id;
    if (state.docId === doc.doc_id) btn.classList.add("active");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = doc.file_name;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = doc.status + " · " + doc.chunk_count + " 块";
    btn.appendChild(name);
    btn.appendChild(meta);
    btn.onclick = () => inspectDoc(doc.doc_id);
    box.appendChild(btn);
  });
}

function setUploadStatus(text, cls) {
  const el = $("uploadStatus");
  el.textContent = text;
  el.className = "muted" + (cls ? " " + cls : "");
}

async function uploadDoc() {
  const file = $("fileInput").files[0];
  if (!file) {
    setUploadStatus("请先选择文件", "err");
    return;
  }
  $("uploadBtn").disabled = true;
  setUploadStatus("上传中…");
  try {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch(API + "/docs", {
      method: "POST",
      headers: authHeaders(false),
      body: fd,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.code !== 0) throw new Error(data.message || "上传失败");
    setUploadStatus("已上传，解析中：" + data.data.doc_id);
    await pollDoc(data.data.doc_id);
  } catch (e) {
    setUploadStatus("上传失败：" + e.message, "err");
  } finally {
    $("uploadBtn").disabled = false;
  }
}

async function pollDoc(id) {
  for (let i = 0; i < 120; i++) {
    let d;
    try {
      d = await apiGet("/docs/" + id);
    } catch (e) {
      setUploadStatus("查询状态失败：" + e.message, "err");
      return;
    }
    setUploadStatus(
      "解析状态：" + d.status + " · " + d.chunk_count + " 块" +
      (d.error_msg ? " · " + d.error_msg : ""),
      d.status === "failed" ? "err" : d.status === "ready" ? "ok" : ""
    );
    if (d.status === "ready" || d.status === "failed") {
      await listDocs();
      await inspectDoc(id);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  setUploadStatus("解析超时，请稍后刷新列表", "err");
}

async function inspectDoc(id, opts) {
  opts = opts || {};
  state.docId = id;
  state.docPage = opts.page || null;
  $("inspector").classList.remove("hidden");
  $("inspName").textContent = "加载中…";
  $("inspMeta").textContent = "";
  $("tracePath").textContent = opts.traceLabel || "";
  $("sourcePane").textContent = "加载中…";
  $("parsedPane").textContent = "加载中…";
  $("chunkList").textContent = "加载中…";
  $("chunkDetail").textContent = "";
  document.querySelectorAll(".doc-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === id);
  });
  try {
    const detail = await apiGet("/docs/" + id);
    $("inspName").textContent = detail.file_name;
    $("inspMeta").textContent =
      detail.status + " · " + detail.chunk_count + " 块" +
      (detail.kb_name ? " · " + detail.kb_name : "");
    await Promise.all([
      renderSourcePane(detail, opts.page),
      loadParsed(id),
      loadChunks(id, opts.vectorId),
    ]);
  } catch (e) {
    $("inspName").textContent = "加载失败";
    $("inspMeta").textContent = e.message;
  }
}

async function renderSourcePane(doc, page) {
  const pane = $("sourcePane");
  pane.textContent = "加载中…";
  try {
    const resp = await fetch(API + "/docs/" + doc.doc_id + "/source", {
      headers: authHeaders(false),
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const blob = await resp.blob();
    if (state.docBlobUrl) URL.revokeObjectURL(state.docBlobUrl);
    state.docBlobUrl = URL.createObjectURL(blob);
    const ext = (doc.file_name.split(".").pop() || "").toLowerCase();
    pane.textContent = "";
    pane.style.padding = "0";
    if (ext === "pdf") {
      const frame = document.createElement("iframe");
      frame.className = "pdf-frame";
      frame.src = state.docBlobUrl + (page ? "#page=" + page : "");
      pane.appendChild(frame);
    } else if (ext === "txt" || ext === "md") {
      const pre = document.createElement("pre");
      pre.className = "pane-body";
      pre.style.height = "340px";
      pre.textContent = await blob.text();
      pane.appendChild(pre);
    } else {
      const box = document.createElement("div");
      box.className = "source-fallback";
      box.style.padding = "10px";
      const p = document.createElement("p");
      p.textContent = "浏览器无法直接预览 " + ext + "，可下载后查看。";
      const a = document.createElement("a");
      a.href = state.docBlobUrl;
      a.download = doc.file_name;
      a.textContent = "下载原文件";
      box.appendChild(p);
      box.appendChild(a);
      pane.appendChild(box);
    }
  } catch (e) {
    pane.textContent = "源文件加载失败：" + e.message;
  }
}

async function loadParsed(id) {
  const pre = $("parsedPane");
  pre.textContent = "加载中…";
  try {
    const d = await apiGet("/docs/" + id + "/parsed");
    pre.textContent = d.available ? d.text : (d.hint || "暂无解析文本");
  } catch (e) {
    pre.textContent = "解析文本加载失败：" + e.message;
  }
}

async function loadChunks(id, selected) {
  const box = $("chunkList");
  box.textContent = "加载中…";
  try {
    const d = await apiGet("/docs/" + id + "/chunks");
    state.chunkItems = d.items || [];
    box.innerHTML = "";
    if (!state.chunkItems.length) {
      box.textContent = "暂无切块";
      return;
    }
    state.chunkItems.forEach((item) => {
      const btn = document.createElement("button");
      btn.className = "chunk-item";
      btn.dataset.id = item.id;
      const row = document.createElement("div");
      row.className = "row1";
      const level = document.createElement("span");
      level.className = "level " + (item.metadata.chunk_level || "");
      level.textContent = item.metadata.chunk_level || "chunk";
      const meta = document.createElement("span");
      meta.textContent = "id " + item.id + (item.metadata.page ? " · p" + item.metadata.page : "");
      row.appendChild(level);
      row.appendChild(meta);
      const snip = document.createElement("div");
      snip.className = "snippet";
      snip.textContent = (item.content || "").slice(0, 80);
      btn.appendChild(row);
      btn.appendChild(snip);
      btn.onclick = () => selectChunk(item.id);
      box.appendChild(btn);
    });
    if (selected) selectChunk(selected);
    else if (state.chunkItems[0]) selectChunk(state.chunkItems[0].id);
  } catch (e) {
    box.textContent = "切块加载失败：" + e.message;
  }
}

function selectChunk(id) {
  const item = state.chunkItems.find((x) => x.id === id) ||
    state.chunkItems.find((x) => x.metadata.parent_id === id);
  document.querySelectorAll(".chunk-item").forEach((el) => {
    el.classList.toggle("selected", item && el.dataset.id === item.id);
  });
  const detail = $("chunkDetail");
  if (!item) {
    detail.textContent = "未找到该块";
    return;
  }
  let text =
    "chunk_id: " + item.id +
    "\nlevel: " + item.metadata.chunk_level +
    "\nparent_id: " + (item.metadata.parent_id || "-") +
    "\npage: " + (item.metadata.page || "-") +
    "\n\n" + item.content;
  if (item.parent_content) text += "\n\n--- 父块 ---\n" + item.parent_content;
  detail.textContent = text;
  const el = document.querySelector('.chunk-item[data-id="' + item.id + '"]');
  if (el) el.scrollIntoView({ block: "nearest" });
}

function openTrace(src) {
  if (!src.doc_id) return;
  const chunkId = src.vector_id || src.parent_id || "";
  switchTab("ingest");
  inspectDoc(src.doc_id, {
    vectorId: chunkId,
    page: src.page,
    traceLabel: "答案 [" + src.id + "] → 块 " + chunkId + " → 解析文本 → 源文件",
  });
}

async function openSourceFile() {
  if (!state.docId) return;
  if (!state.docBlobUrl) {
    await renderSourcePane(
      { doc_id: state.docId, file_name: $("inspName").textContent },
      state.docPage
    );
  }
  if (state.docBlobUrl) window.open(state.docBlobUrl, "_blank");
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
  $("uploadBtn").onclick = () => uploadDoc();
  $("refreshDoc").onclick = () => state.docId && inspectDoc(state.docId);
  $("openSource").onclick = () => openSourceFile();
  $("fileInput").onchange = () => {
    const f = $("fileInput").files[0];
    setUploadStatus(f ? "已选择：" + f.name : "请选择文件");
  };
}

window.addEventListener("DOMContentLoaded", () => {
  wire();
  login().catch((e) => {
    $("userInfo").textContent = "登录失败：" + e.message;
  });
});
