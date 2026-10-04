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
  )
  .replace(
    /import \{ createAccessibilityWorkshop \} from\s+"[^"]+";/,
    "const createAccessibilityWorkshop = () => ({ open() {}, close() {}, isOpen() { return false; } });",
  )
  .replace(
    /import \{ assemblePdfPages \} from\s+"[^"]+";/,
    "const assemblePdfPages = () => {};",
  ).concat(`
    window.__pdfLabelerTest = {
      state: state,
      renderFieldsList: renderFieldsList,
      acknowledgeWorkshopRenames: acknowledgeWorkshopRenames
      , remapFieldsForPageManagerDraft: remapFieldsForPageManagerDraft,
      setPageManagerState: value => { pageManagerState = value; },
      updatePageManagerControls: updatePageManagerControls,
      renderPageManagerPages: renderPageManagerPages,
      pageManagerState: () => pageManagerState,
      prepareWorkshopPdf: prepareWorkshopPdf,
      buildUniqueExportNameMap: buildUniqueExportNameMap
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

test("inserted pages import editable fields and duplicate copies get distinct IDs and export names", async () => {
  await withLabelerPage(async (page) => {
    const result = await page.evaluate(() => {
      const t = window.__pdfLabelerTest;
      t.state.fields = [
        { id: "active", name: "name", pageIndex: 0, type: "text" },
        { id: "removed", name: "removed", pageIndex: 1, type: "text" },
      ];
      const imported = {
        id: "imported",
        name: "name",
        pageIndex: 0,
        type: "signature",
        x: 0.2,
        y: 0.3,
        width: 0.4,
        height: 0.05,
      };
      t.setPageManagerState({
        sources: {
          active: {},
          insert: {
            fields: [
              imported,
              { id: "reserved", name: "name__1", pageIndex: 1, type: "text" },
            ],
          },
        },
        draftPages: [
          { sourceId: "active", sourcePageIndex: 0 },
          { sourceId: "insert", sourcePageIndex: 0 },
          { sourceId: "insert", sourcePageIndex: 0 },
          { sourceId: "insert", sourcePageIndex: 1 },
        ],
      });
      const fields = t.remapFieldsForPageManagerDraft();
      t.state.fields = fields;
      return {
        fields,
        imported,
        exported: [...t.buildUniqueExportNameMap().values()],
      };
    });
    assert.equal(result.fields.length, 4);
    assert.equal(new Set(result.fields.map((f) => f.id)).size, 4);
    assert.deepEqual(
      result.fields.map((f) => f.pageIndex),
      [0, 1, 2, 3],
    );
    assert.equal(result.fields[1].type, "signature");
    assert.equal(result.fields[1].y, 0.3);
    assert.equal(result.imported.pageIndex, 0);
    assert.deepEqual(result.exported, [
      "name",
      "name__2",
      "name__3",
      "name__1",
    ]);
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

test("duplicate-name summary is reachable above the workshop and preparation waits for dismissal", async () => {
  await withLabelerPage(async (page) => {
    await page.addStyleTag({
      content: `
      .hidden { display: none !important; }
      #a11y-workshop { position: fixed; inset: 0; z-index: 1090; background: white; }
      #field-rename-summary-modal { position: fixed; inset: 0; }
    `,
    });
    await page.evaluate(() => {
      document.querySelector("#a11y-workshop").hidden = false;
      document.querySelector("#app").inert = true;
      window.acknowledged = false;
      window.__pdfLabelerTest
        .acknowledgeWorkshopRenames([
          { index: 1, old_name: "name", new_name: "name__2" },
        ])
        .then(() => {
          window.acknowledged = true;
        });
    });
    assert.equal(await page.evaluate(() => window.acknowledged), false);
    assert.equal(
      await page.locator("#a11y-workshop").evaluate((el) => el.inert),
      true,
    );
    assert.match(
      await page.locator("#field-rename-summary-list").innerText(),
      /name__2/,
    );
    await page.locator("#field-rename-summary-done").click();
    await page.waitForFunction(() => window.acknowledged);
    assert.equal(
      await page.locator("#a11y-workshop").evaluate((el) => el.inert),
      false,
    );
    assert.equal(
      await page.locator("#field-rename-summary-modal").isVisible(),
      false,
    );
  });
});

test("tagged page edits wait for an author choice and preservation blocks insertion", async () => {
  await withLabelerPage(async (page) => {
    await page.evaluate(() => {
      const h = window.__pdfLabelerTest;
      h.state.pageCount = 2;
      h.setPageManagerState({
        sources: {
          active: { id: "active", name: "Tagged.pdf", tagged: true },
          insert: { id: "insert", name: "New.pdf", tagged: false },
        },
        checkingTags: false,
        tagPolicy: "",
        insertSourceId: null,
        insertSelections: new Set(),
        draftPages: [
          {
            id: "p",
            sourceId: "active",
            sourcePageIndex: 1,
            splitBefore: false,
          },
        ],
      });
      document.querySelector("#page-manager-modal").classList.remove("hidden");
      h.updatePageManagerControls();
    });
    assert.equal(
      await page.locator("#page-manager-tag-choice").isVisible(),
      true,
    );
    assert.equal(await page.locator("#page-manager-apply").isDisabled(), true);
    await page.locator("#page-manager-tags").selectOption("preserve");
    assert.equal(await page.locator("#page-manager-apply").isDisabled(), false);
    await page.evaluate(() => {
      const h = window.__pdfLabelerTest;
      h.setPageManagerState({
        sources: {
          active: { id: "active", name: "Tagged.pdf", tagged: true },
          insert: { id: "insert", name: "New.pdf", tagged: false },
        },
        checkingTags: false,
        tagPolicy: "preserve",
        insertSourceId: null,
        insertSelections: new Set(),
        draftPages: [
          {
            id: "p",
            sourceId: "active",
            sourcePageIndex: 1,
            splitBefore: false,
          },
          {
            id: "n",
            sourceId: "insert",
            sourcePageIndex: 0,
            splitBefore: true,
          },
        ],
      });
      h.updatePageManagerControls();
    });
    assert.equal(await page.locator("#page-manager-apply").isDisabled(), true);
    assert.equal(
      await page.locator("#page-manager-download-splits").isDisabled(),
      true,
    );
    await page.locator("#page-manager-tags").selectOption("rebuild");
    assert.equal(await page.locator("#page-manager-apply").isDisabled(), false);
    assert.equal(
      await page.locator("#page-manager-download-splits").isDisabled(),
      false,
    );
  });
});


test("page-only edits preserve original controls when opening the workshop, and field edits still apply", async () => {
  await withLabelerPage(async (page) => {
    const result = await page.evaluate(async () => {
      const h = window.__pdfLabelerTest, requests = [];
      h.state.pdfBytes = new TextEncoder().encode("%PDF-original-values");
      h.state.fileName = "Tagged.pdf";
      h.state.pageCount = 2;
      h.state.pageSizes = [{width:612,height:792},{width:612,height:792}];
      h.state.fields = [h.state.fields[0]];
      h.state.hasUnsavedChanges = true;
      h.state.preservedPageFields = JSON.stringify(h.state.fields);
      window.fetch = async (url, options) => {
        const form = options.body;
        requests.push({url, action: form.get("action"), pages: form.get("page_indexes"),
          original: await form.get("file").text()});
        return new Response(JSON.stringify({success:true,data:{
          pdf_base64:btoa("%PDF-original-values"),remediation_result:{renames:[]}
        }}), {status:200});
      };
      const preserved = await h.prepareWorkshopPdf();
      h.state.fields[0].name = "Edited";
      await h.prepareWorkshopPdf();
      return {requests, bytes:new TextDecoder().decode(preserved.bytes)};
    });
    assert.equal(result.bytes, "%PDF-original-values");
    assert.equal(result.requests[0].action, "preserve_pages");
    assert.equal(result.requests[0].pages, "[0,1]");
    assert.equal(result.requests[0].original, "%PDF-original-values");
    assert.match(result.requests[1].url, /apply-fields$/);
  });
});


test("page reorder buttons work from the keyboard and keep focus on the moved page", async () => {
  await withLabelerPage(async (page) => {
    await page.evaluate(() => {
      const h = window.__pdfLabelerTest;
      h.state.pageCount = 3;
      h.setPageManagerState({sources:{active:{kind:"active",name:"Form.pdf"}}, checkingTags:false,
        tagPolicy:"",insertSourceId:null,insertSelections:new Set(),
        draftPages:[0,1,2].map(i=>({id:"page-"+i,sourceId:"active",sourcePageIndex:i,splitBefore:false}))});
      document.querySelector("#page-manager-modal").classList.remove("hidden");
      h.renderPageManagerPages();
    });
    await page.locator('[data-page-manager-action=earlier][data-page-id=page-2]').focus();
    await page.keyboard.press("Enter");
    assert.deepEqual(await page.evaluate(() => window.__pdfLabelerTest.pageManagerState().draftPages.map(p=>p.sourcePageIndex)),[0,2,1]);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.pageId), "page-2");
    await page.keyboard.press("Enter");
    assert.deepEqual(await page.evaluate(() => window.__pdfLabelerTest.pageManagerState().draftPages.map(p=>p.sourcePageIndex)),[2,0,1]);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.pageId), "page-2");
    assert.equal(await page.evaluate(() => document.activeElement.dataset.pageManagerAction), "later");
  });
});
