/* ViveEnglish — single-page front end (vanilla JS, no build step). */
"use strict";

const API = "/api";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  lessons: [], progress: {}, themes: [], profile: null,
  artStyles: null, activeTheme: "all", ai: { online: false },
};

// ---------- helpers ----------
async function api(path, opts) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body) });

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.add("hidden"), 2200);
}
function esc(s) { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; }

// --- voices / tutor character ---
let _voices = [];
function loadVoices() { try { _voices = speechSynthesis.getVoices() || []; } catch { _voices = []; } }
if (typeof speechSynthesis !== "undefined") {
  loadVoices();
  speechSynthesis.onvoiceschanged = loadVoices;
}
const FEMALE_NAMES = ["zira", "hazel", "susan", "samantha", "victoria", "catherine",
  "linda", "heera", "eva", "aria", "jenny", "michelle", "sonia", "libby", "natasha",
  "clara", "amber", "woman", "girl", "kvshasini", "swara"];
const MALE_NAMES = ["david", "mark", "george", "daniel", "james", "ryan", "guy", "eric",
  "brandon", "alex", "fred", "oliver", "thomas", "william", "christopher", "liam",
  "ravi", "prabhat", "man", "boy"];
function pickVoice(gender) {
  const en = _voices.filter(v => /^en/i.test(v.lang));
  let pool = en.length ? en : _voices;
  if (!pool.length) return null;
  // Privacy/offline first: prefer on-device voices (localService === true).
  // Some browser voices (e.g. Google/Edge "Online") stream to the cloud — avoid
  // them unless no local voice exists at all.
  const local = pool.filter(v => v.localService === true);
  if (local.length) pool = local;
  const fem = n => FEMALE_NAMES.some(h => n.includes(h)) || n.includes("female");
  const mal = n => MALE_NAMES.some(h => n.includes(h)) || (n.includes("male") && !n.includes("female"));
  const want = gender === "male" ? mal : fem;
  const avoid = gender === "male" ? fem : mal;
  return pool.find(v => want(v.name.toLowerCase()))
    || pool.find(v => !avoid(v.name.toLowerCase()))
    || pool[0];
}
function speak(text, gender) {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US"; u.rate = 0.92;
    if (gender) {
      const v = pickVoice(gender);
      if (v) u.voice = v;
      u.pitch = gender === "male" ? 0.8 : 1.2;   // reinforce perceived gender
    } else {
      const en = _voices.filter(v => v.lang && v.lang.startsWith("en"));
      const v = en.find(v => v.localService === true) || en[0];
      if (v) u.voice = v;
    }
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  } catch (e) { /* TTS unsupported */ }
}

const TUTORS = {
  female: { name: "Vivi", avatar: "/characters/vivi.svg", gender: "female", label: "Vivi（女性）" },
  male: { name: "Theo", avatar: "/characters/theo.svg", gender: "male", label: "Theo（男性）" },
};
function tutor() { return TUTORS[state.profile && state.profile.tutor_gender] || TUTORS.female; }

function setAiBadge() {
  const el = $("#aiStatus");
  el.classList.toggle("online", !!state.ai.online);
  el.classList.toggle("offline", !state.ai.online);
  $(".ai-label", el).textContent = state.ai.online ? "AI接続中" : "AIオフライン";
  el.title = state.ai.online
    ? `Foundry Local 接続中 (${state.ai.model || ""})`
    : "Foundry Local 未接続 — 事前収録コンテンツで学習できます。クリックで再接続";
}
$("#aiStatus").addEventListener("click", async () => {
  toast("AIへ再接続中…");
  try { state.ai = await post("/ai/reconnect", {}); setAiBadge();
    toast(state.ai.online ? "AIに接続しました" : "AIに接続できませんでした"); }
  catch { toast("再接続に失敗しました"); }
});

