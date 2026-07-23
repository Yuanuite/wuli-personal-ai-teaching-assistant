const state = {
  entries: [],
  folders: [],
  openFolders: new Set(),
  knownFolders: new Set(),
  current: null,
  file: null,
  solution: "student",
  solutionDrafts: { student: "", teacher: "" },
  dirty: { student: false, teacher: false },
  problemDirty: false,
  zoom: 100,
  publicationImageIndex: 0,
  publicationImageDrafts: [],
  publicationImageObject: null,
  publicationRedactionStart: null,
  agent: null,
  activeJobs: {},
  jobPollTimer: null,
  jobDismissTimer: null,
  modelSettings: null,
  retrievalReview: {
    cases: [],
    candidates: [],
    validation: null,
    index: 0,
    selected: new Set(),
    dirty: false,
  },
};

function currentJob() {
  return state.activeJobs[state.current?.id] || null;
}

const $ = (id) => document.getElementById(id);
const AGENT_TIER_KEY = "wuli.teacher-console.agent-tier";
const AGENT_MODEL_KEY = "wuli.teacher-console.agent-model";

function selectedAgentTier() {
  const value = $("agent-tier")?.value || "auto";
  return ["auto", "economy", "expert", "custom"].includes(value) ? value : "auto";
}

function selectedAgentModelId() {
  const value = $("agent-model")?.value || "auto";
  return value || "auto";
}

function selectedRegisteredModel(agent = state.agent) {
  const modelId = selectedAgentModelId();
  if (!agent || modelId === "auto") return null;
  return (agent.model_registry?.models || []).find(model => model.id === modelId) || null;
}

function withRoutingTier(body = {}) {
  const tier = selectedAgentTier();
  return {
    ...body,
    routing_tier: tier === "custom" ? "auto" : tier,
    model_id: tier === "custom" ? selectedAgentModelId() : "auto",
  };
}

function agentTierLabel(value) {
  return { auto: "自动", economy: "经济", expert: "深度", custom: "自定义", standard: "标准" }[value] || value || "";
}

function syncAgentTierLabels(agent = state.agent) {
  const provider = selectedAgentProvider(agent);
  const models = provider?.routing_models || {};
  const mapping = { auto: models.standard, economy: models.economy, expert: models.expert };
  for (const [tier, model] of Object.entries(mapping)) {
    const option = document.querySelector(`#agent-tier option[value="${tier}"]`);
    if (option) {
      option.textContent = model ? `${agentTierLabel(tier)} · ${model}` : agentTierLabel(tier);
    }
  }
}

function syncAgentModelVisibility() {
  const custom = selectedAgentTier() === "custom";
  $("agent-model-control")?.classList.toggle("hidden", !custom);
}

function syncAgentModelOptions(agent = state.agent) {
  const select = $("agent-model");
  if (!select) return;
  let previous = select.value || "auto";
  try { previous = localStorage.getItem(AGENT_MODEL_KEY) || previous; } catch (_error) { /* optional preference */ }
  select.replaceChildren(new Option("自动选择可用模型", "auto"));
  const models = agent?.model_registry?.models || [];
  for (const model of models) {
    const label = [
      model.display_name || model.id,
      Array.isArray(model.tags) && model.tags.length ? model.tags.join("/") : "",
      model.available ? "" : "不可用",
    ].filter(Boolean).join(" · ");
    const option = new Option(label, model.id);
    option.disabled = !model.enabled || !model.available;
    option.title = model.reason || model.description || "";
    select.append(option);
  }
  select.value = [...select.options].some(option => option.value === previous && !option.disabled) ? previous : "auto";
  syncAgentModelVisibility();
}

function ensureAgentRoutingReady() {
  if (selectedAgentTier() !== "custom") return true;
  if (selectedAgentModelId() !== "auto") return true;
  toast("自定义模式下请先选择一个具体模型。", true);
  return false;
}

function registryModels(agent = state.agent) {
  return agent?.model_registry?.models || [];
}

function registryModelOptions({ includeAuto = true } = {}) {
  const options = [];
  if (includeAuto) options.push({ id: "auto", label: "自动匹配" });
  for (const model of registryModels()) {
    options.push({ id: model.id, label: model.display_name || model.id });
  }
  return options;
}

const STATE_LABELS = {
  "needs-source-review": "待复核题干",
  "needs-analysis-and-answer": "待生成解析",
  "needs-answer-review": "待复核答案",
  "needs-visualization-build": "待构建可视化",
  "needs-visualization-review": "待复核可视化",
  "ready-to-finish": "可生成交付",
  delivered: "已交付",
};
const STEPS = [
  ["上传", ["needs-source-review", "needs-analysis-and-answer", "needs-answer-review", "needs-visualization-build", "needs-visualization-review", "ready-to-finish", "delivered"]],
  ["题干复核", ["needs-analysis-and-answer", "needs-answer-review", "needs-visualization-build", "needs-visualization-review", "ready-to-finish", "delivered"]],
  ["生成解析", ["needs-answer-review", "needs-visualization-build", "needs-visualization-review", "ready-to-finish", "delivered"]],
  ["答案复核", ["needs-visualization-build", "needs-visualization-review", "ready-to-finish", "delivered"]],
  ["可视化（可选）", []],
  ["生成文件", ["delivered"]],
  ["下载", ["delivered"]],
];

function encodePath(path) {
  return path.split("/").filter(Boolean).map(encodeURIComponent).join("/");
}

function installMarkdownExtensions() {
  if (!window.marked) return;
  window.marked.use({
    extensions: [
      {
        name: "displayMathDollar",
        level: "block",
        start(src) { return src.indexOf("$$"); },
        tokenizer(src) {
          const match = /^\$\$\s*([\s\S]+?)\s*\$\$(?:\n|$)/.exec(src);
          if (match) return { type: "displayMathDollar", raw: match[0], text: match[1] };
        },
        renderer(token) { return `<div data-math="${encodeURIComponent(token.text)}" data-display="1"></div>`; },
      },
      {
        name: "displayMathBracket",
        level: "block",
        start(src) { return src.indexOf("\\["); },
        tokenizer(src) {
          const match = /^\\\[\s*([\s\S]+?)\s*\\\](?:\n|$)/.exec(src);
          if (match) return { type: "displayMathBracket", raw: match[0], text: match[1] };
        },
        renderer(token) { return `<div data-math="${encodeURIComponent(token.text)}" data-display="1"></div>`; },
      },
      {
        name: "inlineMathDollar",
        level: "inline",
        start(src) { return src.indexOf("$"); },
        tokenizer(src) {
          const match = /^\$([^$\n]+?)\$/.exec(src);
          if (match) return { type: "inlineMathDollar", raw: match[0], text: match[1] };
        },
        renderer(token) { return `<span data-math="${encodeURIComponent(token.text)}" data-display="0"></span>`; },
      },
      {
        name: "inlineMathParen",
        level: "inline",
        start(src) { return src.indexOf("\\("); },
        tokenizer(src) {
          const match = /^\\\((.+?)\\\)/.exec(src);
          if (match) return { type: "inlineMathParen", raw: match[0], text: match[1] };
        },
        renderer(token) { return `<span data-math="${encodeURIComponent(token.text)}" data-display="0"></span>`; },
      },
    ],
  });
}

function sanitizeRenderedHtml(html, imageSpecs) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const allowedTags = new Set([
    "A", "ARTICLE", "BLOCKQUOTE", "BR", "CODE", "DEL", "DIV", "EM", "H1", "H2", "H3", "H4", "H5", "H6",
    "HR", "IMG", "LI", "OL", "P", "PRE", "SPAN", "STRONG", "SUB", "SUP", "TABLE", "TBODY", "TD", "TH", "THEAD", "TR", "UL",
  ]);
  const nodes = [...template.content.querySelectorAll("*")];
  for (const node of nodes) {
    if (!allowedTags.has(node.tagName)) {
      node.remove();
      continue;
    }
    for (const attribute of [...node.attributes]) {
      const keep =
        (node.tagName === "A" && ["href", "title"].includes(attribute.name)) ||
        (node.tagName === "IMG" && ["src", "alt", "title"].includes(attribute.name)) ||
        (["data-math", "data-display", "data-image-index", "data-note"].includes(attribute.name)) ||
        (["class", "style"].includes(attribute.name));
      if (!keep) node.removeAttribute(attribute.name);
    }
    if (node.tagName === "A") {
      const href = node.getAttribute("href") || "";
      if (!/^(https?:|mailto:|#|\/)/i.test(href)) node.removeAttribute("href");
      else { node.target = "_blank"; node.rel = "noopener noreferrer"; }
    }
    if (node.tagName === "IMG") rewritePreviewImage(node);
  }
  for (const placeholder of template.content.querySelectorAll("[data-image-index]")) {
    const spec = imageSpecs[Number(placeholder.dataset.imageIndex)];
    if (!spec) { placeholder.remove(); continue; }
    const image = document.createElement("img");
    image.alt = spec.alt;
    image.src = previewAssetUrl(spec.src);
    image.style.width = `${spec.width}%`;
    placeholder.replaceWith(image);
  }
  return template.content;
}

function previewAssetUrl(src) {
  const value = String(src || "").trim();
  if (value.startsWith("/api/entry-file/")) return value;
  if (/^(https?:|data:|javascript:)/i.test(value) || !state.current) return "";
  const normalized = value.replace(/^\.\//, "");
  return `/api/entry-file/${encodeURIComponent(state.current.id)}/${encodePath(normalized)}`;
}

function rewritePreviewImage(image) {
  const src = image.getAttribute("src") || "";
  const rewritten = previewAssetUrl(src);
  if (rewritten) image.src = rewritten;
  else image.removeAttribute("src");
}

function renderMarkdown(markdown, target) {
  const source = String(markdown || "");
  if (!window.marked || !window.katex) {
    target.textContent = source || "暂无内容";
    return;
  }
  const imageSpecs = [];
  const normalized = source.replace(
    /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)\{width=(\d+)%\}/g,
    (_, alt, src, width) => {
      const index = imageSpecs.push({ alt, src, width: Math.min(100, Math.max(10, Number(width))) }) - 1;
      return `<span data-image-index="${index}"></span>`;
    },
  );
  try {
    const html = window.marked.parse(normalized, { gfm: true, breaks: true });
    const fragment = sanitizeRenderedHtml(html, imageSpecs);
    target.replaceChildren(fragment);
    for (const math of target.querySelectorAll("[data-math]")) {
      const formula = decodeURIComponent(math.dataset.math || "");
      try {
        window.katex.render(formula, math, {
          displayMode: math.dataset.display === "1",
          throwOnError: false,
          strict: "ignore",
          trust: false,
          output: "htmlAndMathml",
        });
      } catch {
        math.textContent = formula;
        math.classList.add("math-error");
      }
    }
  } catch (error) {
    target.textContent = `预览失败：${error.message}\n\n${source}`;
  }
}

function debounce(callback, delay = 120) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => callback(...args), delay);
  };
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method === "POST") headers["X-Teacher-Console"] = "1";
  if (options.body && typeof options.body !== "string" && !(options.body instanceof Blob)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({ errors: ["服务返回了无法读取的结果"] }));
  if (!response.ok) throw new Error((data.errors || [response.statusText]).join("；"));
  return data;
}

