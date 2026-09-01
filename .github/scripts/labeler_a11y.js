const fs = require("node:fs");
const path = require("node:path");

const { chromium } = require("playwright");
const AxeBuilder = require("@axe-core/playwright").default;

const serverUrl = String(process.env.SERVER_URL || "").replace(/\/$/, "");
const email = process.env.EDITOR_EMAIL;
const password = process.env.EDITOR_PASSWORD;

if (!serverUrl || !email || !password) {
  throw new Error("SERVER_URL, EDITOR_EMAIL, and EDITOR_PASSWORD are required");
}

const pageErrors = [];
const blockingViolations = [];

async function signInIfNeeded(page, endpoint) {
  await page.goto(`${serverUrl}${endpoint}`, { waitUntil: "domcontentloaded" });

  if (new URL(page.url()).pathname === endpoint) return;

  const emailInput = page
    .locator('input[type="email"], input[name="email"], input[name="username"]')
    .first();
  await emailInput.fill(email);
  await page.locator('input[type="password"]').first().fill(password);
  await page
    .getByRole("button", { name: /sign in|log in/i })
    .first()
    .click();
  await page.waitForURL((url) => url.pathname === endpoint, {
    timeout: 30_000,
  });
}

// The Bootstrap theme transitions button color/background/border over
// 150ms on hover, focus and active-state changes. Scanning immediately
// after a click can catch axe mid-transition and report a transient,
// non-representative color as a contrast violation. Settling briefly
// before each scan avoids that false positive.
async function settle(page) {
  await page.waitForTimeout(250);
}

async function audit(page, label, scope) {
  await settle(page);
  const builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]);
  if (scope) builder.include(scope);
  const result = await builder.analyze();

  console.log(`${label}: ${result.violations.length} axe violation(s)`);
  for (const violation of result.violations) {
    console.log(
      JSON.stringify({
        id: violation.id,
        impact: violation.impact,
        help: violation.help,
        helpUrl: violation.helpUrl,
        targets: violation.nodes.map((node) => node.target),
      }),
    );
    blockingViolations.push({ label, violation });
  }
}

const IMPACT_ORDER = ["critical", "serious", "moderate", "minor"];

function impactRank(impact) {
  const index = IMPACT_ORDER.indexOf(String(impact));
  return index === -1 ? IMPACT_ORDER.length : index;
}

function writeReport() {
  const findings = blockingViolations.map(({ label, violation }) => ({
    label,
    id: violation.id,
    impact: violation.impact,
    help: violation.help,
    helpUrl: violation.helpUrl,
    targets: violation.nodes.map((node) => node.target),
  }));
  findings.sort(
    (a, b) =>
      impactRank(a.impact) - impactRank(b.impact) ||
      a.label.localeCompare(b.label) ||
      a.id.localeCompare(b.id),
  );

  fs.writeFileSync(
    "axe-results.json",
    JSON.stringify({ pageErrors, findings }, null, 2),
  );

  const summaryPath = process.env.GITHUB_STEP_SUMMARY;
  if (!summaryPath) return;

  const lines = ["## Flask labeler accessibility", ""];
  if (!findings.length && !pageErrors.length) {
    lines.push("No axe violations and no browser page errors.");
  }
  if (findings.length) {
    lines.push(
      `${findings.length} axe violation occurrence(s), most severe first.`,
      "",
      "| Impact | Rule | Where | Nodes |",
      "| --- | --- | --- | --- |",
    );
    for (const finding of findings) {
      lines.push(
        `| ${finding.impact || "unknown"} | [${finding.id}](${finding.helpUrl}) |` +
          ` ${finding.label} | ${finding.targets.length} |`,
      );
    }
    lines.push(
      "",
      "Full detail, including selectors, is in the `axe-results` artifact.",
    );
  }
  if (pageErrors.length) {
    lines.push("", "### Browser page errors", "");
    for (const error of pageErrors) lines.push(`- \`${error}\``);
  }
  fs.appendFileSync(summaryPath, lines.join("\n") + "\n");
}

async function auditVisibleModal(page, label, openAction, modalSelector) {
  await openAction();
  const modal = page.locator(modalSelector);
  await modal.waitFor({ state: "visible", timeout: 10_000 });
  await audit(page, label, modalSelector);
  const closeButton = modal
    .locator('.btn-close, button[id*="close"], button[id*="cancel"]')
    .first();
  await closeButton.click();
  await modal.waitFor({ state: "hidden", timeout: 10_000 });
}