// ---------- router ----------
const routes = {};
function nav(name, arg) {
  location.hash = arg ? `#${name}/${arg}` : `#${name}`;
}
function renderRoute() {
  const [name, arg] = location.hash.replace(/^#/, "").split("/");
  const r = routes[name] || routes.home;
  $$(".navbtn").forEach(b => b.classList.toggle("active", b.dataset.nav === (name || "home")));
  window.scrollTo(0, 0);
  r(arg);
}
window.addEventListener("hashchange", renderRoute);
$$("[data-nav]").forEach(b => b.addEventListener("click", () => nav(b.dataset.nav)));

// ---------- HOME ----------
routes.home = () => {
  const done = Object.values(state.progress).filter(p => p.status === "completed").length;
  const total = state.lessons.length;
  const streak = computeStreak(state.activity || []);
  const savedN = (state.savedWords || []).length;

  $("#app").innerHTML = `
  <section class="card hero">
    <div>
      <h1>英語が、身近になる。</h1>
      <p>学校・旅行・ビジネス・食事・暮らし・日本文化。身近なテーマの短いレッスンで、
      読む・聞く・話すをAIと一緒に練習します。わからない単語はタップするだけで和訳。</p>
      <div style="display:flex;gap:.7rem;flex-wrap:wrap;margin-top:1rem">
        <button class="btn" onclick="location.hash='#lessons'">学習を始める</button>
        <button class="btn ghost" onclick="location.hash='#progress'">進捗を見る</button>
      </div>
    </div>
    <div class="hero-art">🎨 やさしい挿絵と<br>AI対話で学ぶ</div>
  </section>

  <div class="stat-row">
    <div class="card stat"><div class="v">${done}/${total}</div><div class="l">完了レッスン</div></div>
    <div class="card stat"><div class="v">${streak}<span style="font-size:1rem">日</span></div><div class="l">連続学習</div></div>
    <div class="card stat"><div class="v">${savedN}</div><div class="l">単語帳</div></div>
    <div class="card stat"><div class="v">${state.themes.length}</div><div class="l">テーマ</div></div>
  </div>

  <h2>学習メソッド（3つの柱）</h2>
  <p class="sub">「日本にいながら英語が話せるようになった人がみんなやっていること」を、各レッスンに落とし込みました。</p>
  <div class="pillars">
    <div class="card pillar"><div class="n">① 基礎固め</div><h3>語彙・フレーズ</h3>
      <p>自分が使う場面の単語と言い回しを、例文ごと音読して身につけます。</p></div>
    <div class="card pillar"><div class="n">② 大量インプット</div><h3>読む・聞く</h3>
      <p>身近なテーマの会話を、単語タップ和訳と音声で繰り返しインプット。</p></div>
    <div class="card pillar"><div class="n">③ 話すトレーニング</div><h3>AI対話・発話</h3>
      <p>AI相手のロールプレイと発話チェックで、覚えた表現を実際に使います。</p></div>
  </div>

  <h2>続きから / おすすめ</h2>
  <div class="lesson-grid" id="homeLessons"></div>`;

  const pick = pickRecommended();
  $("#homeLessons").innerHTML = pick.map(lessonCard).join("");
  bindLessonCards();
};

function pickRecommended() {
  const inProg = state.lessons.filter(l => state.progress[l.id]?.status === "in_progress");
  const notStarted = state.lessons.filter(l => !state.progress[l.id] || state.progress[l.id].status === "not_started");
  return [...inProg, ...notStarted].slice(0, 3);
}

// ---------- LESSONS ----------
routes.lessons = () => {
  const chips = [`<button class="chip ${state.activeTheme === "all" ? "active" : ""}" data-th="all">すべて</button>`]
    .concat(state.themes.map(t => `<button class="chip ${state.activeTheme === t.theme ? "active" : ""}" data-th="${esc(t.theme)}">${esc(t.theme_ja)}</button>`))
    .join("");
  $("#app").innerHTML = `
    <h1>レッスン</h1>
    <p class="sub">1レッスンは5〜15分。気になるテーマから始めましょう。</p>
    <div class="filter-bar">${chips}</div>
    <div class="lesson-grid" id="grid"></div>`;
  $$(".chip").forEach(c => c.addEventListener("click", () => { state.activeTheme = c.dataset.th; routes.lessons(); }));
  const list = state.activeTheme === "all" ? state.lessons : state.lessons.filter(l => l.theme === state.activeTheme);
  $("#grid").innerHTML = list.map(lessonCard).join("");
  bindLessonCards();
};

const THEME_EMOJI = { School: "🏫", Travel: "✈️", Business: "💼", Food: "🍜", Lifestyle: "🌿", "Japanese Culture": "⛩️", "Grammar Basics": "🔤", "Grade English": "🎓" };

function lessonCard(l) {
  const p = state.progress[l.id];
  const pct = p?.status === "completed" ? 100 : p?.status === "in_progress" ? 50 : 0;
  const mark = p?.status === "completed" ? `<span class="done-mark">✓ 完了</span>` : "";
  const art = l.image || `/illustrations/${l.id}.svg`;
  return `
  <div class="card lesson-card" data-id="${l.id}">
    <div class="lc-art"><img src="${art}" alt="${esc(l.title_en)}" loading="lazy"
         onerror="this.parentElement.classList.add('noimg');this.parentElement.textContent='${THEME_EMOJI[l.theme] || "📘"}'" /></div>
    <div class="lc-body">
      <div class="lc-theme">${esc(l.theme_ja)}</div>
      <div class="lc-title">${esc(l.title_en)}</div>
      <div class="lc-title-ja">${esc(l.title_ja)}</div>
      <div class="lc-meta">
        <span class="badge ${l.level}">${levelJa(l.level)}</span>
        <span>⏱ ${l.est_minutes}分</span>
        <span class="progress-pill">${mark}</span>
      </div>
      <div class="bar" style="margin-top:.6rem"><span style="width:${pct}%"></span></div>
    </div>
  </div>`;
}
const levelJa = lv => ({ beginner: "入門", elementary: "初級", intermediate: "中級" }[lv] || lv);
function bindLessonCards() {
  $$(".lesson-card").forEach(c => c.addEventListener("click", () => nav("lesson", c.dataset.id)));
}

// ---------- LESSON DETAIL ----------
routes.lesson = async (id) => {
  let L;
  try { L = await api(`/lessons/${id}`); } catch { $("#app").innerHTML = "<p>レッスンが見つかりません。</p>"; return; }
  state.current = L;
  if (!state.progress[id] || state.progress[id].status === "not_started") {
    post("/progress", { lesson_id: id, status: "in_progress" }).then(r => state.progress[id] = r).catch(()=>{});
  }
  $("#app").innerHTML = `
  <div class="card lesson-head">
    <div class="crumbs" onclick="location.hash='#lessons'">← レッスン一覧</div>
    <h1>${esc(L.title_en)}</h1>
    <p class="sub" style="margin:.2rem 0">${esc(L.title_ja)} ・ <span class="badge ${L.level}">${levelJa(L.level)}</span> ・ ⏱ ${L.est_minutes}分 ・ ${esc(L.theme_ja)}</p>
    <p style="margin:.4rem 0 0">${esc(L.summary_ja)}</p>
  </div>
  <div class="tabs">
    <button class="tab active" data-p="learn">① 語彙・フレーズ</button>
    <button class="tab" data-p="read">② 読む・聞く</button>
    <button class="tab" data-p="speak">③ 発話チェック</button>
    <button class="tab" data-p="chat">AI対話</button>
    <button class="tab" data-p="quiz">クイズ</button>
    <button class="tab" data-p="art">挿絵</button>
  </div>
  <div id="panels"></div>`;
  const panels = $("#panels");
  panels.innerHTML = panelLearn(L) + panelRead(L) + panelSpeak(L) + panelChat(L) + panelQuiz(L) + panelArt(L);
  $$(".tab").forEach(t => t.addEventListener("click", () => {
    $$(".tab").forEach(x => x.classList.remove("active")); t.classList.add("active");
    $$(".panel").forEach(p => p.classList.toggle("active", p.dataset.panel === t.dataset.p));
  }));
  initInteractions(L);
};

function panelLearn(L) {
  const grammar = (L.grammar_points || []).map(g => `
    <div class="grammar-item">
      <div class="grammar-title">${esc(g.title_ja)}</div>
      <div class="grammar-pattern">${esc(g.pattern)}</div>
      <div class="grammar-note">${esc(g.explanation_ja)}</div>
      <ul>${(g.examples || []).map(ex => `<li>${esc(ex)}</li>`).join("")}</ul>
    </div>`).join("");
  const vocab = L.vocab.map(v => `
    <div class="vocab-item">
      <div class="vocab-top">
        <span class="vocab-en">${esc(v.en)}</span>
        <button class="spk" data-say="${esc(v.en)}">🔊</button>
        <span class="vocab-ja">${esc(v.ja)}</span>
      </div>
      <div class="vocab-ex">${esc(v.example_en)} <button class="spk" data-say="${esc(v.example_en)}">🔊</button><br><span class="ja">${esc(v.example_ja)}</span></div>
    </div>`).join("");
  const phrases = L.phrases.map(p => `
    <div class="phrase">
      <span class="en">${esc(p.en)}</span> <button class="spk" data-say="${esc(p.en)}">🔊</button>
      <div class="ja">${esc(p.ja)}</div>
      <div class="when">📍 ${esc(p.when_ja)}</div>
    </div>`).join("");
  return `<div class="panel active" data-panel="learn">
    <div class="hint">💡 ${esc(L.warmup_ja)}</div>
    ${grammar ? `<h2>文法ポイント</h2><div class="grammar-grid">${grammar}</div>` : ""}
    <h2>キーボキャブラリー</h2>${vocab}
    <h2>使えるフレーズ（音読推奨）</h2>${phrases}
  </div>`;
}

function panelRead(L) {
  const lines = L.dialogue.map((d, i) => `
    <div class="line" data-i="${i}">
      <div class="who">${esc(d.speaker)}</div>
      <div class="bubble">
        <div class="en-text">${tokenize(d.text)}</div>
        <div class="ja-text hidden">${esc(d.ja)}</div>
      </div>
      <button class="lspk" data-say="${esc(d.text)}">🔊</button>
    </div>`).join("");
  return `<div class="panel" data-panel="read">
    <div class="hint">💡 英単語を<b>タップ</b>すると和訳が出ます。文を<b>選択（ドラッグ）</b>すると範囲ごと和訳。🔊で発音、まずは真似して音読しましょう。</div>
    <div class="dlg-controls">
      <button class="btn sm accent" id="playAll">🔊 全文を続けて再生</button>
      <button class="btn sm ghost" id="toggleJa">和訳をすべて表示/非表示</button>
    </div>
    ${lines}
  </div>`;
}

function panelSpeak(L) {
  const items = L.speaking_lines.map((s, i) => `
    <div class="speak-line" data-target="${esc(s)}" data-i="${i}">
      <div class="speak-target">${esc(s)} <button class="spk" data-say="${esc(s)}">🔊</button></div>
      <div class="speak-row">
        <button class="btn sm rec" data-rec="${i}">🎤 録音して発話</button>
        <input class="said-input" placeholder="または、言った内容を入力" />
        <button class="btn sm" data-check="${i}">チェック</button>
      </div>
      <div class="feedback hidden" data-fb="${i}"></div>
    </div>`).join("");
  return `<div class="panel" data-panel="speak">
    <div class="hint">💡 お手本を聞いて、声に出して言ってみましょう。マイク録音（要Foundry Local音声認識）または入力でAIが発音・正確さをチェックします。</div>
    ${items}
  </div>`;
}

function panelChat(L) {
  const t = tutor();
  return `<div class="panel" data-panel="chat">
    <div class="hint">💡 AIの「${esc(t.name)}」とロールプレイ。状況：<b>${esc(L.roleplay.scenario_ja)}</b><br>英語で返信すると、やさしい訂正と和訳がもらえます。<span class="muted">（講師は設定で変更できます）</span></div>
    <div class="chat-box">
      <div class="chat-log" id="chatLog"></div>
      <div class="chat-input">
        <input id="chatText" placeholder="英語で入力してEnter…" autocomplete="off" />
        <button class="btn" id="chatSend">送信</button>
      </div>
    </div>
  </div>`;
}

function panelQuiz(L) {
  const qs = L.quiz.map((q, qi) => `
    <div class="quiz-q" data-qi="${qi}" data-ans="${q.answer}">
      <div class="q">Q${qi + 1}. ${esc(q.q_ja)}</div>
      ${q.options.map((o, oi) => `<button class="opt" data-oi="${oi}">${esc(o)}<span class="ex hidden">${esc(q.explain_ja)}</span></button>`).join("")}
    </div>`).join("");
  return `<div class="panel" data-panel="quiz">
    <div class="hint">💡 全問終わると結果が記録され、レッスンが完了になります。</div>
    ${qs}
    <button class="btn" id="quizSubmit">採点する</button>
    <div id="quizResult" style="margin-top:1rem"></div>
  </div>`;
}

function panelArt(L) {
  return `<div class="panel" data-panel="art">
    <div class="hint">💡 このレッスンの挿絵を生成するためのプロンプトです。テイストは設定画面の「挿絵スタイル」が全レッスンに反映されます。</div>
    <div class="card illus-card" id="illusCard">読み込み中…</div>
  </div>`;
}

// ---------- interactions within a lesson ----------
function initInteractions(L) {
  // speak buttons (TTS)
  $("#panels").addEventListener("click", e => {
    const b = e.target.closest("[data-say]");
    if (b) { speak(b.dataset.say); }
  });

  // read panel controls
  $("#toggleJa").addEventListener("click", () => {
    const any = $$("#panels .ja-text.hidden").length > 0;
    $$("#panels .ja-text").forEach(j => j.classList.toggle("hidden", !any));
  });
  $("#playAll").addEventListener("click", () => playSequential(L.dialogue.map(d => d.text)));

  // word tap translation
  $$("#panels .en-text").forEach(box => box.addEventListener("click", e => {
    const w = e.target.closest(".w"); if (!w) return;
    e.stopPropagation();
    showGloss(w, cleanWord(w.textContent), "word", L.id);
  }));

  // selection translate
  setupSelectionTranslate(L.id);

  // speaking checks
  $$("#panels [data-rec]").forEach(btn => btn.addEventListener("click", () => toggleRecord(btn, L)));
  $$("#panels [data-check]").forEach(btn => btn.addEventListener("click", () => doSpeechCheck(btn, L)));

  // chat
  initChat(L);

  // quiz
  initQuiz(L);

  // illustration
  loadIllustration(L.id);
}

function tokenize(text) {
  // wrap word tokens in clickable spans, keep punctuation/space as-is
  return text.split(/(\s+)/).map(tok => {
    if (/^\s+$/.test(tok)) return tok;
    return `<span class="w">${esc(tok)}</span>`;
  }).join("");
}
const cleanWord = w => w.replace(/^[^A-Za-z']+|[^A-Za-z']+$/g, "");

async function playSequential(texts) {
  for (const t of texts) {
    await new Promise(res => {
      const u = new SpeechSynthesisUtterance(t); u.lang = "en-US"; u.rate = 0.92;
      u.onend = res; u.onerror = res; speechSynthesis.speak(u);
    });
  }
}

// ---------- gloss popover ----------
const pop = $("#glossPop");
let popCtx = { word: "", meaning: "", lesson: null };
$("#glossClose").addEventListener("click", () => pop.classList.add("hidden"));
$("#glossSpeak").addEventListener("click", () => speak(popCtx.word));
$("#glossSave").addEventListener("click", async () => {
  if (!popCtx.word) return;
  await post("/words", { word: popCtx.word, meaning: popCtx.meaning, lesson_id: popCtx.lesson });
  toast("単語帳に保存しました ★");
});
document.addEventListener("click", e => {
  if (!pop.contains(e.target) && !e.target.closest(".w")) pop.classList.add("hidden");
});

async function showGloss(anchor, text, mode, lessonId) {
  if (!text) return;
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.min(window.scrollX + r.left, window.scrollX + window.innerWidth - 270) + "px";
  pop.style.top = (window.scrollY + r.bottom + 6) + "px";
  $("#glossWord").textContent = text;
  $("#glossBody").innerHTML = "<span style='color:#aaa'>翻訳中…</span>";
  pop.classList.remove("hidden");
  popCtx = { word: text, meaning: "", lesson: lessonId };
  try {
    const t = await post("/translate", { text, mode, lesson_id: lessonId });
    popCtx.meaning = t.translation;
    $("#glossBody").innerHTML =
      `${t.pos ? `<span class="pos">${esc(t.pos)}</span> ` : ""}${esc(t.translation)}`
      + (t.note ? `<div class="note">${esc(t.note)}</div>` : "")
      + (t.offline_fallback ? `<div class="note">（AIオフライン：レッスン語彙から表示）</div>` : "");
  } catch { $("#glossBody").textContent = "翻訳に失敗しました。"; }
}

function setupSelectionTranslate(lessonId) {
  const btn = $("#selXlate");
  document.addEventListener("mouseup", () => {
    const sel = window.getSelection();
    const text = sel.toString().trim();
    const within = sel.anchorNode && sel.anchorNode.parentElement && sel.anchorNode.parentElement.closest(".en-text");
    if (text && text.split(/\s+/).length > 1 && within) {
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      btn.style.left = (window.scrollX + rect.left) + "px";
      btn.style.top = (window.scrollY + rect.top - 40) + "px";
      btn.classList.remove("hidden");
      btn.onclick = () => {
        const anchorSpan = within;
        showGlossAtRect(rect, text, "sentence", lessonId);
        btn.classList.add("hidden");
      };
    } else { btn.classList.add("hidden"); }
  });
}
async function showGlossAtRect(rect, text, mode, lessonId) {
  pop.style.left = Math.min(window.scrollX + rect.left, window.scrollX + window.innerWidth - 270) + "px";
  pop.style.top = (window.scrollY + rect.bottom + 6) + "px";
  $("#glossWord").textContent = text.length > 40 ? text.slice(0, 38) + "…" : text;
  $("#glossBody").innerHTML = "<span style='color:#aaa'>翻訳中…</span>";
  pop.classList.remove("hidden");
  popCtx = { word: text, meaning: "", lesson: lessonId };
  try {
    const t = await post("/translate", { text, mode, lesson_id: lessonId });
    popCtx.meaning = t.translation;
    $("#glossBody").innerHTML = esc(t.translation) + (t.note ? `<div class="note">${esc(t.note)}</div>` : "");
  } catch { $("#glossBody").textContent = "翻訳に失敗しました。"; }
}

// ---------- speaking ----------
let mediaRec = null, recChunks = [], recBtn = null;
async function toggleRecord(btn, L) {
  if (mediaRec && mediaRec.state === "recording") { mediaRec.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia) { toast("録音に未対応のブラウザです。入力でチェックできます。"); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRec = new MediaRecorder(stream); recChunks = []; recBtn = btn;
    mediaRec.ondataavailable = e => recChunks.push(e.data);
    mediaRec.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      btn.classList.remove("recording"); btn.textContent = "🎤 録音して発話";
      const blob = new Blob(recChunks, { type: "audio/webm" });
      const wav = await blobToWav16k(blob);
      const i = btn.dataset.rec;
      const input = btn.closest(".speak-line").querySelector(".said-input");
      input.placeholder = "音声を認識中…";
      try {
        const fd = new FormData(); fd.append("audio", wav, "clip.wav");
        const r = await fetch(API + "/speech/transcribe", { method: "POST", body: fd });
        const j = await r.json();
        if (j.online && j.text) { input.value = j.text; doSpeechCheck(btn.closest(".speak-line").querySelector("[data-check]"), L); }
        else { input.placeholder = "聞き取った内容を入力してチェック"; toast(j.note || "音声認識オフライン"); }
      } catch { input.placeholder = "認識失敗。入力でチェックできます。"; }
    };
    mediaRec.start(); btn.classList.add("recording"); btn.textContent = "■ 停止";
  } catch { toast("マイクを使用できません。入力でチェックできます。"); }
}

