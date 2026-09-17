const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { chromium } = require("playwright");

const template = fs
  .readFileSync(
    path.join(__dirname, "../data/templates/pdf_labeler.html"),
    "utf8",
  )
  .replace(/<script[\s\S]*?<\/script>/g, "");

const labelerSource = fs
  .readFileSync(path.join(__dirname, "../data/static/pdf_labeler.js"), "utf8")
  .replace(
    /import \* as pdfjsLib from\s+"[^"]+";/,
    "const pdfjsLib = { GlobalWorkerOptions: {} };",
  ).concat(`
    window.__pdfLabelerTest = {
      state: state,
      renderFieldsList: renderFieldsList
    };
  `);

async function withLabelerPage(run) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.addInitScript(() => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({}),
      });
    });
    await page.setContent(template);
    await page.addScriptTag({ content: labelerSource, type: "module" });
    await page.waitForFunction(() => Boolean(window.__pdfLabelerTest));
    await page.evaluate(() => {
      const harness = window.__pdfLabelerTest;
      harness.state.pageCount = 1;
      harness.state.pageSizes = [{ width: 612, height: 792 }];
      harness.state.fields = [
        {
          id: "field-1",
          name: "choice",
          type: "dropdown",
          pageIndex: 0,
          x: 0.1,
          y: 0.1,
          width: 0.2,
          height: 0.04,
          options: ["One", "Two"],
        },
      ];
      harness.state.selectedFieldId = "field-1";
      harness.renderFieldsList();
    });
    await run(page);
  } finally {
    await browser.close();
  }
}

test("typing multiple-choice options keeps focus and accepts every character", async () => {
  await withLabelerPage(async (page) => {
    const options = page.locator('[data-action="field-options"]');
    await options.fill("");
    await options.pressSequentially("Alpha, Beta");

    assert.equal(await options.inputValue(), "Alpha, Beta");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.action),
      "field-options",
    );
  });
});

test("typing a field label keeps focus when rename previews rerender the list", async () => {
  await withLabelerPage(async (page) => {
    await page.evaluate(() => {
      window.__pdfLabelerTest.state.fieldsPanelMode = "rename";
      window.__pdfLabelerTest.renderFieldsList();
    });
    const name = page.locator('[data-action="field-name"]');
    await name.fill("");
    await name.pressSequentially("new_label");

    assert.equal(await name.inputValue(), "new_label");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.action),
      "field-name",
    );
  });
});
