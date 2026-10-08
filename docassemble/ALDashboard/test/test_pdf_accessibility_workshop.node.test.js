const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const modulePath = pathToFileURL(
  path.join(__dirname, "../data/static/pdf_accessibility_workshop.js"),
).href;

function loadWorkshop() {
  return import(modulePath);
}

test("a printed label loses its colon, asterisk and leader underscores", async () => {
  const { cleanLabelText } = await loadWorkshop();
  assert.equal(cleanLabelText("Date of birth *:"), "Date of birth");
  assert.equal(cleanLabelText("Name ________"), "Name");
  assert.equal(cleanLabelText("  • Phone "), "Phone");
});

function fieldsAt(entries) {
  return entries.map(function ([name, pageIndex, x, y]) {
    return { name, pageIndex, box: { x, y, width: 0.2, height: 0.02 } };
  });
}

test("visual order reads rows top to bottom, then left to right", async () => {
  const { visualFieldOrder } = await loadWorkshop();
  const fields = fieldsAt([
    ["respondent_name", 0, 0.5, 0.2],
    ["page2", 1, 0.1, 0.1],
    ["petitioner_name", 0, 0.1, 0.201],
    ["petitioner_phone", 0, 0.1, 0.3],
  ]);
  assert.deepEqual(visualFieldOrder(fields), [
    "petitioner_name",
    "respondent_name",
    "petitioner_phone",
    "page2",
  ]);
});

test("column order reads down each column before the next", async () => {
  const { columnFieldOrder } = await loadWorkshop();
  const fields = fieldsAt([
    ["respondent_name", 0, 0.5, 0.2],
    ["petitioner_phone", 0, 0.1, 0.3],
    ["petitioner_name", 0, 0.1, 0.2],
    ["respondent_phone", 0, 0.5, 0.3],
  ]);
  assert.deepEqual(columnFieldOrder(fields), [
    "petitioner_name",
    "petitioner_phone",
    "respondent_name",
    "respondent_phone",
  ]);
});

test("a jump back up the page counts, starting a new column does not", async () => {
  const { countOrderJumps } = await loadWorkshop();
  const fields = fieldsAt([
    ["a", 0, 0.1, 0.2],
    ["b", 0, 0.1, 0.3],
    ["c", 0, 0.5, 0.2],
    ["d", 0, 0.1, 0.25],
  ]);
  const byName = new Map(
    fields.map(function (field) {
      return [field.name, field];
    }),
  );
  assert.equal(countOrderJumps(["a", "b", "c"], byName), 0);
  assert.equal(countOrderJumps(["a", "b", "d"], byName), 1);
});

test("announcements read the way a screen reader phrases them", async () => {
  const { spokenAnnouncement } = await loadWorkshop();
  const fields = new Map([
    ["dob", { type: "text", required: true }],
    ["agree", { type: "checkbox", required: false }],
  ]);
  assert.equal(
    spokenAnnouncement(
      { kind: "field", name: "dob", text: "Date of birth" },
      fields,
    ),
    "Date of birth, edit text, required",
  );
  assert.equal(
    spokenAnnouncement({ kind: "field", name: "agree", text: "" }, fields),
    "unlabeled, check box",
  );
  assert.equal(
    spokenAnnouncement({ kind: "text", role: "H2", text: "1. About you" }),
    "Heading level 2, 1. About you",
  );
  assert.equal(
    spokenAnnouncement({ kind: "text", role: "Figure", text: "Map" }),
    "Graphic, Map",
  );
});

test("the language guess needs enough evidence", async () => {
  const { guessLanguage } = await loadWorkshop();
  assert.equal(guessLanguage("Name"), "");
  assert.equal(
    guessLanguage(
      "Please complete all of the sections and sign the form. You must tell the court if your address changes and this is the notice for you.",
    ),
    "en-US",
  );
  assert.equal(
    guessLanguage(
      "Por favor complete todas las secciones de la solicitud y firme el formulario para que el tribunal pueda revisar su caso y los documentos.",
    ),
    "es-US",
  );
});

const fs = require("node:fs");
const { chromium } = require("playwright");

