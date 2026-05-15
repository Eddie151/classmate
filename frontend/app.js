const API_BASE = "https://classmate-m8aj.onrender.com";

// ── DOM refs ──────────────────────────────────────────────────
const stepSchool       = document.getElementById("step-school");
const stepCourse       = document.getElementById("step-course");
const schoolCardsEl    = document.getElementById("school-cards");
const backBtn          = document.getElementById("back-btn");
const selectedNameEl   = document.getElementById("selected-school-name");
const searchForm       = document.getElementById("search-form");
const courseInput      = document.getElementById("course-input");
const suggestionsEl    = document.getElementById("suggestions");
const statusEl         = document.getElementById("status");
const resultsEl        = document.getElementById("results");
const coverageNoticeEl = document.getElementById("coverage-notice");
const browseSectionEl  = document.getElementById("browse-section");
const browseGroupsEl   = document.getElementById("browse-groups");

// ── State ─────────────────────────────────────────────────────
let selectedSchool = null;   // {slug, display_name, primary_color}
let courseCatalog  = [];     // [{code, title}, ...]
let activeSuggIdx  = -1;

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

// ── Step management ───────────────────────────────────────────

function showStep1() {
  stepSchool.hidden        = false;
  stepCourse.hidden        = true;
  selectedSchool           = null;
  courseCatalog            = [];
  courseInput.value        = "";
  coverageNoticeEl.hidden  = true;
  coverageNoticeEl.textContent = "";
  browseSectionEl.hidden   = true;
  browseGroupsEl.innerHTML = "";
  hideSuggestions();
  clearResults();
  setStatus("");
}

const _THIN_COVERAGE_NOTICES = {
  ncsu: "NC State support currently covers CS and ECE courses only. More departments coming.",
};

function showStep2(school) {
  selectedSchool             = school;
  selectedNameEl.textContent = school.display_name;
  stepSchool.hidden          = true;
  stepCourse.hidden          = false;
  courseInput.focus();

  const notice = _THIN_COVERAGE_NOTICES[school.slug];
  if (notice) {
    coverageNoticeEl.textContent = notice;
    coverageNoticeEl.hidden      = false;
  } else {
    coverageNoticeEl.hidden = true;
  }

  loadCourseCatalog(school.slug);
  loadBrowseSection(school.slug);
}

// ── School cards ──────────────────────────────────────────────

async function loadSchools() {
  try {
    const schools = await apiFetch("/schools");
    schools.forEach(s => {
      const card = document.createElement("button");
      card.className = "school-card";
      card.type      = "button";
      card.style.setProperty("--accent", s.primary_color);

      const name = document.createElement("span");
      name.className   = "school-card-name";
      name.textContent = s.display_name;

      card.appendChild(name);
      card.addEventListener("click", () => showStep2(s));
      schoolCardsEl.appendChild(card);
    });
  } catch (err) {
    schoolCardsEl.textContent = err.message;
    schoolCardsEl.style.color = "#c0392b";
  }
}

// ── Autocomplete ──────────────────────────────────────────────

async function loadCourseCatalog(slug) {
  try {
    courseCatalog = await apiFetch(`/courses/${slug}`);
  } catch {
    courseCatalog = [];
  }
}

async function loadBrowseSection(slug) {
  browseSectionEl.hidden   = true;
  browseGroupsEl.innerHTML = "";

  let data;
  try {
    data = await apiFetch(`/supported_courses/${slug}`);
  } catch {
    return;
  }

  if (!data.groups || !data.groups.length) return;

  data.groups.forEach(group => {
    const dept = document.createElement("details");
    dept.className = "browse-dept";

    const summary = document.createElement("summary");
    summary.className = "browse-dept-header";

    const label = document.createElement("span");
    label.className   = "browse-dept-label";
    label.textContent = group.prefix;

    const count = document.createElement("span");
    count.className   = "browse-dept-count";
    count.textContent = `${group.courses.length} course${group.courses.length !== 1 ? "s" : ""}`;

    summary.appendChild(label);
    summary.appendChild(count);
    dept.appendChild(summary);

    const courseRow = document.createElement("div");
    courseRow.className = "browse-courses";

    group.courses.forEach(c => {
      const btn = document.createElement("button");
      btn.className = "browse-course";
      btn.type      = "button";
      btn.title     = c.title;
      btn.textContent = c.code;
      btn.addEventListener("click", () => {
        courseInput.value = c.code;
        fetchInsights(slug, c.code);
        browseSectionEl.scrollIntoView({ behavior: "smooth", block: "start" });
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
      courseRow.appendChild(btn);
    });

    dept.appendChild(courseRow);
    browseGroupsEl.appendChild(dept);
  });

  browseSectionEl.hidden = false;
}

function getMatches(query) {
  if (!query) return [];
  const q = query.toLowerCase().trim();
  const scored = [];

  for (const c of courseCatalog) {
    const code = c.code.toLowerCase();
    const codeNoSpace = code.replace(/\s+/g, '');
    const title = c.title.toLowerCase();
    const qNoSpace = q.replace(/\s+/g, '');

    let score = 0;
    if (code === q || codeNoSpace === qNoSpace) score = 1000;
    else if (code.startsWith(q) || codeNoSpace.startsWith(qNoSpace)) score = 500;
    else if (code.includes(q) || codeNoSpace.includes(qNoSpace)) score = 200;
    else if (title.startsWith(q)) score = 100;
    else if (title.includes(' ' + q)) score = 50;
    else if (title.includes(q)) score = 10;

    if (score > 0) scored.push({ course: c, score });
  }

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, 8).map(s => s.course);
}

function showSuggestions(matches) {
  suggestionsEl.innerHTML = "";
  activeSuggIdx = -1;

  if (!matches.length) {
    suggestionsEl.hidden = true;
    return;
  }

  matches.forEach(c => {
    const li = document.createElement("li");
    li.className = "suggestion-item";
    li.setAttribute("role", "option");

    const code = document.createElement("strong");
    code.textContent = c.code;
    const title = document.createElement("span");
    title.textContent = c.title;

    li.appendChild(code);
    li.appendChild(title);
    li.addEventListener("mousedown", e => {
      e.preventDefault();
      pickSuggestion(c);
    });
    suggestionsEl.appendChild(li);
  });

  suggestionsEl.hidden = false;
}

function hideSuggestions() {
  suggestionsEl.hidden = true;
  activeSuggIdx = -1;
}

function pickSuggestion(course) {
  courseInput.value = course.code;
  hideSuggestions();
  fetchInsights(selectedSchool.slug, course.code);
}

function updateActiveItem() {
  const items = suggestionsEl.querySelectorAll(".suggestion-item");
  items.forEach((item, i) => item.classList.toggle("suggestion-active", i === activeSuggIdx));
}

courseInput.addEventListener("input", () => {
  showSuggestions(getMatches(courseInput.value.trim()));
});

courseInput.addEventListener("keydown", e => {
  const items = suggestionsEl.querySelectorAll(".suggestion-item");
  if (!items.length) return;

  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeSuggIdx = Math.min(activeSuggIdx + 1, items.length - 1);
    updateActiveItem();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeSuggIdx = Math.max(activeSuggIdx - 1, -1);
    updateActiveItem();
  } else if (e.key === "Enter" && activeSuggIdx >= 0) {
    e.preventDefault();
    const matches = getMatches(courseInput.value.trim());
    if (matches[activeSuggIdx]) pickSuggestion(matches[activeSuggIdx]);
  } else if (e.key === "Escape") {
    hideSuggestions();
  }
});

