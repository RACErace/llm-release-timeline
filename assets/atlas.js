"use strict";

const byId = id => document.getElementById(id);
const query = byId("query");
const orgSelect = byId("org");
const kindSelect = byId("kind");
const orderSelect = byId("order");
const yearList = byId("year-list");
const matrix = byId("matrix");
const monthPicker = byId("month-picker");
const list = byId("list");
const help = byId("density-help");
const more = byId("more");
const about = byId("about");
const densityDialog = byId("density-dialog");
const densityChart = densityDialog.querySelector(".density");
const chartToggle = document.querySelector("[data-chart]");
const wideScreen = window.matchMedia("(min-width: 1200px)");
const chunk = 35;
const aliases = {
  Alibaba: "阿里巴巴 通义千问 阿里云 Qwen",
  Anthropic: "克劳德",
  Amazon: "亚马逊 AWS Bedrock",
  Baidu: "百度 文心一言 文心大模型 ERNIE",
  ByteDance: "字节跳动 豆包 火山引擎 Doubao Seed",
  DeepSeek: "深度求索 深度探索",
  Google: "谷歌 DeepMind Brain 谷歌大脑",
  Meta: "脸书 Facebook FAIR",
  Microsoft: "微软",
  MiniMax: "稀宇科技 名之梦 海螺",
  Moonshot: "月之暗面 Kimi Moonshot AI",
  NVIDIA: "英伟达 辉达",
  OpenAI: "开放人工智能",
  StepFun: "阶跃星辰 跃问",
  Tencent: "腾讯 混元 Hunyuan",
  Xiaomi: "小米",
  "Z.ai": "智谱 清言 智谱华章 Zhipu ChatGLM",
  "01.AI": "零一万物 Yi",
  Baichuan: "百川智能 Baichuan",
  Meituan: "美团 LongCat 龙猫",
  InternLM: "书生 浦语 上海人工智能实验室",
  SenseTime: "商汤 日日新 SenseNova",
  Ai2: "Allen Institute for AI 艾伦人工智能研究所",
  "美团": "Meituan",
  "上海人工智能实验室": "Shanghai AI Laboratory InternLM 书生 浦语",
  "百川智能": "Baichuan",
  "零一万物": "01.AI",
  "商汤科技": "SenseTime",
  "科大讯飞": "iFlytek Spark 星火",
  "蚂蚁集团": "Inclusion AI Ant Group",
  IBM: "国际商业机器",
  Mistral: "Mistral AI 密斯特拉尔",
  Apple: "苹果",
  xAI: "Grok 马斯克"
};
const chineseNames = {
  Alibaba: "阿里巴巴", Amazon: "Amazon", Baidu: "百度", ByteDance: "字节跳动",
  Microsoft: "Microsoft", Moonshot: "月之暗面", StepFun: "阶跃星辰",
  Tencent: "腾讯", Xiaomi: "小米", "Z.ai": "智谱 Z.ai",
  Meituan: "美团", "01.AI": "零一万物", Baichuan: "百川智能"
};
const kindOrder = ["发布", "预览", "开放权重", "开放 API", "受限开放", "版本更新", "研究公开", "公告"];
const cutoffMonth = byId("updated").dateTime.slice(0, 7);
let releases = [];
let years = [];
let selectedYear = "";
let selectedMonth = "";
let limit = chunk;
let matching = [];
let monthCursor = "";
let cells = [];

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function button(className, value) {
  const node = element("button", className, value);
  node.type = "button";
  return node;
}

function normalize(text) {
  return text.normalize("NFKC").toLocaleLowerCase().replace(/[\p{P}\p{Z}\p{S}]/gu, "");
}

function displayOrg(org) {
  return chineseNames[org] || org;
}

function populateSelect(select, values, label) {
  for (const value of values) select.add(new Option(label(value), value));
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  query.value = params.get("q") || "";
  orgSelect.value = params.get("org") || "";
  kindSelect.value = params.get("type") || "";
  if (orgSelect.selectedIndex === -1) orgSelect.value = "";
  if (kindSelect.selectedIndex === -1) kindSelect.value = "";
  orderSelect.value = params.get("order") === "asc" ? "asc" : "desc";
  selectedYear = years.includes(params.get("year")) ? params.get("year") : "";
  const month = params.get("month") || "";
  selectedMonth = selectedYear && /^(0[1-9]|1[0-2])$/.test(month) ? month : "";
}