// Exercise the real workshop transactions in a browser. Only the PDF transport
// and renderer are fakes; a PDF's JSON bytes carry its announced field name.
async function withWorkshop(run) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 800 },
    });
    const template = fs
      .readFileSync(
        path.join(__dirname, "../data/templates/pdf_labeler.html"),
        "utf8",
      )
      .replace(/<script[\s\S]*?<\/script>/g, "");
    const source = fs
      .readFileSync(
        path.join(__dirname, "../data/static/pdf_accessibility_workshop.js"),
        "utf8",
      )
      .replace(
        "    open: open,",
        `    test: { state: () => ws, loadWorkingCopy, initializeDecisions, saveDecisions,
        writeFieldNames, rebuildStructure, undo, applyChange, confirmOrderPage, writeImages, textFixPayload, refreshLinkDrafts, render },
    open: open,`,
      );
    await page.setContent(template.replace(/<link[^>]*>/g, ""));
    await page.addStyleTag({
      content: fs.readFileSync(
        path.join(__dirname, "../data/static/pdf_accessibility_workshop.css"),
        "utf8",
      ),
    });
    await page.addScriptTag({
      type: "module",
      content:
        source + "\nwindow.createTestWorkshop = createAccessibilityWorkshop;",
    });
    await page.waitForFunction(() => !!window.createTestWorkshop);
    await page.evaluate(async () => {
      window.requests = [];
      window.errors = [];
      window.addEventListener("error", (event) => window.errors.push(event.message));
      window.failInspection = false;
      const encode = (value) => new TextEncoder().encode(JSON.stringify(value));
      window.fetch = async (url, options) => {
        const form = options.body;
        const data = JSON.parse(await form.get("file").text());
        if (url.endsWith("accessibility-inspect")) {
          if (window.failInspection) throw new Error("Inspection failed");
          return {
            data: {
              metadata: {},
              report: { issues: [] },
              field_labels: [],
              content_blocks: window.testContentBlocks || [],
              images: [],
              tag_structure: { present: true },
              structure_editor: data.structureEditor || window.testStructureEditor || {},
              readback: { announcements: window.testReadback || [] },
            },
          };
        }
        const fields = Object.fromEntries(
          [...form.entries()].filter(([key]) => key !== "file"),
        );
        window.requests.push(fields);
        if (window.testMergeStructureEditor && fields.action === "structure" && JSON.parse(fields.operations || "[]").some(op => op.action === "merge_tags")) {
          data.structureEditor = window.testMergeStructureEditor;
        }
        if (fields.field_tooltips)
          Object.assign(data.names, JSON.parse(fields.field_tooltips));
        return {
          data: {
            pdf_base64: btoa(JSON.stringify(data)),
            remediation_result: {},
          },
        };
      };
      const workshop = window.createTestWorkshop({
        root: document.querySelector("#a11y-workshop"),
        apiUrl: (path) => path,
        parseApiResponse: async (response) => ({
          success: true,
          data: response.data,
        }),
        showError: (message) => window.errors.push(message),
        aiEnabled: () => false,
        pdfjsLib: {
          getDocument: ({ data }) => ({
            promise: Promise.resolve({
              numPages: 1,
              destroy() {},
              getPage: async () => ({
                view: [0, 0, 612, 792],
                getViewport: ({ scale }) => ({
                  width: 612 * scale,
                  height: 792 * scale,
                  convertToViewportPoint: (x, y) => [x * scale, (792 - y) * scale],
                }),
                render: () => ({ promise: Promise.resolve() }),
                getTextContent: async () => window.testTextContent || { items: [], styles: {} },
                getAnnotations: async () => [
                  {
                    annotationType: 20,
                    fieldName: "field",
                    fieldType: "Tx",
                    alternativeText: JSON.parse(new TextDecoder().decode(data))
                      .names.field,
                    rect: [20, 20, 100, 40],
                  },
                ],
              }),
            }),
          }),
        },
      });
      window.workshop = workshop.test;
      await workshop.test.loadWorkingCopy(
        encode({ names: { field: "Original name" } }),
      );
      workshop.test.initializeDecisions();
      workshop.test.saveDecisions();
    });
    await run(page);
  } finally {
    await browser.close();
  }
}

test("undo field names restores decisions and a later rebuild cannot write them again", async () => {
  await withWorkshop(async (page) => {
    const result = await page.evaluate(async () => {
      const t = window.workshop,
        state = t.state();
      state.names.field = {
        value: "Undone name",
        confirmed: true,
        written: false,
      };
      await t.writeFieldNames();
      const written = state.names.field.written;
      await t.undo();
      const restored = structuredClone(state.names.field);
      const reviewed = state.reviewed.fields;
      await t.rebuildStructure("Headings", "headings");
      return {
        written,
        restored,
        reviewed,
        requests: window.requests,
        pdf: JSON.parse(new TextDecoder().decode(state.working)),
        errors: window.errors,
      };
    });
    assert.equal(result.written, true);
    assert.equal(result.restored.confirmed, false);
    assert.notEqual(result.restored.written, true);
    assert.ok(!result.reviewed);
    assert.equal(result.pdf.names.field, "Original name");
    assert.deepEqual(JSON.parse(result.requests.at(-1).field_tooltips), {});
    assert.deepEqual(result.errors, []);
  });
});

test("controls without internal names can be reviewed and written without a field rename", async () => {
  await withWorkshop(async (page) => {
    await page.evaluate(() => {
      const t = window.workshop;
      const state = t.state();
      state.inspection.structure_editor = window.testStructureEditor = {
        widgets: [
          {
            name: "",
            pageIndex: 0,
            index: 4,
            widgetId: "widget:0:seal",
            rect: [10, 10, 40, 40],
            tagged: true,
            tooltip: "",
            caption: "",
          },
        ],
      };
      t.refreshLinkDrafts();
      t.saveDecisions();
      state.open = true;
      state.step = "review";
      state.task = "links";
      document.querySelector("#a11y-workshop").hidden = false;
      t.render();
    });
    const input = page.locator('[data-aw-input="link-contents"]');
    await input.fill("Massachusetts court seal");
    await page.locator('[data-aw="link-confirm"]').click();
    await page.locator('[data-aw="links-write"]').click();
    await page.waitForFunction(() => !window.workshop.state().busy);
    const request = await page.evaluate(() => window.requests.at(-1));
    const operations = JSON.parse(request.operations);
    assert.equal(operations[0].action, "set_widget_description");
    assert.equal(operations[0].description, "Massachusetts court seal");
    assert.equal(operations[0].index, 4);
    assert.ok(!request.field_tooltips);
    await page.evaluate(() => window.workshop.undo());
    assert.equal(
      await page.locator('[data-aw-input="link-contents"]').inputValue(),
      "",
    );
    assert.deepEqual(await page.evaluate(() => window.errors), []);
  });
});