async function doSpeechCheck(btn, L) {
  const line = btn.closest(".speak-line");
  const target = line.dataset.target;
  const said = line.querySelector(".said-input").value.trim();
  if (!said) { toast("言った内容を入力するか録音してください。"); return; }
  const fb = line.querySelector("[data-fb]");
  fb.classList.remove("hidden"); fb.innerHTML = "チェック中…";
  try {
    const r = await post("/speech/check", { target, said, level: state.profile.level });
    const cls = r.score >= 80 ? "score-good" : r.score >= 50 ? "score-mid" : "score-low";
    fb.innerHTML = `
      <div><span class="score ${cls}">${r.score}</span> / 100</div>
      ${r.good ? `<div>👍 ${esc(r.good)}</div>` : ""}
      ${r.improve ? `<div>🛠 ${esc(r.improve)}</div>` : ""}
      ${(r.missed_words && r.missed_words.length) ? `<div>🔁 要練習: ${r.missed_words.map(esc).join(", ")}</div>` : ""}
      ${!r.online ? `<div class="note" style="color:#9a6a3f">（AIオフライン：簡易判定）</div>` : ""}`;
  } catch { fb.innerHTML = "チェックに失敗しました。"; }
}

// ---------- chat ----------
function withName(s, name) { return (s || "").split("Vivi").join(name); }