function toast(message, error = false) {
  const element = $("toast");
  element.textContent = message;
  element.className = `toast${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.add("hidden"), 1700);
}

const RETRIEVAL_CATEGORY_LABELS = {
  knowledge_point: "知识点",
  problem_type: "题型",
  error_type: "错因",
  teacher_phrase: "教师表达",
};

function currentRetrievalCase() {
  return state.retrievalReview.cases[state.retrievalReview.index] || null;
}

function retrievalStatusLabel(status) {
  return { draft: "待复核", approved: "已批准", rejected: "已驳回" }[status] || status;
}

function setRetrievalDirty(value = true) {
  state.retrievalReview.dirty = value;
  $("retrieval-review-hint").textContent = value
    ? "当前修改尚未保存。批准前请勾选所有真正相关的题目。"
    : "批准前请至少勾选一道相关题目。";
}

function renderRetrievalSummary() {
  const validation = state.retrievalReview.validation || {};
  const counts = validation.status_counts || {};
  const approved = counts.approved || 0;
  const total = validation.case_count || state.retrievalReview.cases.length;
  $("retrieval-review-progress").textContent = `${approved} / ${total} 已批准`;
  const summary = $("retrieval-review-summary");
  summary.replaceChildren();
  for (const [label, value, tone] of [
    ["待复核", counts.draft || 0, "draft"],
    ["已批准", approved, "approved"],
    ["已驳回", counts.rejected || 0, "rejected"],
  ]) {
    const item = document.createElement("span");
    item.className = tone;
    const strong = document.createElement("strong");
    strong.textContent = String(value);
    item.append(strong, document.createTextNode(label));
    summary.append(item);
  }
}

function renderRetrievalCaseList() {
  const list = $("retrieval-case-list");
  list.replaceChildren();
  state.retrievalReview.cases.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `retrieval-case-item ${item.review_status || "draft"}${index === state.retrievalReview.index ? " active" : ""}`;
    const number = document.createElement("span");
    number.className = "retrieval-case-number";
    number.textContent = String(index + 1).padStart(2, "0");
    const copy = document.createElement("span");
    const category = document.createElement("small");
    category.textContent = `${RETRIEVAL_CATEGORY_LABELS[item.category] || item.category} · ${retrievalStatusLabel(item.review_status)}`;
    const query = document.createElement("strong");
    query.textContent = item.query;
    copy.append(category, query);
    button.append(number, copy);
    button.addEventListener("click", () => selectRetrievalCase(index));
    list.append(button);
  });
}

function candidateSearchText(candidate) {
  return [candidate.title, candidate.problem_excerpt, ...(candidate.knowledge_points || []), ...(candidate.error_types || [])]
    .join(" ").toLowerCase();
}

function renderRetrievalCandidates() {
  const grid = $("retrieval-candidate-grid");
  const selected = state.retrievalReview.selected;
  const filter = $("retrieval-candidate-filter").value.trim().toLowerCase();
  const candidates = [...state.retrievalReview.candidates]
    .filter(candidate => !filter || candidateSearchText(candidate).includes(filter))
    .sort((left, right) => Number(selected.has(right.id)) - Number(selected.has(left.id)) || left.title.localeCompare(right.title, "zh-CN"));
  grid.replaceChildren();
  for (const candidate of candidates) {
    const card = document.createElement("label");
    card.className = `retrieval-candidate-card${selected.has(candidate.id) ? " selected" : ""}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selected.has(candidate.id);
    checkbox.value = candidate.id;
    checkbox.setAttribute("aria-label", `将${candidate.title}标为相关题目`);
    const visual = document.createElement("span");
    visual.className = "retrieval-candidate-visual";
    if (candidate.thumbnail) {
      const image = document.createElement("img");
      image.src = candidate.thumbnail;
      image.alt = `${candidate.title}题图`;
      image.loading = "lazy";
      visual.append(image);
    } else {
      visual.textContent = "物";
    }
    const body = document.createElement("span");
    body.className = "retrieval-candidate-body";
    const title = document.createElement("strong");
    title.textContent = candidate.title;
    const tags = document.createElement("span");
    tags.className = "retrieval-candidate-tags";
    for (const tag of [...(candidate.knowledge_points || []).slice(0, 3), ...(candidate.error_types || []).slice(0, 2)]) {
      const chip = document.createElement("small");
      chip.textContent = tag;
      tags.append(chip);
    }
    const excerpt = document.createElement("span");
    excerpt.className = "retrieval-candidate-excerpt";
    excerpt.textContent = candidate.problem_excerpt || "暂无可读题干摘要";
    body.append(title, tags, excerpt);
    card.append(checkbox, visual, body);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selected.add(candidate.id);
      else selected.delete(candidate.id);
      setRetrievalDirty();
      renderRetrievalCandidates();
      renderRetrievalSelectionCount();
    });
    grid.append(card);
  }
  if (!candidates.length) {
    const empty = document.createElement("p");
    empty.className = "muted retrieval-candidate-empty";
    empty.textContent = "没有符合筛选条件的题目。";
    grid.append(empty);
  }
}

function renderRetrievalSelectionCount() {
  $("retrieval-selection-count").textContent = `已选择 ${state.retrievalReview.selected.size} 道`;
}

function renderRetrievalCase() {
  const item = currentRetrievalCase();
  const empty = !item;
  $("retrieval-review-empty").classList.toggle("hidden", !empty);
  $("retrieval-review-editor").classList.toggle("hidden", empty);
  for (const id of ["retrieval-prev", "retrieval-next", "retrieval-save-draft", "retrieval-reject", "retrieval-approve"]) {
    $(id).disabled = empty;
  }
  if (empty) return;
  $("retrieval-query").value = item.query || "";
  $("retrieval-category").value = item.category || "knowledge_point";
  $("retrieval-candidate-filter").value = "";
  state.retrievalReview.selected = new Set(item.relevant_entry_ids || []);
  setRetrievalDirty(false);
  $("retrieval-prev").disabled = state.retrievalReview.index <= 0;
  $("retrieval-next").disabled = state.retrievalReview.index >= state.retrievalReview.cases.length - 1;
  renderRetrievalCandidates();
  renderRetrievalSelectionCount();
}

function selectRetrievalCase(index) {
  if (index === state.retrievalReview.index) return;
  if (state.retrievalReview.dirty && !window.confirm("当前修改尚未保存，仍要切换吗？")) return;
  state.retrievalReview.index = Math.max(0, Math.min(index, state.retrievalReview.cases.length - 1));
  renderRetrievalCaseList();
  renderRetrievalCase();
}

async function openRetrievalReview() {
  $("retrieval-review-backdrop").classList.remove("hidden");
  $("retrieval-review-progress").textContent = "正在载入…";
  try {
    const snapshot = await api("/api/retrieval-review");
    state.retrievalReview.cases = snapshot.cases || [];
    state.retrievalReview.candidates = snapshot.candidates || [];
    state.retrievalReview.validation = snapshot.validation || {};
    const firstDraft = state.retrievalReview.cases.findIndex(item => item.review_status === "draft");
    state.retrievalReview.index = firstDraft >= 0 ? firstDraft : 0;
    renderRetrievalSummary();
    renderRetrievalCaseList();
    renderRetrievalCase();
  } catch (error) {
    toast(`读取检索评测失败：${error.message}`, true);
    $("retrieval-review-backdrop").classList.add("hidden");
  }
}

function closeRetrievalReview() {
  if (state.retrievalReview.dirty && !window.confirm("当前修改尚未保存，仍要关闭吗？")) return;
  $("retrieval-review-backdrop").classList.add("hidden");
}