test("undo restores image/order decisions, review flags, Sets and the earlier-tree lock", async () => {
  await withWorkshop(async (page) => {
    const result = await page.evaluate(async () => {
      const t = window.workshop,
        state = t.state();
      state.preexistingTree = true;
      state.treeDecision = "keep";
      state.images = { image: { confirmed: false, decision: "", altText: "" } };
      t.saveDecisions();
      state.treeDecision = "replace";
      state.images.image = {
        confirmed: true,
        decision: "figure",
        altText: "New description",
      };
      state.blockOrder = { 0: ["b", "a"] };
      state.blockRoles = { a: "Artifact" };
      state.orderEditedPages.add(0);
      await t.writeImages();
      // Review confirmations made after the transaction must also be undone.
      state.orderConfirmedPages.add(0);
      state.reviewed.order = true;
      await t.undo();
      return {
        images: state.images,
        order: state.blockOrder,
        roles: state.blockRoles,
        edited: [...state.orderEditedPages],
        confirmed: [...state.orderConfirmedPages],
        reviewed: state.reviewed,
        preexisting: state.preexistingTree,
        treeDecision: state.treeDecision,
      };
    });
    assert.equal(result.images.image.confirmed, false);
    assert.deepEqual(result.order, {});
    assert.deepEqual(result.roles, {});
    assert.deepEqual(result.edited, []);
    assert.deepEqual(result.confirmed, []);
    assert.deepEqual(result.reviewed, {});
    assert.equal(result.preexisting, true);
    assert.equal(result.treeDecision, "keep");
  });
});

test("changing the PDF invalidates external and AI evidence and undo restores its recorded tests", async () => {
  await withWorkshop(async (page) => {
    const result = await page.evaluate(async () => {
      const t = window.workshop,
        state = t.state();
      state.external = { validator: { result: "pass", notes: "Original PDF" } };
      state.declare = true;
      state.aiNotes = [{ title: "Earlier PDF" }];
      state.aiRanAt = "Earlier";
      state.replay.heardAll = true;
      t.saveDecisions();
      state.names.field = {
        value: "Changed name",
        confirmed: true,
        written: false,
      };
      await t.writeFieldNames();
      const changed = {
        external: state.external,
        declare: state.declare,
        aiNotes: state.aiNotes,
        heardAll: state.replay.heardAll,
      };
      await t.undo();
      return { changed, restored: state.external, declare: state.declare };
    });
    assert.deepEqual(result.changed.external, {});
    assert.equal(result.changed.declare, false);
    assert.equal(result.changed.aiNotes, null);
    assert.equal(result.changed.heardAll, false);
    assert.deepEqual(result.restored, {
      validator: { result: "pass", notes: "Original PDF" },
    });
    assert.equal(result.declare, true);
  });
});

test("busy and failed undo preserve the history entry and current PDF", async () => {
  await withWorkshop(async (page) => {
    const result = await page.evaluate(async () => {
      const t = window.workshop,
        state = t.state();
      state.names.field = { value: "Saved name", confirmed: true };
      await t.writeFieldNames();
      const before = Array.from(state.working);
      state.busy = true;
      await t.undo();
      const busyCount = state.history.length;
      state.busy = false;
      window.failInspection = true;
      await t.undo();
      return {
        busyCount,
        count: state.history.length,
        before,
        after: Array.from(state.working),
        name: state.names.field.value,
        errors: window.errors,
      };
    });
    assert.equal(result.busyCount, 1);
    assert.equal(result.count, 1);
    assert.deepEqual(result.after, result.before);
    assert.equal(result.name, "Saved name");
    assert.match(result.errors[0], /Undo failed/);
  });
});

test("text fixes use content identities and undo removes a newly saved correction", async () => {
  await withWorkshop(async (page) => {
    const result = await page.evaluate(async () => {
      const t = window.workshop,
        state = t.state();
      state.textFixes["stable-content"] = {
        confirmed: true,
        actualText: "Correct wording",
      };
      const payload = t.textFixPayload();
      await t.applyChange("Text corrections", "", [async (bytes) => bytes]);
      await t.undo();
      return { payload, after: t.textFixPayload() };
    });
    assert.deepEqual(result.payload, [
      {
        contentId: "stable-content",
        actualText: "Correct wording",
        apply: true,
      },
    ]);
    assert.deepEqual(result.after, []);
  });
});

test("initial drafts use repaired block IDs while the original snapshot stays intact", async () => {
  await withWorkshop(async (page) => {
    const result = await page.evaluate(() => {
      const t = window.workshop,
        state = t.state();
      const block = {
        blockId: "before-font-repair",
        pageIndex: 0,
        text: "Same passage",
        box: { x: 0.1, y: 0.1 },
      };
      state.inspection.content_blocks = [block];
      t.initializeDecisions();
      t.saveDecisions();
      const original = state.savedDecisions;
      state.inspection.content_blocks = [
        { ...block, blockId: "after-font-repair" },
      ];
      t.initializeDecisions();
      t.saveDecisions();
      return { original: original.blockOrder, repaired: state.blockOrder };
    });
    assert.deepEqual(result.original, { 0: ["before-font-repair"] });
    assert.deepEqual(result.repaired, { 0: ["after-font-repair"] });
  });
});

async function showPreview(page) {
  await page.evaluate(() => {
    const t = window.workshop,
      state = t.state();
    state.open = true;
    document.querySelector("#app").inert = true;
    state.step = "review";
    state.task = "fields";
    state.showAllFields = true;
    document.querySelector("#a11y-workshop").hidden = false;
    t.render();
  });
  await page.locator(".aw-page-canvas canvas").waitFor();
}

