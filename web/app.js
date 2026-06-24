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
function thinkingHtml(text = "AIからの出力を受信中…") {
  return `<div class="thinking"><span class="thinking-dots"><i></i><i></i><i></i></span><span>${esc(text)}</span></div>`;
}
async function streamPost(path, body, handlers = {}) {
  const res = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  if (!res.body) return api(path.replace(/\/stream$/, ""), { method: "POST", body: JSON.stringify(body) });
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let final = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split(/\n/);
    buf = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const ev = JSON.parse(line);
      if (ev.event === "delta") handlers.onDelta?.(ev.text || "");
      else if (ev.event === "error") handlers.onError?.(ev.note || "");
      else if (ev.event === "final") final = ev.result;
    }
  }
  if (buf.trim()) {
    const ev = JSON.parse(buf);
    if (ev.event === "final") final = ev.result;
  }
  if (!final) throw new Error("stream finished without final result");
  return final;
}
function liveAi(container, label) {
  container.innerHTML = `<div class="ai-live">${thinkingHtml(label)}<pre></pre></div>`;
  const pre = $("pre", container);
  return {
    append(text) {
      pre.textContent += text;
      pre.scrollTop = pre.scrollHeight;
    },
    note(text) {
      if (text) pre.textContent += `\n[${text}]`;
    },
  };
}

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
  const speech = state.ai.speech || {};
  const chatOk = !!state.ai.online;
  const speechOk = !!speech.online;
  const provider = state.ai.provider_label || "AI";
  el.classList.toggle("online", chatOk && speechOk);
  el.classList.toggle("partial", (chatOk || speechOk) && !(chatOk && speechOk));
  el.classList.toggle("offline", !chatOk && !speechOk);
  $(".ai-label", el).textContent = chatOk && speechOk ? "AI/音声接続中"
    : chatOk ? "AI接続中"
    : speechOk ? "音声接続中"
    : "AIオフライン";
  el.title = [
    `会話・翻訳: ${provider} ${chatOk ? `接続中 (${state.ai.model || "model unknown"})` : "未接続"}`,
    `音声認識: ${speechOk ? `利用可 (${speech.model || "model unknown"})` : "未接続"}`,
    state.ai.note || speech.note || "クリックで再接続",
  ].join("\n");
}
$("#aiStatus").addEventListener("click", async () => {
  toast("AIへ再接続中…");
  try { state.ai = await post("/ai/reconnect", {}); setAiBadge();
    toast(state.ai.online || state.ai.speech?.online ? "AI状態を更新しました" : "AIに接続できませんでした"); }
  catch { toast("再接続に失敗しました"); }
});

function speechStatusLine() {
  const s = state.ai.speech || {};
  if (s.online && s.cached) return `音声認識: 接続中（${esc(s.model || "Whisper")}）`;
  if (s.online) return `音声認識: モデル確認済み（${esc(s.model || "Whisper")}、初回準備あり）`;
  return `音声認識: 未接続${s.note ? ` - ${esc(s.note)}` : ""}`;
}

function speechNoticeHtml() {
  const s = state.ai.speech || {};
  const cls = s.online ? "ok" : "";
  const cache = s.online ? (s.cached ? "ダウンロード済み" : "初回録音時に準備") : "利用不可";
  return `<div class="notice ${cls}">
    <b>${speechStatusLine()}</b><br>
    <span class="muted">状態: ${esc(cache)}${s.note ? ` / ${esc(s.note)}` : ""}</span>
  </div>`;
}

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
        <button class="btn accent" onclick="location.hash='#reading'">長文を読む</button>
        <button class="btn ghost" onclick="location.hash='#progress'">進捗を見る</button>
      </div>
    </div>
    <div class="hero-art">🎨 やさしい挿絵と<br>AI対話で学ぶ</div>
  </section>

  <div class="stat-row">
    <div class="card stat"><div class="v">${done}/${total}</div><div class="l">完了レッスン</div></div>
    <div class="card stat"><div class="v">${streak}<span style="font-size:1rem">日</span></div><div class="l">連続学習</div></div>
    <div class="card stat clickable" onclick="location.hash='#words'"><div class="v">${savedN}</div><div class="l">単語帳</div></div>
    <div class="card stat"><div class="v">${state.themes.length}</div><div class="l">テーマ</div></div>
  </div>

  <h2>英語学習の3つの柱</h2>
  <p class="sub">「英語を話せるようになった人がやっていること」を各レッスンに落とし込みました。</p>
  <div class="pillars">
    <div class="card pillar"><div class="n">① 基礎</div><h3>語彙・フレーズ</h3>
      <p>自分が使う場面の単語と言い回しを、例文ごと音読して身につけます。</p></div>
    <div class="card pillar"><div class="n">② インプット</div><h3>読む・聞く</h3>
      <p>身近なテーマの会話を、単語タップ和訳と音声で繰り返しインプット。</p></div>
    <div class="card pillar"><div class="n">③ アウトプット</div><h3>AI対話・発話</h3>
      <p>AI相手のロールプレイと発話チェックで、覚えた表現を実際に使います。</p></div>
  </div>

  <h2>続きから / おすすめ</h2>
  <div class="lesson-grid" id="homeLessons"></div>

  <p class="ai-disclaimer">※ AIによる和訳・添削・会話・長文生成などの結果には、誤りや不自然な表現が含まれる場合があります。学習の参考としてご利用ください。</p>`;

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
    ${speechNoticeHtml()}
    ${items}
  </div>`;
}

