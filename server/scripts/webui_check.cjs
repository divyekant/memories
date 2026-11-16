// Browser check of the web UI (exit check of task P5-5).
//
// Opens each page of the web UI in Chromium with the admin key, and fails if
// a page shows no content, if the page has a console error or a script error,
// or if a request to the server fails or gets HTTP 400 or more. Requests to
// other origins (for example the web fonts) are not checked.
//
// Usage: node server/scripts/webui_check.cjs <base url> <admin key>
// Needs the `playwright` package (NODE_PATH) and its Chromium.
const { chromium } = require("playwright");

const PAGES = ["dashboard", "memories", "extractions", "keys", "health", "settings"];

async function main() {
  const [base, key] = process.argv.slice(2);
  if (!base || !key) {
    console.error("usage: webui_check.cjs <base url> <admin key>");
    process.exit(2);
  }
  const origin = new URL(base).origin;
  const browser = await chromium.launch();
  let failed = false;
  for (const name of PAGES) {
    const context = await browser.newContext();
    await context.addInitScript((k) => localStorage.setItem("memories-api-key", k), key);
    const page = await context.newPage();
    const problems = [];
    const apis = new Set();
    page.on("console", (msg) => {
      const url = msg.location().url || "";
      if (msg.type() === "error" && (url === "" || url.startsWith(origin))) {
        problems.push(`console: ${msg.text()} (${url})`);
      }
    });
    page.on("pageerror", (err) => problems.push(`script error: ${err.message}`));
    page.on("requestfailed", (req) => {
      if (req.url().startsWith(origin)) problems.push(`request failed: ${req.url()}`);
    });
    page.on("response", (res) => {
      const url = res.url();
      if (!url.startsWith(origin)) return;
      const path = new URL(url).pathname;
      if (!path.startsWith("/ui")) apis.add(`${res.request().method()} ${path}`);
      if (res.status() >= 400) problems.push(`HTTP ${res.status()}: ${res.request().method()} ${url}`);
    });
    await page.goto(`${base}/ui#/${name}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    const text = ((await page.textContent("#content")) || "").trim();
    if (text.length === 0) problems.push("the page shows no content");
    console.log(`${problems.length ? "FAIL" : "ok"}: ${name} (${[...apis].sort().join(", ") || "no API call"})`);
    for (const p of problems) console.log(`  ${p}`);
    failed ||= problems.length > 0;
    await context.close();
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
