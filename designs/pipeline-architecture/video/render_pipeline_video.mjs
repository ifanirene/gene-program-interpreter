import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import playwright from "/tmp/gpi-playwright/node_modules/playwright-core/index.js";

const { chromium } = playwright;

const ROOT = "/Volumes/IF_PHAGE/gene-program-interpreter";
const OUT = path.join(ROOT, "designs/pipeline-architecture/video");
const TMP = "/tmp/gpi-pipeline-video";
const AUDIO_DIR = path.join(OUT, "audio_segments");
const FRAMES_DIR = path.join(TMP, "frames");
const MANIFEST = path.join(OUT, "narration_segments.json");
const ARCHITECTURE = path.join(ROOT, "designs/pipeline-architecture/Pipeline Architecture.html");
const ACTUAL_REPORT = "/Volumes/IF_PHAGE/ProgExplorer/results/output/hepatocyte_mouse_perturbseq_programs0617_pre_literature/annotations_sonnet46_high_all50_most_recent_clean/report.html";
const BACKGROUND = path.join(OUT, "GPI_bottleneck_background.png");
const FFMPEG = "/Volumes/IF_PHAGE/conda_envs/multitask/bin/ffmpeg";
const FFPROBE = "/Volumes/IF_PHAGE/conda_envs/multitask/bin/ffprobe";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const FPS = 24;
const WIDTH = 1920;
const HEIGHT = 1080;
const PLAYBACK_SPEED = 1.2;
const SOURCE_PRE_SILENCE = 0.7;
const SOURCE_GAP = 0.35;
const SOURCE_TAIL = 2.5;
const PRE_SILENCE = SOURCE_PRE_SILENCE / PLAYBACK_SPEED;
const GAP = SOURCE_GAP / PLAYBACK_SPEED;
const TAIL = SOURCE_TAIL / PLAYBACK_SPEED;

fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(AUDIO_DIR, { recursive: true });
fs.mkdirSync(FRAMES_DIR, { recursive: true });

function run(command, args, options = {}) {
  const proc = spawnSync(command, args, {
    stdio: options.quiet ? "pipe" : "inherit",
    encoding: "utf8",
    ...options,
  });
  if (proc.status !== 0) {
    throw new Error(`${command} failed (${proc.status})\n${proc.stderr || ""}`);
  }
  return proc.stdout || "";
}

function duration(file) {
  return Number(run(FFPROBE, [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=nw=1:nk=1",
    file,
  ], { quiet: true }).trim());
}

function wrapCaption(text, max = 54) {
  const words = text.split(/\s+/);
  const lines = [];
  let line = "";
  for (const word of words) {
    if (!line) line = word;
    else if (`${line} ${word}`.length <= max) line += ` ${word}`;
    else { lines.push(line); line = word; }
  }
  if (line) lines.push(line);
  if (lines.length <= 2) return lines.join("\n");
  const midpoint = Math.ceil(words.length / 2);
  return `${words.slice(0, midpoint).join(" ")}\n${words.slice(midpoint).join(" ")}`;
}

function captionChunks(text, maxWords = 11) {
  const sentences = text.match(/[^.!?]+[.!?]+|[^.!?]+$/g) ?? [text];
  const chunks = [];
  for (const sentence of sentences) {
    const words = sentence.trim().split(/\s+/).filter(Boolean);
    for (let i = 0; i < words.length; i += maxWords) chunks.push(words.slice(i, i + maxWords).join(" "));
  }
  return chunks;
}