function initChat(L) {
  const log = $("#chatLog");
  const t = tutor();
  const history = [];
  const opener = withName(L.roleplay.opener, t.name);
  const openerJa = withName(L.roleplay.opener_ja, t.name);
  addMsg(log, "vivi", opener, openerJa, "", "");
  history.push({ role: "assistant", content: opener });

  async function send() {
    const input = $("#chatText"); const text = input.value.trim(); if (!text) return;
    input.value = "";
    addMsg(log, "me", text);
    history.push({ role: "user", content: text });
    const thinking = addMsg(log, "vivi", "…");
    try {
      const r = await post("/chat", {
        messages: history.slice(-12), scenario: L.roleplay.scenario,
        level: state.profile.level, tutor_name: t.name, gender: t.gender,
      });
      thinking.remove();
      addMsg(log, "vivi", r.reply, r.reply_ja, r.correction, r.tip);
      history.push({ role: "assistant", content: r.reply });
      if (!r.online) toast("AIオフライン：Foundry Localを起動すると対話できます");
    } catch { thinking.remove(); addMsg(log, "vivi", "(通信エラー)"); }
  }
  $("#chatSend").addEventListener("click", send);
  $("#chatText").addEventListener("keydown", e => { if (e.key === "Enter") send(); });
}
function addMsg(log, who, text, ja, fix, tip) {
  const t = tutor();
  const row = document.createElement("div");
  row.className = "msg-row " + (who === "me" ? "me" : "vivi");
  if (who === "vivi") {
    const av = document.createElement("img");
    av.className = "msg-avatar"; av.src = t.avatar; av.alt = t.name;
    row.appendChild(av);
  }
  const div = document.createElement("div");
  div.className = "msg " + who;
  div.innerHTML = esc(text)
    + (ja ? `<div class="mja">${esc(ja)}</div>` : "")
    + (fix ? `<div class="fix">✏️ ${esc(fix)}</div>` : "")
    + (tip ? `<div class="fix">💡 ${esc(tip)}</div>` : "");
  if (who === "vivi" && text !== "…") {
    const s = document.createElement("button"); s.className = "mini"; s.textContent = "🔊";
    s.style.marginTop = ".3rem"; s.onclick = () => speak(text, t.gender); div.appendChild(s);
  }
  row.appendChild(div);
  log.appendChild(row); log.scrollTop = log.scrollHeight; return row;
}

