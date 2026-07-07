// build.js — assemble the single-file dist/index.html + copy headers file.
const fs = require("fs"), path = require("path");
const src = (f) => fs.readFileSync(path.join("src", f), "utf8");

const ashlib = src("ashlib.js");
const worker = src("worker.js");
const app = src("app.js");
let html = src("template.html");

html = html.replace("/*__ASHLIB__*/", () => ashlib);
html = html.replace("/*__WORKER_SRC__*/", () => JSON.stringify(ashlib + "\n" + worker));
html = html.replace("/*__APP__*/", () => app);

// sanity: every id app.js touches must exist in the markup
const used = [...app.matchAll(/\$\("([\w-]+)"\)/g)].map((m) => m[1]);
const missing = [...new Set(used)].filter((id) => !html.includes(`id="${id}"`));
if (missing.length) throw new Error("missing element ids: " + missing.join(", "));

fs.mkdirSync("dist", { recursive: true });
fs.writeFileSync("dist/index.html", html);
fs.copyFileSync("src/.ic-assets.json5", "dist/.ic-assets.json5");
console.log(`dist/index.html: ${(html.length / 1024).toFixed(1)} KB, all ${new Set(used).size} element ids present`);