function srtTime(seconds) {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const z = ms % 1000;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(z).padStart(3, "0")}`;
}

const segments = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
const audioFiles = segments.map((segment, index) => {
  const file = path.join(AUDIO_DIR, `${segment.id}.wav`);
  if (!fs.existsSync(file)) throw new Error(`Missing voice segment: ${file}`);
  return file;
});

let cursor = PRE_SILENCE;
const timeline = segments.map((segment, index) => {
  const d = duration(audioFiles[index]) / PLAYBACK_SPEED;
  const item = { ...segment, index, start: cursor, end: cursor + d, duration: d };
  cursor = item.end + (index < segments.length - 1 ? GAP : 0);
  return item;
});
const targetDuration = cursor + TAIL;

const voiceover = path.join(OUT, "GPI_pipeline_voiceover.m4a");
const inputs = ["-f", "lavfi", "-t", String(SOURCE_PRE_SILENCE), "-i", "anullsrc=r=48000:cl=mono"];
for (let i = 0; i < audioFiles.length; i++) {
  inputs.push("-i", audioFiles[i]);
  if (i < audioFiles.length - 1) {
    inputs.push("-f", "lavfi", "-t", String(SOURCE_GAP), "-i", "anullsrc=r=48000:cl=mono");
  }
}
inputs.push("-f", "lavfi", "-t", String(SOURCE_TAIL), "-i", "anullsrc=r=48000:cl=mono");
const nAudioInputs = 1 + audioFiles.length + (audioFiles.length - 1) + 1;
const normalizeParts = [];
const concatParts = [];
for (let i = 0; i < nAudioInputs; i++) {
  normalizeParts.push(`[${i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono[a${i}]`);
  concatParts.push(`[a${i}]`);
}
const audioFilter = `${normalizeParts.join(";")};${concatParts.join("")}concat=n=${nAudioInputs}:v=0:a=1,atempo=${PLAYBACK_SPEED},highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=7[aout]`;
run(FFMPEG, ["-y", ...inputs, "-filter_complex", audioFilter, "-map", "[aout]", "-ar", "48000", "-c:a", "aac", "-b:a", "192k", voiceover], { quiet: true });

let captionIndex = 1;
const captions = timeline.flatMap(item => {
  const chunks = captionChunks(item.text);
  const totalWords = chunks.reduce((n, chunk) => n + chunk.split(/\s+/).length, 0);
  let at = item.start;
  return chunks.map((chunk, index) => {
    const words = chunk.split(/\s+/).length;
    const end = index === chunks.length - 1 ? item.end : at + item.duration * words / totalWords;
    const cue = `${captionIndex++}\n${srtTime(at)} --> ${srtTime(end)}\n${wrapCaption(chunk, 42)}\n`;
    at = end;
    return cue;
  });
}).join("\n");
fs.writeFileSync(path.join(OUT, "GPI_pipeline_captions.srt"), captions);
const transcript = [
  "# Gene Program Interpreter — narrated walkthrough",
  "",
  ...timeline.flatMap(item => [
    `## ${srtTime(item.start).replace(",", ".")} — ${item.id.replaceAll("_", " ")}`,
    "",
    item.text,
    "",
  ]),
].join("\n");
fs.writeFileSync(path.join(OUT, "GPI_pipeline_transcript.md"), transcript);
fs.writeFileSync(path.join(OUT, "GPI_pipeline_timeline.json"), JSON.stringify({ fps: FPS, duration: targetDuration, timeline }, null, 2));

const browser = await chromium.launch({
  headless: true,
  executablePath: CHROME,
  args: ["--allow-file-access-from-files", "--disable-features=Translate", "--hide-scrollbars", "--disable-blink-features=AutomationControlled"],
});
const context = await browser.newContext({
  viewport: { width: WIDTH, height: HEIGHT },
  screen: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  colorScheme: "dark",
  locale: "en-US",
  userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
  extraHTTPHeaders: { "Accept-Language": "en-US,en;q=0.9" },
});

// PubMed may replace a page opened by an automated click with an anti-bot
// challenge. Keep the real click in the walkthrough, but preload the same
// official PMID in an isolated, clean browser context for the following shot.
const pubmedContext = await browser.newContext({
  viewport: { width: WIDTH, height: HEIGHT },
  screen: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  colorScheme: "light",
  locale: "en-US",
  userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
  extraHTTPHeaders: { "Accept-Language": "en-US,en;q=0.9" },
});
const cleanPubmedPage = await pubmedContext.newPage();
const pubmedResponse = await cleanPubmedPage.goto(
  "https://pubmed.ncbi.nlm.nih.gov/28628040/",
  { waitUntil: "domcontentloaded", timeout: 45_000 },
);
if (!pubmedResponse?.ok()) {
  throw new Error(`Could not load official PubMed record (HTTP ${pubmedResponse?.status() ?? "unknown"})`);
}
await cleanPubmedPage.evaluate(() => window.scrollTo(0, 0));

