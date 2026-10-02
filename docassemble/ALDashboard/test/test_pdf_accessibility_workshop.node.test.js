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

function block(blockId, text, x, y, width, height) {
  return { blockId, text, box: { x, y, width, height } };
}

test("a printed label loses its colon, asterisk and leader underscores", async () => {
  const { cleanLabelText } = await loadWorkshop();
  assert.equal(cleanLabelText("Date of birth *:"), "Date of birth");
  assert.equal(cleanLabelText("Name ________"), "Name");
  assert.equal(cleanLabelText("  • Phone "), "Phone");
});

test("the label on the same line to the left wins over text above", async () => {
  const { findNearbyLabel } = await loadWorkshop();
  const field = { x: 0.3, y: 0.2, width: 0.3, height: 0.02 };
  const nearby = findNearbyLabel(field, [
    block("above", "Section 1", 0.3, 0.17, 0.2, 0.015),
    block("left", "Full name *", 0.1, 0.202, 0.15, 0.015),
  ]);
  assert.equal(nearby.text, "Full name");
  assert.equal(nearby.required, true);
  assert.equal(nearby.labelBlockId, "left");
  assert.equal(nearby.where, "left of the field");
});

test("a format hint below the field is added to the label above it", async () => {
  const { findNearbyLabel } = await loadWorkshop();
  const field = { x: 0.1, y: 0.3, width: 0.2, height: 0.03 };
  const nearby = findNearbyLabel(field, [
    block("label", "Date of birth", 0.1, 0.28, 0.15, 0.015),
    block("hint", "MM/DD/YYYY", 0.1, 0.335, 0.1, 0.012),
  ]);
  assert.equal(nearby.text, "Date of birth (MM/DD/YYYY)");
  assert.equal(nearby.hintBlockId, "hint");
});

test("a field with no printed words nearby has no drafted label", async () => {
  const { findNearbyLabel } = await loadWorkshop();
  const field = { x: 0.5, y: 0.5, width: 0.2, height: 0.02 };
  assert.equal(
    findNearbyLabel(field, [block("far", "Footer", 0.1, 0.95, 0.2, 0.01)]),
    null,
  );
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

test("generated and placeholder names are not treated as real names", async () => {
  const { isDerivedFromFieldName, isPlaceholderName } = await loadWorkshop();
  assert.equal(isDerivedFromFieldName("users1 name", "users1_name"), true);
  assert.equal(isDerivedFromFieldName("Your name", "users1_name"), false);
  assert.equal(isPlaceholderName("undefined"), true);
  assert.equal(isPlaceholderName("Text12"), true);
  assert.equal(isPlaceholderName("Check Box3"), true);
  assert.equal(isPlaceholderName("Date of birth"), false);
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