test("preview zoom keeps overlays aligned, preserves focus, and fits after resizing", async () => {
  await withWorkshop(async (page) => {
    await showPreview(page);
    const preview = page.locator("[data-aw-ref=page]");
    const initial = await preview.boundingBox();
    await page
      .locator("#a11y-workshop")
      .getByRole("button", { name: "Zoom in", exact: true })
      .click();
    assert.ok((await preview.boundingBox()).width > initial.width);
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.aw),
      "zoom-in",
    );
    for (let i = 0; i < 5; i++)
      await page
        .locator("#a11y-workshop")
        .getByRole("button", { name: "Zoom in", exact: true })
        .click();
    const dimensions = await page.evaluate(() => {
      const well = document.querySelector(".aw-page-well");
      const page = document
        .querySelector("[data-aw-ref=page]")
        .getBoundingClientRect();
      const overlay = document
        .querySelector(".aw-overlay")
        .getBoundingClientRect();
      const mark = document.querySelector(".aw-mark").getBoundingClientRect();
      return {
        width: page.width,
        overlayWidth: overlay.width,
        markWidth: mark.width,
        scroll: well.scrollWidth > well.clientWidth,
        left: page.left,
        wellLeft: well.getBoundingClientRect().left,
      };
    });
    assert.ok(dimensions.scroll);
    assert.equal(dimensions.overlayWidth, dimensions.width);
    assert.ok(
      Math.abs(dimensions.markWidth / dimensions.width - 80 / 612) < 0.001,
    );
    assert.ok(dimensions.left >= dimensions.wellLeft);
    const zoom = await page.locator("[data-aw-ref=zoom]").innerText();
    await page.evaluate(() => window.workshop.render());
    assert.equal(await page.locator("[data-aw-ref=zoom]").innerText(), zoom);
    await page.getByRole("button", { name: "Fit width", exact: true }).click();
    await page.setViewportSize({ width: 1100, height: 800 });
    await page.waitForFunction(() => {
      const well = document.querySelector(".aw-page-well");
      return well.scrollWidth <= well.clientWidth + 1;
    });
    assert.equal(
      await page
        .getByRole("button", { name: "Fit width", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    assert.deepEqual(await page.evaluate(() => window.requests), []);
  });
});

test("review panel resizes by keyboard and drag without losing edits, and stacks on small screens", async () => {
  await withWorkshop(async (page) => {
    await showPreview(page);
    await page.locator("#aw-field-name").fill("An unsaved edit");
    const divider = page.getByRole("separator", { name: "Review panel width" });
    const panel = page.locator("#aw-review-panel");
    await divider.focus();
    const originalWidth = (await panel.boundingBox()).width;
    await divider.press("ArrowRight");
    assert.ok((await panel.boundingBox()).width < originalWidth);
    await divider.press("Home");
    assert.equal(Math.round((await panel.boundingBox()).width), 240);
    await divider.press("End");
    const wide = (await panel.boundingBox()).width;
    const handle = await divider.boundingBox();
    await page.mouse.move(handle.x + handle.width / 2, handle.y + 50);
    await page.mouse.down();
    await page.mouse.move(handle.x + handle.width / 2 + 120, handle.y + 50, {
      steps: 5,
    });
    await page.mouse.up();
    const narrow = (await panel.boundingBox()).width;
    assert.ok(narrow < wide - 100);
    assert.equal(
      await page.locator("#aw-field-name").inputValue(),
      "An unsaved edit",
    );
    await page.evaluate(() => window.workshop.render());
    assert.equal((await panel.boundingBox()).width, narrow);
    await page.setViewportSize({ width: 800, height: 700 });
    assert.equal(await divider.isVisible(), false);
    assert.ok((await panel.boundingBox()).width > 700);
    await page.setViewportSize({ width: 1280, height: 800 });
    await divider.focus();
    await divider.press("Enter");
    assert.equal(
      Math.round((await panel.boundingBox()).width),
      Math.round(originalWidth),
    );
    assert.deepEqual(await page.evaluate(() => window.errors), []);
  });
});

test("decorative push buttons require confirmation and undo restores the choice", async () => {
  await withWorkshop(async (page) => {
    await page.evaluate(() => {
      const t = window.workshop,
        state = t.state();
      state.inspection.structure_editor = window.testStructureEditor = {
        widgets: [
          {
            name: "",
            pageIndex: 0,
            index: 2,
            widgetId: "seal",
            pushButton: true,
            rect: [20, 20, 60, 60],
          },
        ],
      };
      state.tableDrafts = [{id:"table", confirmed:true, rows:[[
        {contentIds:["button-content","text-content"], role:"TD"}
      ]]}];
      state.inspection.readback.announcements = [{contentId:"button-content"},{contentId:"text-content"}];
      window.testReadback = [{contentId:"text-content"}];
      t.refreshLinkDrafts();
      t.saveDecisions();
      state.open = true;
      state.step = "review";
      state.task = "links";
      document.querySelector("#a11y-workshop").hidden = false;
      t.render();
    });
    await page.locator("[data-aw-input=button-decorative]").check();
    assert.equal(
      await page.locator("[data-aw-input=link-contents]").isDisabled(),
      true,
    );
    assert.equal(
      await page.evaluate(() => window.workshop.state().links.seal.confirmed),
      false,
    );
    await page.locator("[data-aw=link-confirm]").click();
    await page.locator("[data-aw=links-write]").click();
    await page.waitForFunction(() => !window.workshop.state().busy);
    const operations = await page.evaluate(() =>
      JSON.parse(window.requests.at(-1).operations),
    );
    assert.deepEqual(operations, [
      { action: "disable_decorative_button", pageIndex: 0, index: 2 },
    ]);
    assert.deepEqual(await page.evaluate(() => window.workshop.state().tableDrafts[0].rows[0][0].contentIds), ["text-content"]);
    await page.evaluate(() => window.workshop.undo());
    assert.equal(
      await page.locator("[data-aw-input=button-decorative]").isChecked(),
      false,
    );
    assert.deepEqual(await page.evaluate(() => window.workshop.state().tableDrafts[0].rows[0][0].contentIds), ["button-content", "text-content"]);
    assert.deepEqual(await page.evaluate(() => window.errors), []);
  });
});

test("font substitution needs a visual decision, offers original comparison, and undo restores review", async () => {
  await withWorkshop(async (page) => {
    await page.evaluate(() => {
      const t = window.workshop,
        state = t.state();
      state.original = state.working.slice();
      state.inspection.report.fonts = [
        {
          resource: "F0",
          name: "UnavailableFont",
          embedded: false,
          pageIndex: 0,
        },
      ];
      state.fontSubs = {
        fonts: [
          {
            resource: "F0",
            candidates: [
              {
                path: "/font.ttf",
                postscript_name: "ChosenFont",
                width_delta: 0,
              },
            ],
          },
        ],
      };
      state.glyphs["sub:F0"] = "/font.ttf";
      t.saveDecisions();
      state.open = true;
      state.step = "review";
      state.task = "text";
      document.querySelector("#a11y-workshop").hidden = false;
      t.render();
    });
    await page.locator("[data-aw=apply-font-subs]").click();
    await page.waitForFunction(() => !window.workshop.state().busy);
    await page.locator("[data-aw=compare-fonts]").click();
    await page.locator(".aw-page-canvas canvas").waitFor();
    assert.equal(
      await page.evaluate(() => window.workshop.state().compareOriginal),
      true,
    );
    assert.match(
      await page.locator("[data-aw-ref=main]").innerText(),
      /UnavailableFont → ChosenFont/,
    );
    await page.locator("[data-aw=text-done]").click();
    assert.equal(
      await page.evaluate(() => !!window.workshop.state().reviewed.text),
      false,
    );
    await page.locator("[data-aw=keep-fonts]").click();
    assert.equal(
      await page.evaluate(
        () => window.workshop.state().fontChecks[0].confirmed,
      ),
      true,
    );
    await page.evaluate(() => window.workshop.undo());
    assert.deepEqual(
      await page.evaluate(() => window.workshop.state().fontChecks),
      [],
    );
    assert.equal(
      await page.evaluate(() => window.workshop.state().compareOriginal),
      false,
    );
  });
});

test("table tools stay collapsed, and confirmed creation survives redrafts but not Undo", async () => {
  await withWorkshop(async (page) => {
    await page.evaluate(() => {
      const t = window.workshop,
        state = t.state();
      state.inspection.structure_editor = window.testStructureEditor = {
        tableItems: ["Heading A", "Heading B", "Value A", "Value B"].map(
          (text, i) => ({
            text,
            contentIds: ["content-" + i],
            pageIndex: 0,
            tableOwner: "",
          }),
        ),
      };
      t.refreshLinkDrafts();
      t.saveDecisions();
      state.open = true;
      state.step = "review";
      state.task = "links";
      document.querySelector("#a11y-workshop").hidden = false;
      t.render();
    });
    assert.equal(
      await page.locator("[data-aw=table-start]").isVisible(),
      false,
    );
    await page
      .locator("summary")
      .filter({ hasText: "Create a table on this page" })
      .click();
    await page.locator("[data-aw=table-start]").click();
    const size = page.locator('[data-aw-input=table-size][data-axis=rows]');
    await size.fill("3");
    await size.dispatchEvent("change");
    await size.fill("2");
    await size.press("Tab");
    assert.deepEqual(await page.evaluate(() => window.errors), []);
    const selects = page.locator("[data-aw-input=table-cell-content]");
    for (let i = 0; i < 4; i++) await selects.nth(i).selectOption([String(i)]);
    await page.locator("[data-aw=table-create]").click();
    await page.waitForFunction(() => !window.workshop.state().busy);
    const created = await page.evaluate(() => ({
      drafts: window.workshop.state().tableDrafts,
      operation: JSON.parse(window.requests.at(-1).operations)[0],
    }));
    assert.equal(created.drafts.length, 1);
    assert.equal(created.operation.action, "create_table");
    assert.equal(created.operation.rows[0][0].role, "TH");
    assert.equal(created.operation.rows[1][1].role, "TD");
    await page.evaluate(() =>
      window.workshop.rebuildStructure("Headings", "headings"),
    );
    assert.equal(
      await page.evaluate(
        () =>
          window.requests.filter(
            (r) =>
              r.operations &&
              JSON.parse(r.operations)[0].action === "create_table",
          ).length,
      ),
      2,
    );
    await page.evaluate(() => window.workshop.undo());
    await page.evaluate(() => window.workshop.undo());
    assert.deepEqual(
      await page.evaluate(() => window.workshop.state().tableDrafts),
      [],
    );
    const before = await page.evaluate(() => window.requests.length);
    await page.evaluate(() =>
      window.workshop.rebuildStructure("Headings", "headings"),
    );
    assert.equal(
      await page.evaluate(
        (before) => window.requests.slice(before).some((r) => r.operations),
        before,
      ),
      false,
    );
    assert.deepEqual(await page.evaluate(() => window.errors), []);
  });
});

test("box selection is independent of drag direction and excludes the gap between regions", async () => {
  const { selectionBox, boxContainsCenter } = await loadWorkshop();
  const box = selectionBox({ x: 0.8, y: 0.5 }, { x: 0.1, y: 0.1 });
  assert.equal(box.x, 0.1);
  assert.equal(box.y, 0.1);
  assert.equal(
    boxContainsCenter(box, { x: 0.2, y: 0.2, width: 0.1, height: 0.1 }),
    true,
  );
  assert.equal(
    boxContainsCenter(box, { x: 0.85, y: 0.2, width: 0.1, height: 0.1 }),
    false,
  );
});

test("draw, merge, ungroup and undo retain disjoint regions and replay grouped decisions", async () => {
  await withWorkshop(async (page) => {
    await page.evaluate(() => {
      const t = window.workshop,
        s = t.state();
      s.open = true;
      s.step = "review";
      s.task = "order";
      s.preexistingTree = false;
      document.querySelector("#a11y-workshop").hidden = false;
      document.querySelector("#app").inert = true;
      s.inspection.content_blocks = [
        {
          blockId: "a",
          pageIndex: 0,
          text: "First",
          box: { x: 0.1, y: 0.1, width: 0.2, height: 0.03 },
        },
        {
          blockId: "b",
          pageIndex: 0,
          text: "Second",
          box: { x: 0.1, y: 0.2, width: 0.3, height: 0.03 },
        },
        {
          blockId: "c",
          pageIndex: 0,
          text: "Third",
          box: { x: 0.6, y: 0.3, width: 0.2, height: 0.03 },
        },
      ];
      window.testContentBlocks = s.inspection.content_blocks;
      s.blockOrder = { 0: ["a", "b", "c"] };
      t.saveDecisions();
      t.render();
    });
    await page.click('[data-aw="draw-tag"]');
    const bounds = await page.locator('[data-aw-ref="page"]').boundingBox();
    await page.mouse.move(
      bounds.x + bounds.width * 0.05,
      bounds.y + bounds.height * 0.05,
    );
    await page.mouse.down();
    await page.mouse.move(
      bounds.x + bounds.width * 0.45,
      bounds.y + bounds.height * 0.25,
    );
    await page.mouse.up();
    assert.equal(await page.locator('[data-aw="block-select"]').count(), 2);
    await page.locator('[data-aw="block-merge"]').first().click();
    assert.equal(await page.locator('[data-aw="block-select"]').count(), 1);
    const grouped = await page.evaluate(() => {
      const s = window.workshop.state();
      return s.customBlocks[s.blockOrder[0][0]];
    });
    assert.equal(grouped.regions.length, 3);
    await page.click('[data-aw="block-split"]');
    assert.equal(await page.locator('[data-aw="block-select"]').count(), 3);
    await page.locator('[data-aw="block-merge"]').first().click();
    await page.evaluate(async () => {
      await window.workshop.confirmOrderPage();
      await window.workshop.rebuildStructure("Again", "");
    });
    const payloads = await page.evaluate(() =>
      window.requests
        .filter((r) => r.action === "draft_structure")
        .map((r) => JSON.parse(r.content_decisions)),
    );
    assert.equal(payloads.length, 2);
    for (const payload of payloads) {
      assert.equal(payload[0].groupId, payload[1].groupId);
      assert.ok(payload[0].groupId);
      assert.equal(payload[2]?.groupId || "", "");
    }
    await page.evaluate(async () => {
      await window.workshop.undo();
      await window.workshop.undo();
    });
    assert.deepEqual(
      await page.evaluate(() => window.workshop.state().customBlocks),
      {},
    );
    assert.deepEqual(await page.evaluate(() => window.errors), []);
  });
});

test("existing tags save directly without rebuilding and remain undoable", async () => {
  await withWorkshop(async (page) => {
    await page.evaluate(() => {
      const t = window.workshop,
        s = t.state();
      s.preexistingTree = true;
      s.treeDecision = "keep";
      s.originalInspection = s.inspection;
      s.open = true;
      document.querySelector("#a11y-workshop").hidden = false;
      s.step = "review";
      s.task = "headings";
      s.inspection.structure_editor = window.testStructureEditor = {
        nodes: [
          {
            path: "0/0",
            role: "P",
            title: "Section",
            actualText: "",
            altText: "",
          },
        ],
      };
      t.saveDecisions();
      t.render();
    });
    await page.locator(".aw-tag-advanced summary").click();
    await page.locator('[data-tag-key="role"]').selectOption("H2");
    await page
      .locator('[data-tag-key="actualText"]')
      .fill("Correct section text");
    await page.locator('[data-aw="existing-tag-save"]').click();
    await page.waitForFunction(
      () => !window.workshop.state().busy && window.requests.length > 0,
    );
    const result = await page.evaluate(async () => {
      const requests = window.requests.slice();
      const history = window.workshop.state().history.length;
      await window.workshop.undo();
      return {
        requests,
        history,
        preserved: window.workshop.state().preexistingTree,
        errors: window.errors,
      };
    });
    assert.equal(result.requests.length, 1);
    assert.equal(result.requests[0].action, "structure");
    assert.deepEqual(JSON.parse(result.requests[0].operations), [
      {
        action: "edit_tag",
        path: "0/0",
        role: "H2",
        title: "Section",
        actualText: "Correct section text",
        altText: "",
      },
    ]);
    assert.equal(result.history, 1);
    assert.equal(result.preserved, true);
    assert.deepEqual(result.errors, []);
  });
});

function tagGeometryPage(items, rotated = false) {
  return {
    getViewport: () => ({
      width: rotated ? 200 : 100, height: rotated ? 100 : 200,
      // Crop origin (10,20), including a 90-degree rotated page.
      convertToViewportPoint: (x, y) => rotated ? [y - 20, x - 10] : [x - 10, 220 - y],
    }),
    getTextContent: async () => ({ items, styles: { f: { ascent: 0.8 } } }),
  };
}
function markedText(mcid, x, y) {
  return [{ type: "beginMarkedContentProps", id: "p3R_mc" + mcid },
    { str: "Repeated label", fontName: "f", width: 20, transform: [10, 0, 0, 10, x, y] },
    { type: "endMarkedContent" }];
}

test("existing tag geometry follows MCIDs, cropped/rotated pages and annotations", async () => {
  const { existingTagRegions } = await loadWorkshop();
  const nodes = [
    { path: "0/0", contentRefs: [{ pageIndex: 0, mcid: 1, stream: "page" }] },
    { path: "0/1", contentRefs: [{ pageIndex: 0, mcid: 2, stream: "page" }] },
    { path: "0/2", contentRefs: [{ pageIndex: 0, rect: [20, 30, 40, 50] }] },
    { path: "0/3", contentRefs: [{ pageIndex: 1, mcid: 1, stream: "page" }] },
  ];
  const items = [...markedText(1, 20, 200), ...markedText(2, 60, 100)];
  const regions = await existingTagRegions(tagGeometryPage(items), nodes, 0);
  assert.deepEqual(regions.get("0/0"), [{ x: 0.1, y: 0.06, width: 0.19999999999999998, height: 0.05 }]);
  assert.equal(regions.get("0/1")[0].x, 0.5);
  assert.equal(regions.has("0/3"), false);
  assert.equal(regions.get("0/2")[0].y, 0.85);
  const rotated = await existingTagRegions(tagGeometryPage(items, true), nodes, 0);
  assert.equal(rotated.get("0/0")[0].y, 0.1);
  assert.ok(Math.abs(rotated.get("0/0")[0].width - 0.05) < 1e-8);
});

test("ambiguous MCIDs across streams do not produce misleading highlights", async () => {
  const { existingTagRegions } = await loadWorkshop();
  const nodes = [
    { path: "0/0", contentRefs: [{ pageIndex: 0, mcid: 1, stream: "page" }] },
    { path: "0/1", contentRefs: [{ pageIndex: 0, mcid: 1, stream: "(9, 0)" }] },
  ];
  const regions = await existingTagRegions(tagGeometryPage(markedText(1, 20, 200)), nodes, 0);
  assert.equal(regions.size, 0);
});

test("tagged images use their painted transform and restore graphics state", async () => {
  const { existingTagRegions } = await loadWorkshop();
  const ops = { save: 1, restore: 2, transform: 3, beginMarkedContentProps: 4, endMarkedContent: 5, paintImageXObject: 6 };
  const page = tagGeometryPage([]);
  page.getOperatorList = async () => ({
    fnArray: [4, 1, 3, 6, 2, 5],
    argsArray: [["Figure", 7], [], [20, 0, 0, 40, 20, 100], ["image"], [], []],
  });
  const result = await existingTagRegions(page, [{ path: "0", contentRefs: [{ pageIndex: 0, mcid: 7 }] }], 0, ops);
  const box = result.get("0")[0];
  assert.equal(box.x, 0.1);
  assert.equal(box.y, 0.4);
  assert.ok(Math.abs(box.height - 0.2) < 1e-8);
});

test("existing tags use the center preview and one editor, retaining unsaved edits across selection", async () => {
  await withWorkshop(async (page) => {
    await page.evaluate(() => {
      const t = window.workshop, s = t.state();
      s.preexistingTree = true; s.treeDecision = "keep"; s.originalInspection = s.inspection;
      s.open = true; s.step = "review"; s.task = "headings";
      document.querySelector("#a11y-workshop").hidden = false;
      s.inspection.structure_editor.nodes = [
        { path: "0", role: "Document", contentRefs: [] },
        { path: "0/0", role: "H1", text: "Court filing", pageIndex: 0, regions: [{ pageIndex: 0, box: { x: .1, y: .1, width: .4, height: .04 } }] },
        { path: "0/1", role: "P", text: "County", pageIndex: 0, regions: [{ pageIndex: 0, box: { x: .1, y: .2, width: .2, height: .03 } }] },
        { path: "0/2", role: "Figure", altText: "Seal", pageIndex: 0, regions: [{ pageIndex: 0, box: { x: .6, y: .1, width: .2, height: .15 } }] },
        { path: "0/3", role: "P", text: "Next page", pageIndex: 1, regions: [{ pageIndex: 1, box: { x: .1, y: .1, width: .4, height: .03 } }] },
        { path: "0/4", role: "P", text: "Unlocated text", pageIndex: 0 },
      ];
      s.pageViews.push([0, 0, 612, 792]);
      t.render();
    });
    await page.locator('[data-aw-mark="tag:0/1"]').click();
    assert.equal(await page.locator('[data-existing-tag]').count(), 1);
    assert.equal(await page.locator('[data-existing-tag]').getAttribute('data-existing-tag'), '0/1');
    assert.equal(await page.locator('.aw-tag-advanced').getAttribute('open'), null);
    await page.locator('[data-tag-key="role"]').selectOption('H2');
    await page.locator('[data-aw-mark="tag:0/0"]').click();
    await page.waitForFunction(() => document.querySelector(".aw-mark-current .aw-mark-label")?.textContent === "H1");
    await page.waitForTimeout(30);
    await page.locator('[data-aw-mark="tag:0/1"]').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('[data-tag-key="role"]').inputValue(), 'H2');
    await page.locator('[data-aw-input="existing-tag-select"]').selectOption('0/2');
    assert.equal(await page.locator('[data-tag-key="altText"]').isVisible(), true);
    await page.locator('[data-aw-input="existing-tag-select"]').selectOption('0/3');
    assert.equal(await page.evaluate(() => window.workshop.state().page), 1);
    await page.locator('[data-aw-mark="tag:0/3"]').waitFor();
    assert.equal(await page.locator('[data-aw-mark="tag:0/1"]').count(), 0);
    await page.locator('[data-aw-input="existing-tag-select"]').selectOption('0');
    assert.equal(await page.locator('[data-existing-tag]').getAttribute('data-existing-tag'), '0');
    await page.locator('[data-aw-input="existing-tag-select"]').selectOption('0/4');
    assert.match(await page.locator('[data-existing-tag]').textContent(), /No reliable page highlight/);
    assert.deepEqual(await page.evaluate(() => window.errors), []);
  });
});