const backgroundData = `data:image/png;base64,${fs.readFileSync(BACKGROUND).toString("base64")}`;
const introPage = await context.newPage();
await introPage.setContent(`<!doctype html>
<html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box} html,body{width:100%;height:100%;margin:0;overflow:hidden}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#f3f8f6;background:#10211d url('${backgroundData}') center/cover no-repeat}
  body::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(6,16,14,.97) 0%,rgba(8,24,20,.91) 42%,rgba(8,22,18,.28) 72%,rgba(4,12,10,.2) 100%)}
  .card{position:absolute;left:84px;top:50%;transform:translateY(-50%);width:920px;padding:54px 62px;background:rgba(7,22,18,.79);border:1px solid rgba(94,234,212,.2);border-radius:28px;box-shadow:0 28px 90px rgba(0,0,0,.42);backdrop-filter:blur(9px)}
  .eyebrow{font:750 17px/1.2 ui-monospace,"SF Mono",monospace;letter-spacing:.18em;text-transform:uppercase;color:#56d8c5;margin-bottom:20px}
  h1{font:850 82px/.94 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:-.055em;margin:0 0 30px;max-width:820px;color:#fff}
  p{font-size:27px;line-height:1.4;color:#c7d8d3;margin:0;max-width:760px}
  .bottleneck{margin-top:34px;padding:24px 28px;border-left:4px solid #40d5bd;background:rgba(21,69,59,.5);border-radius:0 16px 16px 0;opacity:.3;transform:translateY(10px);transition:opacity .7s,transform .7s}
  body.problem .bottleneck{opacity:1;transform:none}
  .problem-label{font:800 16px/1.2 ui-monospace,"SF Mono",monospace;letter-spacing:.13em;text-transform:uppercase;color:#75ead8;margin-bottom:12px}
  .problem-line{font-size:24px;line-height:1.45;color:#f0f7f5}.problem-line b{color:#fff}
  .pills{display:flex;gap:12px;flex-wrap:wrap;margin-top:28px}
  .pill{font-size:16px;font-weight:750;color:#8ef1e1;background:rgba(16,90,77,.45);border:1px solid rgba(75,222,198,.25);border-radius:999px;padding:9px 16px}
  .stats{display:none;margin-top:34px;padding-top:24px;border-top:1px solid rgba(133,220,204,.2);font:650 18px/1.45 ui-monospace,"SF Mono",monospace;color:#9fb9b2}
  body.end .opening{display:none} body.end .ending{display:block}
  .ending{display:none} body.end .stats{display:block}
</style></head><body>
  <section class="card opening"><div class="eyebrow">Claude Skill · single-cell &amp; Perturb-seq</div><h1>Gene Program<br>Interpreter</h1><p>Context-aware interpretation that shows its evidence.</p><div class="bottleneck"><div class="problem-label">The interpretation bottleneck</div><div class="problem-line"><b>Dozens</b> of anonymous programs · <b>hours</b> of PubMed review · generic enrichment or plausible, ungrounded guesses</div></div><div class="pills"><span class="pill">Context in every step</span><span class="pill">Parallel research</span><span class="pill">Resolvable citations</span></div></section>
  <section class="card ending"><div class="eyebrow">Gene Program Interpreter</div><h1>Give time back<br>to the science.</h1><p>From anonymous gene lists to context-aware, auditable biology—without changing code.</p><div class="stats">AI-generated narration · real report · resolvable evidence links</div></section>
</body></html>`);

