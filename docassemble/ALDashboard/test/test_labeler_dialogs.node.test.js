/*  Keyboard behaviour for the shared labeler dialog helper.

    labeler_dialogs.js implements the ARIA dialog pattern (focus in on open,
    focus restored on close, Tab trapped inside, Escape to dismiss) for the
    custom modals in both labelers. That logic is easy to break silently, so
    it is exercised here against a real browser rather than a DOM stub.

    Needs Chromium, so this runs in the browser accessibility workflow:
        npm run test:dialogs
*/
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { chromium } = require("playwright");

const helperSource = fs.readFileSync(
  path.join(__dirname, "../data/static/labeler_dialogs.js"),
  "utf8",
);

// A minimal stand-in for the labelers' markup: an opener on the page, and a
// hidden overlay carrying role="dialog" that is shown by toggling `hidden`.
const PAGE = `<!doctype html><html><body>
<style>.hidden{display:none}</style>
<button id="opener">Open</button>
<button id="other">Other</button>
<div id="settings-modal" class="hidden" role="dialog" aria-modal="true" aria-labelledby="settings-modal-title">
  <h2 id="settings-modal-title">Settings</h2>
  <input id="first">
  <input id="mid">
  <button id="close-settings">Close</button>
</div>
<script>
  document.getElementById("opener").addEventListener("click", function () {
    document.getElementById("settings-modal").classList.remove("hidden");
  });
  document.getElementById("close-settings").addEventListener("click", function () {
    document.getElementById("settings-modal").classList.add("hidden");
  });
<\/script>
</body></html>`;

async function withPage(run) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(PAGE);
    await page.addScriptTag({ content: helperSource });
    page.activeId = () => page.evaluate(() => document.activeElement.id);
    await run(page);
  } finally {
    await browser.close();
  }
}

test("opening a dialog moves focus to its first control", async () => {
  await withPage(async (page) => {
    await page.click("#opener");
    assert.equal(await page.activeId(), "first");
  });
});

test("Tab and Shift+Tab stay inside the open dialog", async () => {
  await withPage(async (page) => {
    await page.click("#opener");

    await page.focus("#close-settings");
    await page.keyboard.press("Tab");
    assert.equal(await page.activeId(), "first", "Tab wraps last to first");

    await page.keyboard.press("Shift+Tab");
    assert.equal(
      await page.activeId(),
      "close-settings",
      "Shift+Tab wraps first to last",
    );

    await page.focus("#mid");
    await page.keyboard.press("Tab");
    assert.equal(
      await page.activeId(),
      "close-settings",
      "focus never reaches the page behind the dialog",
    );
  });
});

test("Escape dismisses the dialog and restores focus to its opener", async () => {
  await withPage(async (page) => {
    await page.click("#opener");
    await page.keyboard.press("Escape");

    const hidden = await page.evaluate(() =>
      document.getElementById("settings-modal").classList.contains("hidden"),
    );
    assert.equal(hidden, true, "Escape uses the dialog's own close button");
    assert.equal(await page.activeId(), "opener");
  });
});

test("Escape does nothing while no dialog is open", async () => {
  await withPage(async (page) => {
    await page.focus("#other");
    await page.keyboard.press("Escape");
    assert.equal(await page.activeId(), "other");
  });
});