function panelChat(L) {
  const t = tutor();
  return `<div class="panel" data-panel="chat">
    <div class="hint">💡 AIの「${esc(t.name)}」とロールプレイ。状況：<b>${esc(L.roleplay.scenario_ja)}</b><br>英語で返信すると、やさしい訂正と和訳がもらえます。<span class="muted">（講師は設定で変更できます）</span></div>
    <div class="chat-box">
      <div class="chat-tools">
        <button class="mini" id="chatToggleJa">${chatShowJa ? "和訳を非表示" : "和訳を表示"}</button>
        <button class="mini" id="chatHelp">ヘルプ</button>
      </div>
      <div class="chat-log" id="chatLog"></div>
      <div class="chat-help hidden" id="chatHelpBox"></div>
      <div class="chat-input">
        <input id="chatText" placeholder="英語で入力してEnter…" autocomplete="off" />
        <button class="btn sm ghost" id="chatVoice">🎤 音声</button>
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
  if (!pop.contains(e.target) && !e.target.closest(".w, .reading-token.word")) pop.classList.add("hidden");
});

async function showGloss(anchor, text, mode, lessonId) {
  if (!text) return;
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.min(window.scrollX + r.left, window.scrollX + window.innerWidth - 270) + "px";
  pop.style.top = (window.scrollY + r.bottom + 6) + "px";
  $("#glossWord").textContent = text;
  const live = liveAi($("#glossBody"), "AIからの翻訳出力を受信中…");
  pop.classList.remove("hidden");
  popCtx = { word: text, meaning: "", lesson: lessonId };
  try {
    const t = await streamPost("/translate/stream", { text, mode, lesson_id: lessonId }, {
      onDelta: delta => live.append(delta),
      onError: note => live.note(note),
    });
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
  const live = liveAi($("#glossBody"), "AIからの和訳出力を受信中…");
  pop.classList.remove("hidden");
  popCtx = { word: text, meaning: "", lesson: lessonId };
  try {
    const t = await streamPost("/translate/stream", { text, mode, lesson_id: lessonId }, {
      onDelta: delta => live.append(delta),
      onError: note => live.note(note),
    });
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
        if (j.speech) { state.ai.speech = j.speech; setAiBadge(); }
        if (j.online && j.text) { input.value = j.text; doSpeechCheck(btn.closest(".speak-line").querySelector("[data-check]"), L); }
        else {
          input.placeholder = "聞き取った内容を入力してチェック";
          const fb = btn.closest(".speak-line").querySelector("[data-fb]");
          fb.classList.remove("hidden");
          fb.innerHTML = `<b>音声認識に接続できません。</b><br><span class="muted">${esc(j.note || "音声認識オフライン")}</span>`;
          toast(j.note || "音声認識オフライン");
        }
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
  fb.classList.remove("hidden"); fb.innerHTML = thinkingHtml("AIが発話内容を確認中…");
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
let chatShowJa = true;

function initChat(L) {
  const log = $("#chatLog");
  const helpBox = $("#chatHelpBox");
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
    const thinking = addMsg(log, "vivi", "");
    const live = liveAi($(".msg", thinking), "AIからの返答を受信中…");
    try {
      const r = await streamPost("/chat/stream", {
        messages: history.slice(-12), scenario: L.roleplay.scenario,
        level: state.profile.level, tutor_name: t.name, gender: t.gender,
      }, {
        onDelta: delta => live.append(delta),
        onError: note => live.note(note),
      });
      if (r.reply && !r.reply_ja) {
        r.reply_ja = await translateChatReply(r.reply, L.id);
      }
      thinking.remove();
      addMsg(log, "vivi", r.reply, r.reply_ja, r.correction, r.tip);
      history.push({ role: "assistant", content: r.reply });
      if (!r.online) toast("AIオフライン：Foundry Localを起動すると対話できます");
    } catch { thinking.remove(); addMsg(log, "vivi", "(通信エラー)"); }
  }
  async function showHelp() {
    helpBox.classList.remove("hidden");
    const live = liveAi(helpBox, "AIからの参考文案出力を受信中…");
    try {
      const r = await streamPost("/chat/help/stream", {
        messages: history.slice(-12), scenario: L.roleplay.scenario, level: state.profile.level,
      }, {
        onDelta: delta => live.append(delta),
        onError: note => live.note(note),
      });
      helpBox.innerHTML = (r.suggestions || []).map((s, i) => `
        <button class="suggestion" data-suggest="${esc(s.en)}">
          <b>${i + 1}. ${esc(s.en)}</b>
          ${s.ja ? `<span>${esc(s.ja)}</span>` : ""}
          ${s.note ? `<em>${esc(s.note)}</em>` : ""}
        </button>`).join("") || `<div class="muted">候補を作れませんでした。</div>`;
      if (!r.online) toast("AIオフライン：定型文を表示しています");
    } catch {
      helpBox.innerHTML = `<div class="muted">参考文案の生成に失敗しました。</div>`;
    }
  }
  $("#chatSend").addEventListener("click", send);
  $("#chatText").addEventListener("keydown", e => { if (e.key === "Enter") send(); });
  $("#chatToggleJa").addEventListener("click", () => {
    chatShowJa = !chatShowJa;
    $$(".mja", log).forEach(x => x.classList.toggle("hidden", !chatShowJa));
    $("#chatToggleJa").textContent = chatShowJa ? "和訳を非表示" : "和訳を表示";
  });
  $("#chatHelp").addEventListener("click", showHelp);
  helpBox.addEventListener("click", e => {
    const b = e.target.closest("[data-suggest]"); if (!b) return;
    $("#chatText").value = b.dataset.suggest;
    $("#chatText").focus();
  });
  $("#chatVoice").addEventListener("click", () => toggleChatVoice($("#chatVoice")));
}
async function translateChatReply(text, lessonId) {
  try {
    const t = await post("/translate", { text, mode: "sentence", lesson_id: lessonId });
    return t.translation || (t.note ? `（${t.note}）` : "");
  } catch {
    return "";
  }
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
  const isThinking = who === "vivi" && /Thinking/.test(String(text || ""));
  div.innerHTML = isThinking ? thinkingHtml(text) : (esc(text)
    + (ja ? `<div class="mja ${chatShowJa ? "" : "hidden"}">${esc(ja)}</div>` : "")
    + (fix ? `<div class="fix">✏️ ${esc(fix)}</div>` : "")
    + (tip ? `<div class="fix">💡 ${esc(tip)}</div>` : ""));
  if (who === "vivi" && !isThinking && text) {
    const s = document.createElement("button"); s.className = "mini"; s.textContent = "🔊";
    s.style.marginTop = ".3rem"; s.onclick = () => speak(text, t.gender); div.appendChild(s);
  }
  row.appendChild(div);
  log.appendChild(row); log.scrollTop = log.scrollHeight; return row;
}

async function toggleChatVoice(btn) {
  if (mediaRec && mediaRec.state === "recording") { mediaRec.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia) { toast("音声入力に未対応のブラウザです。"); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRec = new MediaRecorder(stream); recChunks = [];
    mediaRec.ondataavailable = e => recChunks.push(e.data);
    mediaRec.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      btn.classList.remove("recording"); btn.textContent = "🎤 音声";
      try {
        btn.textContent = "認識中…";
        const wav = await blobToWav16k(new Blob(recChunks, { type: "audio/webm" }));
        const fd = new FormData(); fd.append("audio", wav, "clip.wav");
        const r = await fetch(API + "/speech/transcribe", { method: "POST", body: fd });
        const j = await r.json();
        if (j.speech) { state.ai.speech = j.speech; setAiBadge(); }
        if (j.online && j.text) {
          $("#chatText").value = j.text;
          $("#chatText").focus();
        } else {
          toast(j.note || "音声認識オフライン");
        }
      } catch {
        toast("音声入力に失敗しました");
      } finally {
        btn.textContent = "🎤 音声";
      }
    };
    mediaRec.start();
    btn.classList.add("recording");
    btn.textContent = "■ 停止";
  } catch {
    toast("マイクを使用できません。");
  }
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

  $("#app").innerHTML = `
    <h1>学習の進捗</h1>
    <p class="sub">続けることがいちばんの近道。3つの柱を並行して回しましょう。</p>
    <div class="stat-row">
      <div class="card stat"><div class="v">${done.length}/${total}</div><div class="l">完了</div></div>
      <div class="card stat"><div class="v">${streak}日</div><div class="l">連続学習</div></div>
      <div class="card stat"><div class="v">${avg}</div><div class="l">平均クイズ点</div></div>
      <div class="card stat clickable" onclick="location.hash='#words'"><div class="v">${(data.saved_words||[]).length}</div><div class="l">単語帳</div></div>
    </div>
    <h2>この60日の学習</h2>
    <div class="card" style="padding:1.2rem"><div class="heat" id="heat"></div></div>
    <h2>テーマ別の達成度</h2>${byTheme}`;

  renderHeat(data.activity);
};

// ---------- WORDS (My Vocabulary) ----------
const STORY_FORMATS = [
  { key: "story", label: "📖 物語" },
  { key: "dialogue", label: "💬 会話" },
  { key: "diary", label: "📔 日記" },
  { key: "email", label: "✉️ メール" },
];
const STORY_LENGTHS = [
  { key: "short", label: "短い（2〜3文）" },
  { key: "medium", label: "ふつう（4〜6文）" },
  { key: "long", label: "長い（2段落）" },
];
// Suggested themes; the learner can also type a free theme.
const STORY_THEME_PRESETS = [
  "学校での一日", "旅行先での出来事", "ビジネスの会議", "レストランでの注文",
  "週末の過ごし方", "日本文化の紹介", "友だちとの会話", "将来の夢",
];
// Keeps the selected word set and options across re-renders within the screen.
const wordsUI = { selected: new Set(), customTerms: "", theme: "", format: "story", length: "short" };

routes.words = async () => {
  const data = await api("/progress");
  state.savedWords = data.saved_words;
  state.profile = state.profile || data.profile;
  const words = data.saved_words || [];

  // Drop selections for words that no longer exist.
  const existing = new Set(words.map(w => w.word));
  [...wordsUI.selected].forEach(w => { if (!existing.has(w)) wordsUI.selected.delete(w); });

  const list = words.map(w => `
    <div class="word-row ${wordsUI.selected.has(w.word) ? "picked" : ""}" data-word="${esc(w.word)}">
      <label class="word-pick">
        <input type="checkbox" data-pick="${esc(w.word)}" ${wordsUI.selected.has(w.word) ? "checked" : ""} />
      </label>
      <span class="w">${esc(w.word)}</span>
      <span class="m">${esc(w.meaning || "")}</span>
      <button class="mini" data-say="${esc(w.word)}">🔊</button>
      <button class="mini" data-del="${esc(w.word)}">削除</button>
    </div>`).join("") ||
    `<p class="sub">レッスンの「読む・聞く」で英単語をタップ→<b>★ 単語帳に保存</b>すると、ここに単語が貯まります。</p>`;

  const themeChips = STORY_THEME_PRESETS.map(t =>
    `<button class="chip sm" data-themechip="${esc(t)}">${esc(t)}</button>`).join("");
  const fmtBtns = STORY_FORMATS.map(f =>
    `<button class="seg ${wordsUI.format === f.key ? "active" : ""}" data-fmt="${f.key}">${f.label}</button>`).join("");
  const lenBtns = STORY_LENGTHS.map(l =>
    `<button class="seg ${wordsUI.length === l.key ? "active" : ""}" data-len="${l.key}">${esc(l.label)}</button>`).join("");
  const customCount = parseStoryTerms(wordsUI.customTerms).length;

  $("#app").innerHTML = `
    <h1>マイ単語帳</h1>
    <p class="sub">保存した単語を復習し、選んだ単語や入力した語句を使ったオリジナルの文章をAIに作ってもらえます。</p>

    <div class="words-layout">
      <section>
        <div class="words-head">
          <h2 style="margin:0">登録した単語（${words.length}）</h2>
          <div class="words-head-actions">
            <button class="mini" id="selAll">すべて選択</button>
            <button class="mini" id="selNone">選択解除</button>
          </div>
        </div>
        <div class="word-list" id="wordList">${list}</div>
      </section>

      <section class="card gen-panel">
        <h2 style="margin-top:0">📝 単語から文章を作る</h2>
        <p class="muted" style="margin:.2rem 0 .9rem">選んだ単語（<b id="pickCount">${wordsUI.selected.size}</b>個）と追加語句（<b id="customCount">${customCount}</b>個）を使い、テーマに沿った英文と和訳を生成します。</p>

        <div class="field-label">追加する単語・フレーズ</div>
        <textarea id="customWords" class="term-input" rows="3" placeholder="例：take off, get along with, curious">${esc(wordsUI.customTerms)}</textarea>

        <div class="field-label">テーマ</div>
        <input id="storyTheme" class="theme-input" placeholder="例：旅行先での出来事（自由入力）" value="${esc(wordsUI.theme)}" />
        <div class="theme-chips">${themeChips}</div>

        <div class="field-label">形式</div>
        <div class="seg-row" id="fmtRow">${fmtBtns}</div>

        <div class="field-label">長さ</div>
        <div class="seg-row" id="lenRow">${lenBtns}</div>

        <button class="btn" id="genStory" style="margin-top:1.1rem">✨ 文章を生成</button>
        <div id="storyOut" class="story-out"></div>
      </section>
    </div>`;

  const wordList = $("#wordList");
  const refreshPickUI = () => {
    $("#pickCount").textContent = wordsUI.selected.size;
    $$(".word-row", wordList).forEach(r =>
      r.classList.toggle("picked", wordsUI.selected.has(r.dataset.word)));
  };
  const customInput = $("#customWords");
  const refreshCustomUI = () => {
    $("#customCount").textContent = parseStoryTerms(wordsUI.customTerms).length;
  };

  wordList.addEventListener("click", async e => {
    const del = e.target.closest("[data-del]");
    const say = e.target.closest("[data-say]");
    if (say) { speak(say.dataset.say); return; }
    if (del) {
      await api(`/words/${encodeURIComponent(del.dataset.del)}`, { method: "DELETE" });
      wordsUI.selected.delete(del.dataset.del);
      routes.words();
    }
  });
  wordList.addEventListener("change", e => {
    const pick = e.target.closest("[data-pick]");
    if (!pick) return;
    if (pick.checked) wordsUI.selected.add(pick.dataset.pick);
    else wordsUI.selected.delete(pick.dataset.pick);
    refreshPickUI();
  });
  $("#selAll").addEventListener("click", () => {
    words.forEach(w => wordsUI.selected.add(w.word));
    $$("[data-pick]", wordList).forEach(c => c.checked = true);
    refreshPickUI();
  });
  $("#selNone").addEventListener("click", () => {
    wordsUI.selected.clear();
    $$("[data-pick]", wordList).forEach(c => c.checked = false);
    refreshPickUI();
  });

  customInput.addEventListener("input", () => {
    wordsUI.customTerms = customInput.value;
    refreshCustomUI();
  });

  const themeInput = $("#storyTheme");
  themeInput.addEventListener("input", () => { wordsUI.theme = themeInput.value; });
  $$("[data-themechip]").forEach(c => c.addEventListener("click", () => {
    wordsUI.theme = c.dataset.themechip; themeInput.value = wordsUI.theme;
  }));
  $("#fmtRow").addEventListener("click", e => {
    const b = e.target.closest("[data-fmt]"); if (!b) return;
    wordsUI.format = b.dataset.fmt;
    $$("#fmtRow .seg").forEach(x => x.classList.toggle("active", x === b));
  });
  $("#lenRow").addEventListener("click", e => {
    const b = e.target.closest("[data-len]"); if (!b) return;
    wordsUI.length = b.dataset.len;
    $$("#lenRow .seg").forEach(x => x.classList.toggle("active", x === b));
  });
  $("#genStory").addEventListener("click", generateStory);
};

async function generateStory() {
  const picked = storyTargetWords();
  const out = $("#storyOut");
  if (!picked.length) { toast("単語やフレーズを1つ以上選んでください。"); return; }
  const btn = $("#genStory");
  btn.disabled = true; const label = btn.textContent; btn.textContent = "生成中…";
  const live = liveAi(out, `AIが「${wordsUI.theme || "おまかせ"}」の文章を生成中…`);
  try {
    const r = await streamPost("/words/story/stream", {
      words: picked, theme: wordsUI.theme,
      level: (state.profile && state.profile.level) || "beginner",
      format: wordsUI.format, length: wordsUI.length,
    }, {
      onDelta: delta => live.append(delta),
      onError: note => live.note(note),
    });
    if (!r.story) {
      out.innerHTML = `<div class="notice">${esc(r.note || "文章を生成できませんでした。")}</div>`;
      return;
    }
    const usedSet = new Set((r.used_words || []).map(w => w.toLowerCase()));
    const storyHtml = highlightWords(r.story, picked);
    const notes = (r.vocab_notes || []).map(n =>
      `<li><b>${esc(n.en)}</b> — ${esc(n.ja)}</li>`).join("");
    out.innerHTML = `
      <div class="card story-card">
        ${r.title ? `<div class="story-title">${esc(r.title)}</div>` : ""}
        <div class="story-en">${storyHtml}</div>
        <button class="mini" id="sayStory">🔊 読み上げ</button>
        <div class="story-ja-wrap">
          <button class="mini" id="toggleStoryJa">和訳を表示</button>
          <div class="story-ja hidden">${esc(r.story_ja)}</div>
        </div>
        ${notes ? `<div class="field-label">使った単語</div><ul class="story-notes">${notes}</ul>` : ""}
        ${!r.online ? `<div class="note" style="color:#9a6a3f">（AIオフライン）</div>` : ""}
      </div>`;
    $("#sayStory").addEventListener("click", () => speak(r.story.replace(/<[^>]+>/g, "")));
    $("#toggleStoryJa").addEventListener("click", () => {
      const ja = $(".story-ja", out);
      const hidden = ja.classList.toggle("hidden");
      $("#toggleStoryJa").textContent = hidden ? "和訳を表示" : "和訳を非表示";
    });
    toast("文章を生成しました");
  } catch {
    out.innerHTML = `<div class="notice">文章の生成に失敗しました。AI接続を確認してください。</div>`;
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
}

function parseStoryTerms(value) {
  const terms = [];
  const seen = new Set();
  String(value || "").split(/[\n,、;；]+/).forEach(raw => {
    const term = raw.trim().replace(/\s+/g, " ");
    const key = term.toLowerCase();
    if (term && !seen.has(key)) {
      seen.add(key);
      terms.push(term);
    }
  });
  return terms;
}

function storyTargetWords() {
  const terms = [];
  const seen = new Set();
  [...wordsUI.selected, ...parseStoryTerms(wordsUI.customTerms)].forEach(raw => {
    const term = String(raw || "").trim().replace(/\s+/g, " ");
    const key = term.toLowerCase();
    if (term && !seen.has(key)) {
      seen.add(key);
      terms.push(term);
    }
  });
  return terms;
}

// Wrap any of the learner's target words found in the passage with a highlight.
function highlightWords(text, words) {
  const safe = esc(text);
  const uniq = [...new Set(words.map(w => w.trim()).filter(Boolean))]
    .sort((a, b) => b.length - a.length)
    .map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!uniq.length) return safe;
  const re = new RegExp(`\\b(${uniq.join("|")})\\b`, "gi");
  return safe.replace(re, '<mark class="vw">$1</mark>');
}

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

// ---------- READING SUPPORT ----------
const READING_DEFAULT_TEXT = `Many students feel nervous when they see a long English passage. However, a passage is usually easier to understand when readers look for its structure first. The first paragraph often introduces the topic, and later paragraphs add reasons, examples, or results. This habit gives readers a map before they try to translate every word.

For example, if a sentence begins with because, it probably explains a reason. If another sentence includes therefore or as a result, it probably shows a conclusion or effect. Pronouns such as it, they, and this also matter because they point back to earlier ideas. When readers connect those signals, they can follow the writer's thinking more calmly.

In conclusion, good reading is not only about knowing many words. Readers should find the subject and verb, notice the sentence pattern, and check how each paragraph works. These small steps make a long passage clearer and help learners read with more confidence.`;

const READING_LENGTHS = [
  { key: "medium", label: "標準（2段落）" },
  { key: "long", label: "長め（3段落）" },
  { key: "exam", label: "試験風（論理展開）" },
];

const readingUI = {
  text: "",
  topic: "daily habits and learning",
  length: "medium",
  translation: "",
  translationSource: "",
  note: "",
  initialized: false,
  genCount: 0,
};

const READING_GENERATION_ANGLES = [
  { angle: "a small problem in a local community", terms: ["community", "choice", "result"] },
  { angle: "an unexpected discovery during a school project", terms: ["project", "discover", "explain"] },
  { angle: "a disagreement that becomes a useful compromise", terms: ["opinion", "reason", "solution"] },
  { angle: "a change in nature that affects people's daily decisions", terms: ["environment", "cause", "adapt"] },
  { angle: "a new tool that helps some people but creates another issue", terms: ["technology", "benefit", "challenge"] },
  { angle: "a cultural custom seen through a visitor's eyes", terms: ["culture", "notice", "meaning"] },
  { angle: "a quiet personal habit that slowly changes someone's future", terms: ["habit", "progress", "confidence"] },
];

routes.reading = async () => {
  if (!readingUI.initialized) {
    readingUI.text = READING_DEFAULT_TEXT;
    readingUI.initialized = true;
  }
  const level = (state.profile && state.profile.level) || "beginner";
  const lenBtns = READING_LENGTHS.map(l =>
    `<button class="seg ${readingUI.length === l.key ? "active" : ""}" data-readlen="${l.key}">${esc(l.label)}</button>`).join("");

  $("#app").innerHTML = `
    <h1>長文読解サポート <span class="alpha-tag">α版</span></h1>
    <div class="alpha-banner">
      この機能は<b>α版（試験運用中）</b>です。解析や長文生成の精度は利用中のAIモデルの性能に依存し、
      文構造や段落の判定が安定しないことがあります。結果はあくまで学習の参考としてご利用ください。
    </div>
    <p class="sub">英文を入力するか、AIで長文を生成すると、段落単位で文分割・5文型・スラッシュ読み・句/節/熟語/ディスコースマーカーを確認できます。</p>

    <div class="reading-layout">
      <section class="reading-input">
        <div class="reading-toolbar">
          <input id="readingTopic" class="theme-input" value="${esc(readingUI.topic)}" placeholder="AI生成テーマ：環境問題、学校生活、テクノロジーなど" />
          <div class="seg-row" id="readingLenRow">${lenBtns}</div>
        </div>
        <textarea id="readingText" class="reading-textarea" rows="12" spellcheck="false">${esc(readingUI.text)}</textarea>
        <div class="reading-actions">
          <button class="btn" id="analyzeReading">解析する</button>
          <button class="btn ghost" id="translateReading">和訳を生成</button>
          <button class="btn accent" id="genReading">AIで長文を生成</button>
          <button class="btn ghost sm" id="sampleReading">サンプル</button>
          <button class="btn ghost sm" id="clearReading">クリア</button>
        </div>
        <div id="readingAiNote" class="muted">${esc(readingUI.note)}</div>
        <div class="reading-ja-box ${readingUI.translation ? "" : "hidden"}" id="readingJaBox">
          <button class="mini" id="toggleReadingJa">和訳を表示</button>
          <div class="reading-ja hidden">${esc(readingUI.translation)}</div>
        </div>
      </section>

      <section class="reading-result" id="readingResult"></section>
    </div>`;

  $("#readingText").addEventListener("input", e => {
    readingUI.text = e.target.value;
    if (readingUI.translationSource && readingUI.translationSource !== readingUI.text.trim()) {
      readingUI.translation = "";
      readingUI.translationSource = "";
      syncReadingTranslationBox();
    }
  });
  $("#readingTopic").addEventListener("input", e => { readingUI.topic = e.target.value; });
  $("#readingLenRow").addEventListener("click", e => {
    const b = e.target.closest("[data-readlen]"); if (!b) return;
    readingUI.length = b.dataset.readlen;
    $$("#readingLenRow .seg").forEach(x => x.classList.toggle("active", x === b));
  });
  $("#analyzeReading").addEventListener("click", analyzeReadingInput);
  $("#translateReading").addEventListener("click", () => translateReadingText(true));
  $("#sampleReading").addEventListener("click", () => {
    readingUI.text = READING_DEFAULT_TEXT;
    readingUI.translation = "";
    readingUI.translationSource = "";
    readingUI.note = "";
    $("#readingText").value = readingUI.text;
    syncReadingTranslationBox(true);
    renderReadingAnalysis(readingUI.text);
  });
  $("#clearReading").addEventListener("click", () => {
    readingUI.text = "";
    readingUI.translation = "";
    readingUI.translationSource = "";
    readingUI.note = "";
    $("#readingText").value = "";
    $("#readingAiNote").textContent = "";
    syncReadingTranslationBox();
    $("#readingResult").innerHTML = `<div class="notice">英文を入力すると解析結果が表示されます。</div>`;
  });
  $("#genReading").addEventListener("click", () => generateReadingPassage(level));
  $("#toggleReadingJa").addEventListener("click", () => {
    const box = $(".reading-ja");
    const hidden = box.classList.toggle("hidden");
    $("#toggleReadingJa").textContent = hidden ? "和訳を表示" : "和訳を非表示";
  });

  renderReadingAnalysis(readingUI.text);
};

async function analyzeReadingInput() {
  readingUI.text = $("#readingText").value.trim();
  if (!readingUI.text) { toast("解析する英文を入力してください。"); return; }
  await analyzeReadingWithAi(readingUI.text);
  if (readingUI.translationSource !== readingUI.text) {
    await translateReadingText(false);
  }
}

async function analyzeReadingWithAi(text) {
  const btn = $("#analyzeReading");
  const note = $("#readingAiNote");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "AI解析中…";
  const live = liveAi($("#readingResult"), "AIからの解析JSONを受信中…");
  note.textContent = "AIが文構造を解析しています。";
  try {
    const r = await streamPost("/reading/analyze/stream", {
      text,
      level: (state.profile && state.profile.level) || "beginner",
    }, {
      onDelta: delta => live.append(delta),
      onError: note => live.note(note),
    });
    if (r.restricted) {
      // The model is not capable enough for AI analysis: show the local
      // rule-based simple analysis and explain how to enable AI analysis.
      const msg = r.note || "長文読解のAI解析は高機能なLLM向けです。簡易解析を表示しています。";
      renderReadingAnalysis(text, null, msg, "warn");
      note.textContent = msg;
      return;
    }
    if (r.analysis) {
      const kind = /簡易解析|崩れた/.test(r.note || "") ? "warn" : "ok";
      renderReadingAnalysis(text, normalizeLlmReadingAnalysis(r.analysis, text), r.note || "AI解析を表示しています。", kind);
      note.textContent = r.note || "AI解析を表示しています。";
      return;
    }
    renderReadingAnalysis(text, null, r.note || "AI解析を取得できなかったため、簡易解析を表示しています。");
    note.textContent = r.note || "AI解析を取得できなかったため、簡易解析を表示しています。";
  } catch {
    renderReadingAnalysis(text, null, "AI解析APIに接続できないため、簡易解析を表示しています。");
    note.textContent = "AI解析APIに接続できないため、簡易解析を表示しています。";
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

async function translateReadingText(showToast = true) {
  const text = ($("#readingText")?.value || readingUI.text || "").trim();
  if (!text) { toast("和訳する英文を入力してください。"); return; }
  const btn = $("#translateReading");
  const note = $("#readingAiNote");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "和訳中…";
  const live = liveAi(note, "AIからの和訳出力を受信中…");
  try {
    const r = await streamPost("/translate/stream", { text, mode: "sentence", lesson_id: null }, {
      onDelta: delta => live.append(delta),
      onError: note => live.note(note),
    });
    readingUI.translation = r.translation || "";
    readingUI.translationSource = readingUI.translation ? text : "";
    readingUI.note = r.note || (r.online ? "和訳を生成しました。" : "");
    note.textContent = readingUI.note;
    syncReadingTranslationBox();
    if (showToast) toast(readingUI.translation ? "和訳を生成しました" : "和訳を取得できませんでした");
  } catch {
    note.textContent = "和訳に失敗しました。AI接続を確認してください。";
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

function syncReadingTranslationBox(reveal = false) {
  const box = $("#readingJaBox");
  if (!box) return;
  const ja = $(".reading-ja", box);
  const toggle = $("#toggleReadingJa");
  ja.textContent = readingUI.translation || "";
  ja.classList.toggle("hidden", !reveal);
  toggle.textContent = reveal ? "和訳を非表示" : "和訳を表示";
  box.classList.toggle("hidden", !readingUI.translation);
}

async function generateReadingPassage(level) {
  const btn = $("#genReading");
  const note = $("#readingAiNote");
  const label = btn.textContent;
  readingUI.genCount += 1;
  btn.disabled = true; btn.textContent = "生成中…";
  const live = liveAi(note, "AIからの長文生成出力を受信中…");
  try {
    const r = await requestReadingPassage(level, live);
    readingUI.text = normalizeReadingParagraphs(r.passage || "", readingTargetParagraphs());
    readingUI.translation = r.passage_ja || "";
    readingUI.translationSource = readingUI.text;
    $("#readingText").value = readingUI.text;
    readingUI.note = r.note || (r.online ? "AI生成文を解析しました。" : "");
    toast(r.online ? "長文を生成しました" : "サンプル英文を表示しました");
    routes.reading();
    setTimeout(() => analyzeReadingInput(), 0);
  } catch {
    note.textContent = "長文生成に失敗しました。AI接続を確認してください。";
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
}

async function requestReadingPassage(level, live = null) {
  const spec = readingGenerationSpec();
  try {
    return await streamPost("/reading/passage/stream", {
      topic: readingUI.topic || "reading practice",
      level,
      length: readingUI.length,
    }, {
      onDelta: delta => live?.append(delta),
      onError: note => live?.note(note),
    });
  } catch {
    // Older running servers may not have the dedicated endpoint yet. Reuse the
    // existing vocabulary-story generator with varied terms so the screen still
    // works until restart.
    const story = await post("/words/story", {
      words: readingTopicTerms(spec),
      theme: `${readingUI.topic || "reading practice"} - ${spec.angle} (variation ${spec.seed})`,
      level,
      format: "story",
      length: readingUI.length === "medium" ? "medium" : "long",
    });
    return {
      online: story.online,
      title: story.title || "",
      passage: normalizeReadingParagraphs(story.story || READING_DEFAULT_TEXT, readingTargetParagraphs()),
      passage_ja: story.story_ja || "",
      note: story.note || "既存の文章生成APIで長文を作成しました。アプリ再起動後は読解専用APIを使います。",
    };
  }
}

function readingTargetParagraphs() {
  return readingUI.length === "medium" ? 2 : 3;
}

function normalizeReadingParagraphs(text, target) {
  const raw = String(text || "").trim();
  if (!raw || target <= 1) return raw;
  const paragraphs = raw.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
  if (paragraphs.length === target) return paragraphs.join("\n\n");
  if (paragraphs.length > target) {
    return paragraphs.slice(0, target - 1).concat(paragraphs.slice(target - 1).join(" ")).join("\n\n");
  }
  const sentences = raw.match(/[^.!?]+(?:[.!?]+|$)/g)?.map(s => s.trim()).filter(Boolean) || [];
  if (sentences.length < target) return paragraphs.join("\n\n") || raw;
  const groups = [];
  const base = Math.floor(sentences.length / target);
  let extra = sentences.length % target;
  let pos = 0;
  for (let i = 0; i < target; i++) {
    const take = base + (extra > 0 ? 1 : 0);
    groups.push(sentences.slice(pos, pos + take).join(" "));
    pos += take;
    extra -= 1;
  }
  return groups.filter(Boolean).join("\n\n");
}

function readingGenerationSpec() {
  const seed = `${Date.now().toString(36)}-${readingUI.genCount}`;
  const idx = Math.abs(hashText(`${readingUI.topic}:${readingUI.length}:${seed}`)) % READING_GENERATION_ANGLES.length;
  return { ...READING_GENERATION_ANGLES[idx], seed };
}

function hashText(text) {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = ((h << 5) - h + text.charCodeAt(i)) | 0;
  return h;
}

function readingTopicTerms(spec) {
  const terms = (readingUI.topic.match(/[A-Za-z]+(?:\s+[A-Za-z]+)?/g) || [])
    .map(t => t.trim().toLowerCase())
    .filter(t => t.length >= 4)
    .slice(0, 2);
  (spec?.terms || []).forEach(t => {
    if (!terms.includes(t)) terms.push(t);
  });
  return terms.length ? terms : ["reading", "learning"];
}

const READING_PRONOUNS = new Set("i me my mine you your yours he him his she her hers it its we us our ours they them their theirs this that these those who whom which whose".split(" "));
const READING_FUNCTION_WORDS = new Set("a an the in on at by for from with without into onto over under between among through during before after of to as than and but or nor so yet if because although while since when where that which who whom whose".split(" "));
const READING_PREPOSITIONS = new Set("in on at by for from with without into onto over under between among through during before after of to as than about around across against beyond near inside outside".split(" "));
// Subordinating conjunctions that open an adverbial clause. We cut the post-verb
// span here so a clause like "when they see ..." becomes its own modifier chunk
// instead of being swallowed into the object/complement of the main clause.
const READING_SUBORDINATORS = new Set("when while because since although though if unless until whereas before after as".split(" "));
const READING_AUX = new Set("am is are was were be been being do does did have has had can could will would shall should may might must".split(" "));
const READING_AUX_WITH_MAIN = new Set("do does did have has had can could will would shall should may might must".split(" "));
const READING_NEGATIONS = new Set("not never".split(" "));
const READING_LINKING = new Set("am is are was were be been being become becomes became seem seems seemed feel feels felt look looks looked sound sounds sounded remain remains remained appear appears appeared".split(" "));
const READING_DITRANSITIVE = new Set("give gives gave send sends sent tell tells told show shows showed teach teaches taught offer offers offered ask asks asked bring brings brought buy buys bought lend lends lent".split(" "));
const READING_OBJECT_COMPLEMENT = new Set("make makes made find finds found keep keeps kept call calls called name names named consider considers considered leave leaves left elect elects elected paint paints painted".split(" "));
const READING_COMMON_VERBS = new Set("think thinks thought feel feels felt see sees saw seen look looks looked use uses used make makes made help helps helped shape shapes shaped put puts study studies studied give gives gave focus focuses focused become becomes became lead leads led need needs needed show shows showed call calls called test tests tested find finds found keep keeps kept notice notices noticed choose chooses chose repeat repeats repeated connect connects connected follow follows followed introduce introduces introduced add adds added explain explains explained include includes included matter matters mattered point points pointed turn turns turned read reads reading know knows knew understand understands understood".split(" "));
const READING_SIGNAL_GROUPS = [
  { key: "contrast", label: "対比", phrases: ["however", "although", "but", "yet", "while", "whereas", "on the other hand"] },
  { key: "reason", label: "理由", phrases: ["because", "since", "due to", "for this reason"] },
  { key: "cause", label: "原因", phrases: ["cause", "causes", "lead to", "leads to", "result in", "results in"] },
  { key: "result", label: "結果", phrases: ["therefore", "so", "as a result", "consequently", "thus"] },
  { key: "conclusion", label: "結論", phrases: ["in conclusion", "overall", "in short", "to sum up", "finally"] },
  { key: "example", label: "例示", phrases: ["for example", "for instance", "such as"] },
  { key: "addition", label: "追加", phrases: ["also", "moreover", "in addition", "furthermore"] },
  { key: "sequence", label: "順序", phrases: ["first", "second", "next", "then", "before", "after"] },
  { key: "reference", label: "指示語", phrases: ["this", "that", "these", "those", "it", "they", "them", "their"] },
];

function renderReadingAnalysis(text, analysis = null, modeNote = "", noteKind = "ok") {
  const result = $("#readingResult");
  if (!text.trim()) {
    result.innerHTML = `<div class="notice">英文を入力すると解析結果が表示されます。</div>`;
    return;
  }
  const a = analysis || analyzeReading(text);
  const isSimple = !analysis || Boolean(a.simple);
  const partialSimple = !isSimple && Boolean(a.partialSimple);
  const signalHtml = a.signals.length
    ? a.signals.slice(0, 20).map(s => `<span class="signal-chip ${s.key}">${esc(s.label)}: ${esc(s.match)}</span>`).join("")
    : `<span class="muted">接続語・指示語は少なめです。</span>`;
  const paragraphRoles = a.paragraphs.map(p =>
    `<div class="role-row"><b>P${p.index + 1}</b><span>${esc(p.role)}</span><small>${esc(p.reason)}</small></div>`).join("");
  const passageHtml = a.paragraphs.map(p => `
    <div class="reading-paragraph">
      <div class="paragraph-head"><b>Paragraph ${p.index + 1}</b><span>${esc(p.role)}</span></div>
      ${p.sentences.map(s => renderReadingSentence(s)).join("")}
    </div>`).join("");

  result.innerHTML = `
    ${isSimple ? `<div class="simple-analysis-banner"><b>簡易解析モード</b><span>AIによる段落解析ではありません。文分割・文型・区切りはローカル規則による推定のため、句/節/熟語の詳細抽出は省略されています。</span></div>` : ""}
    ${partialSimple ? `<div class="simple-analysis-banner partial"><b>一部は簡易解析</b><span>AI解析が崩れた段落だけ、ローカル規則による推定で補っています。</span></div>` : ""}
    ${modeNote ? `<div class="notice ${noteKind === "warn" ? "warn" : "ok"}">${esc(modeNote)}</div>` : ""}
    <div class="reading-metrics">
      <div><b>${a.paragraphs.length}</b><span>段落</span></div>
      <div><b>${a.sentences.length}</b><span>文</span></div>
      <div><b>${a.signals.length}</b><span>重要シグナル</span></div>
      <div><b>${a.sentences.filter(s => s.words.length >= 20).length}</b><span>長めの文</span></div>
    </div>
    <div class="reading-legend">
      <span><i class="subj"></i>主語</span><span><i class="verb"></i>動詞</span>
      <span><i class="interrog"></i>疑問詞</span><span><i class="aux"></i>助動詞</span>
      <span><i class="pron"></i>代名詞/指示語</span><span><i class="func"></i>前置詞・接続詞など</span>
      <span><i class="sig"></i>理由・結果・結論</span>
    </div>
    <div class="reading-summary">
      <section><h2>段落ごとの役割</h2>${paragraphRoles}</section>
      <section><h2>理解のポイント</h2><div class="signal-list">${signalHtml}</div></section>
    </div>
    <h2>色分け本文</h2>
    <div class="reading-passage">${passageHtml}</div>`;

  result.onclick = e => {
    const say = e.target.closest("[data-readsay]");
    if (say) { speak(say.dataset.readsay); return; }
    const word = e.target.closest(".reading-token.word");
    if (word) showGloss(word, cleanWord(word.textContent), "word", null);
  };
}

function normalizeLlmReadingAnalysis(analysis, sourceText) {
  const paragraphs = (analysis.paragraphs || []).map((p, pi) => {
    const sentences = (p.sentences || [])
      .map((s, si) => normalizeLlmSentence(s, pi, si, sourceText))
      .filter(Boolean);
    return {
      index: pi,
      role: p.role || "展開・補足",
      reason: p.reason || "AIが段落の役割を推定しました。",
      sentences,
      signals: (p.signals || []).map(sig => normalizeLlmSignal(sig, sourceText)).filter(Boolean),
      simple: Boolean(p.simple),
    };
  }).filter(p => p.sentences.length);
  const sentences = paragraphs.flatMap(p => p.sentences);
  const aiSignals = (analysis.signals || []).map(sig => normalizeLlmSignal(sig, sourceText)).filter(Boolean);
  const localSignals = analyzeReading(sourceText).signals;
  const signals = mergeReadingSignals(aiSignals, paragraphs.flatMap(p => p.signals), sentences.flatMap(s => s.signals), localSignals);
  if (!paragraphs.length) return analyzeReading(sourceText);
  return {
    paragraphs,
    sentences,
    signals,
    simple: Boolean(analysis.simple) || paragraphs.every(p => p.simple),
    partialSimple: Boolean(analysis.partial_simple) || paragraphs.some(p => p.simple),
  };
}

function normalizeLlmSentence(s, paragraphIndex, sentenceIndex, sourceText) {
  const text = String(s.text || "").trim();
  if (!text || isBadLlmValue(text) || containsJapanese(text) || !textCopiedFrom(text, sourceText)) return null;
  const words = text.match(/[A-Za-z]+(?:'[A-Za-z]+)?|\d+/g) || [];
  const lower = words.map(w => w.toLowerCase());
  const signals = (s.signals || []).map(sig => normalizeLlmSignal(sig, text)).filter(Boolean);
  const chunks = (s.chunks || []).map(c => {
    const kind = normalizeChunkKind(c.kind);
    const ctext = String(c.text || "").trim();
    // Always derive the label from the canonical kind so the UI never shows the
    // model's English label ("subject", "linking verb", …). The interrogative
    // label also depends on the wh-word (疑問副詞/疑問代名詞).
    return { kind, label: chunkLabelForKind(kind, ctext), text: ctext };
  }).filter(c => c.text && !isBadLlmValue(c.text) && !containsJapanese(c.text) && textCopiedFrom(c.text, text));
  const slashSegments = normalizeSlashSegments(s.slash || s.slash_reading || s.slash_segments, text, chunks);
  const features = normalizeReadingFeatures(s.features || s.phrases || s.structures, text, slashSegments);
  const wordClasses = words.map(() => new Set());
  chunks.forEach(c => markChunkWords(words, wordClasses, c.text, classForChunk(c.kind)));
  signals.forEach(sig => markChunkWords(words, wordClasses, sig.match, "rs-signal"));
  lower.forEach((w, i) => {
    if (READING_PRONOUNS.has(w)) wordClasses[i].add("rs-pronoun");
    if (READING_FUNCTION_WORDS.has(w)) wordClasses[i].add("rs-function");
  });
  return {
    text, words, lower, paragraphIndex, sentenceIndex,
    pattern: canonicalReadingPattern(s.pattern),
    focus: (isBadLlmValue(s.focus) || !containsJapanese(s.focus)) ? "詳細" : (s.focus || "詳細"),
    chunks,
    slashSegments,
    features,
    signals,
    wordClasses,
  };
}

function normalizeSlashSegments(items, context, chunks = []) {
  const raw = Array.isArray(items) ? items : [];
  const segments = [];
  raw.forEach(item => {
    const text = String((item && typeof item === "object") ? (item.text || item.segment || "") : (item || "")).trim();
    if (!text || isBadLlmValue(text) || containsJapanese(text) || !textCopiedFrom(text, context)) return;
    if (!segments.some(s => s.toLowerCase() === text.toLowerCase())) segments.push(text);
  });
  if (segments.length) return segments;
  return (chunks || []).map(c => c.text).filter(Boolean);
}

const READING_FEATURE_LABELS = {
  noun_phrase: "名詞句",
  adverb_phrase: "副詞句",
  adjective_phrase: "形容詞句",
  noun_clause: "名詞節",
  adverb_clause: "副詞節",
  adjective_clause: "形容詞節",
  idiom: "熟語・慣用句",
  discourse_marker: "ディスコースマーカー",
};

function normalizeFeatureType(raw) {
  const k = String(raw || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
  if (READING_FEATURE_LABELS[k]) return k;
  if (k.includes("discourse") || k.includes("marker") || k.includes("transition")) return "discourse_marker";
  if (k.includes("idiom") || k.includes("set_expression") || k.includes("fixed_expression")) return "idiom";
  if (k.includes("noun") && k.includes("clause")) return "noun_clause";
  if ((k.includes("adverb") || k.includes("adverbial")) && k.includes("clause")) return "adverb_clause";
  if ((k.includes("adjective") || k.includes("relative")) && k.includes("clause")) return "adjective_clause";
  if (k.includes("noun")) return "noun_phrase";
  if (k.includes("adverb") || k.includes("adverbial")) return "adverb_phrase";
  if (k.includes("adjective")) return "adjective_phrase";
  return "idiom";
}

function featureLabel(type, label = "") {
  const text = String(label || "").trim();
  if (text && !isBadLlmValue(text) && containsJapanese(text)) return text;
  return READING_FEATURE_LABELS[type] || "熟語・慣用句";
}

function normalizeReadingFeatures(items, context, slashSegments = []) {
  if (!Array.isArray(items)) return [];
  const seen = new Set();
  return items.map(item => {
    if (!item || typeof item !== "object") return null;
    const text = String(item.text || item.match || "").trim();
    if (!text || isBadLlmValue(text) || containsJapanese(text) || !textCopiedFrom(text, context)) return null;
    const type = normalizeFeatureType(item.type || item.kind);
    const id = `${type}:${text.toLowerCase()}`;
    if (seen.has(id)) return null;
    seen.add(id);
    let relatesTo = String(item.relates_to || item.segment || item.slash || "").trim();
    if (!slashSegments.some(s => s.toLowerCase() === relatesTo.toLowerCase())) {
      relatesTo = slashSegments.find(s => textCopiedFrom(text, s) || textCopiedFrom(s, text)) || "";
    }
    const note = String(item.note || item.role || "").trim();
    return {
      type,
      label: featureLabel(type, item.label),
      text,
      relatesTo,
      note: (note && !isBadLlmValue(note) && containsJapanese(note)) ? note : "",
    };
  }).filter(Boolean);
}

function normalizeLlmSignal(sig, context = "") {
  if (!sig || !sig.match) return null;
  const match = String(sig.match || "").trim();
  if (isBadLlmValue(match) || containsJapanese(match) || (context && !textCopiedFrom(match, context))) return null;
  const key = canonicalSignalKey(sig.key);
  return {
    key,
    label: signalLabel(sig.label, key),
    match,
    words: match.toLowerCase().match(/[a-z]+(?:'[a-z]+)?|\d+/g) || [],
  };
}

function canonicalSignalKey(key) {
  const k = String(key || "").trim().toLowerCase();
  return ["contrast", "reason", "cause", "result", "conclusion", "example", "addition", "sequence", "reference"].includes(k)
    ? k
    : "reference";
}

function signalLabel(label, key) {
  const text = String(label || "").trim();
  if (text && !isBadLlmValue(text) && containsJapanese(text)) return text;
  return {
    contrast: "対比",
    reason: "理由",
    cause: "原因",
    result: "結果",
    conclusion: "結論",
    example: "例示",
    addition: "追加",
    sequence: "順序",
    reference: "指示語",
  }[canonicalSignalKey(key)] || "指示語";
}

function mergeReadingSignals(...groups) {
  const out = [];
  const seen = new Set();
  groups.flat().filter(Boolean).forEach(sig => {
    const key = canonicalSignalKey(sig.key);
    const match = String(sig.match || "").trim();
    if (!match) return;
    const id = `${key}:${match.toLowerCase()}`;
    if (seen.has(id)) return;
    seen.add(id);
    out.push({
      ...sig,
      key,
      label: signalLabel(sig.label, key),
      match,
      words: sig.words || match.toLowerCase().match(/[a-z]+(?:'[a-z]+)?|\d+/g) || [],
    });
  });
  return out;
}

function isBadLlmValue(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return true;
  return text === "..." || text.includes("exact original sentence")
    || text.includes("exact words from the sentence")
    || text.includes("exact signal word or phrase")
    || text.includes("exact slash segment")
    || text.includes("exact phrase or clause")
    || text.includes("sv/svc/svo/svoo/svoc + japanese explanation")
    || text.includes("要点/対比/理由・原因/結果/結論/具体例/詳細など")
    || text.includes("接続語/s 主語/v 動詞/o 目的語/c 補語/修飾句")
    || text.includes("接続語/疑問詞/助動詞/s 主語/v 動詞/o 目的語/c 補語/修飾句")
    || text.includes("日本語ラベル");
}

function containsJapanese(value) {
  return /[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]/.test(String(value || ""));
}

function textCopiedFrom(needle, haystack) {
  const n = String(needle || "").trim().toLowerCase().replace(/^[.,;:!?]+|[.,;:!?]+$/g, "");
  const h = String(haystack || "").trim().toLowerCase();
  return !!n && h.includes(n);
}

function normalizeChunkKind(kind) {
  const k = String(kind || "").trim().toLowerCase();
  if (["connector", "interrogative", "auxiliary", "subject", "verb", "object", "complement", "modifier"].includes(k)) return k;
  if (!k) return "modifier";
  // Models often answer with English grammar terms ("linking verb",
  // "direct object", "subject complement", "auxiliary verb"). Fold them onto a
  // canonical kind so the Japanese label is derived correctly. Check the more
  // specific terms first: "subject complement" must resolve to complement, not
  // subject, and "auxiliary verb" must resolve to auxiliary, not verb.
  if (k.includes("connect") || k.includes("conjunction") || k.includes("transition")) return "connector";
  if (k.includes("interrog") || k.includes("question word") || k.includes("wh-word") || k.includes("wh word") || k.includes("whword")) return "interrogative";
  if (k.includes("auxiliary") || k.includes("aux") || k.includes("modal") || k.includes("operator")) return "auxiliary";
  if (k.includes("complement")) return "complement";
  if (k.includes("object")) return "object";
  if (k.includes("subject")) return "subject";
  if (k.includes("verb") || k.includes("predicate")) return "verb";
  return "modifier";
}

// Wh-words that act adverbially (疑問副詞) vs. as pronouns (疑問代名詞).
const READING_WH_ADVERBS = new Set(["when", "where", "why", "how"]);

function chunkLabelForKind(kind, text = "") {
  const k = normalizeChunkKind(kind);
  if (k === "interrogative") {
    const head = String(text || "").trim().toLowerCase().split(/\s+/)[0].replace(/^[.,;:!?]+|[.,;:!?]+$/g, "");
    return READING_WH_ADVERBS.has(head) ? "疑問副詞" : "疑問代名詞";
  }
  return {
    connector: "接続語",
    auxiliary: "助動詞",
    subject: "S 主語",
    verb: "V 動詞",
    object: "O 目的語",
    complement: "C 補語",
    modifier: "修飾句",
  }[k] || "修飾句";
}

const READING_PATTERN_LABELS = {
  SV: "SV（主語＋動詞）",
  SVC: "SVC（主語＋動詞＋補語）",
  SVO: "SVO（主語＋動詞＋目的語）",
  SVOO: "SVOO（主語＋動詞＋目的語＋目的語）",
  SVOC: "SVOC（主語＋動詞＋目的語＋補語）",
};

// Models often answer with English glosses like
// "SVC (subject + linking verb + complement)". Pull the SV/SVC/… token out and
// relabel it in Japanese so the UI never shows English.
function canonicalReadingPattern(raw) {
  const text = String(raw || "").trim();
  if (!text || isBadLlmValue(text)) return "文型不明";
  const compact = text.replace(/[^A-Za-z]/g, "").toUpperCase();
  const m = /^(SVOO|SVOC|SVO|SVC|SV)/.exec(compact);
  if (m) return READING_PATTERN_LABELS[m[1]];
  if (containsJapanese(text)) return text;  // already a Japanese description
  return "文型不明";
}

function classForChunk(kind) {
  return {
    connector: "rs-signal",
    interrogative: "rs-interrog",
    auxiliary: "rs-aux",
    subject: "rs-subject",
    verb: "rs-verb",
    object: "rs-object",
    complement: "rs-object",
    modifier: "",
  }[normalizeChunkKind(kind)] || "";
}

function markChunkWords(words, wordClasses, phrase, cls) {
  if (!cls || !phrase) return;
  const target = String(phrase).toLowerCase().match(/[a-z]+(?:'[a-z]+)?|\d+/g) || [];
  if (!target.length) return;
  const lower = words.map(w => w.toLowerCase());
  for (let i = 0; i <= lower.length - target.length; i++) {
    let ok = true;
    for (let j = 0; j < target.length; j++) {
      if (lower[i + j] !== target[j]) { ok = false; break; }
    }
    if (ok) {
      for (let j = 0; j < target.length; j++) wordClasses[i + j].add(cls);
      return;
    }
  }
}

function renderReadingSentence(s) {
  const chunks = (s.chunks || []).map(c =>
    `<span class="chunk ${c.kind}"><b>${esc(c.label)}</b>${esc(c.text)}</span>`).join("");
  const focus = s.focus ? `<span class="focus">${esc(s.focus)}</span>` : "";
  const slash = (s.slashSegments || []).length
    ? `<div class="slash-row">${s.slashSegments.map(seg => `<span>${esc(seg)}</span>`).join("")}</div>`
    : "";
  const features = (s.features || []).length
    ? `<div class="feature-row">${s.features.map(f => `
        <span class="feature-chip ${esc(f.type)}">
          <b>${esc(f.label)}</b>${esc(f.text)}
          ${f.relatesTo ? `<em>→ ${esc(f.relatesTo)}</em>` : ""}
          ${f.note ? `<small>${esc(f.note)}</small>` : ""}
        </span>`).join("")}</div>`
    : "";
  return `
    <div class="reading-sentence">
      <div class="sentence-meta">
        <span class="pattern">${esc(s.pattern)}</span>
        ${focus}
        <button class="mini" data-readsay="${esc(s.text)}">🔊</button>
      </div>
      <div class="reading-line">${renderReadingTokens(s)}</div>
      ${slash}
      <div class="chunk-row">${chunks}</div>
      ${features}
    </div>`;
}

function renderReadingTokens(s) {
  let wi = 0;
  return s.text.split(/([A-Za-z]+(?:'[A-Za-z]+)?|\d+)/g).map(part => {
    if (!part) return "";
    if (!/^[A-Za-z0-9']+$/.test(part)) return esc(part);
    const cls = ["reading-token", "word"].concat([...(s.wordClasses[wi] || [])]).join(" ");
    wi += 1;
    return `<span class="${cls}">${esc(part)}</span>`;
  }).join("");
}

function analyzeReading(text) {
  const paragraphTexts = text.trim().split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
  const paragraphs = [];
  const sentences = [];
  paragraphTexts.forEach((pText, pi) => {
    const items = splitReadingSentences(pText).map((sText, si) =>
      analyzeReadingSentence(sText, pi, sentences.length + si));
    const role = paragraphRole(pText, pi, paragraphTexts.length);
    paragraphs.push({ index: pi, text: pText, sentences: items, ...role });
    sentences.push(...items);
  });
  const signals = sentences.flatMap(s => s.signals);
  return { paragraphs, sentences, signals };
}

// Abbreviations ending in a period that must NOT split a sentence (Mr./Ms./…).
const READING_ABBREV = new Set([
  "mr", "mrs", "ms", "dr", "prof", "st", "mt", "sr", "jr", "messrs",
  "vs", "etc", "inc", "ltd", "co", "no", "approx", "dept", "fig",
  "e.g", "i.e", "a.m", "p.m", "u.s", "u.k",
]);

function splitReadingSentences(text) {
  const src = String(text || "").trim();
  if (!src) return [];
  const sentences = [];
  let start = 0;
  const re = /[.!?]+(?:["')\]]+)?/g;
  let m;
  while ((m = re.exec(src))) {
    if (m[0][0] === "." && m[0] === ".") {
      const prefix = src.slice(start, m.index);
      const last = /([A-Za-z][A-Za-z.]*)$/.exec(prefix);
      if (last) {
        const token = last[1].toLowerCase().replace(/\.+$/, "");
        if (READING_ABBREV.has(token) || (token.length === 1 && /[a-z]/.test(token))) continue;
      }
      const before = m.index > 0 ? src[m.index - 1] : "";
      const after = re.lastIndex < src.length ? src[re.lastIndex] : "";
      if (/\d/.test(before) && /\d/.test(after)) continue;  // decimal in a number
    }
    const chunk = src.slice(start, re.lastIndex).trim();
    if (chunk) sentences.push(chunk);
    start = re.lastIndex;
  }
  const tail = src.slice(start).trim();
  if (tail) sentences.push(tail);
  return sentences.length ? sentences : [src];
}

function analyzeReadingSentence(text, paragraphIndex, sentenceIndex) {
  const words = text.match(/[A-Za-z]+(?:'[A-Za-z]+)?|\d+/g) || [];
  const lower = words.map(w => w.toLowerCase());
  const verb = findReadingVerb(lower);
  const signals = findReadingSignals(text);
  const pattern = classifySentencePattern(lower, verb);
  const classes = words.map(() => new Set());
  const subjectStart = Math.max(0, firstContentIndex(lower, verb.start));
  if (verb.start >= 0) {
    for (let i = subjectStart; i < verb.start; i++) classes[i].add("rs-subject");
    for (let i = verb.start; i <= verb.end; i++) classes[i]?.add("rs-verb");
  }
  lower.forEach((w, i) => {
    if (READING_PRONOUNS.has(w)) classes[i].add("rs-pronoun");
    if (READING_FUNCTION_WORDS.has(w)) classes[i].add("rs-function");
    if (signals.some(s => s.words.includes(w))) classes[i].add("rs-signal");
  });
  return {
    text, words, lower, paragraphIndex, sentenceIndex,
    pattern: pattern.label,
    focus: sentenceFocus(signals, sentenceIndex),
    chunks: readingChunks(words, lower, verb, pattern),
    slashSegments: [],
    features: [],
    signals,
    wordClasses: classes,
  };
}

function findReadingVerb(lower) {
  for (let i = 0; i < lower.length; i++) {
    const w = lower[i];
    if (READING_AUX.has(w)) {
      let end = i;
      let j = i + 1;
      if (READING_NEGATIONS.has(lower[j])) {
        end = j;
        j += 1;
      }
      if (READING_AUX_WITH_MAIN.has(w) && lower[j] && isReadingVerbLike(lower[j])) {
        end = j;
      }
      return { start: i, end, word: lower[end] || w };
    }
    if (isReadingVerbLike(w)) {
      return { start: i, end: i, word: w };
    }
  }
  return { start: -1, end: -1, word: "" };
}

function isReadingVerbLike(word) {
  return READING_COMMON_VERBS.has(word) || /(?:ed|ing|ize|ise)$/.test(word);
}

function firstContentIndex(lower, verbStart) {
  if (verbStart <= 0) return 0;
  const starters = new Set(["however", "therefore", "also", "moreover", "first", "second", "next", "then", "finally"]);
  return starters.has(lower[0]) && verbStart > 1 ? 1 : 0;
}

function classifySentencePattern(lower, verb) {
  if (verb.start < 0) return { label: "文型不明", kind: "unknown" };
  const main = verb.word;
  // The S/V/O/C pattern describes only the main clause, so stop counting at the
  // first subordinating conjunction — an adverbial clause ("when they see ...")
  // must not turn an SV/SVC sentence into a spurious SVO/SVOC.
  let tail = lower.slice(verb.end + 1);
  const subAt = tail.findIndex(w => READING_SUBORDINATORS.has(w));
  if (subAt >= 0) tail = tail.slice(0, subAt);
  const after = tail.filter(w =>
    !READING_FUNCTION_WORDS.has(w) && !/^\d+$/.test(w));
  if (READING_LINKING.has(main)) {
    return { label: after.length ? "SVC（主語＋動詞＋補語）" : "SV（主語＋動詞）", kind: after.length ? "svc" : "sv" };
  }
  if (READING_DITRANSITIVE.has(main) && after.length >= 2) return { label: "SVOO（主語＋動詞＋目的語＋目的語）", kind: "svoo" };
  if (READING_OBJECT_COMPLEMENT.has(main) && after.length >= 2) return { label: "SVOC（主語＋動詞＋目的語＋補語）", kind: "svoc" };
  if (after.length) return { label: "SVO（主語＋動詞＋目的語）", kind: "svo" };
  return { label: "SV（主語＋動詞）", kind: "sv" };
}

function readingChunks(words, lower, verb, pattern) {
  if (!words.length) return [];
  if (verb.start < 0) return [{ label: "文", text: words.join(" "), kind: "all" }];
  const chunks = [];
  const subjectStart = Math.max(0, firstContentIndex(lower, verb.start));
  const leadText = words.slice(0, subjectStart).join(" ").trim();
  const sText = words.slice(subjectStart, verb.start).join(" ").trim();
  const vText = words.slice(verb.start, verb.end + 1).join(" ").trim();
  const rest = words.slice(verb.end + 1);
  if (leadText) chunks.push({ label: "接続語", text: leadText, kind: "modifier" });
  if (sText) chunks.push({ label: "S 主語", text: sText, kind: "subject" });
  if (vText) chunks.push({ label: "V 動詞", text: vText, kind: "verb" });
  if (rest.length) {
    const breakAt = rest.findIndex((_, i) => {
      const w = lower[verb.end + 1 + i];
      return READING_PREPOSITIONS.has(w) || READING_SUBORDINATORS.has(w);
    });
    const mainRest = breakAt >= 0 ? rest.slice(0, breakAt) : rest;
    const prepRest = breakAt >= 0 ? rest.slice(breakAt) : [];
    if (mainRest.length) {
      const label = pattern.kind === "svc" ? "C 補語" : pattern.kind === "sv" ? "修飾" : "O 目的語";
      chunks.push({ label, text: mainRest.join(" "), kind: "object" });
    }
    if (prepRest.length) chunks.push({ label: "修飾句", text: prepRest.join(" "), kind: "modifier" });
  }
  return chunks;
}

function findReadingSignals(text) {
  const low = text.toLowerCase();
  const hits = [];
  READING_SIGNAL_GROUPS.forEach(g => {
    g.phrases.forEach(p => {
      const re = new RegExp(`\\b${p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+")}\\b`, "i");
      if (re.test(low)) {
        hits.push({ key: g.key, label: g.label, match: p, words: p.split(/\s+/) });
      }
    });
  });
  const seen = new Set();
  return hits.filter(h => {
    const k = `${h.key}:${h.match}`;
    if (seen.has(k)) return false;
    seen.add(k); return true;
  });
}