// ---------- quiz ----------
function initQuiz(L) {
  let answered = {};
  $$("#panels .quiz-q .opt").forEach(opt => opt.addEventListener("click", () => {
    const q = opt.closest(".quiz-q"); const qi = q.dataset.qi;
    if (answered[qi] !== undefined) return;
    const correct = +q.dataset.ans; const chosen = +opt.dataset.oi;
    answered[qi] = chosen;
    $$(".opt", q).forEach((o, oi) => {
      if (oi === correct) o.classList.add("correct");
      if (oi === chosen && chosen !== correct) o.classList.add("wrong");
    });
    $(".ex", $$(".opt", q)[correct]).classList.remove("hidden");
  }));
  $("#quizSubmit").addEventListener("click", async () => {
    const total = L.quiz.length;
    if (Object.keys(answered).length < total) { toast("すべての問題に答えてください。"); return; }
    let correct = 0;
    L.quiz.forEach((q, qi) => { if (answered[qi] === q.answer) correct++; });
    const score = Math.round(100 * correct / total);
    $("#quizResult").innerHTML = `<div class="card" style="padding:1.1rem"><b>結果：${correct}/${total} 正解（${score}点）</b><br>${score >= 80 ? "すばらしい！レッスン完了です 🎉" : "もう一度本文を音読して復習しましょう。"}</div>`;
    try {
      await post("/progress", { lesson_id: L.id, status: "completed", score, minutes: L.est_minutes });
      const updated = await api("/lessons"); state.progress = updated.progress;
      toast("進捗を記録しました");
    } catch {}
  });
}