const architecturePage = await context.newPage();
await architecturePage.addInitScript(() => {
  localStorage.setItem("gpi-theme", "dark");
  Object.defineProperty(window, "matchMedia", {
    value: query => ({ matches: query.includes("prefers-color-scheme: dark"), media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }),
  });
});
await architecturePage.goto(pathToFileURL(ARCHITECTURE).href, { waitUntil: "load" });
await architecturePage.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
await architecturePage.evaluate(() => {
  document.documentElement.setAttribute("data-theme", "dark");
  document.getElementById("themeLabel").textContent = "Light";
  document.getElementById("tourBtn").innerHTML = '<span class="play-glyph">▶</span>&nbsp;Follow Program 22';
  const crumb = document.querySelector(".context-note");
  if (crumb) crumb.innerHTML = '<span>Walkthrough grounded in a real run:</span><span class="mono">mouse hepatocyte Perturb-seq</span><span class="dotsep"></span><span class="mono">Program 22</span><span class="dotsep"></span><span>fructose-driven lipogenesis</span>';
  const inputStage = document.querySelector("#s0");
  inputStage.querySelector(".desc").innerHTML = "One weighted gene list per program is packaged into a lean <b>research brief</b>: marker and distinctive genes, organism, cell type, conditions, normal-function terms, and condition-specific perturbation regulators. This brief is the <b>only</b> thing each literature agent sees.";
  const brief = inputStage.querySelector(".brief");
  brief.querySelector(".brief-head .id").textContent = "P22";
  const firstLabel = brief.querySelector(".brief-label");
  firstLabel.textContent = "research context — derived from ContextProfile";
  const fieldRow = brief.querySelector(".fieldrow");
  fieldRow.innerHTML = '<span><span class="k">organism</span> <span class="v">mouse</span></span><span><span class="k">cell type</span> <span class="v">hepatocyte</span></span><span><span class="k">conditions</span> <span class="v">aging · MASLD</span></span>';
  const regColumns = brief.querySelectorAll(".reg-split > div");
  if (regColumns[0]) regColumns[0].querySelector(".chips").innerHTML = '<span class="chip up">Mlxipl / ChREBP</span><span class="chip up">Dgat2</span>';
  if (regColumns[1]) regColumns[1].querySelector(".chips").innerHTML = '<span class="chip down">Insig1</span>';
  const mx = document.getElementById("matrix");
  const p22Genes = [["Me1",1],["Pklr",.91],["Acly",.82],["Khk",.74],["Fasn",.68],["Mt1",.58],["Aldh2",.51]];
  mx.innerHTML = p22Genes.map((g,i) => `<div class="matrix-row"><span class="g">${g[0]}</span><div><div class="bar${i > 3 ? " dim" : ""}" style="width:${Math.round(g[1]*100)}%"></div></div></div>`).join("");
  inputStage.querySelector(".viz-cap").innerHTML = '<span class="chip-mono">research brief → literature agent</span><span class="chip-mono">context + condition-specific regulators</span>';
  const stringDesc = document.querySelector("#s1 .desc");
  stringDesc.innerHTML = "The pipeline gathers <b>deterministic STRING evidence</b>: GO and KEGG enrichment over marker genes, plus confidence-scored protein associations linking perturbation regulators to program genes. This evidence stays <b>outside</b> the literature-agent brief and later informs annotation and the report.";
  const verifyDesc = document.querySelector("#s3 .desc");
  verifyDesc.innerHTML = "Before anything is shown, <b>every PMID and DOI is resolved</b> against Crossref and NCBI, checked for retraction, and de-duplicated. Identifiers that do not resolve are dropped. This verifies citation identity and status; contradictions are surfaced by the research agents and retained.";
  const batchBox = document.querySelector("#s4 .batch-box");
  batchBox.innerHTML = "Anthropic&nbsp;Batch<br />one batch job · per-program requests";
  const p22Bodies = [
    'A ranked program led by <span class="mono">Me1, Pklr, Acly, Khk, Fasn</span>, now framed as a mouse-hepatocyte response with regulator direction intact.',
    'Deterministic GO, KEGG, and association evidence is collected separately, then reunited with the verified literature during synthesis.',
    'P22\'s isolated agent researches carbohydrate sensing, fructolysis, de novo lipogenesis, redox buffering, and injury signals.',
    '<b>28 / 28</b> displayed PMIDs resolve to PubMed; none is marked retracted. Resolution verifies the paper identity, not every mechanism sentence.',
    'Cross-program synthesis names the result <b>“Fructose-driven lipogenesis”</b> and keeps the weaker injury-checkpoint module visibly qualified.',
    'From an anonymous ranked list to a regulator-anchored fructose-to-fat interpretation with every module linked to real papers.'
  ];
  document.querySelectorAll(".p10").forEach((box, i) => {
    box.querySelector(".p10-tag").textContent = "P22";
    box.querySelector(".p10-body").innerHTML = p22Bodies[i];
  });
  const laneLabels = document.querySelectorAll("#s2 .lane-title span");
  if (laneLabels[0]) laneLabels[0].textContent = "Program 22";
  if (laneLabels[1]) laneLabels[1].textContent = "Program 37";
  const report = document.querySelector("#s5 .report");
  report.querySelector(".prog-badge").textContent = "Program 22";
  report.querySelector(".path-chip").textContent = "GO top pathway · precursor metabolites & energy";
  report.querySelector(".report-title").textContent = "Fructose-driven lipogenesis";
  report.querySelector(".report-lead").textContent = "Carbohydrate carbon flows through KHK and PKLR toward ACLY- and FASN-dependent fat synthesis, coupled to redox buffering and a weaker injury checkpoint.";
  const moduleRows = [...report.querySelectorAll(".module-row")];
  const moduleData = [
    ["Fructose–glycolytic carbon flux into de novo lipogenesis", ["Khk","Pklr","Me1","Acly","Fasn"], ["28628040","35590219","33667726"]],
    ["Oxidative and aldehyde stress buffering", ["Mt1","Mt2","Aldh2","Gstp1","Gstm7"], ["36329886","34656650","25590808"]],
    ["Damage checkpoint and injury-response signaling", ["Phlda3","Ccng1","Twist1","Sema3e"], ["25966993","23142622","39743585"]]
  ];
  moduleRows.forEach((row, i) => {
    const [title, genes, pmids] = moduleData[i];
    row.querySelector(".module-name").textContent = title;
    row.querySelector(".module-genes").innerHTML = genes.map(g => `<span class="gene">${g}</span>`).join("");
    row.querySelector(".cites").innerHTML = pmids.map(p => `<a class="cite" href="https://pubmed.ncbi.nlm.nih.gov/${p}/" target="_blank" rel="noopener">PMID ${p} <span class="link-ic">↗</span></a>`).join("");
  });
  const honesty = report.querySelectorAll(".honesty");
  honesty[0].innerHTML = '<div class="honesty-top"><span class="status gap">interpretation boundary</span></div><p>“Fructose-driven” is supported by genes, perturbations, and literature; it is not direct proof of dietary fructose exposure.</p>';
  honesty[1].innerHTML = '<div class="honesty-top"><span class="status gap">weaker module</span></div><p>The injury-checkpoint signal is retained but qualified because perturbation regulators primarily anchor the lipogenic module.</p>';
  const style = document.createElement("style");
  style.textContent = `
    html{scroll-behavior:auto!important} body{overflow-x:hidden}
    .det-evidence{margin:12px 0 0;padding:15px 18px;border:1px dashed var(--accent);border-radius:10px;background:var(--accent-soft)}
    .det-evidence .brief-label{color:var(--accent-text)}
    .demo-cursor{position:fixed;z-index:1000;width:30px;height:30px;border-radius:50%;background:rgba(13,148,136,.22);border:3px solid #0d9488;box-shadow:0 0 0 7px rgba(13,148,136,.12);pointer-events:none;opacity:0;transform:translate(-50%,-50%);transition:opacity .18s,transform .12s}
    .demo-cursor.show{opacity:1}.demo-cursor.pressed{transform:translate(-50%,-50%) scale(.72);background:rgba(13,148,136,.46)}
  `;
  document.head.appendChild(style);
  const cursor = document.createElement("div");
  cursor.className = "demo-cursor";
  document.body.appendChild(cursor);
});