function sentenceFocus(signals, sentenceIndex) {
  const keys = new Set(signals.map(s => s.key));
  if (keys.has("conclusion")) return "結論";
  if (keys.has("result")) return "結果";
  if (keys.has("reason") || keys.has("cause")) return "理由・原因";
  if (keys.has("contrast")) return "対比";
  if (keys.has("example")) return "具体例";
  if (sentenceIndex === 0) return "要点";
  if (keys.has("reference")) return "指示語に注意";
  return "詳細";
}

function paragraphRole(text, index, total) {
  const signals = findReadingSignals(text);
  const keys = new Set(signals.map(s => s.key));
  if (index === 0) return { role: "導入・話題提示", reason: "最初の段落なので、テーマや問題意識をつかみます。" };
  if (index === total - 1 && (keys.has("conclusion") || keys.has("result"))) {
    return { role: "結論・まとめ", reason: "結論/結果のシグナルがあり、筆者の主張を回収しています。" };
  }
  if (keys.has("example")) return { role: "具体例", reason: "for example などで抽象的な内容を具体化しています。" };
  if (keys.has("reason") || keys.has("cause")) return { role: "理由・原因の説明", reason: "because などで主張の根拠を示しています。" };
  if (keys.has("contrast")) return { role: "対比・転換", reason: "however などで前の内容との違いを示しています。" };
  if (keys.has("result")) return { role: "結果・影響", reason: "therefore などで結果や帰結を示しています。" };
  return { role: "展開・補足", reason: "前後の段落を支える説明部分です。" };
}