// ---------- illustration ----------
async function loadIllustration(id) {
  try {
    const d = await api(`/lessons/${id}/illustration?style=${encodeURIComponent(state.profile.art_style)}`);
    $("#illusCard").innerHTML = `
      <div class="field-label">現在の挿絵（差し替え可能なプレースホルダー）</div>
      <img class="illus-preview" src="${d.image || ('/illustrations/' + id + '.svg')}" alt="${esc(d.caption_ja)}" />
      <div class="notice">この挿絵は仮の画像です。<code>web/illustrations/${id}.svg</code> を差し替えると、お好みの挿絵に変更できます（同名で保存）。下のプロンプトは画像生成AIで作る際の参考用です。</div>
      <div class="field-label">適用中のスタイル</div>
      <div><b>${esc(d.style_name_ja)}</b> ・ 比率 ${esc(d.aspect)} <button class="btn sm ghost" onclick="location.hash='#profile'">スタイルを変更</button></div>
      <div class="field-label">キャプション</div><div>${esc(d.caption_ja)}</div>
      <div class="field-label">画像生成プロンプト（英語）</div>
      <div class="prompt-box" id="promptBox">${esc(d.prompt)}</div>
      <button class="btn sm" id="copyPrompt" style="margin-top:.6rem">📋 プロンプトをコピー</button>
      <div class="field-label">ネガティブプロンプト</div>
      <div class="prompt-box neg-box">${esc(d.negative_prompt)}</div>`;
    $("#copyPrompt").addEventListener("click", () => {
      navigator.clipboard.writeText(d.prompt).then(() => toast("コピーしました")).catch(() => toast("コピー失敗"));
    });
  } catch { $("#illusCard").textContent = "挿絵情報の取得に失敗しました。"; }
}

// ---------- PROGRESS ----------
routes.progress = async () => {
  const data = await api("/progress");
  state.progress = data.progress; state.profile = data.profile;
  state.activity = data.activity; state.savedWords = data.saved_words;
  const done = Object.values(data.progress).filter(p => p.status === "completed");
  const total = state.lessons.length;
  const avg = done.length ? Math.round(done.reduce((s, p) => s + p.score, 0) / done.length) : 0;
  const streak = computeStreak(data.activity);

  const byTheme = state.themes.map(t => {
    const ls = state.lessons.filter(l => l.theme === t.theme);
    const c = ls.filter(l => data.progress[l.id]?.status === "completed").length;
    return `<div class="card" style="padding:1rem;margin-bottom:.6rem">
      <div style="display:flex;justify-content:space-between"><b>${esc(t.theme_ja)}</b><span>${c}/${ls.length}</span></div>
      <div class="bar" style="margin-top:.5rem"><span style="width:${Math.round(100*c/ls.length)}%"></span></div></div>`;
  }).join("");

  const words = (data.saved_words || []).map(w => `
    <div class="word-row"><span class="w">${esc(w.word)}</span><span class="m">${esc(w.meaning || "")}</span>
    <button class="mini" data-del="${esc(w.word)}">削除</button>
    <button class="mini" data-say="${esc(w.word)}">🔊</button></div>`).join("") || `<p class="sub">読書中に単語をタップ→★で保存すると、ここに単語帳が貯まります。</p>`;

  $("#app").innerHTML = `
    <h1>学習の進捗</h1>
    <p class="sub">続けることがいちばんの近道。3つの柱を並行して回しましょう。</p>
    <div class="stat-row">
      <div class="card stat"><div class="v">${done.length}/${total}</div><div class="l">完了</div></div>
      <div class="card stat"><div class="v">${streak}日</div><div class="l">連続学習</div></div>
      <div class="card stat"><div class="v">${avg}</div><div class="l">平均クイズ点</div></div>
      <div class="card stat"><div class="v">${(data.saved_words||[]).length}</div><div class="l">単語帳</div></div>
    </div>
    <h2>この60日の学習</h2>
    <div class="card" style="padding:1.2rem"><div class="heat" id="heat"></div></div>
    <h2>テーマ別の達成度</h2>${byTheme}
    <h2>マイ単語帳</h2><div class="word-list">${words}</div>`;

  renderHeat(data.activity);
  $("#app").addEventListener("click", async e => {
    const d = e.target.closest("[data-del]"); const s = e.target.closest("[data-say]");
    if (d) { await api(`/words/${encodeURIComponent(d.dataset.del)}`, { method: "DELETE" }); routes.progress(); }
    if (s) speak(s.dataset.say);
  });
};