courseInput.addEventListener("blur", () => {
  setTimeout(hideSuggestions, 150);
});

// ── Canonical code check ──────────────────────────────────────

function looksCanonical(input) {
  return /^[A-Za-z]+\s*\d+$/.test(input.trim());
}

// ── Render: unsupported course ────────────────────────────────

function renderUnsupported(data, school) {
  clearResults();
  setStatus("");

  const card = document.createElement("div");
  card.className = "unsupported-card";

  const msg = document.createElement("p");
  msg.textContent = data.message;
  card.appendChild(msg);

  if (data.suggestions && data.suggestions.length) {
    const sugBox = document.createElement("div");
    sugBox.className = "unsupported-suggestions";

    const label = document.createElement("p");
    label.textContent = "Similar courses we do cover:";
    sugBox.appendChild(label);

    data.suggestions.forEach(s => {
      const btn = document.createElement("button");
      btn.className = "suggestion-chip";
      btn.type      = "button";

      const strong = document.createElement("strong");
      strong.textContent = s.code;

      const span = document.createElement("span");
      span.textContent = s.title;

      btn.appendChild(strong);
      btn.appendChild(span);
      btn.addEventListener("click", () => {
        courseInput.value = s.code;
        fetchInsights(school, s.code);
      });
      sugBox.appendChild(btn);
    });

    card.appendChild(sugBox);
  }

  resultsEl.appendChild(card);
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

function renderCard(data, courseCode, professorName = null, rating = null, numRatings = null) {
  const card = document.createElement("div");
  card.className = "card";

  // Header
  const header = document.createElement("div");
  header.className = "card-header";
  const h2 = document.createElement("h2");
  h2.textContent = courseCode;
  const prof = document.createElement("div");
  prof.className = "professor";
  let profLabel = professorName ?? "Dr. Alex Chen";
  if (rating !== null) profLabel += ` · ${rating}★ (${numRatings} ratings)`;
  prof.textContent = profLabel;
  header.appendChild(h2);
  header.appendChild(prof);
  card.appendChild(header);

  if (!data) {
    const unavailable = document.createElement("p");
    unavailable.className   = "summary insights-unavailable";
    unavailable.textContent = "Insights temporarily unavailable. Check back shortly.";
    card.appendChild(unavailable);
    resultsEl.appendChild(card);
    return;
  }

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

    if (data.source === "unsupported") {
      renderUnsupported(data, school);
      return;
    }

    clearResults();
    setStatus("");

    if (data.professors) {
      if (data.source === "no_data") {
        const card = document.createElement("div");
        card.className   = "no-data-card";
        card.textContent = data.message;
        resultsEl.appendChild(card);
      } else {
        data.professors.forEach(prof =>
          renderCard(prof.insights, code, prof.name, prof.rating, prof.num_ratings)
        );
      }
    } else {
      renderCard(data, code, "Dr. Alex Chen (mock data)", null, null);
    }
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function handleSubmit(e) {
  e.preventDefault();
  hideSuggestions();

  const raw = courseInput.value.trim();
  if (!raw || !selectedSchool) return;

  clearResults();

  if (looksCanonical(raw)) {
    await fetchInsights(selectedSchool.slug, raw);
    return;
  }

  setStatus("Loading…");

  try {
    const resolved = await apiFetch("/resolve", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ school: selectedSchool.slug, input: raw }),
    });

    if (resolved.status === "matched") {
      await fetchInsights(selectedSchool.slug, resolved.code);
    } else if (resolved.status === "ambiguous") {
      setStatus("");
      renderCandidates(resolved.candidates, selectedSchool.slug);
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

backBtn.addEventListener("click", showStep1);
searchForm.addEventListener("submit", handleSubmit);
loadSchools();