// ---------- PROFILE / SETTINGS ----------
routes.profile = async () => {
  const [profile, health, providerInfo] = await Promise.all([
    api("/profile"),
    api("/health").catch(() => null),
    api("/ai/provider").catch(() => null),
  ]);
  const p = state.profile = profile;
  if (health?.ai) state.ai = health.ai;
  if (providerInfo?.status) state.ai = providerInfo.status;
  state.ai.speech = await api("/speech/status").catch(() => state.ai.speech || {});
  setAiBadge();
  const styles = state.artStyles = state.artStyles || await api("/art-styles");
  const speech = state.ai.speech || {};
  const chatOk = !!state.ai.online;
  const speechOk = !!speech.online;
  const speechCache = speechOk ? (speech.cached ? "ダウンロード済み" : "未ダウンロード/初回準備") : "利用不可";
  const provider = (providerInfo?.provider || state.ai.provider || "foundry");
  const providerLabel = state.ai.provider_label || providerInfo?.provider_label || "Foundry Local";
  const baseUrl = providerInfo?.base_url || "";
  const apiKeyEnv = providerInfo?.api_key_env || "";
  const azureEndpoint = providerInfo?.azure_endpoint || "";
  const azureApiVersion = providerInfo?.azure_api_version || "";
  const providerOptions = [
    ["foundry", "Foundry Local"],
    ["ollama", "Ollama"],
    ["openai", "OpenAI互換URL"],
    ["chatgpt", "OpenAI (ChatGPT)"],
    ["azure", "Azure OpenAI"],
  ].map(([v, label]) => `<option value="${v}" ${provider === v ? "selected" : ""}>${label}</option>`).join("");
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
    <h2>AI接続</h2>
    <div class="card ai-panel">
      <div class="provider-grid">
        <div>
          <label>AIプロバイダー</label>
          <select id="aiProvider">${providerOptions}</select>
        </div>
        <div data-field="base_url">
          <label>接続URL</label>
          <input id="aiBaseUrl" value="${esc(baseUrl)}" placeholder="http://localhost:11434/v1" />
        </div>
        <div data-field="azure_endpoint">
          <label>Azure エンドポイント</label>
          <input id="aiAzureEndpoint" value="${esc(azureEndpoint)}" placeholder="https://<resource>.openai.azure.com" />
        </div>
        <div data-field="azure_api_version">
          <label>API バージョン</label>
          <input id="aiAzureApiVersion" value="${esc(azureApiVersion)}" placeholder="2024-10-21" />
        </div>
        <div data-field="api_key_env">
          <label>APIキーの環境変数名</label>
          <input id="aiApiKeyEnv" value="${esc(apiKeyEnv)}" placeholder="OPENAI_API_KEY" />
          <span class="muted" style="font-size:.78rem">${providerInfo?.uses_env_key ? (providerInfo?.has_api_key ? "✓ 環境変数が設定されています" : "⚠ この環境変数が未設定です") : ""}</span>
        </div>
        <div data-field="chat_model">
          <label>モデル / デプロイ名</label>
          <input id="aiChatModelName" value="${esc(providerInfo?.chat_model || "")}" placeholder="gpt-4o-mini" />
        </div>
        <div data-field="api_key">
          <label>APIキー</label>
          <input id="aiApiKey" type="password" placeholder="${providerInfo?.has_api_key ? "保存済み（変更時のみ入力）" : "任意"}" />
        </div>
        <button class="btn sm" id="saveAiProvider">接続先を保存</button>
      </div>
      <p class="sub provider-help" id="aiProviderHelp"></p>
      <div class="ai-row">
        <div>
          <div class="ai-kind">会話・翻訳・採点（${esc(providerLabel)}）</div>
          <div class="muted" id="aiChatModels">会話: ${esc(state.ai.model || "モデル未確認")}<br>和訳・添削: ${esc(state.ai.translate_model || state.ai.model || "モデル未確認")}</div>
        </div>
        <b id="aiChatConn" class="${chatOk ? "ok-text" : "warn-text"}">${chatOk ? "接続中 ✓" : "未接続"}</b>
      </div>
      <div class="ai-note" id="aiChatNote">${esc(state.ai.note || "")}</div>
      <div class="ai-test-box">
        <div class="ai-test-actions">
          <button class="btn sm" id="testChatModel">会話モデルをテスト</button>
          <button class="btn sm ghost" id="testTranslateModel">和訳モデルをテスト</button>
        </div>
        <div id="aiTestResult" class="ai-test-result muted">接続中のモデルに短いリクエストを送り、実際に応答できるか確認できます。</div>
      </div>
      <div class="ai-row">
        <div>
          <div class="ai-kind">音声認識（Whisper）</div>
          <div class="muted" id="aiSpeechModel">${esc(speech.model || "モデル未確認")} / ${esc(speechCache)}</div>
        </div>
        <b id="aiSpeechConn" class="${speechOk ? "ok-text" : "warn-text"}">${speechOk ? "接続中 ✓" : "未接続"}</b>
      </div>
      <div class="ai-note" id="aiSpeechNote">${esc(speech.note || "")}</div>
      <p class="sub" style="margin:.8rem 0 0">録音して発話は音声認識（Whisper）で文字起こししたあと、選択中の会話・翻訳モデルで採点します。音声認識は Foundry Local のWhisperを使います。</p>
    </div>

    <h2>AIモデルの選択・追加</h2>
    <p class="sub">用途ごとに使用するモデルを切り替えられます。Foundry Localでは未取得モデルのダウンロードや取得済みモデルの削除もここから行えます。</p>
    <div class="card model-panel" id="modelPanel">
      <div class="muted">モデル一覧を読み込み中…</div>
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
  bindAiProviderForm();
  bindAiTestButtons();
  loadModelPanel();
};