function computeStreak(activity) {
  const days = new Set(activity.map(a => new Date(a.created_at * 1000).toDateString()));
  let streak = 0; const d = new Date();
  // allow today or yesterday to start the streak
  if (!days.has(d.toDateString())) d.setDate(d.getDate() - 1);
  while (days.has(d.toDateString())) { streak++; d.setDate(d.getDate() - 1); }
  return streak;
}
function renderHeat(activity) {
  const counts = {};
  activity.forEach(a => { const k = new Date(a.created_at * 1000).toDateString(); counts[k] = (counts[k] || 0) + 1; });
  const cells = [];
  for (let i = 59; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const n = counts[d.toDateString()] || 0;
    const lvl = n === 0 ? "" : n < 2 ? "l1" : n < 4 ? "l2" : "l3";
    cells.push(`<div class="d ${lvl}" title="${d.toLocaleDateString()} : ${n}件"></div>`);
  }
  $("#heat").innerHTML = cells.join("");
}

// ---------- PROFILE / SETTINGS ----------
routes.profile = async () => {
  const p = state.profile = await api("/profile");
  const styles = state.artStyles = state.artStyles || await api("/art-styles");
  const styleOpts = Object.entries(styles.presets).map(([k, s]) => `
    <div class="style-opt ${k === p.art_style ? "active" : ""}" data-style="${k}">
      <h4>${esc(s.name_ja)}</h4><p>${esc(s.description_ja)}</p></div>`).join("");
  $("#app").innerHTML = `
    <h1>設定</h1>
    <p class="sub">学習者プロフィールと、全レッスン共通の挿絵テイストを設定します。</p>
    <div class="form-grid">
      <div><label>表示名</label><input id="pName" value="${esc(p.display_name)}" /></div>
      <div><label>レベル</label>
        <select id="pLevel">
          <option value="beginner" ${p.level==="beginner"?"selected":""}>入門（小学生〜初学者）</option>
          <option value="elementary" ${p.level==="elementary"?"selected":""}>初級</option>
          <option value="intermediate" ${p.level==="intermediate"?"selected":""}>中級（社会人など）</option>
        </select></div>
      <div><label>1日の目標レッスン数</label><input id="pGoal" type="number" min="1" max="10" value="${p.daily_goal}" /></div>
    </div>
    <h2>AI講師（対話相手）</h2>
    <p class="sub">対話相手のキャラクターと音声（女性/男性）を選べます。読み上げの声も連動します。</p>
    <div class="tutor-grid">
      ${Object.entries(TUTORS).map(([k, t]) => `
        <div class="tutor-opt ${k === (p.tutor_gender || "female") ? "active" : ""}" data-tutor="${k}">
          <img src="${t.avatar}" alt="${esc(t.name)}" />
          <div class="tutor-name">${esc(t.label)}</div>
          <button class="mini" data-voicetest="${t.gender}">🔊 声を試す</button>
        </div>`).join("")}
    </div>
    <h2>挿絵スタイル（全レッスン共通）</h2>
    <p class="sub">${esc(styles.note_ja)}</p>
    <div class="style-grid">${styleOpts}</div>
    <div style="margin-top:1.5rem"><button class="btn" id="saveProfile">保存する</button></div>
    <h2>AI接続（Foundry Local）</h2>
    <div class="card" style="padding:1.1rem">
      <div>状態：<b>${state.ai.online ? "接続中 ✓" : "未接続"}</b> ${state.ai.model ? `（${esc(state.ai.model)}）` : ""}</div>
      <div class="sub" style="margin:.4rem 0 0">${esc(state.ai.note || "")}</div>
      <p class="sub" style="margin:.6rem 0 0">翻訳・AI対話・発話チェックはローカルのFoundry Localで動作します。未接続でも事前収録の語彙・本文・音読・クイズは利用できます。</p>
      <button class="btn sm ghost" id="reconnect" style="margin-top:.6rem">再接続を試す</button>
    </div>`;

  $$(".style-opt").forEach(o => o.addEventListener("click", () => {
    $$(".style-opt").forEach(x => x.classList.remove("active")); o.classList.add("active");
  }));
  $$(".tutor-opt").forEach(o => o.addEventListener("click", e => {
    if (e.target.closest("[data-voicetest]")) return;   // let the test button handle itself
    $$(".tutor-opt").forEach(x => x.classList.remove("active")); o.classList.add("active");
  }));
  $$("[data-voicetest]").forEach(b => b.addEventListener("click", e => {
    e.stopPropagation();
    const g = b.dataset.voicetest;
    speak(g === "male" ? "Hi, I'm Theo. Let's practice English together!"
                       : "Hi, I'm Vivi. Let's practice English together!", g);
  }));
  $("#saveProfile").addEventListener("click", async () => {
    const art = $(".style-opt.active")?.dataset.style || p.art_style;
    const tg = $(".tutor-opt.active")?.dataset.tutor || p.tutor_gender || "female";
    state.profile = await post("/profile", {
      display_name: $("#pName").value, level: $("#pLevel").value,
      daily_goal: +$("#pGoal").value, art_style: art, tutor_gender: tg,
    });
    toast("設定を保存しました");
  });
  $("#reconnect").addEventListener("click", async () => {
    state.ai = await post("/ai/reconnect", {}); setAiBadge(); routes.profile();
  });
};

