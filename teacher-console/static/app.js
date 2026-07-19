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
};
const $ = (id) => document.getElementById(id);

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
        (["data-math", "data-display", "data-image-index"].includes(attribute.name));
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

async function health() {
  try {
    const data = await api("/api/health");
    document.querySelector(".health").classList.add("online");
    $("health-text").textContent = data.agent_configured ? "本地服务与分析 Agent 已连接" : "本地服务已连接 · 分析 Agent 待配置";
  } catch {
    $("health-text").textContent = "本地服务未连接";
  }
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
  $("agent-message").textContent = state.current.agent_configured ? "点击运行后由本地 Agent 处理。" : "尚未配置 Agent；点击后会生成待处理请求。";
  renderMarkdown(state.current.problem, $("problem-preview"));
  $("publication-privacy-confirmed").checked = false;
  syncTabAvailability(); enforceActiveTabPrerequisite(); renderProgress(); renderImages(); showSolution(state.solution); renderVisualization(); renderDownloads(); renderPublicationImages(); renderPublication(); renderEntries();
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

async function entryAction(action, body, success) {
  if (!state.current) return;
  try {
    const result = await api(`/api/entries/${encodeURIComponent(state.current.id)}/${action}`, { method: "POST", body });
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
$("refresh").addEventListener("click", () => loadEntries().catch(error => toast(error.message, true)));
$("approve-source").addEventListener("click", async () => {
  const wasDirty = state.problemDirty;
  state.problemDirty = false;
  const result = await entryAction("approve-source", { problem: $("problem-editor").value, reviewer: $("source-reviewer").value, note: $("source-note").value }, "题干复核已确认");
  if (!result) state.problemDirty = wasDirty;
  else activateTab("answer", { force: true });
});
$("run-analysis").addEventListener("click", () => {
  if (!requirePrerequisite("answer")) return;
  entryAction("analyze", {}, "解析流程已完成，请复核学生版和教师版");
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
  const note = $("answer-note").value.trim();
  if (!note) { toast("请先写明需要大模型修改的内容。", true); return; }
  const button = $("request-revision");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "大模型正在修改…";
  try {
    await entryAction(
      "request-revision",
      { reviewer: $("answer-reviewer").value, note },
      result => result.status === "awaiting-agent"
        ? "修改意见已记录，等待本地大模型处理"
        : "大模型已完成修改，请重新复核解析",
    );
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
});
$("build-visualization").addEventListener("click", async () => {
  if (!requirePrerequisite("visualization")) return;
  const isGenerating = !hasDynamicVisualization();
  const result = await visualizationAction(
    "build-visualization",
    {
      message: isGenerating ? "我想为这道题生成一个可交互的可视化结果。请完整展示关键物理过程，并提供播放、时间轴、关键事件与缩放控件。" : "",
      base_digest: state.current.visualization?.artifact_digest || "",
    },
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
  const message = $("visualization-message").value.trim();
  if (!message) { toast("请先写下要调整的问题", true); return; }
  const isGenerating = !hasDynamicVisualization();
  const result = await visualizationAction(
    "visualization-chat",
    { message, base_digest: state.current.visualization?.artifact_digest || "" },
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
