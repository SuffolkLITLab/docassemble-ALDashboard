// PDF accessibility workshop: a full-screen review mode of the PDF labeler.
//
// The workshop follows three steps. It finds problems and quietly makes the
// repairs that need no judgment. A person then reviews eight tasks, each a
// question a reader would ask. Last comes testing and export. The server
// keeps nothing between calls: every repair sends the working copy and gets
// a new one back, and every decision lives here until a person applies it.
//
// Three kinds of evidence stay separate all the way to export: machine
// checks, human review, and testing outside this tool. There is no single
// score, and nothing here ever declares conformance on its own.

const TASKS = [
  {
    id: "document",
    category: "Title & language",
    question: "What is this document?",
    done: "Done when a person confirmed the title and the language.",
  },
  {
    id: "fields",
    category: "Form controls",
    question: "Can people tell what to enter?",
    done: "Done when every field has a name you confirmed in context.",
  },
  {
    id: "tab",
    category: "Keyboard order",
    question: "Does Tab move sensibly?",
    done: "Done when you walked the Tab order on every page with fields.",
  },
  {
    id: "order",
    category: "Reading order",
    question: "Does it read in the right order?",
    done: "Done when the spoken sequence matches the page on every page, and nothing is left unread.",
  },
  {
    id: "headings",
    category: "Headings & tags",
    question: "Is the structure right?",
    done: "Done when every heading candidate is confirmed or dismissed, and any earlier tags have a decision.",
  },
  {
    id: "images",
    category: "Images",
    question: "Are images described?",
    done: "Done when every image is decorative or has a description a person checked. AI drafts never count on their own.",
  },
  {
    id: "links",
    category: "Tables & links",
    question: "Can tables & links be navigated?",
    done: "Done when every link has a confirmed description and every table's header cells are confirmed.",
  },
  {
    id: "text",
    category: "Text fidelity",
    question: "Does the text say what it shows?",
    done: "Done when every font is embedded, every glyph maps to text, and every correction was checked by a person.",
  },
];

const TASK_BY_ID = new Map(
  TASKS.map(function (task) {
    return [task.id, task];
  }),
);

// Report checks grouped by the task that fixes them.
const TASK_CHECKS = {
  document: ["document-title", "document-language", "display-title"],
  fields: ["field-names", "form-structure-alt", "form-structure-objects"],
  tab: ["tab-order"],
  order: ["structure-tree", "content-tags"],
  headings: ["mark-info"],
  images: ["figure-alt"],
  links: ["annotation-description"],
  text: ["font-embedding", "unicode-maps"],
};

const READBACK_CATEGORY_TASK = {
  "field-names": "fields",
  "reading-order": "order",
  "heading-outline": "headings",
  "image-coverage": "images",
  "text-encoding": "text",
};

const EXTERNAL_TESTS = [
  {
    id: "validator",
    title: "PDF/UA validator",
    hint: "For example veraPDF with the PDF/UA-1 profile",
  },
  {
    id: "keyboard",
    title: "Keyboard only",
    hint: "Tab through every field in a PDF reader",
  },
  {
    id: "screenReader",
    title: "Real screen reader",
    hint: "Read all and forms mode, for example NVDA or JAWS",
  },
  {
    id: "looks",
    title: "Search, copy & looks",
    hint: "Spot-check text, and compare with the original",
  },
];

const LANGUAGES = [
  ["en-US", "English (US)"],
  ["en", "English"],
  ["es-US", "Spanish (US)"],
  ["es", "Spanish"],
  ["fr", "French"],
  ["pt", "Portuguese"],
  ["vi", "Vietnamese"],
  ["zh", "Chinese"],
  ["ht", "Haitian Creole"],
  ["ru", "Russian"],
  ["ar", "Arabic"],
  ["ko", "Korean"],
  ["tl", "Tagalog"],
];

const LANGUAGE_HINTS = {
  "en-US": ["the", "and", "of", "to", "you", "your", "for", "is", "this"],
  "es-US": ["el", "la", "de", "que", "y", "los", "su", "para", "por"],
  fr: ["le", "la", "les", "des", "et", "est", "vous", "pour", "une"],
  pt: ["o", "os", "da", "do", "que", "e", "para", "não", "uma"],
};

const ROLE_CHOICES = [
  ["P", "Paragraph"],
  ["H1", "Heading 1"],
  ["H2", "Heading 2"],
  ["H3", "Heading 3"],
  ["H4", "Heading 4"],
  ["LI", "List item"],
  ["Artifact", "Don’t read"],
];

const PAGE_RENDER_WIDTH = 760;

// What the server found wrong with a field's current name, in the words a
// reviewer needs. See pdf_field_labels.field_label_problem.
const NAME_PROBLEMS = {
  missing: "It has no name, so a screen reader says only its type.",
  placeholder:
    "Its name is a placeholder or a hint about operating it, which says nothing about the field.",
  derived: "Its name is the internal field name read aloud.",
  instruction:
    "Its name is an instruction. The screen reader already says “edit text” or “check box”; the name should say what the information is.",
  "option-missing":
    "It is one of several answer boxes, and its name doesn’t say which answer it is.",
};

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function plural(count, singular, pluralForm) {
  return (
    String(count) +
    " " +
    (count === 1 ? singular : pluralForm || singular + "s")
  );
}

function countOf(value) {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") return Object.keys(value).length;
  return Number(value) || 0;
}