function bindAiProviderForm() {
  const providerEl = $("#aiProvider");
  const baseEl = $("#aiBaseUrl");
  const keyEl = $("#aiApiKey");
  const keyEnvEl = $("#aiApiKeyEnv");
  const azureEndpointEl = $("#aiAzureEndpoint");
  const azureVersionEl = $("#aiAzureApiVersion");
  const chatModelEl = $("#aiChatModelName");
  const helpEl = $("#aiProviderHelp");
  if (!providerEl || !baseEl) return;
  const defaults = {
    foundry: "",
    ollama: "http://localhost:11434/v1",
    openai: "",
    chatgpt: "https://api.openai.com/v1",
  };
  const keyEnvDefaults = { chatgpt: "OPENAI_API_KEY", azure: "AZURE_OPENAI_API_KEY" };
  const modelPlaceholders = { chatgpt: "gpt-4o-mini", azure: "（Azureのデプロイ名）" };
  const helps = {
    foundry: "Foundry Localを自動起動・自動検出します。接続URLとAPIキーは通常不要です。",
    ollama: "Ollamaを使う場合は先にOllamaを起動し、モデルをpullしてください。例: ollama pull qwen2.5:3b",
    openai: "OpenAI互換の /v1 エンドポイントを指定できます。必要な場合だけAPIキーを入力してください。",
    chatgpt: "OpenAI公式API(api.openai.com)に接続します。APIキーはOSの環境変数に設定し、ここにはその環境変数名を入力します（キー自体はアプリに保存されません）。",
    azure: "Azure OpenAI に接続します。エンドポイント・APIバージョン・デプロイ名を指定し、APIキーは環境変数名で指定します（キー自体はアプリに保存されません）。",
  };
  // Which fields each provider shows.
  const FIELDS = {
    foundry: [],
    ollama: ["base_url"],
    openai: ["base_url", "api_key"],
    chatgpt: ["base_url", "api_key_env", "chat_model"],
    azure: ["azure_endpoint", "azure_api_version", "api_key_env", "chat_model"],
  };
  const sync = () => {
    const p = providerEl.value;
    const show = new Set(FIELDS[p] || []);
    $$("[data-field]").forEach(el => {
      el.style.display = show.has(el.dataset.field) ? "" : "none";
    });
    baseEl.placeholder = defaults[p] || "";
    helpEl.textContent = helps[p] || "";
    if (p === "ollama" && !baseEl.value.trim()) baseEl.value = defaults.ollama;
    if (p === "chatgpt" && !baseEl.value.trim()) baseEl.value = defaults.chatgpt;
    if (keyEnvEl && keyEnvDefaults[p] && !keyEnvEl.value.trim()) keyEnvEl.value = keyEnvDefaults[p];
    if (chatModelEl) chatModelEl.placeholder = modelPlaceholders[p] || "";
  };
  providerEl.addEventListener("change", sync);
  sync();

  $("#saveAiProvider").addEventListener("click", async () => {
    const provider = providerEl.value;
    const body = { provider };
    const fields = new Set(FIELDS[provider] || []);
    if (fields.has("base_url")) body.base_url = baseEl.value.trim();
    if (fields.has("api_key") && keyEl && keyEl.value.trim()) body.api_key = keyEl.value.trim();
    if (fields.has("api_key_env") && keyEnvEl) body.api_key_env = keyEnvEl.value.trim();
    if (fields.has("azure_endpoint") && azureEndpointEl) body.azure_endpoint = azureEndpointEl.value.trim();
    if (fields.has("azure_api_version") && azureVersionEl) body.azure_api_version = azureVersionEl.value.trim();
    if (fields.has("chat_model") && chatModelEl) body.chat_model = chatModelEl.value.trim();
    try {
      state.ai = await post("/ai/provider", body);
      setAiBadge();
      toast(state.ai.online ? "AI接続先を保存して接続しました" : "AI接続先を保存しました");
      routes.profile();
    } catch {
      toast("AI接続先の保存に失敗しました");
    }
  });
}