function writeUrl(replace = false) {
  if (location.protocol === "file:") return;
  const url = new URL(location.href);
  const values = {q: query.value.trim(), org: orgSelect.value, type: kindSelect.value, year: selectedYear, month: selectedMonth, order: orderSelect.value === "asc" ? "asc" : ""};
  for (const [key, value] of Object.entries(values)) {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  if (url.href !== location.href) {
    if (replace) history.replaceState(null, "", url);
    else history.pushState(null, "", url);
  }
}

function densityLevel(n) {
  if (!n) return 0;
  if (n === 1) return 1;
  if (n <= 3) return 2;
  if (n <= 7) return 3;
  if (n <= 15) return 4;
  return 5;
}

function buildMatrix() {
  const fragment = document.createDocumentFragment();
  fragment.append(element("span", "axis"));
  for (let month = 1; month <= 12; month++) {
    fragment.append(element("span", "axis month", String(month).padStart(2, "0")));
  }
  for (const year of years) {
    fragment.append(element("span", "axis year", year));
    for (let month = 1; month <= 12; month++) {
      const cell = button("cell");
      cell.dataset.month = year + "-" + String(month).padStart(2, "0");
      cell.tabIndex = -1;
      cells.push(cell);
      fragment.append(cell);
    }
  }
  matrix.replaceChildren(fragment);
}

function renderMatrix(monthCounts) {
  const first = cells.find(cell => monthCounts.has(cell.dataset.month));
  const active = selectedMonth && selectedYear + "-" + selectedMonth;
  if (!monthCursor) monthCursor = active || first?.dataset.month || cells[0]?.dataset.month;
  for (const cell of cells) {
    const period = cell.dataset.month;
    const n = monthCounts.get(period) || 0;
    const future = period > cutoffMonth;
    const label = period.replace("-", " 年 ") + (future ? " 月 · 尚未到该月份" : " 月 · " + n + " 条记录");
    cell.dataset.level = densityLevel(n);
    cell.toggleAttribute("data-empty", !n);
    cell.toggleAttribute("data-future", future);
    cell.setAttribute("aria-disabled", String(!n));
    cell.setAttribute("aria-label", label);
    cell.title = label;
    cell.setAttribute("aria-pressed", String(active === period));
    cell.tabIndex = period === monthCursor ? 0 : -1;
  }
  help.textContent = "选择月份，查看当月记录";
  monthPicker.replaceChildren(new Option("按月份查看", ""));
  for (const period of [...monthCounts.keys()].sort().reverse()) {
    monthPicker.add(new Option(period.replace("-", " 年 ") + " 月 · " + monthCounts.get(period) + " 条", period));
  }
  monthPicker.value = active || "";
}

function renderYears(counts, total) {
  const fragment = document.createDocumentFragment();
  for (const year of ["", ...[...years].reverse()]) {
    const control = button("year-button");
    control.dataset.year = year;
    const label = year || "全部年份";
    const count = year ? counts.get(year) || 0 : total;
    control.setAttribute("aria-pressed", String(selectedYear === year));
    control.setAttribute("aria-label", label + "，" + count + " 条记录");
    control.append(element("span", "", label), element("span", "year-count", String(count)));
    fragment.append(control);
  }
  yearList.replaceChildren(fragment);
}

function sourceLink(url, label, model) {
  const link = element("a", "source", label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", model + "，" + label + "（新窗口）");
  const arrow = element("span", "", " ↗");
  arrow.setAttribute("aria-hidden", "true");
  link.append(arrow);
  return link;
}

function releaseItem(row) {
  const item = element("li", "entry");
  const stampText = row.date.length === 10 ? row.date.slice(5).replace("-", ".") : row.date.length === 7 ? "当月" : "当年";
  const stamp = element("time", "stamp", stampText);
  stamp.dateTime = row.date;
  stamp.setAttribute("aria-label", row.date);
  const body = element("div", "entry-body");
  const heading = element("div", "entry-top");
  const category = element("span", "type", row.kind);
  category.dataset.kind = row.kind;
  heading.append(element("h3", "", row.name), category);
  const meta = element("div", "entry-meta");
  meta.append(element("span", "lab", displayOrg(row.org)));
  if (row.stage) meta.append(element("span", "stage", row.stage));
  meta.append(sourceLink(row.url, row.source, row.name));
  if (row.url2) meta.append(sourceLink(row.url2, "补充来源", row.name));
  body.append(heading, element("p", "", row.note), meta);
  item.append(stamp, body);
  return item;
}

function renderList() {
  const fragment = document.createDocumentFragment();
  let previous = "";
  let events;
  const shown = matching.slice(0, limit);
  for (const row of shown) {
    const key = row.date.slice(0, 7);
    if (key !== previous) {
      const title = element("h2", "month-heading");
      title.append(element("span", "yr", key.slice(0, 4)), element("span", "mn", key.length === 7 ? Number(key.slice(5)) + " 月" : "月份未详"));
      events = element("ol", "events");
      fragment.append(title, events);
      previous = key;
    }
    events.append(releaseItem(row));
  }
  if (!matching.length) {
    const empty = element("div", "empty");
    const clear = button("text-button", "清除全部筛选");
    clear.addEventListener("click", resetFilters);
    empty.append(element("h2", "", "没有匹配的发布记录"), element("p", "", "可换一个关键词，或放宽年份、机构与事件类型。"), clear);
    fragment.append(empty);
  }
  list.replaceChildren(fragment);
  const remaining = matching.length - shown.length;
  more.hidden = remaining === 0;
  more.textContent = "继续查看 " + Math.min(chunk, remaining) + " 条　↓";
  const summary = byId("summary");
  const period = selectedYear ? selectedYear + " 年" + (selectedMonth ? " " + Number(selectedMonth) + " 月" : "") : "全部年份";
  summary.replaceChildren(document.createTextNode(period + "　/　"), element("strong", "", String(matching.length)), document.createTextNode(" 条记录"));
}

function refresh() {
  const terms = query.value.trim().split(/\s+/u).map(normalize).filter(Boolean);
  const context = releases.filter(row =>
    (!orgSelect.value || orgSelect.value === row.org) &&
    (!kindSelect.value || kindSelect.value === row.kind) &&
    terms.every(term => row.searchText.includes(term))
  );
  const yearCounts = new Map();
  const monthCounts = new Map();
  for (const row of context) {
    const year = row.date.slice(0, 4);
    yearCounts.set(year, (yearCounts.get(year) || 0) + 1);
    if (row.date.length >= 7) {
      const month = row.date.slice(0, 7);
      monthCounts.set(month, (monthCounts.get(month) || 0) + 1);
    }
  }
  const prefix = selectedYear + (selectedMonth ? "-" + selectedMonth : "");
  matching = context.filter(row => row.date.startsWith(prefix));
  const direction = orderSelect.value === "asc" ? 1 : -1;
  matching.sort((a, b) => direction * a.date.localeCompare(b.date) || a.name.localeCompare(b.name));
  renderMatrix(monthCounts);
  renderYears(yearCounts, context.length);
  renderList();
  const filtered = Boolean(query.value || orgSelect.value || kindSelect.value || selectedYear || orderSelect.value === "asc");
  byId("results-bar").hidden = !filtered;
  byId("reset").hidden = !filtered;
}

function changed(replace = false) {
  limit = chunk;
  writeUrl(replace);
  refresh();
}

function resetFilters() {
  query.value = orgSelect.value = kindSelect.value = selectedYear = selectedMonth = "";
  orderSelect.value = "desc";
  changed();
  query.focus({preventScroll: true});
}

function setCursor(cell) {
  for (const item of cells) item.tabIndex = item === cell ? 0 : -1;
  monthCursor = cell.dataset.month;
}

function selectPeriod(period) {
  [selectedYear, selectedMonth] = period ? period.split("-") : ["", ""];
  if (densityDialog.open) densityDialog.close();
  changed();
  list.focus({preventScroll: true});
  const top = list.getBoundingClientRect().top;
  if (top < 0 || top >= window.innerHeight) list.scrollIntoView({block: "start"});
}

function placeDensityChart() {
  const moveFocus = densityDialog.open || densityChart.contains(document.activeElement);
  if (wideScreen.matches) {
    if (densityDialog.open) densityDialog.close();
    yearList.after(densityChart);
    if (moveFocus) matrix.querySelector('[tabindex="0"]')?.focus({preventScroll: true});
  } else {
    densityDialog.append(densityChart);
    if (moveFocus) chartToggle.focus({preventScroll: true});
  }
}

function bindControls() {
  query.addEventListener("input", () => changed(true));
  for (const select of [orgSelect, kindSelect, orderSelect]) select.addEventListener("change", () => changed());
  byId("reset").addEventListener("click", resetFilters);
  yearList.addEventListener("click", event => {
    const item = event.target.closest("[data-year]");
    if (!item) return;
    selectedYear = item.dataset.year;
    selectedMonth = "";
    changed();
    yearList.querySelector('[aria-pressed="true"]').focus({preventScroll: true});
  });
  matrix.addEventListener("click", event => {
    const cell = event.target.closest(".cell");
    if (!cell || cell.hasAttribute("data-empty")) return;
    setCursor(cell);
    selectPeriod(cell.dataset.month);
  });
  monthPicker.addEventListener("change", () => selectPeriod(monthPicker.value));
  function showCell(event) {
    const cell = event.target.closest(".cell");
    if (cell) help.textContent = cell.getAttribute("aria-label");
  }
  matrix.addEventListener("pointerover", showCell);
  matrix.addEventListener("focusin", showCell);
  for (const eventName of ["pointerleave", "focusout"]) {
    matrix.addEventListener(eventName, () => { help.textContent = "选择月份，查看当月记录"; });
  }
  matrix.addEventListener("keydown", event => {
    const i = cells.indexOf(event.target);
    if (i === -1) return;
    const offsets = {ArrowRight: 1, ArrowLeft: -1, ArrowDown: 12, ArrowUp: -12, Home: -(i % 12), End: 11 - i % 12};
    if (!Object.hasOwn(offsets, event.key)) return;
    event.preventDefault();
    const next = cells[Math.max(0, Math.min(cells.length - 1, i + offsets[event.key]))];
    setCursor(next);
    next.focus();
  });
  more.addEventListener("click", () => {
    const prevCount = Math.min(limit, matching.length);
    limit += chunk;
    renderList();
    const next = list.querySelectorAll(".entry")[prevCount];
    if (next) {
      next.tabIndex = -1;
      next.focus({preventScroll: true});
      next.scrollIntoView({block: "nearest"});
    }
  });
  window.addEventListener("popstate", () => { readUrl(); limit = chunk; refresh(); });
}

document.querySelector("[data-about]").addEventListener("click", () => about.showModal());
chartToggle.addEventListener("click", () => densityDialog.showModal());
wideScreen.addEventListener("change", placeDensityChart);
placeDensityChart();
for (const dialog of [about, densityDialog]) {
  dialog.addEventListener("click", event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
  });
}
document.addEventListener("keydown", event => {
  if (event.key !== "/" || event.ctrlKey || event.altKey || event.metaKey || about.open || densityDialog.open || event.target.closest("input,select,textarea,[contenteditable]")) return;
  event.preventDefault();
  query.focus();
});

async function start() {
  const offlineData = byId("offline-data");
  let rows;
  if (offlineData) {
    rows = JSON.parse(offlineData.textContent);
    const home = new URL(location.href);
    home.search = home.hash = "";
    document.querySelector(".wordmark").href = home.href;
    const link = byId("data-link");
    link.href = URL.createObjectURL(new Blob([offlineData.textContent], {type: "application/json"}));
    link.download = "releases.json";
  } else {
    const response = await fetch("./data/releases.json");
    if (!response.ok) throw new Error(String(response.status));
    rows = await response.json();
  }
  releases = rows.map(row => ({
    ...row,
    searchText: normalize([row.name, row.org, displayOrg(row.org), aliases[row.org] || "", row.note, row.date, row.kind, row.stage || ""].join(" "))
  }));
  years = [...new Set(releases.map(row => row.date.slice(0, 4)))].sort();
  const orgs = [...new Set(releases.map(row => row.org))].sort((a, b) => displayOrg(a).localeCompare(displayOrg(b), "zh-CN"));
  const kinds = new Set(releases.map(row => row.kind));
  populateSelect(orgSelect, orgs, displayOrg);
  populateSelect(kindSelect, kindOrder.filter(kind => kinds.has(kind)), value => value);
  byId("total").textContent = releases.length;
  byId("labs").textContent = orgs.length;
  byId("span").textContent = years[0] + "–" + years[years.length - 1];
  byId("chart-range").textContent = years[0] + " — " + years[years.length - 1];
  buildMatrix();
  readUrl();
  bindControls();
  refresh();
  if (location.hash === "#archive") byId("archive").scrollIntoView();
}

start().catch(() => {
  byId("summary").textContent = "发布数据载入失败，请刷新页面重试。";
  const link = element("a", "text-button", "查看 GitHub 上的完整数据 ↗");
  link.href = "https://github.com/RACErace/llm-release-timeline/blob/main/data/releases.json";
  list.replaceChildren(link);
});