function base64ToBytes(text) {
  const binary = window.atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function stripPdf(name) {
  return String(name || "document.pdf").replace(/\.pdf$/i, "");
}

// Printed text is written for the eye: a colon, an asterisk for "required",
// leader underscores. None of that should be spoken in a title.
export function cleanLabelText(text) {
  return String(text || "")
    .replace(/_{2,}/g, " ")
    .replace(/\.{3,}/g, " ")
    .replace(/\s+/g, " ")
    .replace(/[\s:*]+$/g, "")
    .replace(/^[\s:*•·-]+/g, "")
    .trim();
}

function looksLikeFilename(title) {
  const text = String(title || "").trim();
  return (
    !text ||
    /\.(pdf|docx?|indd|ai)$/i.test(text) ||
    /_20/.test(text) ||
    /^untitled/i.test(text) ||
    /^microsoft word/i.test(text)
  );
}

export function guessLanguage(text) {
  const words = String(text || "")
    .toLowerCase()
    .split(/[^a-zà-ÿ]+/)
    .filter(Boolean);
  if (words.length < 12) return "";
  let best = "";
  let bestScore = 0;
  Object.keys(LANGUAGE_HINTS).forEach(function (code) {
    const hints = new Set(LANGUAGE_HINTS[code]);
    const score = words.filter(function (word) {
      return hints.has(word);
    }).length;
    if (score > bestScore) {
      best = code;
      bestScore = score;
    }
  });
  return bestScore / words.length >= 0.04 ? best : "";
}

function languageLabel(code) {
  const match = LANGUAGES.find(function (entry) {
    return entry[0] === code;
  });
  return match ? match[1] : code;
}

// Visual order: page, then rows from the top, then left to right. Fields
// whose tops are within a few points share a row.
export function visualFieldOrder(fields) {
  return fields
    .slice()
    .sort(function (first, second) {
      if (first.pageIndex !== second.pageIndex)
        return first.pageIndex - second.pageIndex;
      const rowDelta = first.box.y - second.box.y;
      if (Math.abs(rowDelta) > 0.008) return rowDelta;
      return first.box.x - second.box.x;
    })
    .map(function (field) {
      return field.name;
    });
}

// Column order: within each page, fields that share a left edge are read
// top to bottom before the next column starts.
export function columnFieldOrder(fields) {
  const byPage = new Map();
  fields.forEach(function (field) {
    if (!byPage.has(field.pageIndex)) byPage.set(field.pageIndex, []);
    byPage.get(field.pageIndex).push(field);
  });
  const result = [];
  Array.from(byPage.keys())
    .sort(function (a, b) {
      return a - b;
    })
    .forEach(function (pageIndex) {
      const columns = [];
      byPage
        .get(pageIndex)
        .slice()
        .sort(function (a, b) {
          return a.box.x - b.box.x;
        })
        .forEach(function (field) {
          const column = columns.find(function (entry) {
            return Math.abs(entry.x - field.box.x) < 0.03;
          });
          if (column) column.fields.push(field);
          else columns.push({ x: field.box.x, fields: [field] });
        });
      columns.forEach(function (column) {
        column.fields
          .sort(function (a, b) {
            return a.box.y - b.box.y;
          })
          .forEach(function (field) {
            result.push(field.name);
          });
      });
    });
  return result;
}

// Count the places where Tab would move back up the page without starting
// a new column -- the jumps a keyboard user notices.
export function countOrderJumps(order, fieldsByName) {
  let jumps = 0;
  for (let index = 1; index < order.length; index += 1) {
    const previous = fieldsByName.get(order[index - 1]);
    const current = fieldsByName.get(order[index]);
    if (!previous || !current || !previous.box || !current.box) continue;
    if (previous.pageIndex !== current.pageIndex) {
      if (current.pageIndex < previous.pageIndex) jumps += 1;
      continue;
    }
    const climbed = previous.box.y - current.box.y;
    const newColumn = current.box.x > previous.box.x + 0.05;
    if (climbed > 0.01 && !newColumn) jumps += 1;
  }
  return jumps;
}

function controlRoleWords(field) {
  if (!field) return "edit text";
  if (field.type === "checkbox") return "check box";
  if (field.type === "radio") return "radio button";
  if (field.type === "combo") return "combo box";
  if (field.type === "list") return "list box";
  if (field.type === "signature") return "signature field";
  if (field.type === "button") return "button";
  return "edit text";
}

export function spokenAnnouncement(item, fieldsByName) {
  const text = String(item.text || "").trim();
  if (item.kind === "field") {
    const field = fieldsByName ? fieldsByName.get(item.name) : null;
    return (
      (text || "unlabeled") +
      ", " +
      controlRoleWords(field) +
      (field && field.required ? ", required" : "")
    );
  }
  const heading = /^H([1-6])$/.exec(String(item.role || ""));
  if (heading) return "Heading level " + heading[1] + ", " + text;
  if (item.role === "Figure") return "Graphic, " + (text || "unlabeled");
  if (item.role === "Link") return "Link, " + text;
  return text;
}

// Only serializable review state belongs in a PDF history entry. Keep Sets
// intact and detach nested drafts so subsequent edits cannot mutate history.
const DECISION_KEYS = [
  "preexistingTree",
  "treeDecision",
  "reviewed",
  "meta",
  "names",
  "tabOrder",
  "tabConfirmedPages",
  "tabEdited",
  "blockOrder",
  "blockRoles",
  "orderEditedPages",
  "orderConfirmedPages",
  "headings",
  "images",
  "links",
  "tables",
  "textFixes",
  "glyphs",
  "external",
  "recording",
  "declare",
  "receipts",
  "receiptDetails",
  "integrity",
  "aiNotes",
  "aiDismissed",
  "aiRanAt",
];

export function snapshotDecisions(state) {
  return structuredClone(
    Object.fromEntries(
      DECISION_KEYS.map(function (key) {
        return [key, state[key]];
      }),
    ),
  );
}

export function createAccessibilityWorkshop(host) {
  const root = document.getElementById("a11y-workshop");
  if (!root) {
    return {
      open: function () {},
      close: function () {},
      isOpen: function () {
        return false;
      },
    };
  }
  const els = {
    filename: root.querySelector("[data-aw-ref=filename]"),
    fileMeta: root.querySelector("[data-aw-ref=file-meta]"),
    rail: root.querySelector("[data-aw-ref=rail]"),
    main: root.querySelector("[data-aw-ref=main]"),
    status: root.querySelector("[data-aw-ref=status]"),
    undo: root.querySelector("[data-aw=undo]"),
    history: root.querySelector("[data-aw-ref=history]"),
    busy: root.querySelector("[data-aw-ref=busy]"),
    busyText: root.querySelector("[data-aw-ref=busy-text]"),
  };
  let ws = freshState();
  let renderVersion = 0;
  let paintVersion = 0;
  let previewContent = { overlays: [], overlaySvg: "" };
  let previewRenderTimer;
  let lastPreviewWidth = 0;
  let panelDrag = null;
  const previewObserver = new ResizeObserver(syncPreviewLayout);
  const pageCanvasCache = new Map();

  function freshState() {
    return {
      open: false,
      signature: "",
      filename: "document.pdf",
      original: null,
      working: null,
      version: 0,
      history: [],
      savedDecisions: null,
      inspection: null,
      originalInspection: null,
      pdfDoc: null,
      pageViews: [],
      fields: [],
      fieldsByName: new Map(),
      busy: false,
      step: "find",
      task: "document",
      page: 0,
      previewZoom: null,
      inspectorWidth: null,
      receipts: [],
      receiptDetails: [],
      integrity: null,
      preexistingTree: false,
      treeDecision: "",
      reviewed: {},
      meta: null,
      names: {},
      nameCursor: 0,
      showAllFields: false,
      tabOrder: [],
      tabConfirmedPages: new Set(),
      tabEdited: false,
      blockOrder: {},
      blockRoles: {},
      orderEditedPages: new Set(),
      orderConfirmedPages: new Set(),
      selectedBlock: "",
      headings: {},
      images: {},
      imageCursor: 0,
      previews: null,
      links: {},
      tables: {},
      textFixes: {},
      fontReview: null,
      fontSubs: null,
      glyphs: {},
      replay: { mode: "all", index: 0, playing: false, heardAll: false },
      external: {},
      recording: "",
      declare: false,
      aiNotes: null,
      aiDismissed: new Set(),
      aiRanAt: 0,
      draftWithAi: false,
    };
  }

  // ---------------------------------------------------------------------
  // Server calls
  // ---------------------------------------------------------------------

  function pdfFile(bytes) {
    return new File([bytes], ws.filename, { type: "application/pdf" });
  }

  async function postPdf(path, bytes, fields) {
    const formData = new FormData();
    formData.append("file", pdfFile(bytes));
    Object.keys(fields || {}).forEach(function (key) {
      const value = fields[key];
      if (value === undefined || value === null) return;
      formData.append(
        key,
        typeof value === "string" ? value : JSON.stringify(value),
      );
    });
    const response = await fetch(host.apiUrl(path), {
      method: "POST",
      headers: { Accept: "application/json" },
      body: formData,
      credentials: "same-origin",
    });
    const payload = await host.parseApiResponse(response);
    if (!payload || !payload.success) {
      throw new Error(
        (payload && payload.error && payload.error.message) ||
          "The request failed.",
      );
    }
    return payload.data;
  }

  async function postJson(path, body) {
    const response = await fetch(host.apiUrl(path), {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(Object.assign({ model: host.model() }, body)),
      credentials: "same-origin",
    });
    const payload = await host.parseApiResponse(response);
    if (!payload || !payload.success) {
      throw new Error(
        (payload && payload.error && payload.error.message) ||
          "The request failed.",
      );
    }
    return payload.data;
  }

  async function remediate(action, fields, bytes) {
    const data = await postPdf(
      "/pdf-labeler/api/accessibility-remediate",
      bytes || ws.working,
      Object.assign({ action: action }, fields),
    );
    const unresolved =
      (data.remediation_result || {}).unresolved_decisions || [];
    if (unresolved.length) {
      host.showError(
        plural(unresolved.length, "saved text correction") +
          " could not be applied because the content is missing or ambiguous. " +
          "The corrections were kept for review; no other passage received them.",
      );
    }
    return {
      bytes: base64ToBytes(data.pdf_base64),
      result: data.remediation_result || {},
    };
  }

  async function inspect(bytes) {
    return postPdf("/pdf-labeler/api/accessibility-inspect", bytes, {});
  }

  // ---------------------------------------------------------------------
  // Loading a working copy
  // ---------------------------------------------------------------------

  async function loadWorkingCopy(bytes) {
    const inspection = await inspect(bytes);
    // pdf.js transfers the buffer it is given, so hand it a copy.
    const pdfDoc = await host.pdfjsLib.getDocument({ data: bytes.slice() })
      .promise;
    const pageViews = [];
    const widgets = [];
    for (let index = 0; index < pdfDoc.numPages; index += 1) {
      const page = await pdfDoc.getPage(index + 1);
      const view = page.view;
      pageViews.push(view);
      const annotations = await page.getAnnotations({ intent: "display" });
      annotations.forEach(function (annotation) {
        if (annotation.annotationType !== 20 || !annotation.fieldName) return;
        widgets.push({ pageIndex: index, view: view, annotation: annotation });
      });
    }
    if (ws.pdfDoc) {
      try {
        ws.pdfDoc.destroy();
      } catch (_error) {}
    }
    ws.working = bytes;
    ws.version += 1;
    pageCanvasCache.clear();
    ws.pdfDoc = pdfDoc;
    ws.pageViews = pageViews;
    ws.inspection = inspection;
    buildFields(widgets);
    ws.page = Math.min(ws.page, Math.max(0, ws.pageViews.length - 1));
  }

  function normalizedRect(rect, view) {
    const width = view[2] - view[0] || 1;
    const height = view[3] - view[1] || 1;
    const left = Math.min(rect[0], rect[2]);
    const right = Math.max(rect[0], rect[2]);
    const bottom = Math.min(rect[1], rect[3]);
    const top = Math.max(rect[1], rect[3]);
    return {
      x: (left - view[0]) / width,
      y: (view[3] - top) / height,
      width: (right - left) / width,
      height: (top - bottom) / height,
    };
  }

  function widgetType(annotation) {
    if (annotation.fieldType === "Sig") return "signature";
    if (annotation.fieldType === "Btn") {
      if (annotation.checkBox) return "checkbox";
      if (annotation.radioButton) return "radio";
      return "button";
    }
    if (annotation.fieldType === "Ch")
      return annotation.combo ? "combo" : "list";
    return "text";
  }

  function buildFields(widgets) {
    const byName = new Map();
    widgets.forEach(function (entry) {
      const annotation = entry.annotation;
      const name = String(annotation.fieldName);
      if (byName.has(name)) {
        byName.get(name).widgetCount += 1;
        return;
      }
      byName.set(name, {
        name: name,
        pageIndex: entry.pageIndex,
        box: normalizedRect(annotation.rect, entry.view),
        type: widgetType(annotation),
        required: !!(Number(annotation.fieldFlags || 0) & 2),
        widgetCount: 1,
        tooltip: "",
        hasCustomTooltip: false,
      });
    });
    const records = (ws.inspection && ws.inspection.fields) || [];
    records.forEach(function (record) {
      const name = String(record.name || "");
      let field = byName.get(name);
      if (!field) {
        field = {
          name: name,
          pageIndex: Number(record.pageIndex || 0),
          box: null,
          type: "text",
          required: false,
          widgetCount: 1,
        };
        byName.set(name, field);
      }
      field.tooltip = record.has_custom_tooltip ? String(record.tooltip) : "";
    });
    const labels = (ws.inspection && ws.inspection.field_labels) || {};
    byName.forEach(function (field, name) {
      field.label = labels[name] || { problem: "", suggested: "" };
      if (field.label.required) field.required = true;
      // A name the server found nothing wrong with is a real name; anything
      // else starts the review empty or as a draft.
      field.hasCustomTooltip = !!field.tooltip && !field.label.problem;
    });
    ws.fieldsByName = byName;
    ws.fields = Array.from(byName.values());
  }

  // ---------------------------------------------------------------------
  // Opening the workshop: inspect, then the repairs that need no judgment
  // ---------------------------------------------------------------------

  // ``options.signature`` identifies the field editor's state. While it is
  // unchanged, reopening the workshop resumes the same review session;
  // ``options.getPdf`` is only called to start a new one.
  async function open(options) {
    stopSpeaking();
    const signature = String(options.signature || "");
    setEditorHidden(true);
    if (ws.inspection && signature && ws.signature === signature) {
      ws.open = true;
      render();
      focusMainHeading();
      return;
    }
    ws = freshState();
    ws.open = true;
    ws.signature = signature;
    render();
    setBusy("Preparing the PDF with its fields…");
    try {
      const prepared = await options.getPdf();
      ws.filename = String(prepared.filename || "document.pdf");
      ws.original = prepared.bytes.slice();
      render();
      setBusy("Inspecting every page…");
      await loadWorkingCopy(ws.original);
      ws.originalInspection = ws.inspection;
      ws.preexistingTree = !!(
        ws.originalInspection.tag_structure &&
        ws.originalInspection.tag_structure.present
      );
      initializeDecisions();
      const originalDecisions = snapshotDecisions(ws);
      const repaired = await runAutomaticRepairs(ws.original);
      await loadWorkingCopy(repaired);
      checkIntegrity();
      if (ws.receipts.length) {
        ws.history.push({
          label: "Automatic repairs",
          bytes: ws.original,
          decisions: originalDecisions,
          taskId: "",
          at: new Date(),
        });
      }
      initializeDecisions();
      saveDecisions();
    } catch (error) {
      host.showError(
        "The accessibility workshop could not inspect this PDF: " +
          (error.message || error),
      );
      ws = freshState();
      close();
      return;
    } finally {
      setBusy("");
    }
    render();
    focusMainHeading();
  }

  async function runAutomaticRepairs(bytes) {
    let current = bytes;
    setBusy("Embedding fonts and adding text mappings…");
    try {
      const fonts = await remediate("fonts", {}, current);
      current = fonts.bytes;
      const embedded = countOf(fonts.result.fonts_embedded);
      const maps = countOf(fonts.result.unicode_maps_added);
      if (embedded)
        addReceipt(
          plural(embedded, "font") + " embedded (exact installed match)",
          fonts.result,
        );
      if (maps)
        addReceipt(
          "Text mappings added for " + plural(maps, "font"),
          fonts.result,
        );
    } catch (error) {
      ws.receiptDetails.push({
        label: "Font repair skipped: " + (error.message || error),
      });
    }
    if (!ws.preexistingTree) {
      setBusy("Drafting tags from the page layout…");
      const draft = await remediate(
        "draft_structure",
        { overwrite: "false", mark_as_tagged: "false" },
        current,
      );
      current = draft.bytes;
      const result = draft.result;
      addReceipt(
        "Draft tags built: " +
          plural(Number(result.text_blocks_tagged || 0), "text block") +
          (result.widgets_tagged
            ? ", " + plural(Number(result.widgets_tagged), "form control")
            : ""),
        result,
      );
      if (result.form_alts_added)
        addReceipt("Form field descriptions copied into the form tags", result);
      if (result.content_artifact_runs)
        addReceipt("Lines and borders marked as decoration", result);
      addReceipt("Structure-parent references written", result);
    }
    setBusy("Reconciling accessibility flags…");
    const flags = await remediate(
      "metadata",
      { repair_declaration: "true", display_doc_title: "false" },
      current,
    );
    current = flags.bytes;
    addReceipt("Tagged-document flag matches the tag tree", flags.result);
    return current;
  }

  function addReceipt(label, detail) {
    ws.receipts.push(label);
    ws.receiptDetails.push({ label: label, detail: detail });
  }

  // The repairs above change font dictionaries and tags only. Prove the
  // things a form author cares about survived.
  function checkIntegrity() {
    const before = ((ws.originalInspection || {}).fields || [])
      .map(function (field) {
        return String(field.name);
      })
      .sort();
    const after = ws.fields
      .map(function (field) {
        return field.name;
      })
      .sort();
    const namesKept =
      before.length === after.length &&
      before.every(function (name, index) {
        return name === after[index];
      });
    ws.integrity = { namesKept: namesKept, fieldCount: after.length };
  }

  function integrityLine() {
    if (!ws.integrity) return "";
    if (!ws.integrity.namesKept)
      return '<li class="aw-receipt-problem">Field names changed during repair. Undo the automatic repairs and report this file.</li>';
    return (
      "<li>" +
      (ws.integrity.fieldCount
        ? "All " +
          plural(ws.integrity.fieldCount, "field name") +
          " preserved; "
        : "") +
      "page size and appearance unchanged: repairs touch tags and font data only</li>"
    );
  }

  // ---------------------------------------------------------------------
  // Decisions start as drafts from what the inspection found
  // ---------------------------------------------------------------------

  function initializeDecisions() {
    // Automatic font repairs can change extracted block IDs. Each initial
    // version gets its own drafts, including the original version for Undo.
    ws.names = {};
    ws.headings = {};
    ws.images = {};
    ws.links = {};
    ws.tables = {};
    ws.blockOrder = {};
    const inspection = ws.inspection;
    const metadata = inspection.metadata || {};
    const headings = inspection.heading_candidates || [];
    const blocks = inspection.content_blocks || [];
    const firstHeading = headings.find(function (candidate) {
      return candidate.suggestedTag === "H1";
    });
    let title = String(metadata.title || "").trim();
    let titleSource = "the file’s current title";
    let titleCandidate = "";
    if (looksLikeFilename(title)) {
      if (firstHeading) {
        title = cleanLabelText(firstHeading.text);
        titleSource = "the largest heading on page 1";
        titleCandidate = firstHeading.candidateId;
      } else if (blocks.length) {
        title = cleanLabelText(blocks[0].text);
        titleSource = "the first line on page 1";
      } else {
        title = "";
        titleSource = "";
      }
    }
    const language =
      String(metadata.language || "").trim() ||
      guessLanguage(
        blocks
          .slice(0, 80)
          .map(function (block) {
            return block.text;
          })
          .join(" "),
      );
    ws.meta = {
      title: title,
      titleSource: titleSource,
      titleCandidate: titleCandidate,
      language: language,
      languageGuessed: !metadata.language && !!language,
      author: String(metadata.author || ""),
      subject: String(metadata.subject || ""),
      confirmed: false,
    };

    ws.fields.forEach(function (field) {
      ws.names[field.name] = {
        value: field.hasCustomTooltip ? field.tooltip : "",
        source: field.hasCustomTooltip ? "existing" : "",
        confirmed: false,
      };
    });

    ws.tabOrder = currentFieldOrder();

    headings.forEach(function (candidate) {
      ws.headings[candidate.candidateId] = {
        status: "pending",
        tag: candidate.suggestedTag || "H2",
        source: "layout",
      };
    });

    (inspection.images || []).forEach(function (image) {
      ws.images[image.assetId] = {
        decision: image.altText ? "figure" : "",
        altText: String(image.altText || ""),
        source: image.altText ? "existing" : "",
        verified: false,
        confirmed: false,
      };
    });

    refreshLinkDrafts();
    resetBlockOrder();
  }

  function currentFieldOrder() {
    const known = new Set(ws.fieldsByName.keys());
    const order = [];
    ((ws.inspection && ws.inspection.field_order) || []).forEach(
      function (name) {
        if (known.has(name) && order.indexOf(name) === -1) order.push(name);
      },
    );
    visualFieldOrder(
      ws.fields.filter(function (field) {
        return field.box;
      }),
    ).forEach(function (name) {
      if (order.indexOf(name) === -1) order.push(name);
    });
    return order;
  }

  function refreshLinkDrafts() {
    const editor = (ws.inspection && ws.inspection.structure_editor) || {};
    (editor.annotations || []).forEach(function (annotation) {
      const key = annotation.pageIndex + ":" + annotation.index;
      if (!ws.links[key]) {
        ws.links[key] = {
          contents: String(annotation.contents || ""),
          confirmed: false,
        };
      }
    });
    (editor.tables || []).forEach(function (table) {
      if (!ws.tables[table.path]) {
        const firstRow = (table.rows || [])[0];
        ws.tables[table.path] = {
          headerRow: !!(
            firstRow &&
            firstRow.cells.length &&
            firstRow.cells.every(function (cell) {
              return cell.role === "TH";
            })
          ),
          confirmed: false,
        };
      }
    });
  }

  function blocksOnPage(pageIndex) {
    return ((ws.inspection && ws.inspection.content_blocks) || []).filter(
      function (block) {
        return Number(block.pageIndex) === pageIndex;
      },
    );
  }

  function resetBlockOrder() {
    const pages = new Set(
      ((ws.inspection && ws.inspection.content_blocks) || []).map(
        function (block) {
          return Number(block.pageIndex);
        },
      ),
    );
    pages.forEach(function (pageIndex) {
      if (ws.blockOrder[pageIndex]) return;
      ws.blockOrder[pageIndex] = blocksOnPage(pageIndex)
        .slice()
        .sort(function (a, b) {
          const rowDelta = a.box.y - b.box.y;
          if (Math.abs(rowDelta) > 0.006) return rowDelta;
          return a.box.x - b.box.x;
        })
        .map(function (block) {
          return block.blockId;
        });
    });
  }

  function blockById(blockId) {
    return ((ws.inspection && ws.inspection.content_blocks) || []).find(
      function (block) {
        return block.blockId === blockId;
      },
    );
  }

  // The role a block will get. A heading candidate's role lives in its
  // heading decision so the two tasks never disagree.
  function blockRole(block) {
    const own = ws.blockRoles[block.blockId];
    if (own === "Artifact") return "Artifact";
    if (block.headingCandidateId && ws.headings[block.headingCandidateId]) {
      const decision = ws.headings[block.headingCandidateId];
      return decision.status === "rejected" ? own || "P" : decision.tag;
    }
    return own || "P";
  }

  function setBlockRole(block, role) {
    if (block.headingCandidateId && ws.headings[block.headingCandidateId]) {
      const decision = ws.headings[block.headingCandidateId];
      if (/^H[1-6]$/.test(role)) {
        decision.status = "approved";
        decision.tag = role;
        decision.source = "person";
        delete ws.blockRoles[block.blockId];
        return;
      }
      decision.status = "rejected";
      decision.source = "person";
    }
    ws.blockRoles[block.blockId] = role;
  }

  // ---------------------------------------------------------------------
  // Derived task state
  // ---------------------------------------------------------------------

  function report() {
    return (ws.inspection && ws.inspection.report) || {};
  }

  function readback() {
    return (ws.inspection && ws.inspection.readback) || {};
  }

  function issueById(issueId) {
    return (report().issues || []).find(function (issue) {
      return issue.id === issueId;
    });
  }

  function readbackFindings(taskId) {
    return (readback().findings || []).filter(function (finding) {
      return READBACK_CATEGORY_TASK[finding.category] === taskId;
    });
  }

  function machineFails(taskId) {
    const reportFails = (TASK_CHECKS[taskId] || []).filter(function (id) {
      const issue = issueById(id);
      return issue && issue.status === "fail";
    }).length;
    const readbackFails = readbackFindings(taskId).filter(function (finding) {
      return finding.severity === "fail";
    }).length;
    return reportFails + readbackFails;
  }

  function duplicateAnnouncedNames() {
    const counts = new Map();
    ws.fields.forEach(function (field) {
      const value = currentName(field.name).trim().toLowerCase();
      if (!value) return;
      counts.set(value, (counts.get(value) || 0) + 1);
    });
    return counts;
  }

  function currentName(fieldName) {
    const decision = ws.names[fieldName];
    return decision ? String(decision.value || "") : "";
  }

  function fieldNeedsAttention(field, duplicates) {
    const decision = ws.names[field.name] || {};
    if (decision.confirmed) return false;
    // A drafted or edited name still needs a person to confirm it.
    if (decision.touched) return true;
    const value = String(decision.value || "").trim();
    if (!value || field.label.problem) return true;
    return (duplicates.get(value.toLowerCase()) || 0) > 1;
  }

  // The naming task walks fields the way the page reads, not in whatever
  // order the form's author created them.
  function fieldsInPageOrder() {
    const placed = ws.fields.filter(function (field) {
      return field.box;
    });
    const unplaced = ws.fields.filter(function (field) {
      return !field.box;
    });
    return visualFieldOrder(placed)
      .map(function (name) {
        return ws.fieldsByName.get(name);
      })
      .concat(unplaced);
  }

  function attentionFields() {
    const duplicates = duplicateAnnouncedNames();
    return fieldsInPageOrder().filter(function (field) {
      return field && fieldNeedsAttention(field, duplicates);
    });
  }

  function fieldsInTask() {
    if (ws.showAllFields) return fieldsInPageOrder();
    const duplicates = duplicateAnnouncedNames();
    return fieldsInPageOrder().filter(function (field) {
      const decision = ws.names[field.name] || {};
      return (
        decision.touched ||
        decision.confirmed ||
        fieldNeedsAttention(field, duplicates)
      );
    });
  }

  function pagesWithFields() {
    return Array.from(
      new Set(
        ws.fields
          .filter(function (field) {
            return field.box;
          })
          .map(function (field) {
            return field.pageIndex;
          }),
      ),
    ).sort(function (a, b) {
      return a - b;
    });
  }

  function pagesWithText() {
    return Object.keys(ws.blockOrder)
      .map(Number)
      .sort(function (a, b) {
        return a - b;
      });
  }

  function hasTagTree() {
    return !!(ws.inspection && (ws.inspection.tag_structure || {}).present);
  }

  function treeLocked() {
    return ws.preexistingTree && ws.treeDecision !== "replace";
  }

  function headingCandidates() {
    return (ws.inspection && ws.inspection.heading_candidates) || [];
  }

  function imageList() {
    return (ws.inspection && ws.inspection.images) || [];
  }

  function linkList() {
    const editor = (ws.inspection && ws.inspection.structure_editor) || {};
    return editor.annotations || [];
  }

  function tableList() {
    const editor = (ws.inspection && ws.inspection.structure_editor) || {};
    return editor.tables || [];
  }

  function fontIssues() {
    return (report().fonts || []).filter(function (font) {
      return !font.embedded || !font.unicodeCoverageComplete;
    });
  }

  function textFindings() {
    return readbackFindings("text").concat(
      (readback().findings || []).filter(function (finding) {
        return /^readback-(image|vector)-only-/.test(finding.id);
      }),
    );
  }

  // Every task answers the same four questions for the overview: how many
  // items need a person, what the machine says, a one-line summary, and
  // whether there is anything to review at all.
  function taskSummary(taskId) {
    const fails = machineFails(taskId);
    const machine = fails ? plural(fails, "fail") : "pass";
    if (taskId === "document") {
      const needs = ws.meta && !ws.meta.confirmed ? 1 : 0;
      const parts = [];
      if (ws.meta && !ws.meta.title) parts.push("The title is empty.");
      else if (ws.meta && ws.meta.titleSource)
        parts.push(
          "We suggest “" +
            ws.meta.title +
            "” from " +
            ws.meta.titleSource +
            ".",
        );
      if (ws.meta && ws.meta.language)
        parts.push(
          "Language " +
            (ws.meta.languageGuessed ? "looks like " : "is ") +
            languageLabel(ws.meta.language) +
            ".",
        );
      else parts.push("No language is declared.");
      return {
        count: needs,
        machine: machine,
        applicable: true,
        summary: parts.join(" "),
        action: "Confirm title & language",
      };
    }
    if (taskId === "fields") {
      if (!ws.fields.length)
        return notApplicable(machine, "This PDF has no form fields.");
      const attention = attentionFields();
      const problemCounts = {};
      attention.forEach(function (field) {
        const problem = field.label.problem;
        if (problem) problemCounts[problem] = (problemCounts[problem] || 0) + 1;
      });
      const duplicateGroups = readbackFindings("fields").length;
      let summary = attention.length
        ? plural(attention.length, "field") +
          " " +
          (attention.length === 1 ? "doesn’t" : "don’t") +
          " have a name a person confirmed."
        : "Every field has a confirmed name.";
      const unnamed =
        (problemCounts.missing || 0) +
        (problemCounts.placeholder || 0) +
        (problemCounts.derived || 0);
      if (unnamed)
        summary +=
          " " +
          plural(unnamed, "field") +
          " would be announced by an internal name, a placeholder or nothing at all.";
      if (problemCounts.instruction)
        summary +=
          " " +
          plural(problemCounts.instruction, "name") +
          " " +
          (problemCounts.instruction === 1
            ? "is an instruction"
            : "are instructions") +
          " (“Type …”) instead of the information.";
      if (problemCounts["option-missing"])
        summary +=
          " " +
          plural(
            problemCounts["option-missing"],
            "answer box",
            "answer boxes",
          ) +
          " don’t say which answer they are.";
      if (attention.length) summary += " We suggest a name for each.";
      if (duplicateGroups)
        summary +=
          " " +
          plural(duplicateGroups, "group") +
          " of fields announce the same name.";
      return {
        count: attention.length,
        machine: machine,
        applicable: true,
        summary: summary,
        action: attention.length
          ? "Review " + plural(attention.length, "name")
          : "Look over names",
      };
    }
    if (taskId === "tab") {
      const pages = pagesWithFields();
      if (!pages.length)
        return notApplicable(machine, "There are no fields to Tab between.");
      const jumps = countOrderJumps(ws.tabOrder, ws.fieldsByName);
      const unconfirmed = pages.filter(function (page) {
        return !ws.tabConfirmedPages.has(page);
      }).length;
      return {
        count: unconfirmed,
        machine: machine,
        applicable: true,
        summary:
          (jumps
            ? "Tab jumps back up the page " + plural(jumps, "time") + ". "
            : "Tab order follows the page from the top. ") +
          plural(pages.length, "page") +
          " with fields to walk through.",
        action: "Walk through tab order",
      };
    }
    if (taskId === "order") {
      const pages = pagesWithText();
      const findings = readbackFindings("order");
      const unconfirmed = pages.filter(function (page) {
        return !ws.orderConfirmedPages.has(page);
      }).length;
      return {
        count: unconfirmed,
        machine:
          findings.length || machineFails("order") ? machine : "not checkable",
        applicable: pages.length > 0,
        summary: findings.length
          ? findings
              .slice(0, 2)
              .map(function (finding) {
                return finding.title + ".";
              })
              .join(" ")
          : "No order problems were detected, but only a person can tell whether the spoken order makes sense.",
        action: "Compare spoken order",
      };
    }
    if (taskId === "headings") {
      const pending = headingCandidates().filter(function (candidate) {
        const decision = ws.headings[candidate.candidateId];
        return !decision || decision.status === "pending";
      }).length;
      const treeNeedsDecision = ws.preexistingTree && !ws.treeDecision ? 1 : 0;
      const parts = [];
      if (headingCandidates().length)
        parts.push(
          plural(headingCandidates().length, "line") +
            " look like headings" +
            (pending ? "; " + pending + " to confirm." : "."),
        );
      else parts.push("No lines stand out as headings.");
      if (treeNeedsDecision)
        parts.push(
          "This PDF already has tags from an earlier edit — decide whether to keep them.",
        );
      return {
        count: pending + treeNeedsDecision,
        machine: machine,
        applicable: true,
        summary: parts.join(" "),
        action: "Review headings",
      };
    }
    if (taskId === "images") {
      const images = imageList();
      if (!images.length)
        return notApplicable(machine, "This PDF has no images.");
      const open = images.filter(function (image) {
        return !(ws.images[image.assetId] || {}).confirmed;
      }).length;
      const undescribed = images.filter(function (image) {
        return !(ws.images[image.assetId] || {}).altText;
      }).length;
      return {
        count: open,
        machine: machine,
        applicable: true,
        summary:
          (undescribed
            ? plural(undescribed, "image") +
              " " +
              (undescribed === 1 ? "has" : "have") +
              " no description. "
            : "") +
          "Decide which carry meaning; we can draft descriptions for those.",
        action: "Review images",
      };
    }
    if (taskId === "links") {
      const links = linkList();
      const tables = tableList();
      if (!links.length && !tables.length)
        return notApplicable(machine, "There are no links or tables to check.");
      const openLinks = links.filter(function (link) {
        const decision = ws.links[link.pageIndex + ":" + link.index] || {};
        return !decision.confirmed;
      }).length;
      const openTables = tables.filter(function (table) {
        return !(ws.tables[table.path] || {}).confirmed;
      }).length;
      const parts = [];
      if (links.length)
        parts.push(
          plural(links.length, "link or note", "links or notes") +
            " to describe.",
        );
      if (tables.length)
        parts.push(
          plural(tables.length, "table") + " whose headers need a person.",
        );
      return {
        count: openLinks + openTables,
        machine: machine,
        applicable: true,
        summary: parts.join(" "),
        action: "Review tables & links",
      };
    }
    // text
    const fonts = fontIssues();
    const findings = textFindings();
    const count = fonts.length + findings.length;
    let summary = "";
    if (!count)
      summary =
        "Every font is embedded and every glyph maps to text. Spot-check a few lines to confirm.";
    else {
      if (fonts.length)
        summary +=
          plural(fonts.length, "font") +
          " still need" +
          (fonts.length === 1 ? "s" : "") +
          " a decision. ";
      if (findings.length)
        summary +=
          plural(findings.length, "passage") +
          " would be spoken differently from how " +
          (findings.length === 1 ? "it reads" : "they read") +
          ".";
    }
    return {
      count: count,
      machine: machine,
      applicable: true,
      summary: summary.trim(),
      action: count ? "Resolve text problems" : "Spot-check text",
    };
  }

  function notApplicable(machine, summary) {
    return {
      count: 0,
      machine: machine,
      applicable: false,
      summary: summary,
      action: "Nothing to review",
    };
  }

  function taskDone(taskId) {
    return !!ws.reviewed[taskId] || !taskSummary(taskId).applicable;
  }

  // Tasks with nothing to review are left out of the count rather than
  // counted as reviewed: nobody looked at them.
  function applicableTasks() {
    return TASKS.filter(function (task) {
      return taskSummary(task.id).applicable;
    });
  }

  function reviewedCount() {
    return applicableTasks().filter(function (task) {
      return !!ws.reviewed[task.id];
    }).length;
  }

  function reviewProgress() {
    const applicable = applicableTasks().length;
    const skipped = TASKS.length - applicable;
    return (
      reviewedCount() +
      " of " +
      plural(applicable, "task") +
      " reviewed" +
      (skipped ? " · " + skipped + " with nothing to review" : "")
    );
  }

  function itemsNeedingPerson() {
    let total = 0;
    let tasks = 0;
    TASKS.forEach(function (task) {
      const summary = taskSummary(task.id);
      if (summary.count && !ws.reviewed[task.id]) {
        total += summary.count;
        tasks += 1;
      }
    });
    return { total: total, tasks: tasks };
  }

  // ---------------------------------------------------------------------
  // Applying decisions
  // ---------------------------------------------------------------------

  function setBusy(message) {
    ws.busy = !!message;
    if (els.busy) {
      els.busy.hidden = !message;
      els.busyText.textContent = message || "";
    }
    root.setAttribute("aria-busy", message ? "true" : "false");
    if (message) announce(message);
  }

  function announce(message) {
    if (!els.status) return;
    els.status.textContent = "";
    window.setTimeout(function () {
      els.status.textContent = message;
    }, 30);
  }

  function saveDecisions() {
    // Pair the PDF with its last committed review state, not the confirmed
    // drafts about to be written. Otherwise Undo would queue those drafts again.
    ws.savedDecisions = snapshotDecisions(ws);
  }

  async function applyChange(label, taskId, steps) {
    if (ws.busy) return false;
    const before = ws.working;
    setBusy(label + "…");
    try {
      let current = before;
      for (const step of steps) {
        const next = await step(current);
        if (next) current = next;
      }
      await loadWorkingCopy(current);
      refreshLinkDrafts();
      resetBlockOrder();
      ws.history.push({
        label: label,
        bytes: before,
        decisions: ws.savedDecisions,
        taskId: taskId,
        at: new Date(),
      });
      saveDecisions();
      setBusy("");
      announce(label + " — done.");
      return true;
    } catch (error) {
      setBusy("");
      host.showError(label + " failed: " + (error.message || error));
      return false;
    } finally {
      render();
    }
  }

  async function undo() {
    if (ws.busy) return;
    const entry = ws.history.pop();
    if (!entry) return;
    stopSpeaking();
    setBusy("Undoing “" + entry.label + "”…");
    try {
      await loadWorkingCopy(entry.bytes);
      Object.assign(ws, structuredClone(entry.decisions));
      ws.fontReview = null;
      ws.fontSubs = null;
      ws.previews = null;
      ws.replay = { mode: "all", index: 0, playing: false, heardAll: false };
      saveDecisions();
      announce("Undid " + entry.label + ".");
    } catch (error) {
      ws.history.push(entry);
      host.showError("Undo failed: " + (error.message || error));
    } finally {
      setBusy("");
      render();
    }
  }

  function confirmedTooltips() {
    const tooltips = {};
    ws.fields.forEach(function (field) {
      const decision = ws.names[field.name];
      if (decision && decision.confirmed && String(decision.value).trim()) {
        tooltips[field.name] = String(decision.value).trim();
      }
    });
    return tooltips;
  }

  function headingDecisionPayload() {
    return Object.keys(ws.headings).map(function (candidateId) {
      const decision = ws.headings[candidateId];
      return {
        candidateId: candidateId,
        // A heading nobody has looked at keeps the layout's suggestion; the
        // draft already used it, and rejecting it silently would hide it.
        status: decision.status === "pending" ? "approved" : decision.status,
        tag: decision.tag,
      };
    });
  }

  function contentDecisionPayload() {
    const payload = [];
    Object.keys(ws.blockOrder).forEach(function (pageKey) {
      const pageIndex = Number(pageKey);
      const reviewed =
        ws.orderEditedPages.has(pageIndex) ||
        ws.orderConfirmedPages.has(pageIndex);
      ws.blockOrder[pageKey].forEach(function (blockId, position) {
        const block = blockById(blockId);
        if (!block) return;
        const role = blockRole(block);
        const fromHeading =
          block.headingCandidateId &&
          ws.headings[block.headingCandidateId] &&
          role !== "Artifact" &&
          !ws.blockRoles[block.blockId];
        payload.push({
          blockId: block.blockId,
          pageIndex: pageIndex,
          text: block.text,
          occurrence: Number(block.occurrence || 0),
          role: fromHeading ? "P" : role,
          order: position,
          roleReviewed: !fromHeading && !!ws.blockRoles[block.blockId],
          orderReviewed: reviewed,
        });
      });
    });
    return payload;
  }

  function imageDecisionPayload() {
    return Object.keys(ws.images)
      .filter(function (assetId) {
        const decision = ws.images[assetId];
        return decision.confirmed && decision.decision;
      })
      .map(function (assetId) {
        const decision = ws.images[assetId];
        return {
          assetId: assetId,
          decision: decision.decision,
          altText: decision.decision === "figure" ? decision.altText : "",
        };
      });
  }

  function textFixPayload() {
    return Object.keys(ws.textFixes)
      .filter(function (key) {
        return ws.textFixes[key].confirmed;
      })
      .map(function (key) {
        return {
          contentId: key,
          actualText: ws.textFixes[key].actualText,
          apply: true,
        };
      });
  }

  // Decisions that live on tag-tree elements must be written again after
  // the tree is rebuilt; decisions on widgets and fonts survive on their own.
  function afterStructureSteps() {
    return [
      async function (bytes) {
        const fields = {
          field_tooltips: confirmedTooltips(),
          set_structure_tab_order: "true",
          repair_declaration: "true",
          display_doc_title: "false",
        };
        if (ws.tabEdited || ws.tabConfirmedPages.size)
          fields.field_order = ws.tabOrder;
        return (await remediate("metadata", fields, bytes)).bytes;
      },
      async function (bytes) {
        const fixes = textFixPayload();
        if (!fixes.length) return bytes;
        return (await remediate("readback_text", { decisions: fixes }, bytes))
          .bytes;
      },
    ];
  }

  function rebuildSteps() {
    return [
      async function (bytes) {
        return (
          await remediate(
            "draft_structure",
            {
              overwrite: hasTagTree() ? "true" : "false",
              mark_as_tagged: "false",
              heading_decisions: headingDecisionPayload(),
              content_decisions: contentDecisionPayload(),
              image_decisions: imageDecisionPayload(),
            },
            bytes,
          )
        ).bytes;
      },
    ].concat(afterStructureSteps());
  }

  async function rebuildStructure(label, taskId) {
    if (treeLocked()) {
      host.showError(
        "This PDF keeps its earlier tags. Choose “Replace with our draft” under Headings & tags to edit structure here.",
      );
      return false;
    }
    const applied = await applyChange(label, taskId, rebuildSteps());
    // Once replaced, the earlier tags are gone and this draft is ours to edit.
    if (applied && ws.treeDecision === "replace") {
      ws.preexistingTree = false;
      saveDecisions();
    }
    return applied;
  }

  function markReviewed(taskId) {
    ws.reviewed[taskId] = true;
    announce(TASK_BY_ID.get(taskId).category + " reviewed.");
  }

  // ---------------------------------------------------------------------
  // Drafting
  // ---------------------------------------------------------------------

  // The server's suggestion: the current name cleaned up, a question and
  // its answer for a box in a row of answers, or the printed label nearby.
  function nearbyFor(field) {
    if (!field || !field.label || !field.label.suggested) return null;
    const label = field.label;
    return {
      text: label.suggested,
      // The server calls a tidied version of the form's own name "existing";
      // here that would read as the untouched original.
      source: label.source === "existing" ? "cleaned" : label.source,
      required: !!label.required,
      boxes: [label.labelBox, label.optionBox, label.qualifierBox].filter(
        Boolean,
      ),
    };
  }

  function draftNamesFromSuggestions(fields) {
    let drafted = 0;
    fields.forEach(function (field) {
      const decision = ws.names[field.name];
      if (!decision || decision.confirmed) return;
      const nearby = nearbyFor(field);
      if (!nearby) return;
      decision.value = nearby.text;
      decision.source = nearby.source;
      decision.touched = true;
      if (nearby.required) field.required = true;
      drafted += 1;
    });
    return drafted;
  }

  async function draftNamesWithAi(fields) {
    const payload = fields.map(function (field) {
      const nearby = nearbyFor(field);
      const group = field.label.group || {};
      return {
        name: field.name,
        type: field.type,
        tooltip: currentName(field.name) || field.tooltip,
        nearby_text: nearby ? [nearby.text] : [],
        question: group.question || "",
        option: group.option || "",
      };
    });
    const data = await postJson("/pdf-labeler/api/accessibility-ai-tooltips", {
      fields: payload,
    });
    let drafted = 0;
    Object.keys(data.tooltips || {}).forEach(function (name) {
      const decision = ws.names[name];
      if (!decision || decision.confirmed) return;
      decision.value = data.tooltips[name];
      decision.source = "ai";
      decision.touched = true;
      drafted += 1;
    });
    return drafted;
  }

  async function draftHeadingsWithAi() {
    const data = await postJson("/pdf-labeler/api/accessibility-ai-headings", {
      candidates: headingCandidates(),
    });
    let drafted = 0;
    (data.decisions || []).forEach(function (decision) {
      const current = ws.headings[decision.candidateId];
      if (!current || current.source === "person") return;
      current.status = "pending";
      current.tag = decision.isHeading ? decision.suggestedTag || "H2" : "P";
      current.aiReason = String(decision.reason || "");
      current.source = "ai";
      drafted += 1;
    });
    return drafted;
  }

  async function loadPreviews() {
    if (ws.previews || !imageList().length) return;
    try {
      const data = await postPdf(
        "/pdf-labeler/api/accessibility-image-previews",
        ws.working,
        {},
      );
      ws.previews = data.previews || {};
    } catch (_error) {
      ws.previews = {};
    }
  }

  async function draftImagesWithAi(assetIds) {
    const data = await postPdf(
      "/pdf-labeler/api/accessibility-ai-image-alt",
      ws.working,
      { asset_ids: assetIds, model: host.model() },
    );
    let drafted = 0;
    (data.descriptions || []).forEach(function (item) {
      const decision = ws.images[item.assetId];
      if (!decision || decision.confirmed || item.error) return;
      decision.decision = item.decorative ? "artifact" : "figure";
      decision.altText = item.decorative ? "" : String(item.altText || "");
      decision.source = "ai";
      decision.verified = false;
      drafted += 1;
    });
    return drafted;
  }

  async function autoDraftAll() {
    if (ws.busy) return;
    setBusy("Drafting what we can…");
    const notes = [];
    try {
      const fields = attentionFields();
      const nearby = draftNamesFromSuggestions(fields);
      if (nearby) notes.push(plural(nearby, "suggested field name"));
      if (ws.draftWithAi && host.aiEnabled()) {
        const stillVague = attentionFields().filter(function (field) {
          return !(ws.names[field.name] || {}).source;
        });
        if (stillVague.length) {
          setBusy(
            "Asking the AI about " + plural(stillVague.length, "field") + "…",
          );
          const ai = await draftNamesWithAi(stillVague);
          if (ai) notes.push(plural(ai, "field name") + " from AI");
        }
        if (headingCandidates().length) {
          setBusy("Asking the AI about heading levels…");
          const headings = await draftHeadingsWithAi();
          if (headings)
            notes.push(plural(headings, "heading level") + " from AI");
        }
        const undecided = imageList()
          .filter(function (image) {
            return !(ws.images[image.assetId] || {}).decision;
          })
          .map(function (image) {
            return image.assetId;
          });
        if (undecided.length) {
          setBusy(
            "Asking the AI what " +
              plural(undecided.length, "image") +
              " show…",
          );
          const images = await draftImagesWithAi(undecided);
          if (images)
            notes.push(plural(images, "image description") + " from AI");
        }
      }
      host.showSuccess(
        notes.length
          ? "Drafted " +
              notes.join(", ") +
              ". Drafts don’t count until you confirm them."
          : "Nothing more could be drafted automatically.",
      );
    } catch (error) {
      host.showError("Drafting stopped: " + (error.message || error));
    } finally {
      setBusy("");
      render();
    }
  }

  // ---------------------------------------------------------------------
  // Speech: a simulation, always labelled as one
  // ---------------------------------------------------------------------

  let speechQueue = [];

  function speechAvailable() {
    return typeof window.speechSynthesis !== "undefined";
  }

  function stopSpeaking() {
    speechQueue = [];
    if (speechAvailable()) window.speechSynthesis.cancel();
    if (ws && ws.replay) ws.replay.playing = false;
  }

  function speakLines(lines, onLine, onDone) {
    stopSpeaking();
    if (!speechAvailable()) {
      host.showError("This browser cannot speak. Read the list instead.");
      return;
    }
    speechQueue = lines.slice();
    const lang = (ws.meta && ws.meta.language) || "en-US";
    let position = 0;
    function next() {
      if (!speechQueue.length) {
        if (onDone) onDone();
        return;
      }
      const line = speechQueue.shift();
      const utterance = new window.SpeechSynthesisUtterance(line);
      utterance.lang = lang;
      utterance.rate = 1.05;
      const current = position;
      position += 1;
      utterance.onstart = function () {
        if (onLine) onLine(current);
      };
      utterance.onend = next;
      utterance.onerror = function () {
        speechQueue = [];
        if (onDone) onDone();
      };
      window.speechSynthesis.speak(utterance);
    }
    next();
  }

  // ---------------------------------------------------------------------
  // Rendering: shell
  // ---------------------------------------------------------------------

  function render() {
    if (!ws.open) return;
    renderVersion += 1;
    const pageCount = ws.pageViews.length;
    els.filename.textContent = ws.filename;
    els.fileMeta.textContent = pageCount
      ? plural(pageCount, "page") + " · Working copy · Original file untouched"
      : "Inspecting…";
    els.undo.disabled = !ws.history.length || ws.busy;
    renderHistory();
    renderRail();
    if (!ws.inspection) {
      els.main.innerHTML = "";
      return;
    }
    if (ws.step === "find") renderFind();
    else if (ws.step === "review") renderReview();
    else renderTest();
    previewObserver.disconnect();
    previewObserver.observe(els.main);
    const well = els.main.querySelector(".aw-page-well");
    if (well) previewObserver.observe(well);
    syncPreviewLayout();
  }

  function renderHistory() {
    if (!els.history) return;
    if (!ws.history.length) {
      els.history.innerHTML =
        '<p class="aw-muted aw-small">No changes yet. Each repair you apply is listed here and can be undone.</p>';
      return;
    }
    els.history.innerHTML =
      '<ol class="aw-history-list">' +
      ws.history
        .map(function (entry) {
          return (
            "<li><span>" +
            esc(entry.label) +
            '</span><span class="aw-mono aw-muted">' +
            esc(
              entry.at.toLocaleTimeString([], {
                hour: "numeric",
                minute: "2-digit",
              }),
            ) +
            "</span></li>"
          );
        })
        .reverse()
        .join("") +
      "</ol>";
  }

  function countBadge(count, done, notApplicable) {
    if (notApplicable)
      return '<span class="aw-badge aw-badge-empty" aria-hidden="true">–</span>';
    if (done)
      return '<span class="aw-badge aw-badge-done" aria-hidden="true"><svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.2 3L13 4.5"/></svg></span>';
    if (!count)
      return '<span class="aw-badge aw-badge-empty" aria-hidden="true"></span>';
    return (
      '<span class="aw-badge" aria-hidden="true">' + esc(count) + "</span>"
    );
  }

  function renderRail() {
    if (!ws.inspection) {
      els.rail.innerHTML = "";
      return;
    }
    const pageCount = ws.pageViews.length;
    const step = function (id, number, title, subtitle) {
      const current = ws.step === id;
      return (
        '<button type="button" class="aw-step' +
        (current ? " is-current" : "") +
        '" data-aw="step" data-step="' +
        id +
        '"' +
        (current ? ' aria-current="step"' : "") +
        '><span class="aw-step-number">' +
        number +
        '</span><span class="aw-step-text"><span class="aw-step-title">' +
        esc(title) +
        '</span><span class="aw-step-subtitle">' +
        esc(subtitle) +
        "</span></span></button>"
      );
    };
    const tasks = TASKS.map(function (task) {
      const summary = taskSummary(task.id);
      const done = taskDone(task.id);
      const current = ws.step === "review" && ws.task === task.id;
      const state = done
        ? summary.applicable
          ? "reviewed"
          : "nothing to review"
        : plural(summary.count, "item") + " to review";
      return (
        '<li><button type="button" class="aw-task-link' +
        (current ? " is-current" : "") +
        '" data-aw="task" data-task="' +
        task.id +
        '"' +
        (current ? ' aria-current="true"' : "") +
        ">" +
        countBadge(summary.count, done, !summary.applicable) +
        '<span class="aw-task-link-text"><span class="aw-task-question">' +
        esc(task.question) +
        '</span><span class="aw-task-category">' +
        esc(task.category) +
        '</span></span><span class="aw-sr">, ' +
        esc(state) +
        "</span></button></li>"
      );
    }).join("");
    els.rail.innerHTML =
      '<nav aria-label="Workshop steps"><ol class="aw-steps">' +
      "<li>" +
      step(
        "find",
        "1",
        "Find problems",
        "Inspected " +
          plural(pageCount, "page") +
          " · " +
          plural(ws.receipts.length, "repair") +
          " applied",
      ) +
      "</li><li>" +
      step("review", "2", "Review the experience", reviewProgress()) +
      '<ul class="aw-task-links">' +
      tasks +
      "</ul></li><li>" +
      step("test", "3", "Test & export", "Replay, export, validate") +
      "</li></ol></nav>";
  }

  function machineChip(machine) {
    const tone =
      machine === "pass"
        ? "pass"
        : machine === "not checkable"
          ? "neutral"
          : "fail";
    return (
      '<span class="aw-chip aw-chip-' + tone + '">' + esc(machine) + "</span>"
    );
  }

  function focusMainHeading() {
    window.setTimeout(function () {
      const heading = els.main.querySelector("h2");
      if (heading) {
        heading.setAttribute("tabindex", "-1");
        heading.focus({ preventScroll: false });
      }
    }, 0);
  }

  // ---------------------------------------------------------------------
  // Step 1: Find problems
  // ---------------------------------------------------------------------

  function evidencePanel() {
    const summary = report().summary || {};
    const external = EXTERNAL_TESTS.filter(function (test) {
      return ws.external[test.id] && ws.external[test.id].result;
    }).length;
    return (
      '<section class="aw-card aw-evidence" aria-labelledby="aw-evidence-title">' +
      '<h3 id="aw-evidence-title" class="aw-card-title">Evidence so far</h3>' +
      '<dl class="aw-evidence-list">' +
      '<div><dt class="aw-eyebrow">Machine checks</dt><dd><strong>' +
      esc(Number(summary.passed_checks || 0)) +
      " pass · " +
      esc(Number(summary.failed_checks || 0)) +
      ' fail</strong><span class="aw-muted aw-small">Every fail belongs to a task.</span></dd></div>' +
      '<div><dt class="aw-eyebrow">Human review</dt><dd><strong>' +
      esc(reviewProgress()) +
      '</strong><span class="aw-muted aw-small">Drafts don’t count until you confirm them.</span></dd></div>' +
      '<div><dt class="aw-eyebrow">External testing</dt><dd><strong>' +
      (external
        ? external + " of " + EXTERNAL_TESTS.length + " recorded"
        : "Not started") +
      '</strong><span class="aw-muted aw-small">Validator, keyboard and screen reader tests happen after export.</span></dd></div>' +
      "</dl></section>"
    );
  }

  function receiptsPanel() {
    const details = ws.receiptDetails
      .map(function (entry) {
        return (
          "<li><strong>" +
          esc(entry.label) +
          "</strong>" +
          (entry.detail
            ? '<pre class="aw-pre">' +
              esc(JSON.stringify(entry.detail, null, 2).slice(0, 4000)) +
              "</pre>"
            : "") +
          "</li>"
        );
      })
      .join("");
    const issues = (report().issues || [])
      .map(function (issue) {
        return (
          '<li><span class="aw-mono">' +
          esc(issue.status) +
          "</span> " +
          esc(issue.title) +
          (issue.rule
            ? ' <span class="aw-muted">(' + esc(issue.rule) + ")</span>"
            : "") +
          "</li>"
        );
      })
      .join("");
    return (
      '<section class="aw-card" aria-labelledby="aw-done-title">' +
      '<h3 id="aw-done-title" class="aw-card-title">Done for you · ' +
      plural(ws.receipts.length, "repair") +
      "</h3>" +
      (ws.receipts.length
        ? '<ul class="aw-receipts">' +
          ws.receipts
            .map(function (receipt) {
              return "<li>" + esc(receipt) + "</li>";
            })
            .join("") +
          integrityLine() +
          "</ul>"
        : '<p class="aw-muted aw-small">No automatic repairs were needed.</p>') +
      '<details class="aw-details"><summary>See technical details</summary>' +
      (ws.preexistingTree
        ? '<p class="aw-small">This PDF came with its own tags, so we did not draft new ones.</p>'
        : "") +
      '<ul class="aw-tech">' +
      details +
      '</ul><h4 class="aw-eyebrow">Machine checks</h4><ul class="aw-tech">' +
      issues +
      "</ul></details></section>"
    );
  }

  function renderFind() {
    const needs = itemsNeedingPerson();
    const cards = TASKS.map(function (task) {
      const summary = taskSummary(task.id);
      const done = taskDone(task.id);
      return (
        '<li class="aw-task-card' +
        (done ? " is-done" : "") +
        '"><div class="aw-task-card-head"><div><p class="aw-eyebrow">' +
        esc(task.category) +
        '</p><h3 class="aw-task-card-title">' +
        esc(task.question) +
        "</h3></div>" +
        countBadge(summary.count, done, !summary.applicable) +
        '</div><p class="aw-task-card-summary">' +
        esc(summary.summary) +
        '</p><div class="aw-task-card-foot">' +
        (summary.applicable
          ? '<button type="button" class="aw-btn aw-btn-secondary" data-aw="task" data-task="' +
            task.id +
            '">' +
            esc(done ? "Review again" : summary.action) +
            "</button>"
          : '<span class="aw-muted aw-small">Nothing to review</span>') +
        machineChip(summary.machine) +
        "</div></li>"
      );
    }).join("");
    const aiOption = host.aiEnabled()
      ? '<label class="aw-check"><input type="checkbox" data-aw-input="draft-ai"' +
        (ws.draftWithAi ? " checked" : "") +
        "> <span>Use AI where nearby text isn’t enough (vague labels, heading levels, images)</span></label>"
      : '<p class="aw-muted aw-small">Sign in to also draft with AI.</p>';
    els.main.innerHTML =
      '<div class="aw-find"><div class="aw-find-main">' +
      '<p class="aw-eyebrow">Step 1 · Find problems</p>' +
      '<h2 class="aw-title">' +
      (needs.total
        ? esc(plural(needs.total, "item")) +
          " across " +
          esc(plural(needs.tasks, "task")) +
          " need a person"
        : "Nothing is waiting on a person") +
      "</h2>" +
      '<p class="aw-lead">We inspected every page and made the repairs that don’t involve judgment. What’s left is listed by the question it answers for a reader.</p>' +
      '<section class="aw-card aw-autodraft" aria-labelledby="aw-autodraft-title"><div>' +
      '<h3 id="aw-autodraft-title" class="aw-card-title">Draft everything we can, then review it</h3>' +
      '<p class="aw-small">Field names from the page and the form’s own hints, and heading levels from the layout. Drafts land in each task marked <span class="aw-tag-draft">Draft</span>. Nothing counts as reviewed until you confirm it.</p>' +
      aiOption +
      '</div><button type="button" class="aw-btn aw-btn-primary" data-aw="auto-draft">Draft what we can</button></section>' +
      '<ul class="aw-task-grid" aria-label="Tasks">' +
      cards +
      "</ul></div>" +
      '<aside class="aw-find-side" aria-label="Evidence and automatic repairs">' +
      evidencePanel() +
      receiptsPanel() +
      '<button type="button" class="aw-btn aw-btn-primary aw-btn-wide" data-aw="start-review">Start reviewing</button>' +
      "</aside></div>";
  }

  // ---------------------------------------------------------------------
  // Page viewer with an overlay of what the current task is about
  // ---------------------------------------------------------------------

  function previewNaturalWidth() {
    const view = ws.pageViews[ws.page] || [0, 0, 612, 792];
    return (view[2] - view[0]) * (96 / 72);
  }

  function panelBounds() {
    const body = els.main.querySelector(".aw-review-body");
    const width = body ? body.clientWidth : els.main.clientWidth;
    return { min: 240, max: Math.max(240, Math.min(600, width - 272)) };
  }

  function previewDivider() {
    return (
      '<div class="aw-divider" role="separator" tabindex="0" ' +
      'aria-label="Review panel width" aria-orientation="vertical" ' +
      'aria-controls="aw-review-panel" aria-valuemin="240" aria-valuemax="600" aria-valuenow="440" ' +
      'aria-describedby="aw-divider-help" title="Drag to resize the review panel">' +
      '<span id="aw-divider-help" class="aw-sr">Use Left and Right arrow keys to resize. ' +
      "Home makes the panel narrowest, End widest, and Enter resets its width.</span></div>"
    );
  }

  function syncPreviewLayout() {
    const bounds = panelBounds();
    const desired =
      ws.inspectorWidth === null
        ? Math.min(440, els.main.clientWidth * 0.4)
        : ws.inspectorWidth;
    const width = Math.round(
      Math.max(bounds.min, Math.min(bounds.max, desired)),
    );
    root.style.setProperty("--aw-inspector-width", width + "px");
    const divider = els.main.querySelector(".aw-divider");
    if (divider) {
      divider.setAttribute("aria-valuemin", String(bounds.min));
      divider.setAttribute("aria-valuemax", String(bounds.max));
      divider.setAttribute("aria-valuenow", String(width));
      divider.setAttribute("aria-valuetext", width + " pixels wide");
    }
    const page = els.main.querySelector("[data-aw-ref=page]");
    if (!page) return;
    page.style.width =
      ws.previewZoom === null
        ? "100%"
        : (previewNaturalWidth() * ws.previewZoom) / 100 + "px";
    const actualWidth = page.getBoundingClientRect().width;
    const zoom =
      ws.previewZoom === null
        ? Math.round((actualWidth / previewNaturalWidth()) * 100)
        : ws.previewZoom;
    const label = els.main.querySelector("[data-aw-ref=zoom]");
    if (label) label.textContent = zoom + "%";
    const out = els.main.querySelector("[data-aw=zoom-out]");
    const into = els.main.querySelector("[data-aw=zoom-in]");
    const fit = els.main.querySelector("[data-aw=zoom-fit]");
    if (out) out.disabled = zoom <= 25;
    if (into) into.disabled = zoom >= 300;
    if (fit) fit.setAttribute("aria-pressed", String(ws.previewZoom === null));
    if (actualWidth !== lastPreviewWidth) {
      lastPreviewWidth = actualWidth;
      window.clearTimeout(previewRenderTimer);
      previewRenderTimer = window.setTimeout(function () {
        if (ws.open)
          paintPage(previewContent.overlays, previewContent.overlaySvg);
      }, 120);
    }
  }

  function changePreviewZoom(delta) {
    const page = els.main.querySelector("[data-aw-ref=page]");
    const current =
      ws.previewZoom === null
        ? page
          ? (page.getBoundingClientRect().width / previewNaturalWidth()) * 100
          : 100
        : ws.previewZoom;
    ws.previewZoom = Math.max(
      25,
      Math.min(300, Math.round(current / 25) * 25 + delta),
    );
    syncPreviewLayout();
  }

  function pageViewer(options) {
    const pageCount = ws.pageViews.length;
    return (
      '<section class="aw-viewer" aria-label="Page preview"><div class="aw-viewer-bar">' +
      '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="page-prev"' +
      (ws.page <= 0 ? " disabled" : "") +
      ' aria-label="Previous page">‹</button><span class="aw-mono">Page ' +
      (ws.page + 1) +
      " of " +
      pageCount +
      '</span><button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="page-next"' +
      (ws.page >= pageCount - 1 ? " disabled" : "") +
      ' aria-label="Next page">›</button>' +
      '<div class="aw-zoom-controls" role="group" aria-label="Preview zoom">' +
      '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="zoom-out" aria-label="Zoom out">−</button>' +
      '<output class="aw-mono" data-aw-ref="zoom" aria-label="Preview zoom level">100%</output>' +
      '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="zoom-in" aria-label="Zoom in">+</button>' +
      '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="zoom-fit" aria-pressed="true">Fit width</button></div>' +
      (options && options.caption
        ? '<span class="aw-viewer-caption">' + esc(options.caption) + "</span>"
        : "") +
      '</div><div class="aw-page-well"><div class="aw-page" data-aw-ref="page"><div class="aw-page-canvas" data-aw-ref="canvas"></div><div class="aw-overlay" data-aw-ref="overlay" aria-hidden="true"></div></div></div></section>'
    );
  }

  async function paintPage(overlays, overlaySvg) {
    previewContent = { overlays: overlays || [], overlaySvg: overlaySvg || "" };
    const myPaintVersion = ++paintVersion;
    const container = els.main.querySelector("[data-aw-ref=canvas]");
    const overlay = els.main.querySelector("[data-aw-ref=overlay]");
    const pageEl = els.main.querySelector("[data-aw-ref=page]");
    if (!container || !ws.pdfDoc) return;
    const myVersion = renderVersion;
    const renderWidth = Math.max(
      PAGE_RENDER_WIDTH,
      Math.ceil(pageEl.getBoundingClientRect().width / 100) * 100,
    );
    const key = ws.version + ":" + ws.page + ":" + renderWidth;
    let canvas = pageCanvasCache.get(key);
    if (!canvas) {
      const page = await ws.pdfDoc.getPage(ws.page + 1);
      const baseViewport = page.getViewport({ scale: 1 });
      const scale =
        (renderWidth / baseViewport.width) * (window.devicePixelRatio || 1);
      const viewport = page.getViewport({ scale: scale });
      canvas = document.createElement("canvas");
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", "Page " + (ws.page + 1) + " as drawn");
      await page.render({
        canvasContext: canvas.getContext("2d"),
        viewport: viewport,
      }).promise;
      pageCanvasCache.set(key, canvas);
      if (pageCanvasCache.size > 8)
        pageCanvasCache.delete(pageCanvasCache.keys().next().value);
    }
    if (myVersion !== renderVersion || myPaintVersion !== paintVersion) return;
    container.innerHTML = "";
    container.appendChild(canvas);
    const view = ws.pageViews[ws.page] || [0, 0, 612, 792];
    pageEl.style.aspectRatio =
      String(view[2] - view[0]) + " / " + String(view[3] - view[1]);
    overlay.innerHTML =
      (overlaySvg || "") +
      (overlays || [])
        .map(function (item) {
          if (!item.box) return "";
          const style =
            "left:" +
            (item.box.x * 100).toFixed(3) +
            "%;top:" +
            (item.box.y * 100).toFixed(3) +
            "%;width:" +
            (item.box.width * 100).toFixed(3) +
            "%;height:" +
            (item.box.height * 100).toFixed(3) +
            "%";
          return (
            '<div class="aw-mark aw-mark-' +
            esc(item.tone || "info") +
            (item.target ? " is-clickable" : "") +
            '" style="' +
            style +
            '"' +
            (item.target ? ' data-aw-mark="' + esc(item.target) + '"' : "") +
            ">" +
            (item.label
              ? '<span class="aw-mark-label">' + esc(item.label) + "</span>"
              : "") +
            "</div>"
          );
        })
        .join("");
    if (ws.scrollToMark) {
      const target = overlay.querySelector(".aw-mark-current, .aw-mark-focus");
      if (target)
        target.scrollIntoView({ block: "center", behavior: "smooth" });
      ws.scrollToMark = false;
    }
  }

  function pointBox(x, y) {
    const view = ws.pageViews[ws.page] || [0, 0, 612, 792];
    const width = view[2] - view[0] || 1;
    const height = view[3] - view[1] || 1;
    return {
      x: (x - view[0]) / width - 0.006,
      y: (view[3] - y) / height - 0.012,
      width: 0.012,
      height: 0.016,
    };
  }

  // ---------------------------------------------------------------------
  // Step 2: Review the experience
  // ---------------------------------------------------------------------

  function renderReview() {
    const task = TASK_BY_ID.get(ws.task) || TASKS[0];
    const summary = taskSummary(task.id);
    const view = summary.applicable
      ? taskView(task.id)
      : { lead: summary.summary, overlays: [], html: "" };
    els.main.innerHTML =
      '<div class="aw-review"><header class="aw-review-head"><p class="aw-eyebrow">Step 2 · Review the experience · ' +
      esc(task.category) +
      '</p><h2 class="aw-title">' +
      esc(task.question) +
      '</h2><p class="aw-lead">' +
      esc(view.lead) +
      "</p></header>" +
      '<div class="aw-review-body' +
      (view.noViewer ? " aw-no-preview" : "") +
      '">' +
      (view.noViewer
        ? ""
        : pageViewer({ caption: view.caption }) + previewDivider()) +
      '<section id="aw-review-panel" class="aw-inspector" aria-label="' +
      esc(task.category) +
      ' review">' +
      (summary.applicable
        ? view.html
        : '<p class="aw-empty">' + esc(summary.summary) + "</p>") +
      '<p class="aw-done-note">' +
      esc(task.done) +
      "</p></section></div></div>";
    if (!view.noViewer) paintPage(view.overlays || [], view.overlaySvg);
    if (view.after) view.after();
  }

  function taskView(taskId) {
    if (taskId === "document") return documentView();
    if (taskId === "fields") return fieldsView();
    if (taskId === "tab") return tabView();
    if (taskId === "order") return orderView();
    if (taskId === "headings") return headingsView();
    if (taskId === "images") return imagesView();
    if (taskId === "links") return linksView();
    return textView();
  }

  function reviewedBanner(taskId) {
    return ws.reviewed[taskId]
      ? '<p class="aw-reviewed">' +
          countBadge(0, true) +
          "<span>You reviewed this task. Changes you make now are applied the same way.</span></p>"
      : "";
  }

  // --- Title & language ---

  function documentView() {
    const meta = ws.meta;
    const blocks = blocksOnPage(0);
    const titleBlock = meta.titleCandidate
      ? blocks.filter(function (block) {
          return block.headingCandidateId === meta.titleCandidate;
        })
      : [];
    if (titleBlock.length) ws.page = 0;
    const languageOptions = LANGUAGES.map(function (entry) {
      return (
        '<option value="' +
        esc(entry[0]) +
        '"' +
        (entry[0] === meta.language ? " selected" : "") +
        ">" +
        esc(entry[1] + " (" + entry[0] + ")") +
        "</option>"
      );
    }).join("");
    const custom =
      meta.language &&
      !LANGUAGES.some(function (entry) {
        return entry[0] === meta.language;
      });
    return {
      lead: "The title is what a screen reader announces when the file opens, and what the viewer shows instead of the file name. The language picks the voice.",
      caption: titleBlock.length ? "Title source outlined" : "",
      overlays: titleBlock.map(function (block) {
        return { box: block.box, tone: "focus", label: "Title source" };
      }),
      html:
        reviewedBanner("document") +
        '<div class="aw-field-group"><label for="aw-title" class="aw-label">Title</label>' +
        '<input id="aw-title" class="aw-input" type="text" data-aw-input="meta-title" value="' +
        esc(meta.title) +
        '">' +
        (meta.titleSource
          ? '<p class="aw-help">Suggested from ' +
            esc(meta.titleSource) +
            ".</p>"
          : '<p class="aw-help">There is no title yet. Use the name a reader would recognize.</p>') +
        "</div>" +
        '<div class="aw-field-group"><label for="aw-language" class="aw-label">Language</label>' +
        '<select id="aw-language" class="aw-input" data-aw-input="meta-language"><option value="">Choose a language</option>' +
        languageOptions +
        (custom
          ? '<option value="' +
            esc(meta.language) +
            '" selected>' +
            esc(meta.language) +
            "</option>"
          : "") +
        "</select>" +
        (meta.languageGuessed
          ? '<p class="aw-help">Guessed from the words on the page. Check it.</p>'
          : "") +
        "</div>" +
        '<details class="aw-details"><summary>Author and subject (optional)</summary>' +
        '<div class="aw-field-group"><label for="aw-author" class="aw-label">Author</label><input id="aw-author" class="aw-input" type="text" data-aw-input="meta-author" value="' +
        esc(meta.author) +
        '"></div><div class="aw-field-group"><label for="aw-subject" class="aw-label">Subject</label><input id="aw-subject" class="aw-input" type="text" data-aw-input="meta-subject" value="' +
        esc(meta.subject) +
        '"></div></details>' +
        '<div class="aw-preview-box"><p class="aw-eyebrow">The viewer’s title bar will show</p><p class="aw-spoken" data-aw-ref="title-preview">' +
        esc(meta.title || "(the file name)") +
        "</p></div>" +
        '<div class="aw-actions"><button type="button" class="aw-btn aw-btn-primary" data-aw="confirm-document">Confirm title & language</button></div>',
    };
  }

  async function confirmDocument() {
    const meta = ws.meta;
    if (!meta.title.trim()) {
      host.showError("Enter a title first.");
      return;
    }
    if (!meta.language.trim()) {
      host.showError("Choose the document’s language first.");
      return;
    }
    const applied = await applyChange("Title & language", "document", [
      async function (bytes) {
        return (
          await remediate(
            "metadata",
            {
              metadata: {
                title: meta.title.trim(),
                language: meta.language.trim(),
                author: meta.author.trim(),
                subject: meta.subject.trim(),
              },
              display_doc_title: "true",
            },
            bytes,
          )
        ).bytes;
      },
    ]);
    if (applied) {
      meta.confirmed = true;
      meta.languageGuessed = false;
      markReviewed("document");
      saveDecisions();
      goToNextTask();
    }
  }

  // --- Form controls ---

  function sourceChip(source) {
    if (source === "nearby")
      return '<span class="aw-tag-draft">Drafted from nearby text</span>';
    if (source === "cleaned")
      return '<span class="aw-tag-draft">Cleaned up from the form’s own name</span>';
    if (source === "option-group")
      return '<span class="aw-tag-draft">Drafted from the question and answer</span>';
    if (source === "ai")
      return '<span class="aw-tag-draft">AI draft · not verified</span>';
    if (source === "existing")
      return '<span class="aw-tag">Already in the PDF</span>';
    if (source === "person") return '<span class="aw-tag">Edited by you</span>';
    return "";
  }

  function fieldsView() {
    const list = fieldsInTask();
    if (!list.length && !ws.showAllFields) {
      return {
        lead: "Read each field where it sits on the page, then confirm the name a screen reader will announce.",
        overlays: fieldOverlays(null),
        html:
          reviewedBanner("fields") +
          '<p class="aw-empty">Every field has a confirmed name.</p>' +
          '<div class="aw-actions"><button type="button" class="aw-btn aw-btn-secondary" data-aw="fields-show-all">Show all fields</button>' +
          (ws.reviewed.fields
            ? ""
            : '<button type="button" class="aw-btn aw-btn-primary" data-aw="fields-done">Mark names reviewed</button>') +
          "</div>",
      };
    }
    ws.nameCursor = Math.min(ws.nameCursor, Math.max(0, list.length - 1));
    const field = list[ws.nameCursor];
    if (field && field.pageIndex !== ws.page) ws.page = field.pageIndex;
    const decision = ws.names[field.name];
    const nearby = nearbyFor(field);
    const duplicates = duplicateAnnouncedNames();
    const value = String(decision.value || "");
    const isDuplicate = value && (duplicates.get(value.toLowerCase()) || 0) > 1;
    const duplicateOthers = isDuplicate
      ? ws.fields.filter(function (other) {
          return (
            other !== field &&
            currentName(other.name).toLowerCase() === value.toLowerCase()
          );
        })
      : [];
    const pendingWrites = ws.fields.filter(function (item) {
      const entry = ws.names[item.name];
      return entry.confirmed && !entry.written;
    }).length;
    const items = list
      .map(function (item, index) {
        const entry = ws.names[item.name];
        const current = index === ws.nameCursor;
        return (
          '<li><button type="button" class="aw-list-item' +
          (current ? " is-current" : "") +
          '" data-aw="field-pick" data-index="' +
          index +
          '"' +
          (current ? ' aria-current="true"' : "") +
          ">" +
          countBadge(entry.confirmed ? 0 : index + 1, entry.confirmed) +
          '<span class="aw-list-text">' +
          esc(currentName(item.name) || "(no name)") +
          '<span class="aw-muted aw-small"> · page ' +
          (item.pageIndex + 1) +
          "</span></span></button></li>"
        );
      })
      .join("");
    return {
      lead: "Read each field where it sits on the page, then confirm the name a screen reader will announce.",
      caption: "Field " + (ws.nameCursor + 1) + " of " + list.length,
      overlays: fieldOverlays(field, nearby),
      html:
        reviewedBanner("fields") +
        '<div class="aw-inspector-head"><span class="aw-eyebrow">Field ' +
        (ws.nameCursor + 1) +
        " of " +
        list.length +
        " · Page " +
        (field.pageIndex + 1) +
        "</span>" +
        sourceChip(decision.source) +
        "</div>" +
        '<p class="aw-small"><span class="aw-muted">Announced today</span> <span class="aw-said">“' +
        esc((field.tooltip || field.name) + ", " + controlRoleWords(field)) +
        "”</span></p>" +
        (field.label.problem && !decision.confirmed
          ? '<p class="aw-small">' +
            esc(NAME_PROBLEMS[field.label.problem] || "") +
            "</p>"
          : "") +
        '<div class="aw-field-group"><label for="aw-field-name" class="aw-label">Announced name</label>' +
        '<input id="aw-field-name" class="aw-input aw-input-strong" type="text" data-aw-input="field-name" value="' +
        esc(value) +
        '" autocomplete="off">' +
        '<p class="aw-help">' +
        (nearby ? esc(suggestionNote(field)) + " " : "") +
        'Internal name: <span class="aw-mono">' +
        esc(field.name) +
        "</span> — it never changes.</p>" +
        (nearby && value !== nearby.text
          ? '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="field-use-nearby">Use “' +
            esc(nearby.text) +
            "”</button>"
          : "") +
        "</div>" +
        (isDuplicate
          ? '<p class="aw-warning">Same name as ' +
            esc(
              duplicateOthers
                .slice(0, 3)
                .map(function (other) {
                  return "a field on page " + (other.pageIndex + 1);
                })
                .join(", "),
            ) +
            ". Add what makes this one different.</p>"
          : "") +
        '<div class="aw-preview-box"><p class="aw-eyebrow">A screen reader will say</p><p class="aw-spoken" data-aw-ref="field-preview">“' +
        esc(
          spokenAnnouncement(
            { kind: "field", text: value, name: field.name },
            ws.fieldsByName,
          ),
        ) +
        "”</p></div>" +
        '<div class="aw-actions"><button type="button" class="aw-btn aw-btn-primary" data-aw="field-confirm">Confirm name</button>' +
        '<button type="button" class="aw-btn aw-btn-secondary" data-aw="field-skip">Skip for now</button>' +
        (host.aiEnabled()
          ? '<button type="button" class="aw-btn aw-btn-quiet" data-aw="field-ai">Redraft with AI</button>'
          : "") +
        "</div>" +
        '<div class="aw-subhead"><h3 class="aw-card-title">This task</h3>' +
        '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="fields-draft-all">Use all suggestions</button></div>' +
        '<ol class="aw-list">' +
        items +
        "</ol>" +
        '<label class="aw-check aw-small"><input type="checkbox" data-aw-input="fields-show-all"' +
        (ws.showAllFields ? " checked" : "") +
        "> <span>Show fields that already have names</span></label>" +
        '<div class="aw-sticky-actions"><p class="aw-small aw-muted">Confirmed names are copied into the form tags for you. Field names, values and types stay the same.</p>' +
        '<button type="button" class="aw-btn aw-btn-primary" data-aw="fields-write"' +
        (pendingWrites ? "" : " disabled") +
        ">Write " +
        plural(pendingWrites, "confirmed name") +
        " to the PDF</button></div>",
    };
  }

  // Where a suggestion came from, said plainly, with its source outlined.
  function suggestionNote(field) {
    const label = field.label;
    const group = label.group;
    if (label.source === "option-group" && group) {
      return (
        "One of " +
        group.size +
        " answers to “" +
        (group.question || "the question") +
        "”: this box is “" +
        group.option +
        "” (both outlined on the page)."
      );
    }
    if (label.source === "existing")
      return "Suggested from the form’s own name, without the instruction.";
    return "Suggested from the printed label (outlined on the page).";
  }

  function fieldOverlays(currentField, nearby) {
    const duplicates = duplicateAnnouncedNames();
    /** @type {Array<any>} */
    const marks = ws.fields
      .filter(function (field) {
        return field.box && field.pageIndex === ws.page;
      })
      .map(function (field) {
        const isCurrent = currentField && field.name === currentField.name;
        const needs = fieldNeedsAttention(field, duplicates);
        return {
          box: field.box,
          tone: isCurrent ? "current" : needs ? "attention" : "quiet",
          target: "field:" + field.name,
        };
      });
    if (nearby && currentField && currentField.pageIndex === ws.page) {
      nearby.boxes.forEach(function (box) {
        marks.push({ box: box, tone: "source" });
      });
    }
    if (currentField && currentField.pageIndex === ws.page)
      ws.scrollToMark = true;
    return marks;
  }

  function moveFieldCursor(delta) {
    const list = fieldsInTask();
    if (!list.length) return;
    ws.nameCursor = (ws.nameCursor + delta + list.length) % list.length;
  }

  function confirmFieldName() {
    const list = fieldsInTask();
    const field = list[ws.nameCursor];
    if (!field) return;
    const decision = ws.names[field.name];
    if (!String(decision.value || "").trim()) {
      host.showError("Type the name a screen reader should announce.");
      return;
    }
    decision.confirmed = true;
    decision.written = false;
    decision.touched = true;
    const remaining = fieldsInTask();
    const nextIndex = remaining.findIndex(function (item, index) {
      return index >= ws.nameCursor && !ws.names[item.name].confirmed;
    });
    if (nextIndex >= 0) ws.nameCursor = nextIndex;
    announce("Confirmed “" + decision.value + "”.");
    render();
    focusField("aw-field-name");
  }

  async function writeFieldNames() {
    const applied = await applyChange("Field names", "fields", [
      async function (bytes) {
        return (
          await remediate(
            "metadata",
            {
              field_tooltips: confirmedTooltips(),
              display_doc_title: "false",
            },
            bytes,
          )
        ).bytes;
      },
    ]);
    if (!applied) return;
    ws.fields.forEach(function (field) {
      const entry = ws.names[field.name];
      if (entry.confirmed) entry.written = true;
    });
    if (!attentionFields().length) {
      markReviewed("fields");
      render();
    }
    saveDecisions();
  }

  // --- Keyboard order ---

  function tabView() {
    const pages = pagesWithFields();
    if (pages.indexOf(ws.page) === -1 && pages.length) ws.page = pages[0];
    const onPage = ws.tabOrder.filter(function (name) {
      const field = ws.fieldsByName.get(name);
      return field && field.box && field.pageIndex === ws.page;
    });
    const fieldsHere = onPage.map(function (name) {
      return ws.fieldsByName.get(name);
    });
    const jumps = countOrderJumps(onPage, ws.fieldsByName);
    const confirmed = ws.tabConfirmedPages.has(ws.page);
    const overlays = fieldsHere.map(function (field, index) {
      return {
        box: field.box,
        tone: "quiet",
        label: String(index + 1),
        target: "tab:" + field.name,
      };
    });
    const items = fieldsHere
      .map(function (field, index) {
        return (
          '<li class="aw-order-row"><span class="aw-order-number">' +
          (index + 1) +
          '</span><span class="aw-order-text">' +
          esc(currentName(field.name) || field.name) +
          '</span><span class="aw-order-buttons"><button type="button" class="aw-icon-btn" data-aw="tab-up" data-name="' +
          esc(field.name) +
          '" aria-label="Move ' +
          esc(currentName(field.name) || field.name) +
          ' earlier"' +
          (index === 0 ? " disabled" : "") +
          '>↑</button><button type="button" class="aw-icon-btn" data-aw="tab-down" data-name="' +
          esc(field.name) +
          '" aria-label="Move ' +
          esc(currentName(field.name) || field.name) +
          ' later"' +
          (index === fieldsHere.length - 1 ? " disabled" : "") +
          ">↓</button></span></li>"
        );
      })
      .join("");
    const pagePicker = pages
      .map(function (page) {
        return (
          '<button type="button" class="aw-page-chip' +
          (page === ws.page ? " is-current" : "") +
          (ws.tabConfirmedPages.has(page) ? " is-done" : "") +
          '" data-aw="go-page" data-page="' +
          page +
          '"' +
          (page === ws.page ? ' aria-current="true"' : "") +
          ">Page " +
          (page + 1) +
          (ws.tabConfirmedPages.has(page)
            ? '<span class="aw-sr"> (walked)</span>'
            : "") +
          "</button>"
        );
      })
      .join("");
    return {
      lead: "Walk through the fields in the order Tab will visit them. Numbers on the page show that order.",
      caption: plural(fieldsHere.length, "field") + " on this page",
      overlays: overlays,
      overlaySvg: tabPathSvg(fieldsHere),
      html:
        reviewedBanner("tab") +
        '<div class="aw-page-chips" role="group" aria-label="Pages with fields">' +
        pagePicker +
        "</div>" +
        (jumps
          ? '<p class="aw-warning">On this page Tab jumps back up ' +
            plural(jumps, "time") +
            ".</p>"
          : '<p class="aw-small aw-muted">Tab moves down this page without jumping back up.</p>') +
        '<div class="aw-actions aw-actions-tight"><button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="tab-rows">Row by row</button><button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="tab-columns">Down each column</button>' +
        (speechAvailable()
          ? '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="tab-listen">Listen</button>'
          : "") +
        "</div>" +
        '<ol class="aw-order-list" aria-label="Tab order on page ' +
        (ws.page + 1) +
        '">' +
        items +
        "</ol>" +
        '<div class="aw-sticky-actions"><button type="button" class="aw-btn aw-btn-primary" data-aw="tab-confirm">' +
        (confirmed
          ? "Walked · write order again"
          : "I walked page " + (ws.page + 1) + " · confirm order") +
        "</button></div>",
    };
  }

  function tabPathSvg(fields) {
    if (fields.length < 2) return "";
    const points = fields
      .map(function (field) {
        return (
          ((field.box.x + field.box.width / 2) * 100).toFixed(2) +
          "," +
          ((field.box.y + field.box.height / 2) * 100).toFixed(2)
        );
      })
      .join(" ");
    return (
      '<svg class="aw-path" viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="' +
      points +
      '" /></svg>'
    );
  }

  function reorderTabPage(order) {
    const onPage = new Set(order);
    const positions = [];
    ws.tabOrder.forEach(function (name, index) {
      if (onPage.has(name)) positions.push(index);
    });
    positions.forEach(function (position, index) {
      ws.tabOrder[position] = order[index];
    });
    ws.tabEdited = true;
    ws.tabConfirmedPages.delete(ws.page);
  }

  function moveTab(name, delta) {
    const onPage = ws.tabOrder.filter(function (item) {
      const field = ws.fieldsByName.get(item);
      return field && field.box && field.pageIndex === ws.page;
    });
    const index = onPage.indexOf(name);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= onPage.length) return;
    onPage.splice(index, 1);
    onPage.splice(target, 0, name);
    reorderTabPage(onPage);
  }

  function applyTabHeuristic(kind) {
    const fieldsHere = ws.fields.filter(function (field) {
      return field.box && field.pageIndex === ws.page;
    });
    reorderTabPage(
      kind === "columns"
        ? columnFieldOrder(fieldsHere)
        : visualFieldOrder(fieldsHere),
    );
  }

  async function confirmTabPage() {
    const page = ws.page;
    const applied = await applyChange("Tab order, page " + (page + 1), "", [
      async function (bytes) {
        return (
          await remediate(
            "metadata",
            {
              field_order: ws.tabOrder,
              set_structure_tab_order: "true",
              display_doc_title: "false",
            },
            bytes,
          )
        ).bytes;
      },
    ]);
    if (!applied) return;
    ws.tabConfirmedPages.add(page);
    const remaining = pagesWithFields().filter(function (item) {
      return !ws.tabConfirmedPages.has(item);
    });
    if (!remaining.length) markReviewed("tab");
    else ws.page = remaining[0];
    saveDecisions();
    render();
  }

  // --- Reading order ---

  function lockedTreeNotice() {
    return (
      '<div class="aw-callout"><p class="aw-eyebrow">This PDF already has tags</p><p>Someone tagged this file before. To change order, headings or images here, replace those tags with our draft. Until then you can listen and compare, but not edit.</p>' +
      '<div class="aw-actions"><button type="button" class="aw-btn aw-btn-secondary" data-aw="task" data-task="headings">Decide under Headings & tags</button></div></div>'
    );
  }

  function announcementsOnPage(pageIndex) {
    return (readback().announcements || []).filter(function (item) {
      return Number(item.page) === pageIndex;
    });
  }

  function orderView() {
    const pages = pagesWithText();
    if (pages.indexOf(ws.page) === -1 && pages.length) ws.page = pages[0];
    const order = ws.blockOrder[ws.page] || [];
    const findings = readbackFindings("order").filter(function (finding) {
      return (
        finding.page === undefined ||
        finding.page === null ||
        Number(finding.page) === ws.page
      );
    });
    const otherPages = readbackFindings("order").filter(function (finding) {
      return (
        finding.page !== undefined &&
        finding.page !== null &&
        Number(finding.page) !== ws.page
      );
    });
    const overlays = order.map(function (blockId, index) {
      const block = blockById(blockId);
      const role = blockRole(block);
      return {
        box: block.box,
        tone:
          blockId === ws.selectedBlock
            ? "current"
            : role === "Artifact"
              ? "muted"
              : "quiet",
        label: role === "Artifact" ? "×" : String(index + 1),
        target: "block:" + blockId,
      };
    });
    const roleOptions = function (current) {
      return ROLE_CHOICES.map(function (choice) {
        return (
          '<option value="' +
          choice[0] +
          '"' +
          (choice[0] === current ? " selected" : "") +
          ">" +
          esc(choice[1]) +
          "</option>"
        );
      }).join("");
    };
    const rows = order
      .map(function (blockId, index) {
        const block = blockById(blockId);
        const role = blockRole(block);
        const selected = blockId === ws.selectedBlock;
        return (
          '<li class="aw-order-row' +
          (selected ? " is-current" : "") +
          (role === "Artifact" ? " is-muted" : "") +
          '"><span class="aw-order-number">' +
          (index + 1) +
          '</span><button type="button" class="aw-order-text aw-linklike" data-aw="block-select" data-block="' +
          esc(blockId) +
          '">' +
          esc(
            block.text.length > 90 ? block.text.slice(0, 88) + "…" : block.text,
          ) +
          '</button><label class="aw-sr" for="aw-role-' +
          index +
          '">Role of item ' +
          (index + 1) +
          '</label><select id="aw-role-' +
          index +
          '" class="aw-role-select" data-aw-input="block-role" data-block="' +
          esc(blockId) +
          '"' +
          (treeLocked() ? " disabled" : "") +
          ">" +
          roleOptions(role) +
          '</select><span class="aw-order-buttons"><button type="button" class="aw-icon-btn" data-aw="block-up" data-block="' +
          esc(blockId) +
          '" aria-label="Move item ' +
          (index + 1) +
          ' earlier"' +
          (index === 0 || treeLocked() ? " disabled" : "") +
          '>↑</button><button type="button" class="aw-icon-btn" data-aw="block-down" data-block="' +
          esc(blockId) +
          '" aria-label="Move item ' +
          (index + 1) +
          ' later"' +
          (index === order.length - 1 || treeLocked() ? " disabled" : "") +
          ">↓</button></span></li>"
        );
      })
      .join("");
    const pagePicker = pages
      .map(function (page) {
        const done = ws.orderConfirmedPages.has(page);
        return (
          '<button type="button" class="aw-page-chip' +
          (page === ws.page ? " is-current" : "") +
          (done ? " is-done" : "") +
          '" data-aw="go-page" data-page="' +
          page +
          '"' +
          (page === ws.page ? ' aria-current="true"' : "") +
          ">Page " +
          (page + 1) +
          (done ? '<span class="aw-sr"> (confirmed)</span>' : "") +
          "</button>"
        );
      })
      .join("");
    const edited = ws.orderEditedPages.has(ws.page);
    return {
      lead: "Compare what is spoken to what you see. Move items in the list to change the order; form fields join next to their labels automatically.",
      caption: "Numbers show the order text will be read",
      overlays: overlays,
      html:
        reviewedBanner("order") +
        (treeLocked() ? lockedTreeNotice() : "") +
        '<div class="aw-page-chips" role="group" aria-label="Pages">' +
        pagePicker +
        "</div>" +
        findings
          .map(function (finding) {
            return (
              '<div class="aw-callout aw-callout-attention"><p class="aw-eyebrow">' +
              esc(finding.title) +
              '</p><p class="aw-small">' +
              esc(finding.detail) +
              "</p></div>"
            );
          })
          .join("") +
        (otherPages.length
          ? '<p class="aw-small aw-muted">Also flagged on ' +
            esc(
              Array.from(
                new Set(
                  otherPages.map(function (finding) {
                    return "page " + (Number(finding.page) + 1);
                  }),
                ),
              ).join(", "),
            ) +
            ".</p>"
          : "") +
        (speechAvailable()
          ? '<div class="aw-actions aw-actions-tight"><button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="order-listen-now">Hear the file now</button><button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="order-listen-mine">Hear my order</button><span class="aw-chip aw-chip-neutral">Simulation</span></div>'
          : "") +
        '<ol class="aw-order-list" aria-label="Reading order on page ' +
        (ws.page + 1) +
        '">' +
        rows +
        "</ol>" +
        '<div class="aw-sticky-actions">' +
        (edited
          ? '<p class="aw-small">You changed this page. Confirming rebuilds the tags with your order.</p>'
          : "") +
        '<button type="button" class="aw-btn aw-btn-primary" data-aw="order-confirm"' +
        (treeLocked() ? " disabled" : "") +
        ">Confirm page " +
        (ws.page + 1) +
        " order</button></div>",
    };
  }

  function moveBlock(blockId, delta) {
    const order = ws.blockOrder[ws.page] || [];
    const index = order.indexOf(blockId);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= order.length) return;
    order.splice(index, 1);
    order.splice(target, 0, blockId);
    ws.selectedBlock = blockId;
    ws.orderEditedPages.add(ws.page);
    ws.orderConfirmedPages.delete(ws.page);
  }

  async function confirmOrderPage() {
    const page = ws.page;
    const needsRebuild = ws.orderEditedPages.has(page);
    if (needsRebuild) {
      const applied = await rebuildStructure(
        "Reading order, page " + (page + 1),
        "",
      );
      if (!applied) return;
      ws.orderEditedPages.delete(page);
    }
    ws.orderConfirmedPages.add(page);
    const remaining = pagesWithText().filter(function (item) {
      return !ws.orderConfirmedPages.has(item);
    });
    if (!remaining.length) markReviewed("order");
    else ws.page = remaining[0];
    if (needsRebuild) saveDecisions();
    render();
  }

  // --- Headings & tags ---

  function headingsView() {
    const candidates = headingCandidates();
    const pageCandidates = candidates.filter(function (candidate) {
      return Number(candidate.pageIndex) === ws.page;
    });
    const overlays = pageCandidates.map(function (candidate) {
      const decision = ws.headings[candidate.candidateId] || {};
      const isHeading = decision.status !== "rejected" && decision.tag !== "P";
      return {
        box: candidate.box,
        tone:
          decision.status === "pending"
            ? "attention"
            : isHeading
              ? "quiet"
              : "muted",
        label: isHeading
          ? decision.tag + (decision.status === "pending" ? "?" : "")
          : "P",
        target: "heading:" + candidate.candidateId,
      };
    });
    const levels = ["H1", "H2", "H3", "H4", "P"];
    let previousLevel = 0;
    let skipped = false;
    let h1Count = 0;
    const outline = candidates
      .map(function (candidate) {
        const decision = ws.headings[candidate.candidateId] || {};
        const tag = decision.status === "rejected" ? "P" : decision.tag;
        const level = /^H(\d)$/.exec(tag);
        if (level) {
          const number = Number(level[1]);
          if (number === 1) h1Count += 1;
          if (previousLevel && number > previousLevel + 1) skipped = true;
          previousLevel = number;
        }
        const radios = levels
          .map(function (choice) {
            const id =
              "aw-h-" +
              candidate.candidateId.replace(/[^a-z0-9]/gi, "-") +
              "-" +
              choice;
            return (
              '<input type="radio" class="aw-seg-input" id="' +
              id +
              '" name="aw-h-' +
              esc(candidate.candidateId) +
              '" value="' +
              choice +
              '" data-aw-input="heading-level" data-candidate="' +
              esc(candidate.candidateId) +
              '"' +
              (tag === choice ? " checked" : "") +
              (treeLocked() ? " disabled" : "") +
              '><label class="aw-seg" for="' +
              id +
              '">' +
              choice +
              "</label>"
            );
          })
          .join("");
        const indent = level ? Number(level[1]) - 1 : 3;
        return (
          '<li class="aw-outline-row' +
          (decision.status === "pending" ? " is-pending" : "") +
          '" style="--indent:' +
          indent +
          '"><div class="aw-outline-text"><button type="button" class="aw-linklike" data-aw="heading-show" data-candidate="' +
          esc(candidate.candidateId) +
          '">' +
          (decision.status === "pending"
            ? '<span class="aw-q" aria-hidden="true">?</span>'
            : "") +
          esc(candidate.text) +
          '</button><span class="aw-muted aw-small"> · page ' +
          (Number(candidate.pageIndex) + 1) +
          (decision.status === "pending"
            ? " · " +
              esc(
                decision.source === "ai"
                  ? "AI: " + (decision.aiReason || "suggested")
                  : candidate.reason,
              )
            : "") +
          '</span></div><fieldset class="aw-segmented"><legend class="aw-sr">Level for ' +
          esc(candidate.text) +
          "</legend>" +
          radios +
          "</fieldset></li>"
        );
      })
      .join("");
    const pending = candidates.filter(function (candidate) {
      return (ws.headings[candidate.candidateId] || {}).status === "pending";
    }).length;
    const treeChoice = ws.preexistingTree
      ? '<div class="aw-callout' +
        (ws.treeDecision ? "" : " aw-callout-attention") +
        '"><p class="aw-eyebrow">This PDF already has tags</p><p class="aw-small">Someone tagged this file before (' +
        esc(
          plural(
            Number((ws.originalInspection.tag_structure || {}).node_count || 0),
            "tag",
          ),
        ) +
        "). What should we do with them?</p>" +
        '<fieldset class="aw-choices"><legend class="aw-sr">Earlier tags</legend>' +
        '<label class="aw-choice"><input type="radio" name="aw-tree" value="keep" data-aw-input="tree-decision"' +
        (ws.treeDecision === "keep" ? " checked" : "") +
        '><span><strong>Keep them</strong><span class="aw-muted aw-small">Order, headings and images stay as they are. You can still name fields, set Tab order and fix links.</span></span></label>' +
        '<label class="aw-choice"><input type="radio" name="aw-tree" value="replace" data-aw-input="tree-decision"' +
        (ws.treeDecision === "replace" ? " checked" : "") +
        '><span><strong>Replace with our draft</strong><span class="aw-muted aw-small">Rebuild tags from the page layout and your decisions here.</span></span></label>' +
        "</fieldset></div>"
      : "";
    return {
      lead: "Decide what is a heading and how the headings nest. We build the rest of the tag tree from your decisions.",
      caption: pageCandidates.length
        ? plural(pageCandidates.length, "candidate") + " on this page"
        : "",
      overlays: overlays,
      html:
        reviewedBanner("headings") +
        treeChoice +
        (candidates.length
          ? '<div class="aw-subhead"><h3 class="aw-card-title">Proposed outline</h3>' +
            (host.aiEnabled() && !treeLocked()
              ? '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="headings-ai">Review with AI</button>'
              : "") +
            "</div>" +
            '<ol class="aw-outline">' +
            outline +
            "</ol>" +
            '<p class="aw-checks"><span class="aw-chip aw-chip-' +
            (skipped ? "fail" : "pass") +
            '">' +
            (skipped ? "skips a level" : "no skipped levels") +
            '</span> <span class="aw-chip aw-chip-' +
            (h1Count === 1 ? "pass" : "neutral") +
            '">' +
            plural(h1Count, "H1") +
            "</span></p>" +
            '<p class="aw-small aw-muted">To make any other line a heading, change its role under Reading order.</p>'
          : '<p class="aw-empty">No lines stand out as headings. If the document has sections, set their role under Reading order.</p>') +
        '<div class="aw-sticky-actions">' +
        (pending
          ? '<p class="aw-small">' +
            plural(pending, "candidate") +
            " still marked “?”. Confirming accepts the level shown for each.</p>"
          : "") +
        '<button type="button" class="aw-btn aw-btn-primary" data-aw="headings-confirm"' +
        (ws.preexistingTree && !ws.treeDecision ? " disabled" : "") +
        ">" +
        (treeLocked() ? "Keep earlier tags" : "Confirm outline") +
        "</button></div>",
    };
  }

  async function confirmHeadings() {
    if (treeLocked()) {
      markReviewed("headings");
      goToNextTask();
      return;
    }
    Object.keys(ws.headings).forEach(function (candidateId) {
      const decision = ws.headings[candidateId];
      if (decision.status === "pending")
        decision.status = decision.tag === "P" ? "rejected" : "approved";
      decision.source =
        decision.source === "ai" ? "ai-confirmed" : decision.source;
    });
    const applied = await rebuildStructure("Headings", "headings");
    if (applied) {
      markReviewed("headings");
      saveDecisions();
      goToNextTask();
    }
  }

  // --- Images ---

  function imagesView() {
    const images = imageList();
    ws.imageCursor = Math.min(ws.imageCursor, images.length - 1);
    const image = images[ws.imageCursor];
    if (Number(image.pageIndex) !== ws.page) ws.page = Number(image.pageIndex);
    const decision = ws.images[image.assetId];
    const preview = ws.previews ? ws.previews[image.assetId] : undefined;
    const confirmedOthers = images
      .filter(function (item) {
        return item !== image && (ws.images[item.assetId] || {}).confirmed;
      })
      .map(function (item) {
        const entry = ws.images[item.assetId];
        return (
          "Image " +
          (images.indexOf(item) + 1) +
          (entry.decision === "artifact"
            ? " was marked decorative by you."
            : " has a description you checked.")
        );
      });
    const items = images
      .map(function (item, index) {
        const entry = ws.images[item.assetId] || {};
        const current = index === ws.imageCursor;
        return (
          '<li><button type="button" class="aw-list-item' +
          (current ? " is-current" : "") +
          '" data-aw="image-pick" data-index="' +
          index +
          '"' +
          (current ? ' aria-current="true"' : "") +
          ">" +
          countBadge(entry.confirmed ? 0 : index + 1, entry.confirmed) +
          '<span class="aw-list-text">Image ' +
          (index + 1) +
          '<span class="aw-muted aw-small"> · page ' +
          (Number(item.pageIndex) + 1) +
          " · " +
          esc(
            entry.decision === "artifact"
              ? "decorative"
              : entry.altText
                ? entry.altText.slice(0, 40)
                : "undecided",
          ) +
          "</span></span></button></li>"
        );
      })
      .join("");
    return {
      lead: "Decide whether each image carries meaning. If it does, check the description against the image itself.",
      caption:
        "Image " +
        (ws.imageCursor + 1) +
        " of " +
        images.length +
        " is on this page",
      overlays: [],
      after: function () {
        if (ws.previews === null) {
          loadPreviews().then(function () {
            if (ws.step === "review" && ws.task === "images") render();
          });
        }
      },
      html:
        reviewedBanner("images") +
        (treeLocked() ? lockedTreeNotice() : "") +
        '<div class="aw-inspector-head"><span class="aw-eyebrow">Image ' +
        (ws.imageCursor + 1) +
        " of " +
        images.length +
        " · Page " +
        (Number(image.pageIndex) + 1) +
        "</span>" +
        sourceChip(decision.source) +
        "</div>" +
        '<figure class="aw-image-preview">' +
        (preview
          ? '<img src="' +
            esc(preview) +
            '" alt="Image ' +
            (ws.imageCursor + 1) +
            ' as drawn in the PDF">'
          : ws.previews === null
            ? '<p class="aw-muted aw-small">Loading a preview…</p>'
            : '<p class="aw-muted aw-small">Too small to preview (' +
              esc(image.width + "×" + image.height) +
              " pixels). Find it on the page.</p>") +
        "</figure>" +
        '<fieldset class="aw-choices"><legend class="aw-label">What does this image do for a reader?</legend>' +
        '<label class="aw-choice"><input type="radio" name="aw-image-kind" value="figure" data-aw-input="image-kind"' +
        (decision.decision === "figure" ? " checked" : "") +
        (treeLocked() ? " disabled" : "") +
        '><span><strong>It carries information</strong><span class="aw-muted aw-small">A reader would miss something without it.</span></span></label>' +
        '<label class="aw-choice"><input type="radio" name="aw-image-kind" value="artifact" data-aw-input="image-kind"' +
        (decision.decision === "artifact" ? " checked" : "") +
        (treeLocked() ? " disabled" : "") +
        '><span><strong>It’s decorative</strong><span class="aw-muted aw-small">Screen readers will skip it.</span></span></label></fieldset>' +
        (decision.decision === "figure"
          ? '<div class="aw-field-group"><label for="aw-alt" class="aw-label">Description</label><textarea id="aw-alt" class="aw-input" rows="3" data-aw-input="image-alt">' +
            esc(decision.altText) +
            '</textarea><p class="aw-help">Say what the image tells a reader, not what it looks like. Skip anything the text next to it already says.</p></div>' +
            '<label class="aw-check"><input type="checkbox" data-aw-input="image-verified"' +
            (decision.verified ? " checked" : "") +
            "> <span>I compared this description with the image on the page</span></label>"
          : "") +
        '<div class="aw-actions"><button type="button" class="aw-btn aw-btn-primary" data-aw="image-confirm"' +
        (treeLocked() ? " disabled" : "") +
        ">Confirm</button>" +
        '<button type="button" class="aw-btn aw-btn-secondary" data-aw="image-skip">Skip for now</button>' +
        (host.aiEnabled() && !treeLocked()
          ? '<button type="button" class="aw-btn aw-btn-quiet" data-aw="image-ai">Draft with AI</button>'
          : "") +
        "</div>" +
        (confirmedOthers.length
          ? '<p class="aw-small aw-muted">' +
            esc(confirmedOthers.join(" ")) +
            "</p>"
          : "") +
        '<div class="aw-subhead"><h3 class="aw-card-title">This task</h3></div><ol class="aw-list">' +
        items +
        "</ol>" +
        '<div class="aw-sticky-actions"><button type="button" class="aw-btn aw-btn-primary" data-aw="images-write"' +
        (imageDecisionPayload().length && !treeLocked() ? "" : " disabled") +
        ">Write image decisions to the PDF</button></div>",
    };
  }

  function confirmImage() {
    const images = imageList();
    const image = images[ws.imageCursor];
    const decision = ws.images[image.assetId];
    if (!decision.decision) {
      host.showError("Choose whether the image carries information.");
      return;
    }
    if (decision.decision === "figure") {
      if (!decision.altText.trim()) {
        host.showError("Write a description, or mark the image decorative.");
        return;
      }
      if (!decision.verified) {
        host.showError(
          "Compare the description with the image, then tick the box.",
        );
        return;
      }
    }
    decision.confirmed = true;
    const next = images.findIndex(function (item) {
      return !(ws.images[item.assetId] || {}).confirmed;
    });
    if (next >= 0) ws.imageCursor = next;
    render();
  }

  async function writeImages() {
    const applied = await rebuildStructure("Image decisions", "");
    if (!applied) return;
    const open = imageList().filter(function (image) {
      return !(ws.images[image.assetId] || {}).confirmed;
    });
    if (!open.length) markReviewed("images");
    saveDecisions();
    render();
  }

  // --- Tables & links ---

  function linksView() {
    const links = linkList();
    const tables = tableList();
    const pageLinks = links.filter(function (link) {
      return Number(link.pageIndex) === ws.page;
    });
    const linkRows = links
      .map(function (link) {
        const key = link.pageIndex + ":" + link.index;
        const decision = ws.links[key] || { contents: "" };
        const id = "aw-link-" + key.replace(":", "-");
        return (
          '<li class="aw-link-row' +
          (decision.confirmed ? " is-done" : "") +
          '"><label for="' +
          id +
          '" class="aw-label">' +
          esc(link.subtype === "Link" ? "Link" : link.subtype) +
          " · page " +
          (Number(link.pageIndex) + 1) +
          (link.tagged
            ? ""
            : ' <span class="aw-chip aw-chip-fail">not tagged</span>') +
          '</label><div class="aw-inline"><input id="' +
          id +
          '" class="aw-input" type="text" data-aw-input="link-contents" data-key="' +
          esc(key) +
          '" value="' +
          esc(decision.contents) +
          '"><button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="link-confirm" data-key="' +
          esc(key) +
          '">' +
          (decision.confirmed ? "Confirmed" : "Confirm") +
          "</button></div></li>"
        );
      })
      .join("");
    const tableRows = tables
      .map(function (table) {
        const decision = ws.tables[table.path] || {};
        const grid = (table.rows || [])
          .slice(0, 4)
          .map(function (row) {
            return (
              "<tr>" +
              row.cells
                .map(function (cell) {
                  return (
                    "<td>" +
                    esc(cell.role) +
                    (cell.scope ? " · " + esc(cell.scope) : "") +
                    "</td>"
                  );
                })
                .join("") +
              "</tr>"
            );
          })
          .join("");
        return (
          '<li class="aw-card' +
          (decision.confirmed ? " is-done" : "") +
          '"><p class="aw-eyebrow">Table · page ' +
          (Number(table.pageIndex) + 1) +
          " · " +
          plural((table.rows || []).length, "row") +
          "</p>" +
          ((table.issueIds || []).length
            ? '<p class="aw-warning">' +
              esc(table.issueIds.join(", ").replace(/-/g, " ")) +
              "</p>"
            : "") +
          '<table class="aw-mini-table"><caption class="aw-sr">Current cell tags</caption>' +
          grid +
          "</table>" +
          '<label class="aw-check"><input type="checkbox" data-aw-input="table-header" data-path="' +
          esc(table.path) +
          '"' +
          (decision.headerRow ? " checked" : "") +
          "> <span>The first row holds the column headers</span></label>" +
          '<button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="table-confirm" data-path="' +
          esc(table.path) +
          '">Confirm headers</button></li>'
        );
      })
      .join("");
    return {
      lead: "A listener hears a link by its description and moves through a table by its headers. Check both.",
      caption: pageLinks.length
        ? plural(pageLinks.length, "link") + " on this page"
        : "",
      overlays: [],
      html:
        reviewedBanner("links") +
        (links.length
          ? '<h3 class="aw-card-title">Links and notes</h3><p class="aw-small aw-muted">Describe where each link goes or what it does, as a reader would want to hear it.</p><ul class="aw-plain-list">' +
            linkRows +
            "</ul>"
          : "") +
        (tables.length
          ? '<h3 class="aw-card-title">Tables</h3><ul class="aw-plain-list">' +
            tableRows +
            "</ul>"
          : "") +
        '<div class="aw-sticky-actions"><button type="button" class="aw-btn aw-btn-primary" data-aw="links-write">Write links & tables to the PDF</button></div>',
    };
  }

  async function writeLinks() {
    const operations = [];
    linkList().forEach(function (link) {
      const key = link.pageIndex + ":" + link.index;
      const decision = ws.links[key];
      if (!decision || !decision.confirmed) return;
      operations.push({
        action: "set_annotation_contents",
        pageIndex: link.pageIndex,
        index: link.index,
        contents: decision.contents,
      });
      if (!link.tagged)
        operations.push({
          action: "tag_annotation",
          pageIndex: link.pageIndex,
          index: link.index,
          role: link.suggestedRole,
        });
    });
    tableList().forEach(function (table) {
      const decision = ws.tables[table.path];
      if (!decision || !decision.confirmed) return;
      if ((table.issueIds || []).indexOf("table-columns") !== -1)
        operations.push({ action: "pad_table", path: table.path });
      const firstRow = (table.rows || [])[0];
      if (!firstRow) return;
      firstRow.cells.forEach(function (cell) {
        if (cell.role !== "TH" && cell.role !== "TD") return;
        operations.push({
          action: "set_role",
          path: cell.path,
          role: decision.headerRow ? "TH" : "TD",
        });
        if (decision.headerRow)
          operations.push({
            action: "set_scope",
            path: cell.path,
            scope: "Column",
          });
      });
    });
    if (!operations.length) {
      host.showError("Confirm at least one link or table first.");
      return;
    }
    const applied = await applyChange("Links & tables", "", [
      async function (bytes) {
        return (await remediate("structure", { operations: operations }, bytes))
          .bytes;
      },
    ]);
    if (!applied) return;
    const open = taskSummary("links").count;
    if (!open) markReviewed("links");
    saveDecisions();
    render();
  }

  // --- Text fidelity ---

  function textView() {
    const fonts = fontIssues();
    const findings = textFindings();
    const unembedded = fonts.filter(function (font) {
      return !font.embedded;
    });
    const unmapped = fonts.filter(function (font) {
      return font.embedded && !font.unicodeCoverageComplete;
    });
    const encoding = findings.filter(function (finding) {
      return finding.category === "text-encoding";
    });
    const scanned = findings.filter(function (finding) {
      return /^readback-(image|vector)-only-/.test(finding.id);
    });
    const fontRows = unembedded
      .map(function (font) {
        const options = ((ws.fontSubs && ws.fontSubs.fonts) || []).find(
          function (item) {
            return item.resource === font.resource;
          },
        );
        const candidates = options ? options.candidates || [] : [];
        const select = options
          ? candidates.length
            ? '<label class="aw-sr" for="aw-sub-' +
              esc(font.resource) +
              '">Substitute for ' +
              esc(font.name) +
              '</label><select id="aw-sub-' +
              esc(font.resource) +
              '" class="aw-input" data-aw-input="font-sub" data-resource="' +
              esc(font.resource) +
              '"><option value="">Leave unembedded</option>' +
              candidates
                .map(function (candidate) {
                  return (
                    '<option value="' +
                    esc(candidate.path) +
                    '"' +
                    (ws.glyphs["sub:" + font.resource] === candidate.path
                      ? " selected"
                      : "") +
                    ">" +
                    esc(
                      candidate.postscript_name +
                        " · width difference " +
                        candidate.width_delta,
                    ) +
                    "</option>"
                  );
                })
                .join("") +
              "</select>"
            : '<p class="aw-small aw-muted">No installed font has matching widths.</p>'
          : "";
        return (
          '<li class="aw-card"><p class="aw-eyebrow">Not embedded</p><p><strong>' +
          esc(font.name) +
          '</strong> <span class="aw-muted aw-small">' +
          esc(font.subtype) +
          (font.pageIndex === null || font.pageIndex === undefined
            ? " · form fields"
            : " · page " + (Number(font.pageIndex) + 1)) +
          "</span></p>" +
          select +
          "</li>"
        );
      })
      .join("");
    const glyphFonts = ((ws.fontReview && ws.fontReview.fonts) || []).map(
      function (font) {
        const glyphs = (font.glyphs || []).slice(0, 48);
        return (
          '<li class="aw-card"><p class="aw-eyebrow">' +
          esc(font.font) +
          " · " +
          plural(glyphs.length, "unmapped glyph") +
          '</p><div class="aw-glyph-grid">' +
          glyphs
            .map(function (glyph) {
              const outline = glyph.outline;
              const key = font.resource + ":" + glyph.code;
              const value =
                ws.glyphs[key] !== undefined
                  ? ws.glyphs[key]
                  : glyph.proposal
                    ? glyph.proposal.character
                    : "";
              let svg = '<span class="aw-muted aw-small">no outline</span>';
              if (outline && outline.path) {
                const bbox = outline.bbox || [
                  0,
                  0,
                  outline.unitsPerEm,
                  outline.unitsPerEm,
                ];
                const width = Math.max(1, bbox[2] - bbox[0]);
                const height = Math.max(1, bbox[3] - bbox[1]);
                svg =
                  '<svg class="aw-glyph" viewBox="' +
                  [bbox[0], -bbox[3], width, height].join(" ") +
                  '" aria-hidden="true"><path transform="scale(1,-1)" d="' +
                  esc(outline.path) +
                  '"/></svg>';
              }
              return (
                '<div class="aw-glyph-cell">' +
                svg +
                '<label class="aw-sr" for="aw-g-' +
                esc(key) +
                '">Character for code ' +
                esc(glyph.codeLabel) +
                '</label><input id="aw-g-' +
                esc(key) +
                '" class="aw-input aw-glyph-input" maxlength="4" data-aw-input="glyph" data-key="' +
                esc(key) +
                '" value="' +
                esc(value) +
                '"><span class="aw-mono aw-tiny">' +
                esc(glyph.codeLabel) +
                (glyph.proposal
                  ? " · " + esc(glyph.proposal.unicodeName || "")
                  : "") +
                "</span></div>"
              );
            })
            .join("") +
          '</div><div class="aw-actions aw-actions-tight"><button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="glyph-map" data-resource="' +
          esc(font.resource) +
          '">Map these characters</button><button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="glyph-artifact" data-resource="' +
          esc(font.resource) +
          '">It only draws decoration</button></div></li>'
        );
      },
    );
    const encodingRows = encoding
      .map(function (finding) {
        const key = finding.contentId;
        if (!key) return "";
        const fix = ws.textFixes[key] || {
          actualText: finding.suggestion || "",
          confirmed: false,
        };
        ws.textFixes[key] = fix;
        return (
          '<li class="aw-card' +
          (fix.confirmed ? " is-done" : "") +
          '"><p class="aw-eyebrow">' +
          esc(finding.title) +
          " · page " +
          (Number(finding.page) + 1) +
          '</p><p class="aw-small">A screen reader says <span class="aw-said">“' +
          esc(finding.announced || "") +
          '”</span></p><label class="aw-label" for="aw-fix-' +
          esc(key) +
          '">What it should say</label><div class="aw-inline"><input id="aw-fix-' +
          esc(key) +
          '" class="aw-input" type="text" data-aw-input="text-fix" data-key="' +
          esc(key) +
          '" value="' +
          esc(fix.actualText) +
          '"><button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="text-fix-confirm" data-key="' +
          esc(key) +
          '">' +
          (fix.confirmed ? "Confirmed" : "Confirm") +
          "</button></div>" +
          (finding.confident
            ? ""
            : '<p class="aw-small aw-muted">We can’t determine this. Look at the page.</p>') +
          "</li>"
        );
      })
      .join("");
    const nothing =
      !unembedded.length &&
      !unmapped.length &&
      !encoding.length &&
      !scanned.length;
    return {
      lead: "A screen reader speaks the text a PDF stores, not what it draws. Make the two agree.",
      overlays: [],
      html:
        reviewedBanner("text") +
        (nothing
          ? '<p class="aw-empty">Every font is embedded and every glyph maps to text. Spot-check a few lines by selecting and copying them in a PDF reader.</p>'
          : "") +
        (scanned.length
          ? '<div class="aw-callout aw-callout-attention"><p class="aw-eyebrow">Pages without a text layer</p><p class="aw-small">' +
            esc(
              scanned
                .map(function (finding) {
                  return finding.detail || finding.title;
                })
                .join(" "),
            ) +
            '</p><div class="aw-actions"><button type="button" class="aw-btn aw-btn-secondary" data-aw="run-ocr">Recognize text (OCR)</button></div><p class="aw-small aw-muted">OCR is a guess about pixels. Check what it read before trusting it.</p></div>'
          : "") +
        (unembedded.length
          ? '<h3 class="aw-card-title">Fonts that aren’t embedded</h3><p class="aw-small aw-muted">No exact installed match was found. A substitute must have matching widths; look at the result before exporting.</p>' +
            (ws.fontSubs
              ? ""
              : '<button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="load-font-subs">Find substitutes</button>') +
            '<ul class="aw-plain-list">' +
            fontRows +
            "</ul>" +
            (ws.fontSubs
              ? '<button type="button" class="aw-btn aw-btn-secondary" data-aw="apply-font-subs">Embed chosen substitutes</button>'
              : "")
          : "") +
        (unmapped.length
          ? '<h3 class="aw-card-title">Glyphs with no text behind them</h3>' +
            (ws.fontReview
              ? '<ul class="aw-plain-list">' + glyphFonts.join("") + "</ul>"
              : '<p class="aw-small aw-muted">' +
                esc(
                  unmapped
                    .map(function (font) {
                      return font.name;
                    })
                    .join(", "),
                ) +
                '</p><button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="load-font-review">Show the glyphs</button>')
          : "") +
        (encoding.length
          ? '<h3 class="aw-card-title">Text spoken differently from how it reads</h3><ul class="aw-plain-list">' +
            encodingRows +
            "</ul>"
          : "") +
        '<div class="aw-sticky-actions">' +
        (encoding.length
          ? '<button type="button" class="aw-btn aw-btn-secondary" data-aw="text-fixes-write">Write corrections</button>'
          : "") +
        '<button type="button" class="aw-btn aw-btn-primary" data-aw="text-done">' +
        (nothing ? "I spot-checked the text" : "Mark text reviewed") +
        "</button></div>",
    };
  }

  // ---------------------------------------------------------------------
  // Step 3: Test & export
  // ---------------------------------------------------------------------

  function replayItems() {
    const all = readback().announcements || [];
    if (ws.replay.mode === "tab")
      return all.filter(function (item) {
        return item.kind === "field";
      });
    if (ws.replay.mode === "headings")
      return all.filter(function (item) {
        return /^H[1-6]$/.test(String(item.role || ""));
      });
    return all.filter(function (item) {
      return String(item.text || "").trim() || item.kind === "field";
    });
  }

  function findingsByAnnouncement() {
    const map = new Map();
    (readback().findings || []).forEach(function (finding) {
      if (
        finding.announcedIndex === undefined ||
        finding.announcedIndex === null
      )
        return;
      const key = Number(finding.announcedIndex);
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(finding);
    });
    return map;
  }

  function renderTest() {
    const items = replayItems();
    ws.replay.index = Math.min(ws.replay.index, Math.max(0, items.length - 1));
    const current = items[ws.replay.index];
    if (current && current.page !== null && current.page !== undefined)
      ws.page = Number(current.page);
    const flags = findingsByAnnouncement();
    const windowStart = Math.max(0, ws.replay.index - 3);
    const windowItems = items.slice(windowStart, windowStart + 12);
    const rows = windowItems
      .map(function (item, offset) {
        const index = windowStart + offset;
        const itemFlags = flags.get(Number(item.index)) || [];
        return (
          '<li class="aw-replay-row' +
          (index === ws.replay.index ? " is-current" : "") +
          '"' +
          (index === ws.replay.index ? ' aria-current="true"' : "") +
          '><button type="button" class="aw-linklike" data-aw="replay-go" data-index="' +
          index +
          '">' +
          esc(spokenAnnouncement(item, ws.fieldsByName)) +
          (item.page !== null &&
          item.page !== undefined &&
          item.page !== ws.page
            ? ' <span class="aw-muted aw-small">(page ' +
              (Number(item.page) + 1) +
              ")</span>"
            : "") +
          "</button>" +
          itemFlags
            .map(function (finding) {
              const taskId = READBACK_CATEGORY_TASK[finding.category];
              return (
                '<span class="aw-flag">' +
                esc(finding.title) +
                (taskId
                  ? ' <button type="button" class="aw-linklike" data-aw="task" data-task="' +
                    taskId +
                    '">Fix in ' +
                    esc(TASK_BY_ID.get(taskId).category) +
                    "</button>"
                  : "") +
                "</span>"
              );
            })
            .join("") +
          "</li>"
        );
      })
      .join("");
    const marks = [];
    if (
      current &&
      current.x !== null &&
      current.x !== undefined &&
      current.y !== null
    ) {
      marks.push({ box: pointBox(current.x, current.y), tone: "current" });
    } else if (current && current.kind === "field") {
      const field = ws.fieldsByName.get(current.name);
      if (field && field.box) marks.push({ box: field.box, tone: "current" });
    }
    const summary = report().summary || {};
    const failing = (report().issues || []).filter(function (issue) {
      return issue.status === "fail";
    });
    const externalRows = EXTERNAL_TESTS.map(function (test) {
      const record = ws.external[test.id] || {};
      const editing = ws.recording === test.id;
      return (
        '<li class="aw-external-row"><div><strong>' +
        esc(test.title) +
        '</strong><span class="aw-muted aw-small">' +
        esc(test.hint) +
        "</span>" +
        (record.result
          ? '<span class="aw-small">' +
            machineChip(
              record.result === "pass"
                ? "pass"
                : record.result === "fail"
                  ? "fail"
                  : "not checkable",
            ) +
            (record.notes ? " " + esc(record.notes) : "") +
            "</span>"
          : "") +
        "</div>" +
        (editing
          ? '<fieldset class="aw-record"><legend class="aw-sr">Result for ' +
            esc(test.title) +
            '</legend><div class="aw-inline">' +
            ["pass", "fail", "n/a"]
              .map(function (result) {
                const id = "aw-ext-" + test.id + "-" + result.replace("/", "");
                return (
                  '<input type="radio" class="aw-seg-input" name="aw-ext-' +
                  test.id +
                  '" id="' +
                  id +
                  '" value="' +
                  result +
                  '" data-aw-input="external-result" data-test="' +
                  test.id +
                  '"' +
                  (record.result === result ? " checked" : "") +
                  '><label class="aw-seg" for="' +
                  id +
                  '">' +
                  esc(
                    result === "n/a"
                      ? "Not applicable"
                      : result === "pass"
                        ? "Passed"
                        : "Failed",
                  ) +
                  "</label>"
                );
              })
              .join("") +
            '</div><label class="aw-label" for="aw-ext-notes-' +
            test.id +
            '">Notes (tool, version, what you found)</label><input id="aw-ext-notes-' +
            test.id +
            '" class="aw-input" type="text" data-aw-input="external-notes" data-test="' +
            test.id +
            '" value="' +
            esc(record.notes || "") +
            '"><button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="external-done">Done</button></fieldset>'
          : '<button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="external-record" data-test="' +
            test.id +
            '">' +
            (record.result ? "Change" : "Record result") +
            "</button>") +
        "</li>"
      );
    }).join("");
    const recorded = EXTERNAL_TESTS.filter(function (test) {
      return ws.external[test.id] && ws.external[test.id].result;
    }).length;
    const allPassed = EXTERNAL_TESTS.every(function (test) {
      const record = ws.external[test.id];
      return record && (record.result === "pass" || record.result === "n/a");
    });
    const canDeclare = recorded === EXTERNAL_TESTS.length && allPassed;
    els.main.innerHTML =
      '<div class="aw-test"><header class="aw-review-head"><p class="aw-eyebrow">Step 3 · Test & export</p>' +
      '<h2 class="aw-title">Hear it the way a screen reader user will</h2>' +
      '<p class="aw-lead">Read the replay against the page. Anything that sounds wrong links back to the task that fixes it.</p></header>' +
      '<div class="aw-review-body">' +
      pageViewer({ caption: "Highlight follows the replay" }) +
      previewDivider() +
      '<section id="aw-review-panel" class="aw-inspector" aria-label="Replay">' +
      (readback().available === false
        ? '<p class="aw-warning">' +
          esc(readback().reason || "There is no tag tree to replay.") +
          "</p>"
        : "") +
      '<div class="aw-segmented-row" role="group" aria-label="Replay mode">' +
      [
        ["all", "Read all"],
        ["tab", "Tab through"],
        ["headings", "Headings"],
      ]
        .map(function (mode) {
          return (
            '<button type="button" class="aw-seg-btn' +
            (ws.replay.mode === mode[0] ? " is-current" : "") +
            '" aria-pressed="' +
            (ws.replay.mode === mode[0] ? "true" : "false") +
            '" data-aw="replay-mode" data-mode="' +
            mode[0] +
            '">' +
            mode[1] +
            "</button>"
          );
        })
        .join("") +
      '</div><div class="aw-replay-controls"><button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="replay-prev"' +
      (ws.replay.index <= 0 ? " disabled" : "") +
      ">Previous</button>" +
      (speechAvailable()
        ? '<button type="button" class="aw-btn aw-btn-primary aw-btn-small" data-aw="replay-play">' +
          (ws.replay.playing ? "Pause" : "Play") +
          "</button>"
        : "") +
      '<button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="replay-next"' +
      (ws.replay.index >= items.length - 1 ? " disabled" : "") +
      '>Next</button><span class="aw-mono aw-small">' +
      (items.length ? ws.replay.index + 1 : 0) +
      " / " +
      items.length +
      '</span><span class="aw-chip aw-chip-neutral">Simulation</span></div>' +
      '<ol class="aw-replay" aria-label="Announcements">' +
      rows +
      "</ol>" +
      '<label class="aw-check"><input type="checkbox" data-aw-input="heard-all"' +
      (ws.replay.heardAll ? " checked" : "") +
      "> <span>I read the full replay against the pages</span></label>" +
      '<section class="aw-card"><p class="aw-eyebrow">Machine checks</p><p><strong>' +
      esc(Number(summary.passed_checks || 0)) +
      " pass · " +
      esc(Number(summary.failed_checks || 0)) +
      ' fail</strong></p><p class="aw-small aw-muted">Built-in checks only. They can’t tell whether names, order or descriptions make sense.</p>' +
      (failing.length
        ? '<ul class="aw-plain-list aw-small">' +
          failing
            .map(function (issue) {
              const taskId = Object.keys(TASK_CHECKS).find(function (key) {
                return TASK_CHECKS[key].indexOf(issue.id) !== -1;
              });
              return (
                "<li>" +
                esc(issue.title) +
                (taskId
                  ? ' <button type="button" class="aw-linklike" data-aw="task" data-task="' +
                    taskId +
                    '">Open ' +
                    esc(TASK_BY_ID.get(taskId).category) +
                    "</button>"
                  : "") +
                "</li>"
              );
            })
            .join("") +
          "</ul>"
        : "") +
      "</section>" +
      aiPanel() +
      '<section class="aw-card"><p class="aw-eyebrow">Export</p><p class="aw-small">Export keeps field names, values and types, page size and appearance. ' +
      (ws.integrity && ws.integrity.namesKept
        ? "Field-name check passed."
        : "") +
      '</p><button type="button" class="aw-btn aw-btn-primary" data-aw="export">Export reviewed PDF</button>' +
      (reviewedCount() < applicableTasks().length
        ? '<p class="aw-small aw-muted">' +
          esc(
            plural(applicableTasks().length - reviewedCount(), "review task"),
          ) +
          " still open. You can export a draft anyway.</p>"
        : "") +
      "</section>" +
      '<section class="aw-card"><p class="aw-eyebrow">Test outside this tool · after export</p><ul class="aw-plain-list">' +
      externalRows +
      "</ul></section>" +
      '<section class="aw-card"><p class="aw-eyebrow">PDF/UA declaration</p><p class="aw-small">Only you can decide there is enough evidence to declare conformance. We never add the declaration on our own.</p>' +
      (canDeclare
        ? '<label class="aw-check"><input type="checkbox" data-aw-input="declare"' +
          (ws.declare ? " checked" : "") +
          "> <span>I’ve reviewed the evidence and declare this file conforms to PDF/UA-1</span></label>"
        : '<p class="aw-small aw-muted">Opens once every outside test is recorded as passed: ' +
          recorded +
          " of " +
          EXTERNAL_TESTS.length +
          " recorded.</p>") +
      '<button type="button" class="aw-btn aw-btn-secondary" data-aw="declare"' +
      (canDeclare && ws.declare ? "" : " disabled") +
      ">Add declaration & re-export</button></section>" +
      "</section></div></div>";
    paintPage(marks);
    if (current) {
      const currentRow = els.main.querySelector(".aw-replay-row.is-current");
      if (currentRow) currentRow.scrollIntoView({ block: "nearest" });
    }
  }

  function aiPanel() {
    if (!host.aiEnabled()) return "";
    if (!ws.aiNotes) {
      return (
        '<section class="aw-card"><p class="aw-eyebrow">AI second opinion · advisory</p><p class="aw-small">An AI reads your drafts against the page text and suggests what to look at again. It never passes or fails anything.</p>' +
        '<button type="button" class="aw-btn aw-btn-secondary" data-aw="ai-review">Get a second opinion</button></section>'
      );
    }
    const open = ws.aiNotes.filter(function (note) {
      return !ws.aiDismissed.has(note.id);
    });
    return (
      '<section class="aw-card"><p class="aw-eyebrow">AI second opinion · advisory</p><p class="aw-small">' +
      (open.length
        ? plural(open.length, "thing") +
          " an AI reviewer would look at again. These are suggestions."
        : "Nothing left to look at.") +
      ' <button type="button" class="aw-linklike" data-aw="ai-review">Run again</button></p><ul class="aw-plain-list">' +
      open
        .map(function (note) {
          const taskId = aiNoteTask(note);
          return (
            '<li class="aw-note"><p class="aw-eyebrow">' +
            esc(taskId ? TASK_BY_ID.get(taskId).category : note.category) +
            "</p><p><strong>" +
            esc(note.title) +
            '</strong></p><p class="aw-small">' +
            esc(note.explanation) +
            '</p><div class="aw-actions aw-actions-tight">' +
            (taskId
              ? '<button type="button" class="aw-btn aw-btn-secondary aw-btn-small" data-aw="ai-open" data-note="' +
                esc(note.id) +
                '">Open in task</button>'
              : "") +
            '<button type="button" class="aw-btn aw-btn-quiet aw-btn-small" data-aw="ai-dismiss" data-note="' +
            esc(note.id) +
            '">Looks right — dismiss</button></div></li>'
          );
        })
        .join("") +
      "</ul></section>"
    );
  }

  function aiNoteTask(note) {
    const change = note.change || {};
    if (change.kind === "metadata") return "document";
    if (change.kind === "field_tooltip") return "fields";
    if (change.kind === "image_alt_text") return "images";
    if (change.kind === "heading") return "headings";
    if (change.kind === "content_order" || change.kind === "repair_structure")
      return "order";
    const category = String(note.category || "").toLowerCase();
    if (/field|form|tooltip/.test(category)) return "fields";
    if (/heading/.test(category)) return "headings";
    if (/order/.test(category)) return "order";
    if (/image|figure|alt/.test(category)) return "images";
    if (/link|table|annot/.test(category)) return "links";
    if (/text|encod|font|glyph/.test(category)) return "text";
    if (/title|language|metadata|document/.test(category)) return "document";
    return "";
  }

  // An AI suggestion becomes a draft in its task, never a decision.
  function openAiNote(note) {
    const change = note.change || {};
    const taskId = aiNoteTask(note);
    if (
      change.kind === "metadata" &&
      ws.meta &&
      typeof change.value === "string"
    ) {
      if (change.target in ws.meta) ws.meta[change.target] = change.value;
    } else if (change.kind === "field_tooltip" && ws.names[change.target]) {
      const decision = ws.names[change.target];
      decision.value = String(change.value || "");
      decision.source = "ai";
      decision.confirmed = false;
      decision.touched = true;
      const list = fieldsInTask();
      const index = list.findIndex(function (field) {
        return field.name === change.target;
      });
      if (index >= 0) ws.nameCursor = index;
    } else if (change.kind === "image_alt_text" && ws.images[change.target]) {
      const decision = ws.images[change.target];
      decision.decision = "figure";
      decision.altText = String(change.value || "");
      decision.source = "ai";
      decision.verified = false;
      decision.confirmed = false;
      ws.imageCursor = Math.max(
        0,
        imageList().findIndex(function (image) {
          return image.assetId === change.target;
        }),
      );
    } else if (change.kind === "heading" && ws.headings[change.target]) {
      const decision = ws.headings[change.target];
      decision.status = "pending";
      decision.tag = String(change.value || "P");
      decision.source = "ai";
      decision.aiReason = note.title;
    }
    if (taskId) goToTask(taskId);
  }

  function aiReviewContext() {
    const inspection = ws.inspection;
    return {
      filename: ws.filename,
      metadata: {
        title: ws.meta.title,
        language: ws.meta.language,
        author: ws.meta.author,
        subject: ws.meta.subject,
      },
      textSample: (inspection.content_blocks || [])
        .slice(0, 300)
        .map(function (block) {
          return block.text;
        })
        .join("\n"),
      fields: ws.fields.map(function (field) {
        const decision = ws.names[field.name] || {};
        return {
          fieldId: field.name,
          name: field.name,
          type: field.type,
          page: field.pageIndex + 1,
          tooltip: currentName(field.name),
          tooltipSource: decision.confirmed
            ? "reviewed"
            : decision.source || "",
        };
      }),
      headings: headingCandidates().map(function (candidate) {
        const decision = ws.headings[candidate.candidateId] || {};
        return Object.assign({}, candidate, {
          decision: decision.status,
          tag: decision.tag,
        });
      }),
      contentBlocks: contentDecisionPayload().map(function (item) {
        const block = blockById(item.blockId);
        return Object.assign({}, item, {
          page: item.pageIndex + 1,
          box: block ? block.box : {},
        });
      }),
      images: imageList().map(function (image) {
        const decision = ws.images[image.assetId] || {};
        return {
          assetId: image.assetId,
          page: Number(image.pageIndex) + 1,
          name: image.name,
          width: image.width,
          height: image.height,
          altText: decision.altText || "",
        };
      }),
      reportIssues: report().issues || [],
      readbackFindings: readback().findings || [],
      structureSummary: {
        tagTreePresent: !!(inspection.tag_structure || {}).present,
        tables: tableList().length,
        figures: ((inspection.structure_editor || {}).figures || []).length,
      },
    };
  }

  async function runAiReview() {
    if (ws.busy) return;
    setBusy("Asking an AI reviewer for a second opinion…");
    try {
      const data = await postJson("/pdf-labeler/api/accessibility-ai-review", {
        context: aiReviewContext(),
      });
      ws.aiNotes = (data.findings || []).map(function (note, index) {
        return Object.assign({ id: note.id || "note-" + index }, note);
      });
      ws.aiDismissed = new Set();
      ws.aiRanAt = Date.now();
    } catch (error) {
      host.showError("The AI review failed: " + (error.message || error));
    } finally {
      setBusy("");
      render();
    }
  }

  function exportPdf(bytes, suffix) {
    host.downloadBlob(
      new Blob([bytes], { type: "application/pdf" }),
      stripPdf(ws.filename) + "-" + suffix + ".pdf",
    );
  }

  async function declareConformance() {
    if (!ws.declare) return;
    const applied = await applyChange("PDF/UA declaration", "", [
      async function (bytes) {
        return (
          await remediate(
            "catalog_flags",
            { marked: "true", display_doc_title: "false" },
            bytes,
          )
        ).bytes;
      },
    ]);
    if (applied) exportPdf(ws.working, "pdfua");
  }

  function replayStep(delta) {
    const items = replayItems();
    ws.replay.index = Math.max(
      0,
      Math.min(items.length - 1, ws.replay.index + delta),
    );
    render();
  }

  function toggleReplay() {
    if (ws.replay.playing) {
      stopSpeaking();
      render();
      return;
    }
    const items = replayItems();
    const start = ws.replay.index;
    const lines = items.slice(start).map(function (item) {
      return spokenAnnouncement(item, ws.fieldsByName);
    });
    speakLines(
      lines,
      function (offset) {
        ws.replay.index = start + offset;
        ws.replay.playing = true;
        render();
      },
      function () {
        ws.replay.playing = false;
        render();
      },
    );
    ws.replay.playing = true;
    render();
  }

  // ---------------------------------------------------------------------
  // Navigation
  // ---------------------------------------------------------------------

  function goToTask(taskId) {
    stopSpeaking();
    ws.step = "review";
    ws.task = taskId;
    if (taskId === "document") ws.page = 0;
    if (taskId === "tab" || taskId === "order") {
      const pages = taskId === "tab" ? pagesWithFields() : pagesWithText();
      const firstOpen = pages.find(function (page) {
        return !(
          taskId === "tab" ? ws.tabConfirmedPages : ws.orderConfirmedPages
        ).has(page);
      });
      if (firstOpen !== undefined) ws.page = firstOpen;
    }
    render();
    focusMainHeading();
  }

  function goToNextTask() {
    const index = TASKS.findIndex(function (task) {
      return task.id === ws.task;
    });
    const next = TASKS.slice(index + 1)
      .concat(TASKS.slice(0, index))
      .find(function (task) {
        return !taskDone(task.id);
      });
    if (next) goToTask(next.id);
    else {
      ws.step = "test";
      render();
      focusMainHeading();
    }
  }

  function focusField(id) {
    window.setTimeout(function () {
      const input = document.getElementById(id);
      if (input) input.focus();
    }, 0);
  }

  // The workshop covers the field editor; keep the editor out of the tab
  // order and the accessibility tree while it does.
  function setEditorHidden(hidden) {
    root.hidden = !hidden;
    const editor = document.getElementById("app");
    if (editor) editor.inert = hidden;
    document.body.classList.toggle("a11y-workshop-open", hidden);
  }

  function close() {
    stopSpeaking();
    ws.open = false;
    setEditorHidden(false);
    if (host.onClose) host.onClose();
  }

  // ---------------------------------------------------------------------
  // Events
  // ---------------------------------------------------------------------

  const clickHandlers = {
    close: function () {
      if (
        ws.history.length &&
        !window.confirm(
          "Return to the field editor? Your review stays here while the form is unchanged, but the field editor does not receive these repairs: export the reviewed PDF to keep them.",
        )
      )
        return;
      close();
    },
    undo: undo,
    step: function (target) {
      stopSpeaking();
      ws.step = target.dataset.step;
      if (ws.step === "review") {
        const open = TASKS.find(function (task) {
          return !taskDone(task.id);
        });
        ws.task = open ? open.id : ws.task;
      }
      render();
      focusMainHeading();
    },
    task: function (target) {
      goToTask(target.dataset.task);
    },
    "start-review": function () {
      const open = TASKS.find(function (task) {
        return !taskDone(task.id);
      });
      goToTask(open ? open.id : "document");
    },
    "auto-draft": autoDraftAll,
    "zoom-in": function () {
      changePreviewZoom(25);
    },
    "zoom-out": function () {
      changePreviewZoom(-25);
    },
    "zoom-fit": function () {
      ws.previewZoom = null;
      syncPreviewLayout();
    },
    "page-prev": function () {
      ws.page = Math.max(0, ws.page - 1);
      render();
    },
    "page-next": function () {
      ws.page = Math.min(ws.pageViews.length - 1, ws.page + 1);
      render();
    },
    "go-page": function (target) {
      ws.page = Number(target.dataset.page);
      render();
    },
    "confirm-document": confirmDocument,
    "field-pick": function (target) {
      ws.nameCursor = Number(target.dataset.index);
      render();
      focusField("aw-field-name");
    },
    "field-confirm": confirmFieldName,
    "field-skip": function () {
      moveFieldCursor(1);
      render();
      focusField("aw-field-name");
    },
    "field-use-nearby": function () {
      const field = fieldsInTask()[ws.nameCursor];
      const nearby = nearbyFor(field);
      if (!nearby) return;
      const decision = ws.names[field.name];
      decision.value = nearby.text;
      decision.source = nearby.source;
      decision.touched = true;
      render();
      focusField("aw-field-name");
    },
    "field-ai": async function () {
      const field = fieldsInTask()[ws.nameCursor];
      if (!field || ws.busy) return;
      setBusy("Asking the AI for a name…");
      try {
        const count = await draftNamesWithAi([field]);
        if (!count)
          host.showError("The AI did not suggest a name for this field.");
      } catch (error) {
        host.showError("AI drafting failed: " + (error.message || error));
      } finally {
        setBusy("");
        render();
        focusField("aw-field-name");
      }
    },
    "fields-draft-all": function () {
      const count = draftNamesFromSuggestions(attentionFields());
      announce(
        count
          ? "Filled in " +
              plural(count, "suggested name") +
              ". Confirm each one."
          : "There were no suggestions to use.",
      );
      render();
    },
    "fields-write": writeFieldNames,
    "fields-show-all": function () {
      ws.showAllFields = true;
      render();
    },
    "fields-done": function () {
      markReviewed("fields");
      goToNextTask();
    },
    "tab-up": function (target) {
      moveTab(target.dataset.name, -1);
      render();
    },
    "tab-down": function (target) {
      moveTab(target.dataset.name, 1);
      render();
    },
    "tab-rows": function () {
      applyTabHeuristic("rows");
      render();
    },
    "tab-columns": function () {
      applyTabHeuristic("columns");
      render();
    },
    "tab-listen": function () {
      const lines = ws.tabOrder
        .map(function (name) {
          return ws.fieldsByName.get(name);
        })
        .filter(function (field) {
          return field && field.box && field.pageIndex === ws.page;
        })
        .map(function (field) {
          return spokenAnnouncement(
            { kind: "field", text: currentName(field.name), name: field.name },
            ws.fieldsByName,
          );
        });
      speakLines(lines);
    },
    "tab-confirm": confirmTabPage,
    "block-select": function (target) {
      ws.selectedBlock = target.dataset.block;
      render();
    },
    "block-up": function (target) {
      moveBlock(target.dataset.block, -1);
      render();
    },
    "block-down": function (target) {
      moveBlock(target.dataset.block, 1);
      render();
    },
    "order-listen-now": function () {
      speakLines(
        announcementsOnPage(ws.page).map(function (item) {
          return spokenAnnouncement(item, ws.fieldsByName);
        }),
      );
    },
    "order-listen-mine": function () {
      speakLines(
        (ws.blockOrder[ws.page] || [])
          .map(blockById)
          .filter(function (block) {
            return block && blockRole(block) !== "Artifact";
          })
          .map(function (block) {
            return spokenAnnouncement({
              kind: "text",
              role: blockRole(block),
              text: block.text,
            });
          }),
      );
    },
    "order-confirm": confirmOrderPage,
    "heading-show": function (target) {
      const candidate = headingCandidates().find(function (item) {
        return item.candidateId === target.dataset.candidate;
      });
      if (candidate) ws.page = Number(candidate.pageIndex);
      render();
    },
    "headings-ai": async function () {
      if (ws.busy) return;
      setBusy("Asking the AI about heading levels…");
      try {
        const count = await draftHeadingsWithAi();
        announce("The AI drafted " + plural(count, "heading level") + ".");
      } catch (error) {
        host.showError("AI review failed: " + (error.message || error));
      } finally {
        setBusy("");
        render();
      }
    },
    "headings-confirm": confirmHeadings,
    "image-pick": function (target) {
      ws.imageCursor = Number(target.dataset.index);
      render();
    },
    "image-confirm": confirmImage,
    "image-skip": function () {
      const count = imageList().length;
      ws.imageCursor = (ws.imageCursor + 1) % Math.max(1, count);
      render();
    },
    "image-ai": async function () {
      const image = imageList()[ws.imageCursor];
      if (!image || ws.busy) return;
      setBusy("Asking the AI what this image shows…");
      try {
        const count = await draftImagesWithAi([image.assetId]);
        if (!count) host.showError("The AI could not describe this image.");
      } catch (error) {
        host.showError("AI drafting failed: " + (error.message || error));
      } finally {
        setBusy("");
        render();
      }
    },
    "images-write": writeImages,
    "link-confirm": function (target) {
      const decision = ws.links[target.dataset.key];
      if (!decision) return;
      if (!String(decision.contents || "").trim()) {
        host.showError("Describe the link first.");
        return;
      }
      decision.confirmed = true;
      render();
    },
    "table-confirm": function (target) {
      const decision = ws.tables[target.dataset.path];
      if (decision) decision.confirmed = true;
      render();
    },
    "links-write": writeLinks,
    "run-ocr": function () {
      const language = String((ws.meta && ws.meta.language) || "en").slice(
        0,
        2,
      );
      const tesseract =
        { en: "eng", es: "spa", fr: "fra", pt: "por", de: "deu" }[language] ||
        "eng";
      applyChange(
        "Text recognition",
        "",
        [
          async function (bytes) {
            return (await remediate("ocr", { language: tesseract }, bytes))
              .bytes;
          },
        ].concat(ws.preexistingTree ? [] : rebuildSteps()),
      );
    },
    "load-font-subs": async function () {
      setBusy("Looking for fonts with matching widths…");
      try {
        ws.fontSubs = await postPdf(
          "/pdf-labeler/api/accessibility-font-substitutes",
          ws.working,
          {},
        );
      } catch (error) {
        host.showError(error.message || String(error));
      } finally {
        setBusy("");
        render();
      }
    },
    "apply-font-subs": function () {
      const decisions = Object.keys(ws.glyphs)
        .filter(function (key) {
          return key.indexOf("sub:") === 0 && ws.glyphs[key];
        })
        .map(function (key) {
          return { resource: key.slice(4), path: ws.glyphs[key] };
        });
      if (!decisions.length) {
        host.showError("Choose a substitute first.");
        return;
      }
      applyChange("Font substitution", "", [
        async function (bytes) {
          return (
            await remediate("substitute_fonts", { decisions: decisions }, bytes)
          ).bytes;
        },
      ]).then(function () {
        ws.fontSubs = null;
      });
    },
    "load-font-review": async function () {
      setBusy("Reading glyph outlines…");
      try {
        ws.fontReview = await postPdf(
          "/pdf-labeler/api/accessibility-font-review",
          ws.working,
          {},
        );
      } catch (error) {
        host.showError(error.message || String(error));
      } finally {
        setBusy("");
        render();
      }
    },
    "glyph-map": function (target) {
      const resource = target.dataset.resource;
      const font = ((ws.fontReview && ws.fontReview.fonts) || []).find(
        function (item) {
          return item.resource === resource;
        },
      );
      if (!font) return;
      const mappings = {};
      (font.glyphs || []).forEach(function (glyph) {
        const key = resource + ":" + glyph.code;
        const value =
          ws.glyphs[key] !== undefined
            ? ws.glyphs[key]
            : glyph.proposal
              ? glyph.proposal.character
              : "";
        if (value) mappings[String(glyph.code)] = value;
      });
      if (!Object.keys(mappings).length) {
        host.showError("Type the character each glyph stands for.");
        return;
      }
      applyChange("Glyph mappings", "", [
        async function (bytes) {
          return (
            await remediate(
              "unicode_map",
              {
                decisions: [
                  { resource: resource, action: "map", mappings: mappings },
                ],
              },
              bytes,
            )
          ).bytes;
        },
      ]).then(function () {
        ws.fontReview = null;
      });
    },
    "glyph-artifact": function (target) {
      const resource = target.dataset.resource;
      applyChange("Decorative font", "", [
        async function (bytes) {
          return (
            await remediate(
              "unicode_map",
              { decisions: [{ resource: resource, action: "artifact" }] },
              bytes,
            )
          ).bytes;
        },
      ]).then(function () {
        ws.fontReview = null;
      });
    },
    "text-fix-confirm": function (target) {
      const fix = ws.textFixes[target.dataset.key];
      if (!fix || !String(fix.actualText || "").trim()) {
        host.showError("Type what the text should say.");
        return;
      }
      fix.confirmed = true;
      render();
    },
    "text-fixes-write": function () {
      const fixes = textFixPayload();
      if (!fixes.length) {
        host.showError("Confirm at least one correction first.");
        return;
      }
      applyChange("Text corrections", "", [
        async function (bytes) {
          return (await remediate("readback_text", { decisions: fixes }, bytes))
            .bytes;
        },
      ]);
    },
    "text-done": function () {
      markReviewed("text");
      goToNextTask();
    },
    "replay-mode": function (target) {
      stopSpeaking();
      ws.replay.mode = target.dataset.mode;
      ws.replay.index = 0;
      render();
    },
    "replay-prev": function () {
      stopSpeaking();
      replayStep(-1);
    },
    "replay-next": function () {
      stopSpeaking();
      replayStep(1);
    },
    "replay-play": toggleReplay,
    "replay-go": function (target) {
      stopSpeaking();
      ws.replay.index = Number(target.dataset.index);
      render();
    },
    "ai-review": runAiReview,
    "ai-open": function (target) {
      const note = (ws.aiNotes || []).find(function (item) {
        return item.id === target.dataset.note;
      });
      if (note) openAiNote(note);
    },
    "ai-dismiss": function (target) {
      ws.aiDismissed.add(target.dataset.note);
      render();
    },
    export: function () {
      exportPdf(ws.working, "accessible");
    },
    "external-record": function (target) {
      ws.recording = target.dataset.test;
      render();
    },
    "external-done": function () {
      ws.recording = "";
      render();
    },
    declare: declareConformance,
  };

  root.addEventListener("click", function (event) {
    const mark = event.target.closest("[data-aw-mark]");
    if (mark) {
      handleMarkClick(mark.dataset.awMark);
      return;
    }
    const target = event.target.closest("[data-aw]");
    if (!target || !root.contains(target) || target.disabled) return;
    const handler = clickHandlers[target.dataset.aw];
    if (!handler) return;
    event.preventDefault();
    Promise.resolve(handler(target)).catch(function (error) {
      host.showError(error.message || String(error));
    });
  });

  function handleMarkClick(value) {
    const separator = value.indexOf(":");
    const kind = value.slice(0, separator);
    const id = value.slice(separator + 1);
    if (kind === "field") {
      const list = fieldsInTask();
      let index = list.findIndex(function (field) {
        return field.name === id;
      });
      if (index === -1) {
        ws.showAllFields = true;
        index = fieldsInTask().findIndex(function (field) {
          return field.name === id;
        });
      }
      if (index >= 0) ws.nameCursor = index;
      render();
      focusField("aw-field-name");
    } else if (kind === "block") {
      ws.selectedBlock = id;
      render();
    }
  }

  const inputHandlers = {
    "draft-ai": function (target) {
      ws.draftWithAi = !!target.checked;
    },
    "meta-title": function (target) {
      ws.meta.title = target.value;
      const preview = els.main.querySelector("[data-aw-ref=title-preview]");
      if (preview) preview.textContent = target.value || "(the file name)";
    },
    "meta-language": function (target) {
      ws.meta.language = target.value;
      ws.meta.languageGuessed = false;
    },
    "meta-author": function (target) {
      ws.meta.author = target.value;
    },
    "meta-subject": function (target) {
      ws.meta.subject = target.value;
    },
    "field-name": function (target) {
      const field = fieldsInTask()[ws.nameCursor];
      if (!field) return;
      const decision = ws.names[field.name];
      decision.value = target.value;
      decision.source = "person";
      decision.confirmed = false;
      decision.touched = true;
      const preview = els.main.querySelector("[data-aw-ref=field-preview]");
      if (preview)
        preview.textContent =
          "“" +
          spokenAnnouncement(
            { kind: "field", text: target.value, name: field.name },
            ws.fieldsByName,
          ) +
          "”";
    },
    "fields-show-all": function (target) {
      ws.showAllFields = !!target.checked;
      ws.nameCursor = 0;
      render();
    },
    "block-role": function (target) {
      const block = blockById(target.dataset.block);
      if (!block) return;
      setBlockRole(block, target.value);
      ws.selectedBlock = block.blockId;
      ws.orderEditedPages.add(Number(block.pageIndex));
      ws.orderConfirmedPages.delete(Number(block.pageIndex));
      render();
    },
    "heading-level": function (target) {
      const decision = ws.headings[target.dataset.candidate];
      if (!decision) return;
      decision.tag = target.value;
      decision.status = target.value === "P" ? "rejected" : "approved";
      decision.source = "person";
      render();
    },
    "tree-decision": function (target) {
      ws.treeDecision = target.value;
      render();
    },
    "image-kind": function (target) {
      const image = imageList()[ws.imageCursor];
      const decision = ws.images[image.assetId];
      decision.decision = target.value;
      decision.confirmed = false;
      if (decision.source !== "ai") decision.source = "person";
      render();
    },
    "image-alt": function (target) {
      const image = imageList()[ws.imageCursor];
      const decision = ws.images[image.assetId];
      decision.altText = target.value;
      decision.confirmed = false;
    },
    "image-verified": function (target) {
      const image = imageList()[ws.imageCursor];
      ws.images[image.assetId].verified = !!target.checked;
    },
    "link-contents": function (target) {
      const decision = ws.links[target.dataset.key];
      if (!decision) return;
      decision.contents = target.value;
      decision.confirmed = false;
    },
    "table-header": function (target) {
      const decision = ws.tables[target.dataset.path];
      if (!decision) return;
      decision.headerRow = !!target.checked;
      decision.confirmed = false;
    },
    "font-sub": function (target) {
      ws.glyphs["sub:" + target.dataset.resource] = target.value;
    },
    glyph: function (target) {
      ws.glyphs[target.dataset.key] = target.value;
    },
    "text-fix": function (target) {
      const fix = ws.textFixes[target.dataset.key];
      if (!fix) return;
      fix.actualText = target.value;
      fix.confirmed = false;
    },
    "heard-all": function (target) {
      ws.replay.heardAll = !!target.checked;
    },
    "external-result": function (target) {
      const record = ws.external[target.dataset.test] || {};
      record.result = target.value;
      ws.external[target.dataset.test] = record;
    },
    "external-notes": function (target) {
      const record = ws.external[target.dataset.test] || {};
      record.notes = target.value;
      ws.external[target.dataset.test] = record;
    },
    declare: function (target) {
      ws.declare = !!target.checked;
      render();
    },
  };

  function handleInput(event) {
    const target = event.target.closest("[data-aw-input]");
    if (!target) return;
    const handler = inputHandlers[target.dataset.awInput];
    if (!handler) return;
    // Text boxes update as you type; choices re-render on change.
    const isText =
      target.tagName === "TEXTAREA" ||
      (target.tagName === "INPUT" && /^(text|search)$/.test(target.type));
    if ((event.type === "input") !== isText) return;
    handler(target);
  }

  root.addEventListener("input", handleInput);
  root.addEventListener("change", handleInput);

  root.addEventListener("pointerdown", function (event) {
    const divider = event.target.closest(".aw-divider");
    if (!divider || event.button !== 0) return;
    event.preventDefault();
    divider.focus();
    panelDrag = {
      x: event.clientX,
      width: Number(divider.getAttribute("aria-valuenow")),
      pointerId: event.pointerId,
    };
    divider.setPointerCapture(event.pointerId);
    root.classList.add("is-resizing");
  });
  root.addEventListener("pointermove", function (event) {
    if (!panelDrag || panelDrag.pointerId !== event.pointerId) return;
    const bounds = panelBounds();
    ws.inspectorWidth = Math.max(
      bounds.min,
      Math.min(bounds.max, panelDrag.width + panelDrag.x - event.clientX),
    );
    syncPreviewLayout();
  });
  function endPanelDrag() {
    panelDrag = null;
    root.classList.remove("is-resizing");
  }
  root.addEventListener("pointerup", endPanelDrag);
  root.addEventListener("pointercancel", endPanelDrag);
  root.addEventListener("lostpointercapture", endPanelDrag);

  root.addEventListener("keydown", function (event) {
    const divider = event.target.closest(".aw-divider");
    if (
      divider &&
      ["ArrowLeft", "ArrowRight", "Home", "End", "Enter"].includes(event.key)
    ) {
      event.preventDefault();
      const bounds = panelBounds();
      const current = Number(divider.getAttribute("aria-valuenow"));
      const values = {
        ArrowLeft: current + 20,
        ArrowRight: current - 20,
        Home: bounds.min,
        End: bounds.max,
      };
      ws.inspectorWidth =
        event.key === "Enter"
          ? null
          : Math.max(bounds.min, Math.min(bounds.max, values[event.key]));
      syncPreviewLayout();
      return;
    }
    if (event.key === "Escape" && ws.replay.playing) {
      stopSpeaking();
      render();
      return;
    }
    if (
      event.key === "Enter" &&
      event.target &&
      event.target.dataset &&
      event.target.dataset.awInput === "field-name"
    ) {
      event.preventDefault();
      confirmFieldName();
    }
  });

  return {
    open: open,
    close: close,
    isOpen: function () {
      return ws.open;
    },
  };
}