function bindAiTestButtons() {
  const chatBtn = $("#testChatModel");
  const translateBtn = $("#testTranslateModel");
  const out = $("#aiTestResult");
  if (!chatBtn || !translateBtn || !out) return;
  const run = async (kind, btn) => {
    const buttons = [chatBtn, translateBtn];
    const labels = new Map(buttons.map(b => [b, b.textContent]));
    buttons.forEach(b => b.disabled = true);
    btn.textContent = "テスト中…";
    out.className = "ai-test-result";
    out.innerHTML = thinkingHtml(`${kind === "translate" ? "和訳" : "会話"}モデルへテスト送信中…`);
    try {
      const r = await post("/ai/test", { kind });
      if (r.status) {
        state.ai = r.status;
        setAiBadge();
        refreshAiConnectionPanel();
      }
      const cls = r.ok ? "notice ok" : "notice";
      const elapsed = r.elapsed_ms != null ? ` / ${r.elapsed_ms}ms` : "";
      const sample = r.sample ? `<div class="ai-test-sample">${esc(r.sample)}</div>` : "";
      out.className = "ai-test-result";
      out.innerHTML = `<div class="${cls}"><b>${r.ok ? "テスト成功" : "テスト失敗"}</b><br>
        ${esc(r.provider_label || "AI")} / ${esc(r.model || "モデル未確認")}${elapsed}<br>
        <span class="muted">${esc(r.note || "")}</span>${sample}</div>`;
    } catch (e) {
      out.className = "ai-test-result";
      out.innerHTML = `<div class="notice">テストAPIに接続できませんでした。<br><span class="muted">${esc(String(e))}</span></div>`;
    } finally {
      buttons.forEach(b => { b.disabled = false; b.textContent = labels.get(b); });
    }
  };
  chatBtn.addEventListener("click", () => run("chat", chatBtn));
  translateBtn.addEventListener("click", () => run("translate", translateBtn));
}

