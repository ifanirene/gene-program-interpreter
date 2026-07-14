import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const ROOT = "/Volumes/IF_PHAGE/gene-program-interpreter";
const OUT = path.join(ROOT, "designs/pipeline-architecture/video");
const SCRIPT = path.join(OUT, "GPI_pipeline_transcript.md");
const MANIFEST = path.join(OUT, "narration_segments.json");
const CONFIG_FILE = path.join(OUT, "tts_config.json");
const AUDIO_DIR = path.join(OUT, "audio_segments");
const CACHE_FILE = path.join(OUT, "GPI_tts_manifest.json");

function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!(match[1] in process.env)) process.env[match[1]] = value;
  }
}

function parseScript(markdown) {
  const viewById = {
    opening: "intro_problem",
    input: "stage1",
    context: "stage2",
    research: "stage3",
    verification: "stage4",
    synthesis: "stage5",
    report_biology: "program22_report",
    summary: "summary_end",
  };
  const sections = markdown.split(/^##\s+/m).slice(1);
  return sections.map(section => {
    const [heading, ...body] = section.split(/\r?\n/);
    const label = heading.split(/\s+—\s+/).at(-1).trim();
    const id = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    if (!viewById[id]) throw new Error(`Unknown transcript section: ${label}`);
    const text = body.join("\n").trim().replace(/\s+/g, " ");
    if (!text) throw new Error(`Empty transcript section: ${label}`);
    return { id, view: viewById[id], text };
  });
}

async function synthesize(segment, config, apiKey, cache) {
  const fingerprint = crypto.createHash("sha256")
    .update(JSON.stringify({ text: segment.text, ...config }))
    .digest("hex");
  const file = path.join(AUDIO_DIR, `${segment.id}.wav`);
  if (cache[segment.id]?.fingerprint === fingerprint && fs.existsSync(file)) {
    console.log(`Reusing ${segment.id}`);
    return [segment.id, { fingerprint, file: path.basename(file) }];
  }
  const response = await fetch("https://api.openai.com/v1/audio/speech", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: config.model,
      voice: config.voice,
      input: segment.text,
      instructions: config.instructions,
      response_format: config.response_format,
    }),
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 1200);
    throw new Error(`OpenAI TTS failed for ${segment.id} (HTTP ${response.status}): ${detail}`);
  }
  fs.writeFileSync(file, Buffer.from(await response.arrayBuffer()));
  console.log(`Generated ${segment.id}`);
  return [segment.id, { fingerprint, file: path.basename(file) }];
}

loadEnv(path.join(ROOT, ".env"));
const apiKey = process.env.OPENAI_API_KEY;
if (!apiKey) throw new Error("OPENAI_API_KEY is missing from .env");
const config = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
const segments = parseScript(fs.readFileSync(SCRIPT, "utf8"));
if (segments.length !== 8) throw new Error(`Expected 8 transcript sections, found ${segments.length}`);
fs.mkdirSync(AUDIO_DIR, { recursive: true });
fs.writeFileSync(MANIFEST, `${JSON.stringify(segments, null, 2)}\n`);
const cacheRoot = fs.existsSync(CACHE_FILE) ? JSON.parse(fs.readFileSync(CACHE_FILE, "utf8")) : {};
const cache = cacheRoot.segments ?? cacheRoot;
const nextCache = {};
for (let i = 0; i < segments.length; i += 3) {
  const results = await Promise.all(segments.slice(i, i + 3).map(s => synthesize(s, config, apiKey, cache)));
  for (const [id, meta] of results) nextCache[id] = meta;
}
fs.writeFileSync(CACHE_FILE, `${JSON.stringify({ model: config.model, voice: config.voice, segments: nextCache }, null, 2)}\n`);
console.log(`Voice ready: ${config.voice} · ${segments.length} segments`);