async function checkDocxLabeler(page) {
  const endpoint = "/al/docx-labeler";
  await signInIfNeeded(page, endpoint);
  await audit(page, "DOCX labeler empty state");

  await auditVisibleModal(
    page,
    "DOCX labeler settings modal",
    () => page.locator("#settings-btn").click(),
    "#settings-modal",
  );
  await auditVisibleModal(
    page,
    "DOCX labeler utilities modal",
    () => page.locator("#utilities-btn").click(),
    "#utilities-modal",
  );
  await auditVisibleModal(
    page,
    "DOCX labeler repair modal",
    () => page.locator("#repair-btn").click(),
    "#repair-modal",
  );

  await page
    .locator("#file-input")
    .setInputFiles(
      path.join(process.cwd(), "docassemble/ALDashboard/test/condo_deed.docx"),
    );
  await page
    .locator("#main-panel")
    .waitFor({ state: "visible", timeout: 30_000 });
  await audit(page, "DOCX labeler loaded document");

  await page.locator("#tab-suggestions").click();
  await audit(page, "DOCX labeler suggestions tab");
  await page.locator("#tab-existing").click();

  await auditVisibleModal(
    page,
    "DOCX labeler bulk replace modal",
    () => page.locator("#bulk-replace-btn").click(),
    "#bulk-replace-modal",
  );

  // The edit-label dialog only opens from a label in the tree, so it was
  // invisible to earlier runs of this script -- and its close button was
  // missing an accessible name as a result. Audit it whenever the fixture
  // document yields at least one label.
  const firstLabel = page.locator("#existing-labels-tree .tree-item").first();
  if ((await firstLabel.count()) > 0) {
    await auditVisibleModal(
      page,
      "DOCX labeler edit label modal",
      () => firstLabel.click(),
      "#edit-label-modal",
    );
  } else {
    console.log("DOCX labeler edit label modal: no labels in fixture, skipped");
  }
}

async function checkPdfLabeler(page) {
  const endpoint = "/al/pdf-labeler";
  await signInIfNeeded(page, endpoint);
  await audit(page, "PDF labeler empty state");

  await page
    .locator("#file-input")
    .setInputFiles(
      path.join(
        process.cwd(),
        "docassemble/ALDashboard/test/civil_docketing_statement_polished_repaired.pdf",
      ),
    );
  await page
    .locator("#pdf-pages")
    .waitFor({ state: "visible", timeout: 30_000 });
  await audit(page, "PDF labeler loaded document");

  for (const [label, button, modal] of [
    ["PDF labeler settings modal", "#settings-btn", "#settings-modal"],
    [
      "PDF labeler accessibility modal",
      "#accessibility-btn",
      "#accessibility-modal",
    ],
    [
      "PDF labeler normalization modal",
      "#normalize-pass-btn",
      "#normalization-modal",
    ],
    ["PDF labeler repair modal", "#repair-btn", "#repair-modal"],
    ["PDF labeler utilities modal", "#utilities-btn", "#utilities-modal"],
    [
      "PDF labeler page manager modal",
      "#manage-pages-btn",
      "#page-manager-modal",
    ],
  ]) {
    const buttonLocator = page.locator(button);
    if ((await buttonLocator.count()) === 0) {
      throw new Error(`Expected ${button} on ${endpoint}`);
    }
    await auditVisibleModal(
      page,
      label,
      () => buttonLocator.first().click(),
      modal,
    );
  }
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1200 },
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => pageErrors.push(String(error)));

  try {
    await checkDocxLabeler(page);
    await checkPdfLabeler(page);
  } finally {
    await context.close();
    await browser.close();
  }

  writeReport();

  if (pageErrors.length) {
    console.error("Browser page errors:");
    for (const error of pageErrors) console.error(error);
    process.exitCode = 1;
  }
  if (blockingViolations.length) {
    console.error(
      `Found ${blockingViolations.length} axe violation occurrence(s).`,
    );
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error);
  // Still emit whatever was collected before the run broke, so the artifact
  // and job summary are useful when a selector or the server goes missing.
  try {
    writeReport();
  } catch {}
  process.exitCode = 1;
});