const reportPage = await context.newPage();
await reportPage.goto(`${pathToFileURL(ACTUAL_REPORT).href}#program-22`, { waitUntil: "domcontentloaded" });
await reportPage.waitForTimeout(1200);
await reportPage.evaluate(() => {
  document.documentElement.setAttribute("data-theme", "dark");
  document.getElementById("themebtn").textContent = "Light";
  const style = document.createElement("style");
  style.textContent = '.report-cursor{position:fixed;z-index:9999;width:30px;height:30px;border-radius:50%;background:rgba(45,212,191,.25);border:3px solid #2dd4bf;box-shadow:0 0 0 7px rgba(45,212,191,.13);pointer-events:none;opacity:0;transform:translate(-50%,-50%)}.report-cursor.show{opacity:1}.report-cursor.pressed{transform:translate(-50%,-50%) scale(.72)}';
  document.head.appendChild(style);
  const cursor = document.createElement("div");
  cursor.className = "report-cursor";
  document.body.appendChild(cursor);
  window.scrollTo(0, 0);
});
const reportModulesY = await reportPage.evaluate(() => {
  const el = document.querySelector("#sec-modules");
  return Math.max(0, el.getBoundingClientRect().top + window.scrollY - 110);
});

const stageMetrics = await architecturePage.evaluate(() => [...document.querySelectorAll(".stage")].map(el => ({
  top: el.offsetTop,
  height: el.offsetHeight,
}))); 
const documentHeight = await architecturePage.evaluate(() => document.documentElement.scrollHeight);
const maxScroll = documentHeight - HEIGHT;
const centers = stageMetrics.map(m => Math.max(0, Math.min(maxScroll, m.top + m.height / 2 - HEIGHT / 2 + 32)));
const stageNames = ["context & regulators", "STRING evidence", "parallel research", "verification", "batch synthesis", "one-click report"];

