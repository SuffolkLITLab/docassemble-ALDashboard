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
    const page = await browser.newPage();
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
        writeFieldNames, rebuildStructure, undo, applyChange, confirmOrderPage, writeImages, textFixPayload },
    open: open,`,
      );
    await page.setContent(template);
    await page.addScriptTag({
      type: "module",
      content:
        source + "\nwindow.createTestWorkshop = createAccessibilityWorkshop;",
    });
    await page.waitForFunction(() => !!window.createTestWorkshop);
    await page.evaluate(async () => {
      window.requests = [];
      window.errors = [];
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
              content_blocks: [],
              images: [],
              tag_structure: { present: true },
              readback: { announcements: [] },
            },
          };
        }
        const fields = Object.fromEntries(
          [...form.entries()].filter(([key]) => key !== "file"),
        );
        window.requests.push(fields);
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
        pdfjsLib: {
          getDocument: ({ data }) => ({
            promise: Promise.resolve({
              numPages: 1,
              destroy() {},
              getPage: async () => ({
                view: [0, 0, 612, 792],
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