// ---------- audio: webm/opus blob -> 16k mono WAV ----------
async function blobToWav16k(blob) {
  const buf = await blob.arrayBuffer();
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ac = new Ctx();
  const decoded = await ac.decodeAudioData(buf);
  const targetRate = 16000;
  const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * targetRate), targetRate);
  const src = off.createBufferSource();
  // downmix to mono
  const mono = off.createBuffer(1, decoded.length, decoded.sampleRate);
  const tmp = mono.getChannelData(0);
  for (let c = 0; c < decoded.numberOfChannels; c++) {
    const ch = decoded.getChannelData(c);
    for (let i = 0; i < ch.length; i++) tmp[i] += ch[i] / decoded.numberOfChannels;
  }
  src.buffer = mono; src.connect(off.destination); src.start();
  const rendered = await off.startRendering();
  const samples = rendered.getChannelData(0);
  return encodeWav(samples, targetRate);
}
function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const w = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  w(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE");
  w(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  w(36, "data"); view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

// ---------- first-launch AI setup ----------
let _setupPoll = null;
function _setupEls() {
  return {
    box: $("#setup"), card: $(".setup-card"), title: $("#setupTitle"),
    msg: $("#setupMsg"), bar: $(".setup-bar"), fill: $("#setupFill"),
    pct: $("#setupPct"), detail: $("#setupDetail"),
  };
}
const SETUP_LABELS = {
  checking: "AIの準備を確認しています", preparing: "対応モジュールを準備しています",
  downloading: "AIモデルをダウンロード中", loading: "モデルを読み込んでいます",
  ready: "準備が完了しました", offline: "オフラインで利用します", error: "準備に問題が発生しました",
};

async function initAiSetup() {
  const e = _setupEls();
  $("#setupSkip").addEventListener("click", () => { stopSetup(); e.box.classList.add("hidden"); });
  // If AI is already online and ready, skip entirely.
  if (state.ai.online && state.ai.note === "ready") return;

  let st;
  try { st = await post("/ai/setup", {}); }
  catch { return; }                       // setup endpoint missing → ignore
  // Only surface the overlay if the SDK is actually doing work.
  if (["checking", "preparing", "downloading", "loading"].includes(st.state)) {
    e.box.classList.remove("hidden");
  } else if (st.state === "offline") {
    return;                               // no SDK installed → stay offline silently
  }
  renderSetup(st);
  _setupPoll = setInterval(pollSetup, 1200);
}
function stopSetup() { if (_setupPoll) { clearInterval(_setupPoll); _setupPoll = null; } }

async function pollSetup() {
  let st;
  try { st = await api("/ai/setup-state"); } catch { return; }
  renderSetup(st);
  if (["ready", "offline", "error"].includes(st.state)) {
    stopSetup();
    if (st.state === "ready") {
      state.ai = await post("/ai/reconnect", {}); setAiBadge();
      setTimeout(() => _setupEls().box.classList.add("hidden"), 1400);
      toast("AIの準備が完了しました");
    }
  }
}

function renderSetup(st) {
  const e = _setupEls();
  e.box.classList.remove("hidden");
  e.title.textContent = SETUP_LABELS[st.state] || "AIの準備をしています";
  e.msg.textContent = st.message || "";
  e.card.classList.toggle("done", st.state === "ready");
  e.card.classList.toggle("error", st.state === "error");
  const determinate = st.state === "downloading" || st.state === "ready";
  e.bar.classList.toggle("indeterminate", !determinate);
  const p = Math.max(0, Math.min(100, Math.round(st.progress || 0)));
  if (determinate) { e.fill.style.width = p + "%"; e.pct.textContent = p + "%"; }
  else { e.pct.textContent = ""; }
  e.detail.textContent = st.detail || "";
  $("#setupSkip").textContent = (st.state === "error") ? "オフラインで続ける" : "今はオフラインで使う";
  if (st.state === "ready") { e.fill.style.width = "100%"; e.pct.textContent = "100%"; }
}

// ---------- boot ----------
(async function init() {
  try {
    const [lessonsRes, themes, health, profile] = await Promise.all([
      api("/lessons"), api("/themes"), api("/health"), api("/profile"),
    ]);
    state.lessons = lessonsRes.lessons; state.progress = lessonsRes.progress;
    state.themes = themes; state.ai = health.ai; state.profile = profile;
    const pr = await api("/progress"); state.activity = pr.activity; state.savedWords = pr.saved_words;
    setAiBadge();
    if (!location.hash) location.hash = "#home";
    renderRoute();
    initAiSetup();   // first-launch model download with progress (non-blocking)
  } catch (e) {
    $("#app").innerHTML = `<div class="notice">起動に失敗しました。サーバが動作しているか確認してください。<br>${esc(String(e))}</div>`;
  }
})();