test("reading order merges Ctrl/Cmd-selected existing tags and undo restores them", async () => {
  await withWorkshop(async page => {
    await page.evaluate(async () => {
      const t = window.workshop, s = t.state();
      const leaf = (path, mcid, role = "P") => ({path, role, pageIndex:0, text: "Text " + mcid, contentRefs:[{pageIndex:0,mcid,stream:"page"}]});
      window.testStructureEditor = {nodes:[leaf("0/0",0), leaf("0/1",1), leaf("0/2",2)]};
      window.testMergeStructureEditor = {nodes:[
        {path:"0/0",role:"P",merged:true,pageIndex:0,text:"Text 0 Text 1",contentRefs:[]},
        leaf("0/0/0",0,"Span"),leaf("0/0/1",1,"Span"),leaf("0/1",2),
      ]};
      window.testTextContent = {styles:{f:{ascent:.8}},items:[0,1,2].flatMap(mcid => [
        {type:"beginMarkedContentProps",id:"p3R_mc"+mcid},
        {str:"Text "+mcid,fontName:"f",width:120,transform:[12,0,0,12,60,700-mcid*40]},
        {type:"endMarkedContent"},
      ])};
      await t.loadWorkingCopy(s.working);
      s.originalInspection = s.inspection; s.preexistingTree=true; s.treeDecision="keep";
      s.open=true; s.step="review";s.task="order";s.reviewed.order=true;s.reviewed.headings=true;
      document.querySelector("#a11y-workshop").hidden=false;
      t.saveDecisions();t.render();
    });
    const mark = path => page.locator(`[data-aw-mark="tag:${path}"]`).first();
    await mark('0/0').click();
    await mark('0/2').click({modifiers:['Control']});
    assert.equal(await page.locator('[data-aw="existing-tags-merge"]').isDisabled(),true);
    assert.match(await page.locator('#aw-review-panel').textContent(),/without skipping/);
    await mark('0/2').click({modifiers:['Meta']});
    await mark('0/1').click({modifiers:['Control']});
    assert.equal(await page.locator('[data-aw-input="existing-tag-check"]:checked').count(),2);
    assert.equal(await page.locator('.aw-mark-current').count(),2);
    await page.locator('[data-aw="existing-tags-merge"]').click();
    await page.waitForFunction(()=>!window.workshop.state().busy && window.requests.length > 0);
    assert.deepEqual(await page.evaluate(()=>JSON.parse(window.requests[0].operations)),[{action:'merge_tags',paths:['0/0','0/1']}]);
    assert.equal(await page.locator('[data-aw-input="existing-tag-check"]').count(),2);
    assert.equal(await page.locator('[data-aw-mark="tag:0/0"]').count(),2);
    assert.equal(await page.locator('[data-aw-mark="tag:0/0/0"]').count(),0);
    assert.equal(await page.evaluate(()=>!!window.workshop.state().reviewed.headings),false);
    await page.evaluate(()=>window.workshop.undo());
    assert.equal(await page.locator('[data-aw-input="existing-tag-check"]').count(),3);
    // Checkboxes provide the same selection without modifier keys.
    await page.locator('[data-aw-input="existing-tag-check"][data-path="0/0"]').check();
    await page.locator('[data-aw-input="existing-tag-check"][data-path="0/1"]').check();
    assert.equal(await page.locator('[data-aw="existing-tags-merge"]').isEnabled(),true);
    await page.evaluate(()=>{const t=window.workshop;t.state().task="headings";t.render();});
    assert.equal(await page.locator('[data-aw="existing-tags-merge"]').count(),0);
    assert.equal(await page.locator('#aw-review-panel [data-aw="task"][data-task="order"]').filter({hasText:'Reading order'}).count(),1);
    assert.deepEqual(await page.evaluate(()=>window.errors),[]);
  });
});