// ---------- AI model management (settings) ----------
const MODEL_KINDS = [
  { kind: "chat", label: "会話モデル", hint: "AI対話・採点に使用", filter: m => m.kind === "chat" },
  { kind: "translate", label: "和訳・添削モデル", hint: "空欄なら会話モデルを使用", filter: m => m.kind === "chat", allowDefault: true },
  { kind: "transcribe", label: "音声認識モデル（Whisper）", hint: "録音した発話の文字起こし", filter: m => m.kind === "speech" },
];

const MODEL_KIND_LABELS = { chat: "会話", translate: "和訳", transcribe: "音声" };

function refreshAiConnectionPanel() {
  const speech = state.ai.speech || {};
  const chatOk = !!state.ai.online;
  const speechOk = !!speech.online;
  const speechCache = speechOk ? (speech.cached ? "ダウンロード済み" : "未ダウンロード/初回準備") : "利用不可";
  const chatModels = $("#aiChatModels");
  if (!chatModels) return;
  chatModels.innerHTML = `会話: ${esc(state.ai.model || "モデル未確認")}<br>和訳・添削: ${esc(state.ai.translate_model || state.ai.model || "モデル未確認")}`;
  const chatConn = $("#aiChatConn");
  chatConn.className = chatOk ? "ok-text" : "warn-text";
  chatConn.textContent = chatOk ? "接続中 ✓" : "未接続";
  $("#aiChatNote").textContent = state.ai.note || "";
  $("#aiSpeechModel").textContent = `${speech.model || "モデル未確認"} / ${speechCache}`;
  const speechConn = $("#aiSpeechConn");
  speechConn.className = speechOk ? "ok-text" : "warn-text";
  speechConn.textContent = speechOk ? "接続中 ✓" : "未接続";
  $("#aiSpeechNote").textContent = speech.note || "";
}