function ease(x) {
  const v = Math.max(0, Math.min(1, x));
  return v * v * (3 - 2 * v);
}

function segmentAt(t) {
  let selected = timeline[0];
  for (const item of timeline) {
    if (t >= item.start) selected = item;
    else break;
  }
  return selected;
}

let lastStage = -99;
let lastScroll = 0;
async function setArchitectureFrame(t, item) {
  let stageIndex = -1;
  let target = 0;
  const rel = Math.max(0, t - item.start);
  if (item.view === "hero_stage1") {
    stageIndex = rel > item.duration * 0.62 ? 0 : -1;
    target = rel > item.duration * 0.7 ? centers[0] : 0;
  } else if (item.view === "stage1") { stageIndex = 0; target = centers[0]; }
  else if (item.view === "stage2") {
    const moveToString = rel >= item.duration * .42;
    stageIndex = moveToString ? 1 : 0;
    target = moveToString ? centers[1] : centers[0];
  }
  else if (item.view === "stage3") { stageIndex = 2; target = centers[2]; }
  else if (item.view === "stage4") { stageIndex = 3; target = centers[3]; }
  else if (item.view === "stage5") { stageIndex = 4; target = centers[4]; }
  else if (item.view === "stage6_top") {
    stageIndex = 5;
    const top = Math.min(maxScroll, stageMetrics[5].top - 82);
    target = top + Math.min(190, 190 * ease(rel / Math.max(1, item.duration)));
  } else if (item.view === "stage6_bottom_pubmed_report") {
    stageIndex = 5;
    target = Math.min(maxScroll, stageMetrics[5].top + 650);
  }
  const transition = ease(Math.min(1, rel / 1.35));
  const scroll = lastStage === stageIndex ? target : lastScroll + (target - lastScroll) * transition;
  if (lastStage !== stageIndex) {
    await architecturePage.evaluate(({ index, name }) => {
      const stages = [...document.querySelectorAll(".stage")];
      stages.forEach((el, i) => el.classList.toggle("is-active", i === index));
      document.body.classList.toggle("touring", index >= 0);
      if (index >= 0) {
        document.getElementById("stepNum").textContent = String(index + 1);
        document.getElementById("stepName").textContent = name;
        document.getElementById("prevBtn").disabled = index === 0;
        document.getElementById("nextBtn").disabled = index === stages.length - 1;
        document.getElementById("resetBtn").style.display = "";
      }
    }, { index: stageIndex, name: stageIndex >= 0 ? stageNames[stageIndex] : "" });
    lastStage = stageIndex;
  }
  await architecturePage.evaluate(({ y, phase, t }) => {
    window.scrollTo(0, y);
    document.querySelectorAll("#s2 .lane").forEach((lane, laneIndex) => {
      const tools = [...lane.querySelectorAll(".tool")];
      const p = (phase + laneIndex * 2) % (tools.length + 1);
      tools.forEach((tool, i) => {
        tool.classList.toggle("done", i < p);
        tool.classList.toggle("active", i === p && p < tools.length);
      });
    });
    document.querySelectorAll(".pulse").forEach((p, i) => {
      const frac = ((t / 2.6) + i * .31) % 1;
      p.style.animation = "none";
      p.style.top = `${frac * 100}%`;
      p.style.opacity = frac < .08 || frac > .92 ? "0" : ".75";
    });
  }, { y: scroll, phase: Math.floor(t / 1.4) % 6, t });
  lastScroll = scroll;
}

for (const file of fs.readdirSync(FRAMES_DIR)) {
  if (file.endsWith(".jpg")) fs.unlinkSync(path.join(FRAMES_DIR, file));
}