async function saveRetrievalCase(reviewStatus, advance = false) {
  const item = currentRetrievalCase();
  if (!item) return;
  const query = $("retrieval-query").value.trim();
  if (!query) { toast("检索语句不能为空", true); return; }
  if (reviewStatus === "approved" && !state.retrievalReview.selected.size) {
    toast("批准前请至少勾选一道相关题目", true);
    return;
  }
  const button = reviewStatus === "approved" ? $("retrieval-approve") : (reviewStatus === "rejected" ? $("retrieval-reject") : $("retrieval-save-draft"));
  button.disabled = true;
  try {
    const snapshot = await api("/api/retrieval-review/save", {
      method: "POST",
      body: {
        id: item.id,
        query,
        category: $("retrieval-category").value,
        relevant_entry_ids: [...state.retrievalReview.selected],
        review_status: reviewStatus,
      },
    });
    state.retrievalReview.cases = snapshot.cases || [];
    state.retrievalReview.candidates = snapshot.candidates || [];
    state.retrievalReview.validation = snapshot.validation || {};
    const currentIndex = state.retrievalReview.cases.findIndex(candidate => candidate.id === item.id);
    state.retrievalReview.index = Math.max(0, currentIndex);
    if (advance) {
      const nextDraft = state.retrievalReview.cases.findIndex((candidate, index) => index > state.retrievalReview.index && candidate.review_status === "draft");
      if (nextDraft >= 0) state.retrievalReview.index = nextDraft;
      else if (state.retrievalReview.index < state.retrievalReview.cases.length - 1) state.retrievalReview.index += 1;
    }
    renderRetrievalSummary();
    renderRetrievalCaseList();
    renderRetrievalCase();
    toast(reviewStatus === "approved" ? "已批准并保存" : (reviewStatus === "rejected" ? "已驳回此条" : "草稿已保存"));
  } catch (error) {
    toast(`保存失败：${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

function hasDynamicVisualization() {
  return Boolean(state.current?.visualization?.has_model);
}

function sourceIsConfirmed() {
  if (!state.current) return false;
  return state.current.source_review?.status === "passed" || state.current.state !== "needs-source-review";
}

function hasGeneratedAnswers() {
  if (!state.current) return false;
  return Boolean(
    String(state.current.student_solution || "").trim()
    && String(state.current.teacher_solution || "").trim(),
  );
}

function answerIsConfirmed() {
  if (!state.current) return false;
  return ["needs-visualization-build", "needs-visualization-review", "ready-to-finish", "delivered"].includes(state.current.state);
}

function prerequisiteMessage(tab) {
  if (!state.current || tab === "source") return "";
  if (!sourceIsConfirmed()) return "请先在“题干复核”确认题干无误，才能进入后续步骤。";
  if (tab === "answer") return "";
  if (!hasGeneratedAnswers() || state.current.state === "needs-analysis-and-answer") {
    return "后台还没有完整解析，请先在“解析复核”运行解析流程。";
  }
  if (!answerIsConfirmed()) return "请先在“解析复核”确认答案正确，才能进入后续步骤。";
  if (tab === "visualization") return "";
  if (tab === "delivery" && hasDynamicVisualization()
      && !["ready-to-finish", "delivered"].includes(state.current.state)) {
    return "请先在“可视化复核”确认完整运动过程，才能生成交付文件。";
  }
  if (tab === "delivery" && !["ready-to-finish", "delivered"].includes(state.current.state)) {
    return "请先完成当前题目的教师复核，才能生成交付文件。";
  }
  return "";
}

function requirePrerequisite(tab) {
  const message = prerequisiteMessage(tab);
  if (!message) return true;
  toast(message, true);
  return false;
}

function activateTab(tab, { force = false } = {}) {
  if (!state.current) return false;
  if (!force && !requirePrerequisite(tab)) return false;
  const button = document.querySelector(`.tab[data-tab="${tab}"]`);
  const panel = $(`tab-${tab}`);
  if (!button || !panel || button.classList.contains("hidden")) return false;
  document.querySelectorAll(".tab").forEach(item => item.classList.toggle("active", item === button));
  document.querySelectorAll(".tab-panel").forEach(item => item.classList.add("hidden"));
  panel.classList.remove("hidden");
  return true;
}

function syncTabAvailability() {
  if (!state.current) return;
  const visualizationTab = document.querySelector('.tab[data-tab="visualization"]');
  const deliveryTab = document.querySelector('.tab[data-tab="delivery"]');
  visualizationTab.classList.remove("hidden");
  visualizationTab.setAttribute("aria-hidden", "false");
  deliveryTab.textContent = "4 交付下载";
  $("approve-answer").textContent = "答案正确，进入可视化（可选）";
}

function enforceActiveTabPrerequisite() {
  const active = document.querySelector(".tab.active")?.dataset.tab || "source";
  if (!prerequisiteMessage(active)) return;
  const fallback = state.current.state === "needs-source-review" ? "source" : "answer";
  activateTab(fallback, { force: true });
}

function normalizeAgentHealth(data) {
  if (data.agent && typeof data.agent === "object") {
    return {
      available: Boolean(data.agent.available),
      selected: String(data.agent.selected || "").trim(),
      providers: Array.isArray(data.agent.providers) ? data.agent.providers : [],
      mode: String(data.agent.mode || "").trim(),
      model_registry: data.agent.model_registry && typeof data.agent.model_registry === "object" ? data.agent.model_registry : { models: [] },
    };
  }
  return {
    available: Boolean(data.agent_configured),
    selected: data.agent_configured ? "本地 Agent" : "",
    providers: [],
    mode: "legacy",
    model_registry: { models: [] },
  };
}

function selectedAgentProvider(agent = state.agent) {
  if (!agent) return null;
  const selected = String(agent.selected || "").toLowerCase();
  const exact = agent.providers.find(provider => String(provider.name || "").toLowerCase() === selected);
  if (exact) return exact;
  return agent.available ? (agent.providers.find(provider => provider.available) || null) : null;
}

function selectedAgentLabel(agent = state.agent) {
  if (!agent) return "Agent 状态未知";
  const provider = selectedAgentProvider(agent);
  const name = agent.selected || provider?.name || (agent.available ? "自动选择" : "Agent 不可用");
  const version = provider?.version ? ` ${provider.version}` : "";
  return `${name}${version}`;
}

function selectedAgentLocality(agent = state.agent) {
  const locality = selectedRegisteredModel(agent)?.data_locality || selectedAgentProvider(agent)?.data_locality;
  return {
    local: "数据留在本机",
    remote: "数据发送到远程 provider",
    "provider-dependent": "数据位置取决于 provider",
    "declared-by-adapter": "数据位置由 adapter 声明",
  }[locality] || "数据位置未声明";
}

function selectedAgentModelInfo(agent = state.agent) {
  const selectedModel = selectedRegisteredModel(agent);
  if (selectedModel) {
    const tags = Array.isArray(selectedModel.tags) && selectedModel.tags.length ? `（${selectedModel.tags.join(" / ")}）` : "";
    return `${selectedModel.display_name || selectedModel.id}${tags}`;
  }
  const provider = selectedAgentProvider(agent);
  if (!provider || provider.name !== "openai-compatible") return "";
  const models = provider.routing_models || {};
  const parts = [];
  if (models.standard) parts.push(`标准 ${models.standard}`);
  if (models.economy) parts.push(`经济 ${models.economy}`);
  if (models.expert) parts.push(`深度 ${models.expert}`);
  const missing = [];
  if (!models.standard) missing.push("标准模型");
  if (!models.economy) missing.push("经济模型");
  if (!models.expert) missing.push("深度模型");
  const configured = parts.length ? parts.join(" / ") : "未配置模型";
  return `${configured}${missing.length ? `；缺 ${missing.join("、")}` : ""}`;
}

function selectedAgentEnvInfo(agent = state.agent) {
  const required = selectedAgentProvider(agent)?.required_env || [];
  return required.length ? `接口变量 ${required.join("、")}` : "";
}

function unavailableAgentReason(agent = state.agent) {
  if (!agent) return "尚未取得 Agent 状态";
  const provider = selectedAgentProvider(agent);
  if (provider?.reason) return provider.reason;
  const reasons = agent.providers.map(item => item.reason).filter(Boolean);
  return reasons.length ? [...new Set(reasons)].join("；") : "未检测到可调用的本地 Agent";
}

function renderAgentMessage() {
  if (!state.current) return;
  if (!state.agent) {
    $("agent-message").textContent = state.current.agent_configured
      ? "本地 Agent 已连接，点击后会在后台处理。"
      : "尚未配置 Agent；任务会保留在页面中，待配置后再处理。";
    return;
  }
  if (state.agent.available) {
    const mode = state.agent.mode && state.agent.mode !== "legacy" ? ` · ${state.agent.mode} 模式` : "";
    const modelInfo = selectedAgentModelInfo();
    const envInfo = selectedAgentEnvInfo();
    const selectedModel = selectedRegisteredModel();
    const support = selectedModel?.description ? `；${selectedModel.description}` : "";
    $("agent-message").textContent = `当前 Agent：${selectedAgentLabel()}${mode}；${selectedAgentLocality()}${modelInfo ? `；模型：${modelInfo}` : ""}${envInfo ? `；${envInfo}` : ""}${support}。任务提交后可继续查看页面，完成时会自动刷新。`;
  } else {
    $("agent-message").textContent = `Agent 暂不可用：${unavailableAgentReason()}。`;
  }
}

async function health() {
  const element = document.querySelector(".health");
  try {
    const data = await api("/api/health");
    state.agent = normalizeAgentHealth(data);
    syncAgentModelOptions();
    syncAgentTierLabels();
    element.classList.add("online");
    element.classList.toggle("agent-unavailable", !state.agent.available);
    $("health-text").textContent = "本地服务已连接";
    const modelInfo = selectedAgentModelInfo();
    const envInfo = selectedAgentEnvInfo();
    const detail = state.agent.available
      ? `Agent：${selectedAgentLabel()}${state.agent.mode && state.agent.mode !== "legacy" ? ` · ${state.agent.mode}` : ""} · ${selectedAgentLocality()}${modelInfo ? ` · 模型：${modelInfo}` : ""}${envInfo ? ` · ${envInfo}` : ""}`
      : `Agent 不可用：${unavailableAgentReason()}`;
    $("agent-health-detail").textContent = detail;
    $("agent-health-detail").title = detail;
    renderAgentMessage();
  } catch {
    state.agent = null;
    syncAgentModelOptions();
    syncAgentTierLabels();
    element.classList.remove("online", "agent-unavailable");
    $("health-text").textContent = "本地服务未连接";
    $("agent-health-detail").textContent = "请确认教师工作台服务正在运行";
  }
}

async function probeAgent() {
  const button = $("probe-agent");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "检测中…";
  try {
    const provider = String(state.agent?.selected || "");
    const result = await api("/api/agent/providers/probe", {
      method: "POST",
      body: { provider: provider === "auto" ? "" : provider, model_id: selectedAgentModelId(), timeout_seconds: 120 },
    });
    state.agent = normalizeAgentHealth({ agent: result });
    await health();
    if (result.live_probe?.status === "passed") {
      toast(`${result.live_probe.provider} 真实连通检测通过`);
    } else {
      toast(`${result.live_probe?.provider || "Agent"} 连通失败，已暂时降级：${result.live_probe?.reason || "未知原因"}`, true);
    }
  } catch (error) {
    toast(`Agent 检测失败：${error.message}`, true);
    await health();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function fillSelect(select, value, options) {
  select.replaceChildren();
  for (const option of options) {
    select.append(new Option(option.label, option.id));
  }
  select.value = options.some(option => option.id === value) ? value : (options[0]?.id || "auto");
}

function modelSettingTemplate() {
  return {
    id: `model-${Date.now()}`,
    display_name: "新模型",
    provider: "openai-compatible",
    base_url: "http://127.0.0.1:8000/v1",
    model: "",
    api_key_env: "TEACHER_CONSOLE_AGENT_API_KEY",
    api_key: "",
    clear_api_key: false,
    model_tier: "selected",
    tags: [],
    capabilities: ["analysis.generate", "answer.revise", "visualization.model"],
    recommended_for: [],
    description: "",
    enabled: true,
    remote: false,
  };
}

function codexVisualizationPreset() {
  return {
    id: "codex-visualization",
    display_name: "Codex 可视化 Agent",
    provider: "codex",
    model_tier: "expert",
    tags: ["Codex", "可视化", "Agent"],
    capabilities: ["visualization.model"],
    recommended_for: ["visualization.model"],
    description: "调用本机 Codex CLI 生成或修正交互可视化；适合复杂运动过程和 HTML 仿真建模。",
    enabled: true,
    remote: false,
  };
}

function applyCodexVisualizationPreset() {
  state.modelSettings ||= { schema_version: 1, defaults: {}, models: [] };
  state.modelSettings.defaults ||= {};
  state.modelSettings.models ||= [];
  const preset = codexVisualizationPreset();
  const index = state.modelSettings.models.findIndex(model => model.id === preset.id);
  if (index >= 0) state.modelSettings.models[index] = { ...state.modelSettings.models[index], ...preset };
  else state.modelSettings.models.push(preset);
  state.modelSettings.defaults["visualization.model"] = preset.id;
  renderSettingsDefaults();
  renderModelEditors();
  toast("已将可视化建模默认设置为 Codex，保存后生效");
}

function modelProbeLabel(model) {
  if (model.probe_passed) return "已测试";
  if (model.probe_status === "failed") return "测试失败";
  return "未测试";
}

function modelProbeTitle(model) {
  const parts = [modelProbeLabel(model)];
  if (model.probe_checked_at) parts.push(model.probe_checked_at);
  if (model.probe_message) parts.push(model.probe_message);
  return parts.join(" · ");
}

function settingField(labelText, input) {
  const label = document.createElement("label");
  label.append(labelText, input);
  return label;
}

function textInput(value, placeholder = "") {
  const input = document.createElement("input");
  input.value = value || "";
  input.placeholder = placeholder;
  return input;
}

function selectInput(value, options) {
  const select = document.createElement("select");
  for (const [id, label] of options) select.append(new Option(label, id));
  select.value = value || options[0]?.[0] || "";
  return select;
}

function renderModelEditors() {
  const list = $("agent-model-editor-list");
  list.replaceChildren();
  const settings = state.modelSettings || { models: [] };
  for (const [index, model] of (settings.models || []).entries()) {
    const card = document.createElement("article");
    card.className = "model-editor";
    const head = document.createElement("div");
    head.className = "model-editor-head";
    const title = document.createElement("strong");
    title.textContent = model.display_name || model.id || "未命名模型";
    const status = document.createElement("span");
    status.className = `model-probe-status ${model.probe_passed ? "passed" : model.probe_status === "failed" ? "failed" : "untested"}`;
    status.textContent = modelProbeLabel(model);
    status.title = modelProbeTitle(model);
    const headTitle = document.createElement("div");
    headTitle.className = "model-editor-title";
    headTitle.append(title, status);
    const test = document.createElement("button");
    test.type = "button";
    test.className = "secondary model-test";
    test.textContent = "测试";
    test.addEventListener("click", () => {
      testAgentModel(index, test).catch(error => toast(`模型测试失败：${error.message}`, true));
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "model-editor-remove";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      settings.models.splice(index, 1);
      renderModelEditors();
      renderSettingsDefaults();
    });
    const actions = document.createElement("div");
    actions.className = "model-editor-actions";
    actions.append(test, remove);
    head.append(headTitle, actions);
    card.classList.toggle("model-untested", !model.probe_passed);

    const grid = document.createElement("div");
    grid.className = "model-editor-grid";
    const id = textInput(model.id, "如 gpt-4.1");
    const display = textInput(model.display_name, "教师看到的名称");
    const provider = selectInput(model.provider, [["openai-compatible", "OpenAI-compatible API"], ["codex", "Codex CLI"], ["claude", "Claude Code"], ["adapter", "JSON Adapter"]]);
    const base = textInput(model.base_url, "https://.../v1 或 http://127.0.0.1:.../v1");
    const apiModel = textInput(model.model, "真实模型名");
    const keyEnv = textInput(model.api_key_env || "TEACHER_CONSOLE_AGENT_API_KEY", "环境变量名，不是 key 本身");
    const apiKey = textInput("", model.api_key_saved || model.api_key_configured ? "已保存；留空表示继续使用原 Key" : "粘贴 API Key，本地保存");
    apiKey.type = "password";
    apiKey.autocomplete = "off";
    const clearApiKey = document.createElement("input");
    clearApiKey.type = "checkbox";
    clearApiKey.checked = false;
    const tier = selectInput(model.model_tier || "selected", [["economy", "经济"], ["standard", "标准"], ["expert", "深度"], ["selected", "自定义"]]);
    const tags = textInput((model.tags || []).join(", "), "如 经济, 快速, GPT");
    const capabilities = textInput((model.capabilities || []).join(", "), "analysis.generate, answer.revise, visualization.model");
    const recommended = textInput((model.recommended_for || []).join(", "), "推荐任务，可留空");
    const description = document.createElement("textarea");
    description.value = model.description || "";
    description.placeholder = "什么时候适合用这个模型";
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = model.enabled !== false;
    const remote = document.createElement("input");
    remote.type = "checkbox";
    remote.checked = Boolean(model.remote);

    grid.append(
      settingField("ID", id),
      settingField("显示名称", display),
      settingField("Provider", provider),
      settingField("模式标签", tier),
      settingField("API 地址", base),
      settingField("真实模型名", apiModel),
      settingField("API Key（本地保存）", apiKey),
      settingField("清除已保存 Key", clearApiKey),
      settingField("Key 环境变量", keyEnv),
      settingField("标签（逗号分隔）", tags),
      settingField("能力（逗号分隔）", capabilities),
      settingField("推荐任务（逗号分隔）", recommended),
      settingField("启用", enabled),
      settingField("远程模型", remote),
      settingField("说明", description),
    );
    grid.lastElementChild.classList.add("wide-field");
    card.append(head, grid);
    list.append(card);

    const sync = () => {
      model.id = id.value.trim();
      model.display_name = display.value.trim();
      model.provider = provider.value;
      model.base_url = base.value.trim();
      model.model = apiModel.value.trim();
      model.api_key = apiKey.value.trim();
      model.clear_api_key = clearApiKey.checked;
      model.api_key_env = keyEnv.value.trim();
      model.model_tier = tier.value;
      model.tags = tags.value.split(",").map(item => item.trim()).filter(Boolean);
      model.capabilities = capabilities.value.split(",").map(item => item.trim()).filter(Boolean);
      model.recommended_for = recommended.value.split(",").map(item => item.trim()).filter(Boolean);
      model.description = description.value.trim();
      model.enabled = enabled.checked;
      model.remote = remote.checked;
      title.textContent = model.display_name || model.id || "未命名模型";
      delete model.probe_passed;
      model.probe_status = "untested";
      model.probe_message = "配置已修改，请重新测试";
      status.className = "model-probe-status untested";
      status.textContent = modelProbeLabel(model);
      status.title = modelProbeTitle(model);
      card.classList.add("model-untested");
      renderSettingsDefaults();
    };
    for (const input of [id, display, provider, base, apiModel, apiKey, clearApiKey, keyEnv, tier, tags, capabilities, recommended, description, enabled, remote]) {
      input.addEventListener("input", sync);
      input.addEventListener("change", sync);
    }
  }
}

function currentModelSettingsPayload() {
  const settings = state.modelSettings || { schema_version: 1, defaults: {}, models: [] };
  settings.defaults = {
    economy: $("settings-default-economy")?.value || settings.defaults?.economy || "auto",
    expert: $("settings-default-expert")?.value || settings.defaults?.expert || "auto",
    "analysis.generate": $("settings-default-analysis")?.value || settings.defaults?.["analysis.generate"] || "auto",
    "answer.revise": $("settings-default-revision")?.value || settings.defaults?.["answer.revise"] || "auto",
    "visualization.model": $("settings-default-visualization")?.value || settings.defaults?.["visualization.model"] || "auto",
  };
  return settings;
}

function mergeReturnedModelSettings(returnedSettings, submittedSettings) {
  const merged = returnedSettings && typeof returnedSettings === "object" ? returnedSettings : submittedSettings;
  merged.defaults ||= submittedSettings.defaults || {};
  merged.models = Array.isArray(merged.models) ? merged.models : [];
  const submittedById = new Map((submittedSettings.models || []).map(model => [model.id, model]));
  merged.models = merged.models.map(model => {
    const submitted = submittedById.get(model.id);
    if (!submitted) return model;
    const next = { ...model };
    for (const key of [
      "base_url", "model", "api_key_env", "timeout_seconds", "provider", "model_tier",
      "tags", "capabilities", "recommended_for", "description", "enabled", "remote",
    ]) {
      if ((next[key] === undefined || next[key] === "" || (Array.isArray(next[key]) && !next[key].length)) && submitted[key] !== undefined) {
        next[key] = submitted[key];
      }
    }
    // 明文 API Key 不回灌到页面；如果这次填写了 key，只同步“已保存”状态。
    if (submitted.api_key) {
      next.api_key = "";
      next.clear_api_key = false;
      next.api_key_saved = true;
      next.api_key_configured = true;
    }
    return next;
  });
  return merged;
}

async function testAgentModel(index, button) {
  const settings = currentModelSettingsPayload();
  const model = settings.models?.[index];
  if (!model?.id) {
    toast("请先填写模型 ID。", true);
    return;
  }
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "测试中…";
  try {
    const result = await api("/api/agent/model-registry/test", {
      method: "POST",
      body: { model_id: model.id, settings, timeout_seconds: 120 },
    });
    state.modelSettings = mergeReturnedModelSettings(result.settings, settings);
    renderSettingsDefaults();
    renderModelEditors();
    await health();
    if (result.status === "passed") toast(`${model.display_name || model.id} 测试通过`);
    else toast(`${model.display_name || model.id} 测试失败，默认不会调用`, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function renderSettingsDefaults() {
  const settings = state.modelSettings || { defaults: {}, models: [] };
  const models = (settings.models || []).map(model => ({ id: model.id, label: model.display_name || model.id })).filter(item => item.id);
  const options = [{ id: "auto", label: "自动匹配" }, ...models];
  const defaults = settings.defaults || {};
  fillSelect($("settings-default-economy"), defaults.economy || "auto", options);
  fillSelect($("settings-default-expert"), defaults.expert || "auto", options);
  fillSelect($("settings-default-analysis"), defaults["analysis.generate"] || "auto", options);
  fillSelect($("settings-default-revision"), defaults["answer.revise"] || "auto", options);
  fillSelect($("settings-default-visualization"), defaults["visualization.model"] || "auto", options);
}

async function openAgentSettings() {
  state.modelSettings = await api("/api/agent/model-registry");
  state.modelSettings.defaults ||= {};
  state.modelSettings.models ||= [];
  $("agent-settings-path").textContent = `本地配置：${state.modelSettings.path || "student-error-library/config/model-registry.json"}`;
  renderSettingsDefaults();
  renderModelEditors();
  $("agent-settings-backdrop").classList.remove("hidden");
}

function closeAgentSettings() {
  $("agent-settings-backdrop").classList.add("hidden");
}

async function saveAgentSettings() {
  const settings = currentModelSettingsPayload();
  const saved = await api("/api/agent/model-registry", { method: "POST", body: settings });
  toast("模型设置已保存");
  closeAgentSettings();
  await health();
  state.agent.model_registry = saved;
  syncAgentModelOptions();
}

async function loadEntries(selectId = null) {
  await refreshCatalog();
  if (selectId) await selectEntry(selectId);
  else if (state.current) await selectEntry(state.current.id);
}

async function refreshCatalog() {
  const data = await api("/api/entries");
  state.entries = Array.isArray(data.entries) ? data.entries : [];
  state.folders = Array.isArray(data.folders) && data.folders.length
    ? data.folders
    : groupEntriesByFolder(state.entries);
  renderEntries();
  return data;
}

function groupEntriesByFolder(entries) {
  const groups = new Map();
  for (const entry of entries) {
    const name = entry.library_folder || "未分类";
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(entry);
  }
  return [...groups].map(([name, folderEntries]) => ({ name, entries: folderEntries }));
}

function renderEntries() {
  const list = $("entry-list");
  list.replaceChildren();
  if (!state.folders.length) {
    const empty = document.createElement("p");
    empty.className = "folder-empty muted";
    empty.textContent = "还没有题目，先上传一份图片或 PDF。";
    list.append(empty);
    return;
  }

  for (const folder of state.folders) {
    const name = String(folder.name || "未分类");
    const isNew = !state.knownFolders.has(name);
    if (isNew) state.openFolders.add(name);
    state.knownFolders.add(name);

    const group = document.createElement("details");
    group.className = "folder-group";
    group.dataset.folder = name;
    group.open = state.openFolders.has(name);

    const summary = document.createElement("summary");
    const titleWrap = document.createElement("span");
    titleWrap.className = "folder-title";
    const folderMark = document.createElement("span"); folderMark.className = "folder-mark"; folderMark.textContent = "▸";
    const title = document.createElement("strong"); title.textContent = name;
    const count = document.createElement("small"); count.textContent = `${(folder.entries || []).length} 题`;
    titleWrap.append(folderMark, title, count);
    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "folder-rename";
    rename.textContent = "改名";
    rename.setAttribute("aria-label", `修改文件夹 ${name} 的名称`);
    rename.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      renameFolder(name);
    });
    summary.append(titleWrap, rename);

    const entries = document.createElement("div");
    entries.className = "folder-entry-list";
    for (const entry of folder.entries || []) entries.append(createEntryCard(entry));
    group.append(summary, entries);
    group.addEventListener("toggle", () => {
      if (group.open) state.openFolders.add(name);
      else state.openFolders.delete(name);
    });
    list.append(group);
  }
}

function createEntryCard(entry) {
  const button = document.createElement("button");
  button.className = `entry-card${state.current?.id === entry.id ? " active" : ""}`;
  const thumb = entry.thumbnail ? document.createElement("img") : document.createElement("div");
  if (entry.thumbnail) { thumb.src = entry.thumbnail; thumb.alt = ""; }
  else thumb.className = "thumb-placeholder";
  const text = document.createElement("div");
  const title = document.createElement("strong"); title.textContent = entry.title;
  const meta = document.createElement("small"); meta.textContent = STATE_LABELS[entry.state] || entry.state;
  text.append(title, meta); button.append(thumb, text);
  button.addEventListener("click", () => selectEntry(entry.id));
  return button;
}

async function renameFolder(oldName) {
  const proposed = window.prompt("修改文件夹名称（会同步到本地文件夹）", oldName);
  if (proposed === null) return;
  const newName = proposed.trim();
  if (!newName || newName === oldName) return;
  try {
    await api("/api/folders/rename", { method: "POST", body: { old_name: oldName, new_name: newName } });
    if (state.openFolders.has(oldName)) state.openFolders.add(newName);
    state.openFolders.delete(oldName);
    state.knownFolders.delete(oldName);
    toast(`文件夹已改名为“${newName}”，并同步到本地`);
    await refreshCatalog();
  } catch (error) {
    toast(error.message, true);
  }
}

async function selectEntry(id) {
  if (state.problemDirty || state.dirty.student || state.dirty.teacher) {
    const proceed = window.confirm("当前题干或解析有未保存修改。放弃修改并切换题目吗？");
    if (!proceed) return;
  }
  state.current = await api(`/api/entries/${encodeURIComponent(id)}`);
  const persistedJob = state.current.agent_job;
  clearTimeout(state.jobDismissTimer);
  if (persistedJob?.id) {
    const jobForEntry = {
      ...persistedJob,
      action: persistedJob.kind,
      entryId: persistedJob.entry_id || state.current.id,
      pollErrors: 0,
    };
    state.activeJobs[state.current.id] = jobForEntry;
    if (["queued", "running"].includes(persistedJob.status)) {
      clearTimeout(state.jobPollTimer);
      state.jobPollTimer = setTimeout(() => pollActiveJob(String(persistedJob.id), state.current.id), 450);
    }
  }
  state.solutionDrafts = {
    student: state.current.student_solution || "",
    teacher: state.current.teacher_solution || "",
  };
  state.dirty = { student: false, teacher: false };
  state.problemDirty = false;
  state.publicationImageIndex = 0;
  state.publicationImageDrafts = (state.current.publication_images?.sources || []).map(source => ({
    ...source,
    crop: [...(source.crop || [0, 0, 1, 1])],
    redactions: (source.redactions || []).map(box => [...box]),
  }));
  $("empty-state").classList.add("hidden");
  $("entry-view").classList.remove("hidden");
  $("entry-id").textContent = state.current.id;
  $("entry-title").textContent = state.current.title;
  $("state-badge").textContent = STATE_LABELS[state.current.state] || state.current.state;
  $("problem-editor").value = state.current.problem;
  $("source-status").textContent = state.current.source_review.status === "passed" ? "已通过" : "等待教师确认";
  renderAgentMessage();
  renderMarkdown(state.current.problem, $("problem-preview"));
  $("publication-privacy-confirmed").checked = false;
  syncTabAvailability(); enforceActiveTabPrerequisite(); renderProgress(); renderImages(); showSolution(state.solution); renderVisualization(); renderDownloads(); renderPublicationImages(); renderPublication(); renderEntries(); renderActiveJob();
}

function renderProgress() {
  const progress = $("progress"); progress.replaceChildren();
  const current = state.current.state;
  const steps = STEPS;
  progress.style.gridTemplateColumns = `repeat(${steps.length}, 1fr)`;
  for (const [label, doneStates] of steps) {
    const item = document.createElement("li"); item.textContent = label;
    if (doneStates.includes(current)
        || (label === "可视化（可选）" && hasDynamicVisualization() && ["ready-to-finish", "delivered"].includes(current))) {
      item.classList.add("done");
    }
    if ((current === "needs-source-review" && label === "题干复核") ||
        (current === "needs-analysis-and-answer" && label === "生成解析") ||
        (current === "needs-answer-review" && label === "答案复核") ||
        (["needs-visualization-build", "needs-visualization-review"].includes(current) && label === "可视化（可选）") ||
        (current === "ready-to-finish" && label === "生成文件") ||
        (current === "delivered" && label === "下载")) item.classList.add("current");
    progress.append(item);
  }
}

function renderImages() {
  const stage = $("image-stage"); stage.replaceChildren(); state.zoom = 100; $("zoom").value = "100";
  $("image-count").textContent = state.current.images.length ? `${state.current.images.length} 页` : "";
  if (!state.current.images.length) { stage.textContent = "暂无图片"; return; }
  state.current.images.forEach((src, index) => {
    const image = document.createElement("img"); image.src = src; image.alt = `题目原图第 ${index + 1} 页`; image.dataset.reviewImage = "1"; stage.append(image);
  });
  applyZoom();
}

function applyZoom() {
  state.zoom = Number($("zoom").value);
  $("image-stage").style.placeItems = state.zoom > 100 ? "start" : "center";
  document.querySelectorAll("[data-review-image]").forEach(image => {
    image.style.transform = "none";
    image.style.width = `${state.zoom}%`;
    image.style.maxWidth = "none";
  });
}

function showSolution(layer) {
  state.solution = layer;
  $("answer-editor").value = state.solutionDrafts[layer] || "";
  renderMarkdown(state.solutionDrafts[layer] || "解析尚未生成。", $("solution-view"));
  updateDraftStatus();
  document.querySelectorAll(".solution-tab").forEach(button => button.classList.toggle("active", button.dataset.solution === layer));
}

function updateDraftStatus(status = null) {
  const text = status || (state.dirty[state.solution] ? "未保存" : "已保存");
  $("draft-status").textContent = text;
}

async function saveCurrentAnswer({ quiet = false } = {}) {
  if (!state.current) return false;
  const layer = state.solution;
  if (!state.dirty[layer]) return true;
  updateDraftStatus("保存中…");
  try {
    const result = await api(`/api/entries/${encodeURIComponent(state.current.id)}/save-answer`, {
      method: "POST",
      body: { layer, markdown: state.solutionDrafts[layer], base_digest: state.current.answer_digest },
    });
    state.dirty[layer] = false;
    updateDraftStatus("已保存");
    if (!quiet) toast(`${layer === "student" ? "学生版" : "教师版"}解析已保存，等待重新复核`);
    await loadEntries(state.current.id);
    return result.status === "saved";
  } catch (error) {
    updateDraftStatus("保存失败");
    toast(error.message, true);
    return false;
  }
}

function renderVisualization() {
  const visualization = state.current?.visualization || {};
  const hasModel = Boolean(visualization.has_model);
  const review = visualization.review || {};
  const build = visualization.build || {};
  const previewUrl = visualization.preview_url || "";
  const hasPreview = Boolean(previewUrl);
  const reviewPassed = review.status === "passed";
  const buildStatus = build.status || build.validation_status || "";

  let status = "等待构建并由教师复核";
  if (!hasModel) status = "尚未生成 · 可由教师按需请求";
  else if (!answerIsConfirmed()) status = "模型与预览已生成 · 请先重新复核答案与统一模型";
  else if (reviewPassed) status = "教师已批准当前动态可视化";
  else if (previewUrl) status = "预览已生成 · 等待教师核对轨迹、方向与关键时刻";
  else if (buildStatus && !["ok", "passed", "success"].includes(buildStatus)) status = `构建状态：${buildStatus}`;
  $("visualization-status").textContent = status;

  const frame = $("visualization-frame");
  const empty = $("visualization-empty");
  if (hasModel && previewUrl) {
    const version = build.model_digest || build.built_at || Date.now();
    const separator = previewUrl.includes("?") ? "&" : "?";
    frame.src = `${previewUrl}${separator}v=${encodeURIComponent(version)}`;
    frame.classList.remove("hidden");
    empty.classList.add("hidden");
  } else {
    frame.removeAttribute("src");
    frame.classList.add("hidden");
    empty.classList.remove("hidden");
    const title = empty.querySelector("strong");
    const copy = empty.querySelector("p");
    if (hasModel) {
      title.textContent = "统一物理模型已就绪，尚未构建预览";
      copy.textContent = "点击“构建 / 刷新预览”查看完整过程。";
    } else {
      title.textContent = "尚未生成交互可视化";
      copy.textContent = "这不代表本题不适合可视化。点击“调用 Skill 生成”，或在右侧说明你想看到的具体过程。";
    }
  }

  const buildButton = $("build-visualization");
  buildButton.disabled = !answerIsConfirmed();
  buildButton.textContent = !hasModel ? "调用 Skill 生成" : (previewUrl ? "重新构建预览" : "构建 / 刷新预览");
  const approveButton = $("approve-visualization");
  approveButton.disabled = !hasPreview || reviewPassed || !answerIsConfirmed();
  approveButton.textContent = reviewPassed ? "当前版本已批准" : "可视化正确，批准";
  $("send-visualization-message").textContent = hasModel ? "发送并重新构建" : "发送并生成";
  renderVisualizationConversation(visualization.conversation);
}

function renderVisualizationConversation(conversation) {
  const container = $("visualization-conversation");
  container.replaceChildren();
  const messages = Array.isArray(conversation)
    ? conversation
    : (Array.isArray(conversation?.messages) ? conversation.messages : []);
  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "conversation-empty";
    empty.innerHTML = "<strong>可以直接说出你看到的问题</strong><span>例如粒子电性、偏转方向、关键事件、播放时长或画面元素。</span>";
    container.append(empty);
    return;
  }
  for (const message of messages) {
    const role = ["user", "teacher"].includes(message.role) ? "user" : "assistant";
    const bubble = document.createElement("article");
    bubble.className = `conversation-message ${role}`;
    const label = document.createElement("strong");
    label.textContent = role === "user" ? "教师" : "大模型";
    const text = document.createElement("p");
    text.textContent = message.content || message.message || message.text || "";
    bubble.append(label, text);
    container.append(bubble);
  }
  requestAnimationFrame(() => { container.scrollTop = container.scrollHeight; });
}

function renderDownloads() {
  const list = $("download-list");
  const guide = $("delivery-guide-list");
  list.replaceChildren();
  guide.replaceChildren();
  const files = (state.current.downloads || []).filter(isUsefulDeliveryFile);
  $("finish-entry").disabled = false;
  $("finish-entry").textContent = state.current.state === "delivered" ? "重新生成最终文件" : "生成最终文件";
  if (!files.length) {
    const paragraph = document.createElement("p"); paragraph.className = "muted delivery-empty"; paragraph.textContent = "尚未生成可交付文件。请先完成题干与答案复核；有动态仿真时还需完成可视化复核。"; list.append(paragraph);
    const guideEmpty = document.createElement("p"); guideEmpty.className = "muted"; guideEmpty.textContent = "成品生成后，这里会逐一说明用途，并标出最适合直接发送给学生的文件。"; guide.append(guideEmpty);
    return;
  }
  for (const file of files) {
    const link = document.createElement("a"); link.className = "download-card"; link.href = file.url;
    const heading = document.createElement("span"); heading.className = "download-card-title";
    const strong = document.createElement("strong"); strong.textContent = file.name;
    heading.append(strong);
    if (file.recommended) {
      const badge = document.createElement("span"); badge.className = "recommended-badge"; badge.textContent = "推荐发送"; heading.append(badge);
    }
    const purpose = document.createElement("p"); purpose.textContent = file.purpose || fallbackFilePurpose(file.name);
    const small = document.createElement("small"); small.textContent = `${humanFileSize(file.size)} · 点击下载`;
    link.append(heading, purpose, small); list.append(link);

    const item = document.createElement("article"); item.className = "delivery-guide-item";
    const itemTitle = document.createElement("strong"); itemTitle.textContent = file.name;
    const itemPurpose = document.createElement("p"); itemPurpose.textContent = file.purpose || fallbackFilePurpose(file.name);
    item.append(itemTitle, itemPurpose); guide.append(item);
  }
}

function currentPublicationImage() {
  return state.publicationImageDrafts[state.publicationImageIndex] || null;
}

function cropMargins(crop) {
  const [x, y, width, height] = crop || [0, 0, 1, 1];
  return {
    top: Math.round(y * 100),
    right: Math.round((1 - x - width) * 100),
    bottom: Math.round((1 - y - height) * 100),
    left: Math.round(x * 100),
  };
}

function syncPublicationCropControls() {
  const page = currentPublicationImage();
  if (!page) return;
  const margins = cropMargins(page.crop);
  for (const side of ["top", "right", "bottom", "left"]) {
    $(`crop-${side}`).value = String(margins[side]);
    $(`crop-${side}-value`).textContent = `${margins[side]}%`;
  }
  $("publication-image-include").checked = page.include !== false;
}

function drawPublicationImage() {
  const page = currentPublicationImage();
  const canvas = $("publication-image-canvas");
  const context = canvas.getContext("2d");
  if (!page) { canvas.width = 1; canvas.height = 1; context.clearRect(0, 0, 1, 1); return; }
  const image = new Image();
  const entryId = state.current?.id;
  image.onload = () => {
    if (state.current?.id !== entryId || currentPublicationImage()?.id !== page.id) return;
    state.publicationImageObject = image;
    const [x, y, width, height] = page.crop;
    const sourceWidth = Math.max(1, width * image.naturalWidth);
    const sourceHeight = Math.max(1, height * image.naturalHeight);
    const scale = Math.min(1, 1000 / sourceWidth, 650 / sourceHeight);
    canvas.width = Math.max(1, Math.round(sourceWidth * scale));
    canvas.height = Math.max(1, Math.round(sourceHeight * scale));
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, x * image.naturalWidth, y * image.naturalHeight, sourceWidth, sourceHeight, 0, 0, canvas.width, canvas.height);
    for (const box of page.redactions || []) {
      const [rx, ry, rw, rh] = box;
      const left = rx * canvas.width, top = ry * canvas.height, boxWidth = rw * canvas.width, boxHeight = rh * canvas.height;
      context.fillStyle = "rgb(246,243,235)";
      context.fillRect(left, top, boxWidth, boxHeight);
      context.strokeStyle = "#9b3b34";
      context.lineWidth = 2;
      context.strokeRect(left, top, boxWidth, boxHeight);
    }
  };
  image.onerror = () => { canvas.width = 420; canvas.height = 180; context.fillText("题图读取失败", 20, 40); };
  image.src = page.url;
}

function renderPublicationImages() {
  const snapshot = state.current?.publication_images || {};
  const editor = $("publication-image-editor");
  const pages = state.publicationImageDrafts;
  editor.classList.toggle("hidden", pages.length === 0);
  if (!pages.length) return;
  state.publicationImageIndex = Math.max(0, Math.min(state.publicationImageIndex, pages.length - 1));
  const passed = snapshot.status === "passed";
  $("publication-image-status").textContent = passed
    ? `公开题图已确认 · 已选 ${snapshot.included_count || 0} 页；原图或配置变化后会自动失效。`
    : (snapshot.status === "stale" ? "原图或公开副本已变化，请重新裁剪并确认。" : "请检查自动裁剪，并遮挡姓名、学校、二维码和不应公开的笔迹。");
  $("publication-image-page").textContent = `第 ${state.publicationImageIndex + 1} / ${pages.length} 页`;
  $("publication-image-prev").disabled = state.publicationImageIndex === 0;
  $("publication-image-next").disabled = state.publicationImageIndex === pages.length - 1;
  $("publication-image-confirmed").checked = false;
  $("save-publication-images").disabled = true;
  syncPublicationCropControls();
  drawPublicationImage();
}

function updatePublicationCrop() {
  const page = currentPublicationImage();
  if (!page) return;
  const top = Number($("crop-top").value), right = Number($("crop-right").value);
  const bottom = Number($("crop-bottom").value), left = Number($("crop-left").value);
  if (left + right > 90 || top + bottom > 90) { toast("裁剪范围过小，请减少相对两边的裁剪量。", true); syncPublicationCropControls(); return; }
  page.crop = [left / 100, top / 100, (100 - left - right) / 100, (100 - top - bottom) / 100];
  page.redactions = [];
  syncPublicationCropControls();
  drawPublicationImage();
}

function publicationCanvasPoint(event) {
  const canvas = $("publication-image-canvas");
  const bounds = canvas.getBoundingClientRect();
  return [Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))];
}

function renderPublication() {
  const publication = state.current?.publication || {};
  const prepared = Boolean(publication.preview_ready && publication.preview_url);
  const published = Boolean(publication.published_local && publication.local_site_url);
  const delivered = state.current?.state === "delivered";
  const imageSnapshot = state.current?.publication_images || {};
  const imageReady = !(imageSnapshot.sources || []).length || imageSnapshot.status === "passed";
  const checkbox = $("publication-privacy-confirmed");
  const frame = $("publication-preview-frame");
  const empty = $("publication-empty");

  let status = "请先生成最终文件，再制作学生端公开预览。";
  if (delivered) status = imageReady ? "已交付，可以生成去隐私的学生端公开预览。" : "请先完成上方公开题图的裁剪、遮挡与教师确认。";
  if (prepared) status = "公开预览已生成，等待教师逐页检查并确认隐私边界。";
  if (published) status = "已发布到本地学生站；尚未自动推送 GitHub。";
  if (publication.pdf?.status === "skipped") status += " 当前环境未生成公开 PDF，学生端仍可阅读 Markdown。";
  $("publication-status").textContent = status;

  $("prepare-publication").disabled = !delivered || !imageReady;
  $("prepare-publication").textContent = prepared ? "重新生成公开预览" : "生成公开预览";
  if (prepared) {
    const separator = publication.preview_url.includes("?") ? "&" : "?";
    frame.src = `${publication.preview_url}${separator}v=${encodeURIComponent(publication.prepared_at || Date.now())}`;
    frame.classList.remove("hidden");
    empty.classList.add("hidden");
  } else {
    frame.removeAttribute("src");
    frame.classList.add("hidden");
    empty.classList.remove("hidden");
  }

  $("publish-publication").disabled = !prepared || !checkbox.checked;
  const localLink = $("publication-local-link");
  if (published) {
    localLink.href = publication.local_site_url;
    localLink.classList.remove("hidden");
  } else {
    localLink.removeAttribute("href");
    localLink.classList.add("hidden");
  }
}

function isUsefulDeliveryFile(file) {
  if (!file || file.visible === false) return false;
  if (file.visible === true || file.purpose || file.kind) return true;
  const name = String(file.name || "").toLowerCase();
  if (name.endsWith(".pdf") || name.endsWith(".md")) return true;
  if (name.endsWith("student-package.zip") || name.includes("学生包") && name.endsWith(".zip")) return true;
  return (/(^|\/)simulation\//.test(name) || name.includes("physics-simulator"))
    && (name.endsWith(".html") || name.endsWith(".zip"));
}

function fallbackFilePurpose(name) {
  const lower = String(name || "").toLowerCase();
  if (lower.endsWith("student-package.zip") || lower.includes("学生包")) return "推荐直接发送给学生：答案、PDF 和必要的可视化已打成一个包。";
  if (lower.endsWith(".pdf")) return "适合打印、课堂投屏或直接发送，版式固定。";
  if (lower.endsWith(".md")) return "适合教师继续修改，或导入 Claude Code 等支持 Markdown 的平台。";
  if (lower.endsWith(".html")) return "课堂上直接打开的交互可视化，无需安装软件。";
  if (lower.endsWith(".zip")) return "单独发送可视化时使用；解压后打开其中的 HTML。";
  return "本题的交付成品。";
}

function humanFileSize(size) {
  const bytes = Number(size || 0);
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function isQueuedJobResponse(result) {
  return result?.status === "queued" && Boolean(result.job?.id);
}

function jobActionLabel(action) {
  return {
    analyze: "生成学生版与教师版解析",
    "analysis.generate": "生成学生版与教师版解析",
    "request-revision": "按教师意见修改解析",
    "answer.revise": "按教师意见修改解析",
    "build-visualization": "调用仿真 Skill 生成可视化",
    "visualization-chat": "按对话要求生成或修正可视化",
    "visualization.model": "按要求生成或修正可视化",
  }[action] || "后台 Agent 任务";
}

function jobFailureReason(job) {
  if (!job) return "任务失败，未返回具体原因";
  if (typeof job.error === "string" && job.error.trim()) return job.error;
  if (job.error?.message) return job.error.message;
  if (typeof job.result?.error === "string" && job.result.error.trim()) return job.result.error;
  if (job.result?.error?.message) return job.result.error.message;
  if (Array.isArray(job.result?.validation_errors) && job.result.validation_errors.length) return `校验原因：${job.result.validation_errors.join("；")}`;
  if (Array.isArray(job.validation_errors) && job.validation_errors.length) return `校验原因：${job.validation_errors.join("；")}`;
  if (Array.isArray(job.result?.unauthorized_changes) && job.result.unauthorized_changes.length) return `越权文件：${job.result.unauthorized_changes.join("、")}`;
  if (Array.isArray(job.unauthorized_changes) && job.unauthorized_changes.length) return `越权文件：${job.unauthorized_changes.join("、")}`;
  if (Array.isArray(job.errors) && job.errors.length) return job.errors.join("；");
  return job.reason || job.message || "任务失败，请检查 Agent 状态后重试";
}

function jobApiUrl(job) {
  const supplied = String(job?.url || "");
  if (/^\/api\/jobs\/[A-Za-z0-9._~-]+(?:\?.*)?$/.test(supplied)) return supplied;
  return `/api/jobs/${encodeURIComponent(job.id)}`;
}

function agentActionButtons() {
  return [
    "run-analysis", "save-answer", "request-revision", "approve-answer",
    "build-visualization", "send-visualization-message", "approve-visualization", "finish-entry",
  ].map($).filter(Boolean);
}

function syncJobControls() {
  const busy = Boolean(currentJob() && !["completed", "failed"].includes(currentJob().status));
  for (const button of agentActionButtons()) {
    if (busy) {
      if (button.dataset.jobDisabled !== "1") {
        button.dataset.jobDisabled = "1";
        button.dataset.jobWasDisabled = button.disabled ? "1" : "0";
      }
      button.disabled = true;
    } else if (!busy && button.dataset.jobDisabled === "1") {
      button.disabled = button.dataset.jobWasDisabled === "1";
      delete button.dataset.jobDisabled;
      delete button.dataset.jobWasDisabled;
    }
  }
}

function renderActiveJob() {
  const panel = $("active-job");
  const job = currentJob();
  if (!job) {
    panel.className = "active-job hidden";
    syncJobControls();
    return;
  }

  const status = String(job.status || "queued");
  const action = jobActionLabel(job.action);
  const titles = {
    queued: `${action} · 已排队`,
    pending: `${action} · 等待执行`,
    running: `${action} · 正在处理`,
    completed: `${action} · 已完成`,
    failed: `${action} · 失败`,
  };
  panel.className = `active-job${status === "completed" ? " completed" : ""}${status === "failed" ? " failed" : ""}`;
  $("active-job-title").textContent = titles[status] || `${action} · ${status}`;
  if (status === "failed") {
    $("active-job-detail").textContent = jobFailureReason(job);
  } else {
    const providerValue = job.provider || job.agent || selectedAgentLabel();
    const provider = typeof providerValue === "object"
      ? (providerValue.name || providerValue.selected || "本地 Agent")
      : providerValue;
    const progress = job.progress_message || job.message || "";
    const result = job.result || {};
    const requestedTier = job.routing_tier || result.requested_tier;
    const actualTier = result.model_tier;
    const selectedModel = result.model_display_name || result.model_id || job.model_id;
    const model = result.model || selectedModel;
    const totalTokens = result.usage?.total_tokens;
    const route = actualTier
      ? `档位 ${agentTierLabel(requestedTier)}→${agentTierLabel(actualTier)}`
      : (requestedTier && `档位 ${agentTierLabel(requestedTier)}`);
    const id = String(job.id || "").slice(0, 12);
    const entry = String(job.entryId || "").slice(0, 18);
    $("active-job-detail").textContent = [provider && `Agent：${provider}`, route, model && `模型 ${model}`, Number.isInteger(totalTokens) && `Token ${totalTokens}`, progress, entry && `题目 ${entry}`, id && `任务 ${id}`].filter(Boolean).join(" · ");
  }
  syncJobControls();
}

function beginQueuedJob(job, { action, entryId, success }) {
  clearTimeout(state.jobPollTimer);
  clearTimeout(state.jobDismissTimer);
  const jobId = String(job.id);
  state.activeJobs[entryId] = {
    ...job,
    id: jobId,
    status: job.status || "queued",
    action,
    entryId,
    success,
    pollErrors: 0,
  };
  renderActiveJob();
  toast(`${jobActionLabel(action)}已进入后台队列，可继续查看页面`);
  state.jobPollTimer = setTimeout(() => pollActiveJob(jobId, entryId), 450);
}

async function refreshAfterJob(job) {
  const currentMatches = state.current?.id === job.entryId;
  const hasUnsavedWork = state.problemDirty || state.dirty.student || state.dirty.teacher;
  if (currentMatches && !hasUnsavedWork) await loadEntries(job.entryId);
  else {
    await refreshCatalog();
    if (currentMatches && hasUnsavedWork) {
      toast("后台任务已结束；为保留未保存内容，请保存后手动刷新查看结果");
    }
  }
}

function _pollJob(entryId) { return state.activeJobs[entryId] || null; }

async function pollActiveJob(jobId, entryId) {
  if (!_pollJob(entryId) || _pollJob(entryId).id !== jobId) return;
  const snapshot = _pollJob(entryId);
  try {
    const response = await api(jobApiUrl(snapshot));
    if (!_pollJob(entryId) || _pollJob(entryId).id !== jobId) return;
    const update = response.job && typeof response.job === "object" ? response.job : response;
    const context = _pollJob(entryId);
    state.activeJobs[entryId] = {
      ...context,
      ...update,
      id: String(update.id || jobId),
      status: update.status || response.status || context.status,
      action: context.action,
      entryId: context.entryId,
      success: context.success,
      pollErrors: 0,
    };
  } catch (error) {
    if (!_pollJob(entryId) || _pollJob(entryId).id !== jobId) return;
    const job = _pollJob(entryId);
    state.activeJobs[entryId] = {
      ...job,
      pollErrors: (job.pollErrors || 0) + 1,
      progress_message: `暂时无法读取进度，正在重试（${(job.pollErrors || 0) + 1}）`,
    };
    renderActiveJob();
    state.jobPollTimer = setTimeout(() => pollActiveJob(jobId, entryId), Math.min(5000, 900 + (job.pollErrors || 0) * 600));
    return;
  }

  renderActiveJob();
  const status = _pollJob(entryId).status;
  if (!["completed", "failed"].includes(status)) {
    state.jobPollTimer = setTimeout(() => pollActiveJob(jobId, entryId), status === "queued" ? 900 : 1400);
    return;
  }

  const finished = _pollJob(entryId);
  await refreshAfterJob(finished);
  if (!_pollJob(entryId) || _pollJob(entryId).id !== jobId) return;
  if (status === "completed") {
    const message = typeof finished.success === "function" ? finished.success(finished) : finished.success;
    toast(message || `${jobActionLabel(finished.action)}已完成，题目内容已刷新`);
    state.jobDismissTimer = setTimeout(() => {
      const job = _pollJob(entryId);
      if (job?.id === jobId && job.status === "completed") {
        delete state.activeJobs[entryId];
        renderActiveJob();
      }
    }, 5200);
  } else {
    toast(`${jobActionLabel(finished.action)}失败：${jobFailureReason(finished)}`, true);
  }
  renderActiveJob();
}

async function entryAction(action, body, success) {
  if (!state.current) return;
  try {
    const result = await api(`/api/entries/${encodeURIComponent(state.current.id)}/${action}`, { method: "POST", body });
    if (isQueuedJobResponse(result)) {
      beginQueuedJob(result.job, { action, entryId: state.current.id, success });
      return result;
    }
    const successMessage = typeof success === "function" ? success(result) : success;
    toast(successMessage || result.status, result.status === "failed");
    await loadEntries(state.current.id);
    return result;
  } catch (error) {
    toast(error.message, true);
    return null;
  }
}

async function visualizationAction(action, body, button, busyLabel, success) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = busyLabel;
  try {
    const result = await api(`/api/entries/${encodeURIComponent(state.current.id)}/${action}`, { method: "POST", body });
    if (isQueuedJobResponse(result)) {
      beginQueuedJob(result.job, { action, entryId: state.current.id, success });
      return result;
    }
    const successMessage = typeof success === "function" ? success(result) : success;
    toast(successMessage || result.status);
    await loadEntries(state.current.id);
    return result;
  } catch (error) {
    toast(error.message, true);
    return null;
  } finally {
    button.disabled = false;
    button.textContent = original;
    if (state.current) renderVisualization();
    syncJobControls();
  }
}

function setupUpload() {
  const input = $("file-input"), dropzone = $("dropzone");
  function choose(file) {
    state.file = file; $("upload-file").textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
    $("upload-file").classList.remove("hidden"); $("upload-run").disabled = false;
  }
  input.addEventListener("change", () => input.files[0] && choose(input.files[0]));
  ["dragenter", "dragover"].forEach(type => dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(type => dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.remove("drag"); }));
  dropzone.addEventListener("drop", event => event.dataTransfer.files[0] && choose(event.dataTransfer.files[0]));
  $("upload-run").addEventListener("click", async () => {
    if (!state.file) return;
    const button = $("upload-run"); button.disabled = true; button.textContent = "正在识别并建立条目…";
    try {
      const upload = await api(`/api/upload?filename=${encodeURIComponent(state.file.name)}`, { method: "POST", body: state.file, headers: { "Content-Type": state.file.type || "application/octet-stream" } });
      const report = await api("/api/run-upload", { method: "POST", body: { filename: upload.filename, vision_capability: "unavailable" } });
      const id = report.work_orders?.[0]?.entry_id; toast("题目已上传，等待题干复核"); state.file = null;
      $("upload-file").classList.add("hidden"); input.value = ""; document.querySelector(".upload-card").open = false;
      await loadEntries(id);
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = !state.file; button.textContent = "上传并开始处理"; }
  });
}

function setupLayout() {
  const shell = document.querySelector(".shell");
  const toggle = $("sidebar-toggle");
  toggle.addEventListener("click", () => {
    const narrow = window.matchMedia("(max-width: 940px)").matches;
    const className = narrow ? "sidebar-open" : "sidebar-collapsed";
    shell.classList.toggle(className);
    const expanded = narrow ? shell.classList.contains("sidebar-open") : !shell.classList.contains("sidebar-collapsed");
    toggle.setAttribute("aria-expanded", String(expanded));
  });
}

function linkScroll(editor, preview) {
  let syncing = false;
  editor.addEventListener("scroll", () => {
    if (syncing) return;
    const editorRange = editor.scrollHeight - editor.clientHeight;
    const previewRange = preview.scrollHeight - preview.clientHeight;
    if (editorRange <= 0 || previewRange <= 0) return;
    syncing = true;
    preview.scrollTop = (editor.scrollTop / editorRange) * previewRange;
    requestAnimationFrame(() => { syncing = false; });
  });
}

installMarkdownExtensions();
setupLayout();
setupUpload();
linkScroll($("problem-editor"), $("problem-preview"));
linkScroll($("answer-editor"), $("solution-view"));

$("entry-title").addEventListener("click", () => {
  const titleEl = $("entry-title");
  if (titleEl.querySelector("input")) return;
  const original = titleEl.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.value = original;
  input.className = "entry-title-edit";
  input.maxLength = 120;
  titleEl.textContent = "";
  titleEl.appendChild(input);
  input.focus();
  input.select();
  const finish = async (save) => {
    const value = input.value.trim();
    input.disabled = true;
    if (save && value && value !== original) {
      const result = await entryAction("rename-entry", { title: value });
      if (result) {
        state.current.title = value;
        renderEntries(state.current.id);
      }
    }
    titleEl.textContent = state.current.title;
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
});

document.querySelectorAll(".tab").forEach(button => button.addEventListener("click", () => activateTab(button.dataset.tab)));
document.querySelectorAll(".solution-tab").forEach(button => button.addEventListener("click", async () => {
  if (button.dataset.solution === state.solution) return;
  if (state.dirty[state.solution]) {
    const saved = await saveCurrentAnswer({ quiet: true });
    if (!saved) return;
  }
  showSolution(button.dataset.solution);
}));

const updateProblemPreview = debounce(() => renderMarkdown($("problem-editor").value, $("problem-preview")));
const updateAnswerPreview = debounce(() => renderMarkdown($("answer-editor").value, $("solution-view")));
$("problem-editor").addEventListener("input", () => {
  state.problemDirty = true;
  updateProblemPreview();
});
$("answer-editor").addEventListener("input", () => {
  state.solutionDrafts[state.solution] = $("answer-editor").value;
  state.dirty[state.solution] = true;
  updateDraftStatus();
  updateAnswerPreview();
});
$("answer-editor").addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
    event.preventDefault(); saveCurrentAnswer();
  }
});
$("zoom").addEventListener("input", applyZoom);
$("zoom-out").addEventListener("click", () => { $("zoom").value = Math.max(50, Number($("zoom").value) - 25); applyZoom(); });
$("zoom-in").addEventListener("click", () => { $("zoom").value = Math.min(250, Number($("zoom").value) + 25); applyZoom(); });
$("zoom-reset").addEventListener("click", () => { $("zoom").value = 100; applyZoom(); });
$("refresh").addEventListener("click", () => {
  health();
  loadEntries().catch(error => toast(error.message, true));
});
$("approve-source").addEventListener("click", async () => {
  const wasDirty = state.problemDirty;
  state.problemDirty = false;
  const result = await entryAction("approve-source", { problem: $("problem-editor").value, reviewer: $("source-reviewer").value, note: $("source-note").value }, "题干复核已确认");
  if (!result) state.problemDirty = wasDirty;
  else activateTab("answer", { force: true });
});
$("run-analysis").addEventListener("click", () => {
  if (!requirePrerequisite("answer")) return;
  if (!ensureAgentRoutingReady()) return;
  entryAction("analyze", withRoutingTier(), "解析流程已完成，请复核学生版和教师版");
});
$("save-answer").addEventListener("click", () => {
  if (!requirePrerequisite("answer")) return;
  saveCurrentAnswer();
});
$("approve-answer").addEventListener("click", async () => {
  if (!requirePrerequisite("answer")) return;
  if (!hasGeneratedAnswers()) { toast("后台还没有完整解析，请先运行解析流程。", true); return; }
  if (state.dirty[state.solution] && !(await saveCurrentAnswer({ quiet: true }))) return;
  const result = await entryAction("approve-answer", { reviewer: $("answer-reviewer").value, note: $("answer-note").value }, "答案已批准");
  if (result) activateTab("visualization", { force: true });
});
$("request-revision").addEventListener("click", async () => {
  if (!requirePrerequisite("answer")) return;
  if (!hasGeneratedAnswers()) { toast("后台还没有完整解析，请先运行解析流程。", true); return; }
  if (!ensureAgentRoutingReady()) return;
  const note = $("answer-note").value.trim();
  if (!note) { toast("请先写明需要大模型修改的内容。", true); return; }
  const button = $("request-revision");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "大模型正在修改…";
  try {
    await entryAction(
      "request-revision",
      withRoutingTier({ reviewer: $("answer-reviewer").value, note }),
      result => result.status === "awaiting-agent"
        ? "修改意见已记录，等待本地大模型处理"
        : "大模型已完成修改，请重新复核解析",
    );
  } finally {
    button.disabled = false;
    button.textContent = original;
    syncJobControls();
  }
});
$("build-visualization").addEventListener("click", async () => {
  if (!requirePrerequisite("visualization")) return;
  if (!ensureAgentRoutingReady()) return;
  const isGenerating = !hasDynamicVisualization();
  const result = await visualizationAction(
    "build-visualization",
    withRoutingTier({
      message: isGenerating ? "我想为这道题生成一个可交互的可视化结果。请完整展示关键物理过程，并提供播放、时间轴、关键事件与缩放控件。" : "",
      base_digest: state.current.visualization?.artifact_digest || "",
    }),
    $("build-visualization"),
    isGenerating ? "正在调用 Skill 生成…" : "正在构建…",
    response => response.status === "awaiting-agent"
      ? "生成请求已记录，等待本地 Agent 调用仿真 Skill"
      : (response.status === "completed" ? "交互可视化已生成；若统一模型改变，请重新复核答案" : "未形成可用的交互可视化，请查看对话中的原因"),
  );
  if (result && state.current.state === "needs-answer-review") {
    activateTab("answer", { force: true });
  }
});
$("approve-visualization").addEventListener("click", async () => {
  if (!requirePrerequisite("visualization")) return;
  const result = await visualizationAction(
    "approve-visualization",
    { reviewer: $("visualization-reviewer").value, note: $("visualization-note").value },
    $("approve-visualization"),
    "正在记录…",
    "可视化复核已批准",
  );
  if (result) activateTab("delivery", { force: true });
});
$("send-visualization-message").addEventListener("click", async () => {
  if (!requirePrerequisite("visualization")) return;
  if (!ensureAgentRoutingReady()) return;
  const message = $("visualization-message").value.trim();
  if (!message) { toast("请先写下要调整的问题", true); return; }
  const isGenerating = !hasDynamicVisualization();
  const result = await visualizationAction(
    "visualization-chat",
    withRoutingTier({ message, base_digest: state.current.visualization?.artifact_digest || "" }),
    $("send-visualization-message"),
    isGenerating ? "正在调用 Skill 生成…" : "模型正在调整并重建…",
    response => response.status === "awaiting-agent"
      ? "请求已记录，等待本地 Agent 调用仿真 Skill"
      : (response.status !== "completed"
        ? "可视化任务未完成，请查看对话中的原因"
        : (isGenerating ? "交互可视化已生成；请核对统一模型与答案" : "模型已完成调整，请重新复核预览")),
  );
  if (result) $("visualization-message").value = "";
  if (result && state.current.state === "needs-answer-review") {
    activateTab("answer", { force: true });
  }
});
$("clear-visualization-chat").addEventListener("click", async () => {
  if (!requirePrerequisite("visualization")) return;
  const conversation = state.current.visualization?.conversation;
  const messages = Array.isArray(conversation)
    ? conversation
    : (Array.isArray(conversation?.messages) ? conversation.messages : []);
  if (!messages.length) { toast("聊天记录已经为空"); return; }
  if (!window.confirm("清空这道题的可视化聊天记录吗？此操作不会修改已生成的仿真。")) return;
  await visualizationAction(
    "clear-visualization-chat",
    {},
    $("clear-visualization-chat"),
    "正在清空…",
    "可视化聊天已清空",
  );
});
$("visualization-message").addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    $("send-visualization-message").click();
  }
});
$("finish-entry").addEventListener("click", () => {
  if (!requirePrerequisite("delivery")) return;
  entryAction("finish", { simulator: "auto" }, "最终文件已生成");
});
$("publication-image-prev").addEventListener("click", () => {
  state.publicationImageIndex = Math.max(0, state.publicationImageIndex - 1);
  renderPublicationImages();
});
$("publication-image-next").addEventListener("click", () => {
  state.publicationImageIndex = Math.min(state.publicationImageDrafts.length - 1, state.publicationImageIndex + 1);
  renderPublicationImages();
});
for (const side of ["top", "right", "bottom", "left"]) {
  $(`crop-${side}`).addEventListener("change", updatePublicationCrop);
  $(`crop-${side}`).addEventListener("input", () => { $(`crop-${side}-value`).textContent = `${$(`crop-${side}`).value}%`; });
}
$("publication-image-include").addEventListener("change", () => {
  const page = currentPublicationImage();
  if (page) page.include = $("publication-image-include").checked;
});
$("undo-publication-redaction").addEventListener("click", () => {
  const page = currentPublicationImage();
  if (page?.redactions?.length) { page.redactions.pop(); drawPublicationImage(); }
});
$("reset-publication-image").addEventListener("click", () => {
  const page = currentPublicationImage();
  if (!page) return;
  page.crop = [0, 0, 1, 1];
  page.redactions = [];
  syncPublicationCropControls();
  drawPublicationImage();
});
$("publication-image-canvas").addEventListener("pointerdown", event => {
  if (!currentPublicationImage()?.include) return;
  event.preventDefault();
  state.publicationRedactionStart = publicationCanvasPoint(event);
  event.currentTarget.setPointerCapture(event.pointerId);
});
$("publication-image-canvas").addEventListener("pointerup", event => {
  const start = state.publicationRedactionStart;
  state.publicationRedactionStart = null;
  if (!start) return;
  const end = publicationCanvasPoint(event);
  const left = Math.min(start[0], end[0]), top = Math.min(start[1], end[1]);
  const width = Math.abs(start[0] - end[0]), height = Math.abs(start[1] - end[1]);
  if (width < 0.006 || height < 0.006) { toast("请拖出一个明确的遮挡矩形。", true); return; }
  currentPublicationImage().redactions.push([left, top, width, height]);
  drawPublicationImage();
});
$("publication-image-confirmed").addEventListener("change", () => {
  $("save-publication-images").disabled = !$("publication-image-confirmed").checked;
});
$("probe-agent").addEventListener("click", probeAgent);
$("open-agent-settings").addEventListener("click", () => {
  openAgentSettings().catch(error => toast(`打开模型设置失败：${error.message}`, true));
});
$("open-retrieval-review").addEventListener("click", openRetrievalReview);
$("close-retrieval-review").addEventListener("click", closeRetrievalReview);
$("retrieval-review-backdrop").addEventListener("click", event => {
  if (event.target === $("retrieval-review-backdrop")) closeRetrievalReview();
});
$("retrieval-query").addEventListener("input", () => setRetrievalDirty());
$("retrieval-category").addEventListener("change", () => setRetrievalDirty());
$("retrieval-candidate-filter").addEventListener("input", renderRetrievalCandidates);
$("retrieval-prev").addEventListener("click", () => selectRetrievalCase(state.retrievalReview.index - 1));
$("retrieval-next").addEventListener("click", () => selectRetrievalCase(state.retrievalReview.index + 1));
$("retrieval-save-draft").addEventListener("click", () => saveRetrievalCase("draft"));
$("retrieval-reject").addEventListener("click", () => saveRetrievalCase("rejected", true));
$("retrieval-approve").addEventListener("click", () => saveRetrievalCase("approved", true));
$("close-agent-settings").addEventListener("click", closeAgentSettings);
$("agent-settings-backdrop").addEventListener("click", event => {
  if (event.target === $("agent-settings-backdrop")) closeAgentSettings();
});
$("add-agent-model").addEventListener("click", () => {
  state.modelSettings ||= { schema_version: 1, defaults: {}, models: [] };
  state.modelSettings.models ||= [];
  state.modelSettings.models.push(modelSettingTemplate());
  renderModelEditors();
  renderSettingsDefaults();
});
$("add-codex-visualization-preset").addEventListener("click", applyCodexVisualizationPreset);
$("save-agent-settings").addEventListener("click", () => {
  saveAgentSettings().catch(error => toast(`保存模型设置失败：${error.message}`, true));
});
try {
  const savedTier = localStorage.getItem(AGENT_TIER_KEY);
  if (["auto", "economy", "expert", "custom"].includes(savedTier)) $("agent-tier").value = savedTier;
} catch (_error) {
  // Private browsing or a locked-down browser may disable local storage.
}
syncAgentModelVisibility();
$("agent-tier").addEventListener("change", () => {
  try { localStorage.setItem(AGENT_TIER_KEY, selectedAgentTier()); } catch (_error) { /* preference is optional */ }
  syncAgentModelVisibility();
  renderAgentMessage();
});
$("agent-model").addEventListener("change", () => {
  try { localStorage.setItem(AGENT_MODEL_KEY, selectedAgentModelId()); } catch (_error) { /* preference is optional */ }
  renderAgentMessage();
});
$("save-publication-images").addEventListener("click", async () => {
  if (!$("publication-image-confirmed").checked) { toast("请先检查全部页面并勾选确认。", true); return; }
  const reviewer = $("publication-image-reviewer").value.trim();
  if (!reviewer) { toast("请填写题图复核人。", true); return; }
  await entryAction("save-publication-images", {
    reviewer,
    note: $("publication-image-note").value,
    privacy_confirmed: true,
    pages: state.publicationImageDrafts.map(page => ({ source_id: page.id, include: page.include !== false, crop: page.crop, redactions: page.redactions || [] })),
  }, result => `公开题图已确认，共选择 ${result.included_count || 0} 页`);
});
$("prepare-publication").addEventListener("click", async () => {
  if (state.current?.state !== "delivered") { toast("请先生成并复核最终交付文件。", true); return; }
  const imageSnapshot = state.current?.publication_images || {};
  if ((imageSnapshot.sources || []).length && imageSnapshot.status !== "passed") { toast("请先裁剪、脱敏并确认公开题图。", true); return; }
  const button = $("prepare-publication");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在生成并检查…";
  try {
    await entryAction("prepare-publication", {}, "公开预览已生成，请检查后确认");
  } finally {
    button.textContent = original;
    if (state.current) renderPublication();
  }
});
$("publication-privacy-confirmed").addEventListener("change", renderPublication);
$("publish-publication").addEventListener("click", async () => {
  if (!$("publication-privacy-confirmed").checked) { toast("请先查看预览并确认隐私边界。", true); return; }
  const reviewer = $("publication-reviewer").value.trim();
  if (!reviewer) { toast("请填写公开复核人。", true); return; }
  const result = await entryAction(
    "publish-publication",
    { reviewer, note: $("publication-note").value, privacy_confirmed: true },
    "已发布到本地学生站；需要你另行推送 GitHub",
  );
  if (result) $("publication-privacy-confirmed").checked = false;
});

health();
loadEntries().catch(error => toast(`无法读取题目：${error.message}`, true));