function modelHasKind(m, field, kind) {
  return Array.isArray(m[field]) && m[field].includes(kind);
}

function modelStateLine(kind, cur, configured, active) {
  if (active) {
    const suffix = kind.allowDefault && !cur ? "（会話モデルと同じ）" : "";
    return `現在使用中: ${active.id}${suffix}`;
  }
  if (configured) return `選択中: ${configured.id}（未読み込み）`;
  if (cur) return `選択設定: ${cur}（${kind.external ? "接続先で未検出" : "未ダウンロード"}）`;
  return "会話モデルと同じ";
}

async function loadModelPanel() {
  const panel = $("#modelPanel");
  if (!panel) return;
  let data;
  try { data = await api("/ai/models"); }
  catch { panel.innerHTML = `<div class="muted">モデル一覧を取得できませんでした。</div>`; return; }
  if (!data.online) {
    panel.innerHTML = `<div class="notice">${esc(data.note || "Foundry Local SDK が利用できないため、モデルを管理できません。")}</div>`;
    return;
  }
  if (data.status) {
    state.ai = data.status;
    setAiBadge();
    refreshAiConnectionPanel();
  }
  const models = data.models || [];
  const sel = data.selected || {};
  const manageable = data.manageable !== false;
  const providerName = data.status?.provider_label || state.ai.provider_label || "接続先";
  const modelKinds = manageable ? MODEL_KINDS : MODEL_KINDS.filter(k => k.kind !== "transcribe");

  const rows = modelKinds.map(k => {
    // Only cached (downloaded) models can be selected for use.
    const opts = models.filter(k.filter).filter(m => m.cached);
    const cur = sel[k.kind] || "";
    const active = opts.find(m => modelHasKind(m, "active_kinds", k.kind));
    const configured = opts.find(m => modelHasKind(m, "selected_kinds", k.kind));
    const selectedModel = active || configured;
    const selectedValue = k.allowDefault && !cur ? "" : (selectedModel ? selectedModel.id : "");
    const hasSelectedValue = selectedValue && opts.some(m => m.id === selectedValue);
    const missingOpt = cur && !hasSelectedValue && !(k.allowDefault && !cur)
      ? `<option value="${esc(cur)}" selected disabled>${esc(cur)}（${manageable ? "未ダウンロード" : "未検出"}）</option>`
      : "";
    const empty = opts.length === 0
      ? `<div class="muted" style="font-size:.82rem">${manageable ? "ダウンロード済みのモデルがありません。下のカタログから取得してください。" : "接続先から利用可能なモデルを取得できませんでした。"}</div>`
      : "";
    const optionTags = (k.allowDefault ? [`<option value="" ${!cur ? "selected" : ""}>（会話モデルと同じ）</option>`] : [])
      .concat(missingOpt ? [missingOpt] : [])
      .concat(opts.map(m => {
        const isSel = !!selectedValue && m.id === selectedValue;
        return `<option value="${esc(m.id)}" ${isSel ? "selected" : ""}>${esc(m.id)}</option>`;
      })).join("");
    return `
      <div class="model-row">
        <div class="model-row-head">
          <b>${esc(k.label)}</b><span class="muted">${esc(k.hint)}</span>
        </div>
        <div class="model-row-ctl">
          ${opts.length ? `<select class="model-select" data-kind="${k.kind}">${optionTags}</select>` : empty}
          <div class="model-state">${esc(modelStateLine({ ...k, external: !manageable }, cur, configured, active))}</div>
        </div>
      </div>`;
  }).join("");

  // List of all models with download buttons for not-yet-cached ones.
  const catalog = models.map(m => {
    const activeKinds = (m.active_kinds || []).map(k => MODEL_KIND_LABELS[k] || k);
    const readyText = manageable ? "ダウンロード済み" : "利用可能";
    const badge = m.cached
      ? `<span class="model-badge ok">${readyText}</span>`
      : `<span class="model-badge">未取得</span>`;
    const activeBadge = activeKinds.length
      ? `<span class="model-badge active">使用中: ${esc(activeKinds.join("・"))}</span>`
      : "";
    const loadedBadge = m.cached && m.loaded && !activeKinds.length
      ? `<span class="model-badge loaded">読み込み済み</span>`
      : "";
    const kindJa = m.kind === "speech" ? "音声" : m.kind === "chat" ? "会話" : "その他";
    const deleteBtn = manageable && m.cached
      ? `<button class="btn sm danger" data-delmodel="${esc(m.id)}" data-active="${activeKinds.length ? "1" : ""}">削除</button>`
      : "";
    const act = m.cached
      ? `${activeBadge}${loadedBadge}${deleteBtn}`
      : manageable
        ? `<button class="btn sm" data-dl="${esc(m.id)}" data-dlkind="${m.kind === "speech" ? "transcribe" : "chat"}">⬇ ダウンロード</button>`
        : `<span class="model-badge">接続先側で管理</span>`;
    return `
      <div class="catalog-row" data-row="${esc(m.id)}">
        <div><span class="model-id">${esc(m.id)}</span> <span class="model-kind">${kindJa}</span></div>
        <div class="catalog-act">${badge}${act}</div>
      </div>`;
  }).join("") || `<div class="muted">利用可能なモデルがありません。</div>`;

  panel.innerHTML = `
    ${manageable ? "" : `<div class="notice">モデルの追加・削除は${esc(providerName)}側で行ってください。この画面では接続先が返したモデルを選択できます。</div>`}
    <div class="model-selects">${rows}</div>
    <div class="field-label" style="margin-top:1rem">${manageable ? "カタログ（ダウンロード・削除）" : "接続先モデル"}</div>
    <div class="catalog-list">${catalog}</div>`;

  $$(".model-select", panel).forEach(s => s.addEventListener("change", async () => {
    try {
      state.ai = await post("/ai/models/select", { kind: s.dataset.kind, alias: s.value });
      setAiBadge();
      refreshAiConnectionPanel();
      toast("モデルを切り替えました");
      loadModelPanel();
    } catch { toast("モデルの切り替えに失敗しました"); }
  }));
  $$("[data-dl]", panel).forEach(b => b.addEventListener("click", () => {
    startModelDownload(b.dataset.dl, b.dataset.dlkind, b);
  }));
  $$("[data-delmodel]", panel).forEach(b => b.addEventListener("click", () => {
    deleteModel(b.dataset.delmodel, b.dataset.active === "1", b);
  }));
}

// Download a catalog model and show inline progress on the button itself,
// polling /api/ai/setup-state until it finishes (works even while AI is online).
let _dlPoll = null;
async function startModelDownload(alias, kind, btn) {
  if (_dlPoll) { toast("別のダウンロードが進行中です"); return; }
  const setLabel = (txt) => { btn.disabled = true; btn.textContent = txt; };
  setLabel("開始中…");
  let st;
  try { st = await post("/ai/models/download", { alias, kind }); }
  catch { toast("ダウンロードを開始できませんでした"); btn.disabled = false; btn.textContent = "⬇ ダウンロード"; return; }
  if (st.state === "error") {
    btn.disabled = false; btn.textContent = "⬇ 再試行";
    toast(st.message || "ダウンロードに失敗しました");
    return;
  }
  toast("ダウンロードを開始しました");

  const renderDl = (s) => {
    // Only reflect progress for the model we're downloading.
    if (s.model && s.model !== alias && !String(s.model).includes(alias)) return;
    if (s.state === "downloading") {
      const p = Math.max(0, Math.min(100, Math.round(s.progress || 0)));
      setLabel(`DL中 ${p}%`);
    } else if (s.state === "loading") {
      setLabel("読み込み中…");
    } else if (s.state === "checking" || s.state === "preparing") {
      setLabel("準備中…");
    }
  };
  renderDl(st);

  _dlPoll = setInterval(async () => {
    let s;
    try { s = await api("/ai/setup-state"); } catch { return; }
    if (["ready", "offline", "error"].includes(s.state)) {
      clearInterval(_dlPoll); _dlPoll = null;
      if (s.state === "error") {
        btn.disabled = false; btn.textContent = "⬇ 再試行";
        toast(s.message || "ダウンロードに失敗しました");
      } else {
        toast(`${alias} のダウンロードが完了しました`);
        loadModelPanel();   // refresh: model now cached & selectable
      }
      return;
    }
    renderDl(s);
  }, 1200);
}

async function deleteModel(alias, isActive, btn) {
  if (_dlPoll) { toast("ダウンロード中は削除できません"); return; }
  const msg = isActive
    ? `${alias} は現在使用中です。削除すると選択設定を解除します。削除しますか？`
    : `${alias} を削除しますか？`;
  if (!confirm(msg)) return;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "削除中…";
  try {
    const data = await post("/ai/models/delete", { alias });
    if (data.status) {
      state.ai = data.status;
      setAiBadge();
      refreshAiConnectionPanel();
    }
    toast(`${alias} を削除しました`);
    loadModelPanel();
  } catch {
    btn.disabled = false;
    btn.textContent = original;
    toast("モデルの削除に失敗しました");
  }
}

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
  // Keep downloading/loading in the background; just hide the overlay so the
  // learner can use pre-authored content while the model finishes preparing.
  $("#setupBackground").addEventListener("click", () => {
    e.box.classList.add("hidden");
    toast("AIの準備はバックグラウンドで続行します");
  });
  // If AI is already online and ready, skip entirely.
  if (state.ai.online && state.ai.note === "ready" && state.ai.speech?.online && state.ai.speech?.cached) return;

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
  if (["ready", "offline", "error"].includes(st.state)) {
    stopSetup();
    if (st.state === "ready") {
      renderSetup(st);
      state.ai = await post("/ai/reconnect", {}); setAiBadge();
      setTimeout(() => _setupEls().box.classList.add("hidden"), 1400);
      toast("AIの準備が完了しました");
      if ($("#modelPanel")) loadModelPanel();   // refresh download/cached state
    } else if (st.state === "offline" && (state.ai.online || state.ai.speech?.online)) {
      _setupEls().box.classList.add("hidden");
    } else {
      renderSetup(st);
    }
    return;
  }
  renderSetup(st);
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
  const working = ["checking", "preparing", "downloading", "loading"].includes(st.state);
  const bg = $("#setupBackground");
  if (bg) bg.classList.toggle("hidden", !working);
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
