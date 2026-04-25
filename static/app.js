const form = document.getElementById("analyze-form");
const fileInput = document.getElementById("file-input");
const urlInput = document.getElementById("url-input");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const submitBtn = document.getElementById("submit-btn");

const SUMMARY_KEYS = ["공사현장", "발주처코드", "기초금액", "추정가격", "투찰율", "투찰마감일", "입찰일", "입찰방식"];

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  statusEl.textContent = "분석 중...";
  statusEl.classList.remove("error");
  resultEl.classList.add("hidden");
  submitBtn.disabled = true;

  const fd = new FormData();
  if (fileInput.files.length) fd.append("file", fileInput.files[0]);
  else if (urlInput.value) fd.append("url", urlInput.value);
  else {
    showError("파일 또는 URL을 입력하세요.");
    return;
  }

  try {
    const res = await fetch("/analyze", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    render(data);
    statusEl.textContent = `완료 (${data.char_count.toLocaleString()}자 처리)`;
  } catch (err) {
    showError(err.message);
  } finally {
    submitBtn.disabled = false;
  }
});

function showError(msg) {
  statusEl.textContent = `오류: ${msg}`;
  statusEl.classList.add("error");
  submitBtn.disabled = false;
}

function render(data) {
  resultEl.classList.remove("hidden");

  const usage = data.usage || {};
  document.getElementById("usage").textContent =
    `토큰 사용량 — 입력: ${usage.prompt_token_count ?? 0}, 캐시: ${usage.cached_content_token_count ?? 0}, 출력: ${usage.candidates_token_count ?? 0}`;

  const summary = document.getElementById("summary-cards");
  summary.innerHTML = "";
  for (const k of SUMMARY_KEYS) {
    const v = data.extracted[k];
    if (v === undefined) continue;
    const cell = document.createElement("div");
    cell.className = "cell";
    cell.innerHTML = `<div class="k">${k}</div><div class="v">${formatValue(v)}</div>`;
    summary.appendChild(cell);
  }

  const tree = document.getElementById("jongmok-tree");
  tree.innerHTML = "";
  const jongmok = data.extracted["종목"];
  if (Array.isArray(jongmok) && jongmok.length) {
    const wrap = document.createElement("div");
    wrap.className = "or";
    jongmok.forEach((group, i) => {
      if (i > 0) {
        const sep = document.createElement("span");
        sep.textContent = "또는";
        wrap.appendChild(sep);
      }
      const grp = document.createElement("span");
      grp.className = "and";
      grp.textContent = group.join(" + ");
      wrap.appendChild(grp);
    });
    tree.appendChild(wrap);
  } else {
    tree.textContent = "(없음)";
  }

  const tbody = document.getElementById("results-body");
  tbody.innerHTML = "";
  for (const [k, v] of Object.entries(data.extracted)) {
    const tr = document.createElement("tr");
    const src = data.source[k] || "";
    tr.innerHTML = `<td>${k}</td><td class="value">${formatValue(v)}</td><td class="source">${escapeHtml(src) || "—"}</td>`;
    tbody.appendChild(tr);
  }

  const issuesEl = document.getElementById("issues-list");
  issuesEl.innerHTML = "";
  if (!data.issues.length) {
    const li = document.createElement("li");
    li.textContent = "이슈 없음";
    issuesEl.appendChild(li);
  } else {
    for (const issue of data.issues) {
      const li = document.createElement("li");
      li.className = issue.kind;
      li.textContent = `[${issue.kind}] ${issue.key}: ${issue.detail}`;
      issuesEl.appendChild(li);
    }
  }
}

function formatValue(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return JSON.stringify(v, null, 0);
  if (typeof v === "object") return `<pre>${escapeHtml(JSON.stringify(v, null, 2))}</pre>`;
  return escapeHtml(String(v));
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