test("draft reading order merges adjacent Ctrl-selected blocks, rejecting gaps", async () => {
  await withWorkshop(async page => {
    await page.evaluate(()=>{
      const t=window.workshop,s=t.state();
      s.preexistingTree=false;s.open=true;s.step="review";s.task="order";
      s.inspection.content_blocks = window.testContentBlocks = [0,1,2].map(i=>({blockId:"b"+i,pageIndex:0,text:"Text "+i,box:{x:.1,y:.1+i*.1,width:.3,height:.03}}));
      t.initializeDecisions();t.saveDecisions();document.querySelector("#a11y-workshop").hidden=false;t.render();
    });
    await page.locator('[data-aw-mark="block:b0"]').click();
    await page.locator('[data-aw-mark="block:b2"]').click({modifiers:['Control']});
    assert.equal(await page.locator('[data-aw="order-selection-merge"]').isDisabled(),true);
    await page.locator('[data-aw-mark="block:b2"]').click({modifiers:['Control']});
    assert.equal(await page.locator(".aw-mark-current").count(), 1);
    await page.locator('[data-aw-mark="block:b1"]').click({modifiers:['Control']});
    await page.locator('[data-aw="order-selection-merge"]').click();
    assert.equal(await page.evaluate(()=>window.workshop.state().blockOrder[0].length),2);
    assert.equal(await page.evaluate(()=>window.requests.length),0);
    await page.locator('[data-aw="order-confirm"]').click();
    await page.waitForFunction(()=>!window.workshop.state().busy && window.requests.length > 0);
    assert.equal(await page.evaluate(()=>window.requests[0].action),'draft_structure');
    await page.evaluate(()=>window.workshop.undo());
    assert.equal(await page.evaluate(()=>window.workshop.state().blockOrder[0].length),3);
    assert.deepEqual(await page.evaluate(()=>window.errors),[]);
  });
});