let reportCitationClicked = false;
let introMode = "opening";
const totalFrames = Math.ceil(targetDuration * FPS);
console.log(`Capturing ${totalFrames} frames (${targetDuration.toFixed(2)} s at ${FPS} fps)`);

for (let frame = 0; frame < totalFrames; frame++) {
  const t = frame / FPS;
  const item = segmentAt(t);
  let activePage = architecturePage;
  if (item.view === "intro_problem") {
    const progress = Math.max(0, Math.min(1, (t - item.start) / item.duration));
    await introPage.evaluate(show => document.body.classList.toggle("problem", show), progress >= .18);
    activePage = introPage;
  } else if (item.view === "program22_report") {
    const progress = Math.max(0, Math.min(1, (t - item.start) / item.duration));
    if (progress < .72) {
      const scrollProgress = ease(Math.max(0, Math.min(1, (progress - .13) / .42)));
      await reportPage.evaluate(({ y, showCursor, pressed }) => {
        window.scrollTo(0, y);
        const cursor = document.querySelector(".report-cursor");
        const link = document.querySelector('#sec-modules article.mod:nth-of-type(1) .pmids a[href*="/28628040/"]');
        if (cursor && link && showCursor) {
          const r = link.getBoundingClientRect();
          cursor.style.left = `${r.left + r.width / 2}px`;
          cursor.style.top = `${r.top + r.height / 2}px`;
          cursor.classList.add("show");
          cursor.classList.toggle("pressed", pressed);
        } else if (cursor) {
          cursor.classList.remove("show", "pressed");
        }
      }, { y: reportModulesY * scrollProgress, showCursor: progress >= .5, pressed: progress >= .67 });
      if (progress >= .68 && !reportCitationClicked) {
        const link = reportPage.locator('#sec-modules article.mod:nth-of-type(1) .pmids a[href*="/28628040/"]');
        const pagePromise = context.waitForEvent("page");
        await link.click();
        const clickedPage = await pagePromise;
        await clickedPage.waitForLoadState("domcontentloaded").catch(() => {});
        reportCitationClicked = true;
      }
      activePage = reportPage;
    } else {
      activePage = cleanPubmedPage;
    }
  } else if (item.view === "summary_end") {
    const progress = Math.max(0, Math.min(1, (t - item.start) / item.duration));
    if (progress < .36) {
      await setArchitectureFrame(t, { ...item, view: "stage3" });
      activePage = architecturePage;
    } else if (progress < .68) {
      await setArchitectureFrame(t, { ...item, start: item.start + item.duration * .36, view: "stage5" });
      activePage = architecturePage;
    } else if (progress < .84) {
      await setArchitectureFrame(t, { ...item, start: item.start + item.duration * .68, view: "stage6_top" });
      activePage = architecturePage;
    } else {
      if (introMode !== "end") {
        await introPage.evaluate(() => document.body.classList.add("end"));
        introMode = "end";
      }
      activePage = introPage;
    }
  } else {
    await setArchitectureFrame(t, item);
  }

  const framePath = path.join(FRAMES_DIR, `frame_${String(frame).padStart(5, "0")}.jpg`);
  await activePage.screenshot({ path: framePath, type: "jpeg", quality: 90, animations: "allow" });
  if (frame % Math.max(1, Math.floor(totalFrames / 10)) === 0) {
    console.log(`${Math.round(frame / totalFrames * 100)}% · frame ${frame}/${totalFrames}`);
  }
}

await browser.close();

const captionsFile = path.join(OUT, "GPI_pipeline_captions.srt");
const finalVideo = path.join(OUT, "GPI_pipeline_narrated.mp4");
run(FFMPEG, [
  "-y",
  "-framerate", String(FPS),
  "-i", path.join(FRAMES_DIR, "frame_%05d.jpg"),
  "-i", voiceover,
  "-i", captionsFile,
  "-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0",
  "-t", targetDuration.toFixed(3),
  "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
  "-c:a", "copy",
  "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
  "-movflags", "+faststart",
  finalVideo,
]);

run(FFMPEG, ["-y", "-ss", "2.0", "-i", finalVideo, "-frames:v", "1", "-q:v", "2", path.join(OUT, "GPI_pipeline_poster.jpg")], { quiet: true });
console.log(`Done: ${finalVideo}`);
