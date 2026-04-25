const API_BASE = "https://classmate-m8aj.onrender.com";

const schoolSelect = document.getElementById("school-select");
const courseInput  = document.getElementById("course-input");
const searchForm   = document.getElementById("search-form");
const statusEl     = document.getElementById("status");
const resultsEl    = document.getElementById("results");

// ── Utilities ────────────────────────────────────────────────

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.className   = isError ? "error" : "";
}

function clearResults() {
  resultsEl.innerHTML = "";
}

async function apiFetch(path, options = {}) {
  let res;
  try {
    res = await fetch(API_BASE + path, options);
  } catch {
    throw new Error("Couldn't reach the server. Is it running?");
  }
  if (!res.ok) {
    let detail = "Something went wrong.";
    try {
      const body = await res.json();
      if (body.detail) detail = String(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// ── Bootstrap ────────────────────────────────────────────────

async function loadSchools() {
  try {
    const schools = await apiFetch("/schools");
    schools.forEach(s => {
      const opt = document.createElement("option");
      opt.value       = s.slug;
      opt.textContent = s.display_name;
      schoolSelect.appendChild(opt);
    });
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ── Canonical code check ──────────────────────────────────────

function looksCanonical(input) {
  return /^[A-Za-z]+\s*\d+$/.test(input.trim());
}

// ── Render: candidate picker ──────────────────────────────────

function renderCandidates(candidates, school) {
  clearResults();
  setStatus("");

  const prompt = document.createElement("p");
  prompt.className   = "candidate-prompt";
  prompt.textContent = "Did you mean…?";
  resultsEl.appendChild(prompt);

  const list = document.createElement("div");
  list.className = "candidate-list";

  candidates.forEach(c => {
    const btn = document.createElement("button");
    btn.className = "candidate-btn";
    btn.type      = "button";

    const strong = document.createElement("strong");
    strong.textContent = c.code;

    const span = document.createElement("span");
    span.textContent = c.title;

    btn.appendChild(strong);
    btn.appendChild(span);
    btn.addEventListener("click", () => fetchInsights(school, c.code));
    list.appendChild(btn);
  });

  resultsEl.appendChild(list);
}

// ── Render: insight card ──────────────────────────────────────

const WORKLOAD_LABELS = {
  front_loaded: "Front-loaded",
  back_loaded:  "Back-loaded",
  steady:       "Steady",
};

const EFFORT_LABELS = {
  generous_curve: "Generous curve",
  weeder:         "Weeder",
  standard:       "Standard grading",
  unknown:        "Unknown",
};

function makeChip(text, fullText = null) {
  const chip = document.createElement("div");
  chip.className   = "chip";
  chip.textContent = text;
  if (fullText) chip.dataset.full = fullText;
  return chip;
}

function renderCard(data, courseCode) {
  clearResults();
  setStatus("");

  const card = document.createElement("div");
  card.className = "card";

  // Header
  const header = document.createElement("div");
  header.className = "card-header";
  const h2 = document.createElement("h2");
  h2.textContent = courseCode;
  const prof = document.createElement("div");
  prof.className   = "professor";
  prof.textContent = "Dr. Alex Chen";
  header.appendChild(h2);
  header.appendChild(prof);
  card.appendChild(header);

  // Chips
  const chips = document.createElement("div");
  chips.className = "chips";

  if (data.difficulty_profile) {
    const full      = data.difficulty_profile;
    const display   = "Difficulty: " + full;
    const truncated = display.length > 60 ? display.slice(0, 57) + "…" : display;
    chips.appendChild(makeChip(truncated, display.length > 60 ? full : null));
  }
  if (data.workload_shape && WORKLOAD_LABELS[data.workload_shape]) {
    chips.appendChild(makeChip(WORKLOAD_LABELS[data.workload_shape]));
  }
  if (data.effort_to_grade) {
    chips.appendChild(makeChip(EFFORT_LABELS[data.effort_to_grade] ?? data.effort_to_grade));
  }

  if (chips.children.length) card.appendChild(chips);

  // Summary
  const summary = document.createElement("p");
  summary.className   = "summary";
  summary.textContent = data.summary;
  card.appendChild(summary);

  // Advice boxes
  if (data.take_if || data.skip_if) {
    const row = document.createElement("div");
    row.className = "advice-row";

    if (data.take_if) {
      const box   = document.createElement("div");
      box.className = "advice-box advice-take";
      const label = document.createElement("div");
      label.className   = "advice-label";
      label.textContent = "Take if";
      const text  = document.createElement("p");
      text.textContent  = data.take_if;
      box.appendChild(label);
      box.appendChild(text);
      row.appendChild(box);
    }

    if (data.skip_if) {
      const box   = document.createElement("div");
      box.className = "advice-box advice-skip";
      const label = document.createElement("div");
      label.className   = "advice-label";
      label.textContent = "Skip if";
      const text  = document.createElement("p");
      text.textContent  = data.skip_if;
      box.appendChild(label);
      box.appendChild(text);
      row.appendChild(box);
    }

    card.appendChild(row);
  }

  // Hidden prerequisites
  if (data.hidden_prerequisites) {
    const callout = document.createElement("div");
    callout.className = "prereq-callout";
    const bold = document.createElement("strong");
    bold.textContent = "Heads up: ";
    callout.appendChild(bold);
    callout.appendChild(document.createTextNode(data.hidden_prerequisites));
    card.appendChild(callout);
  }

  // Meta footer
  const meta = document.createElement("div");
  meta.className   = "card-meta";
  meta.textContent = `Confidence: ${data.confidence} · Based on ${data.sample_size} sources`;
  card.appendChild(meta);

  resultsEl.appendChild(card);
}

// ── Core flow ─────────────────────────────────────────────────

async function fetchInsights(school, code) {
  setStatus("Loading…");
  clearResults();

  try {
    const data = await apiFetch(`/course/${school}/${encodeURIComponent(code)}`);

    if (data.status === "ambiguous") {
      renderCandidates(data.candidates, school);
      return;
    }

    renderCard(data, code);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function handleSubmit(e) {
  e.preventDefault();

  const school = schoolSelect.value;
  const raw    = courseInput.value.trim();

  if (!school || !raw) return;

  clearResults();

  if (looksCanonical(raw)) {
    await fetchInsights(school, raw);
    return;
  }

  setStatus("Loading…");

  try {
    const resolved = await apiFetch("/resolve", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ school, input: raw }),
    });

    if (resolved.status === "matched") {
      await fetchInsights(school, resolved.code);
    } else if (resolved.status === "ambiguous") {
      setStatus("");
      renderCandidates(resolved.candidates, school);
    } else {
      setStatus(
        "No matching course found. Try entering the course code directly (e.g., ITCS 1213).",
        true
      );
    }
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ── Init ─────────────────────────────────────────────────────

searchForm.addEventListener("submit", handleSubmit);
loadSchools();
