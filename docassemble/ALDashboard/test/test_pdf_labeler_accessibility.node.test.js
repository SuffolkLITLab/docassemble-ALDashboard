const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../data/static/pdf_labeler.js'), 'utf8');
const inspection = source.slice(source.indexOf('function remapAccessibilityDecisions('), source.indexOf('\nfunction buildAccessibilityPayload('));

test('font repair preserves reviewed headings and blocks when extraction coordinates change', async () => {
  const old = {candidateId: 'p1:295:43', blockId: 'old', text: '1. Party Information', pageIndex: 0, box: {x: 0.047, y: 0.248}};
  const fresh = {...old, candidateId: 'p1:292:43', blockId: 'new', box: {...old.box, y: 0.246}};
  const decision = {status: 'approved', tag: 'H2', source: 'manual'};
  const blockDecision = {role: 'H2', reviewed: true, order: 2};
  const state = {pdfBytes: [1], fields: [], accessibility: {
    metadata: {}, fieldOrder: [], headingCandidates: [old], contentBlocks: [old],
    headingDecisions: {[old.candidateId]: decision}, contentDecisions: {old: blockDecision},
  }};
  const context = vm.createContext({
    state, FormData: class { append() {} }, getPdfFileForRequests() {}, apiUrl: x => x,
    fetch: async () => ({}), parseApiResponse: async () => ({success: true, data: {
      heading_candidates: [fresh], content_blocks: [fresh],
    }}), contentDecision() {}, headingDecision() {}, refreshAccessibilityFromFields() {},
  });
  vm.runInContext(inspection, context);
  await context.inspectAccessibilityData(true, {preserveAccessibilityDrafts: true});
  assert.deepEqual(state.accessibility.headingDecisions[fresh.candidateId], decision);
  assert.deepEqual(state.accessibility.contentDecisions.new, blockDecision);
  assert.equal(state.accessibility.headingDecisions[old.candidateId], undefined);
  // A repeated label must never inherit another occurrence's approval.
  const ambiguous = context.remapAccessibilityDecisions([old, {...old, candidateId: 'other'}], [fresh], {[old.candidateId]: decision}, 'candidateId');
  assert.equal(Object.keys(ambiguous).length, 0);
});

test('auto-fix sends approvals for the post-font-repair candidates to tag creation', async () => {
  const stop = new Error('tag creation reached');
  const state = {pdfBytes: [1], auth: {aiEnabled: true}, fields: [], accessibility: {
    metadata: {}, images: [], headingCandidates: [{candidateId: 'before-fonts'}], headingDecisions: {},
  }};
  let sent;
  const context = vm.createContext({
    state, updateAccessibilityMetadataFromInputs() {}, showLoading() {},
    draftTooltipsFromNearbyText: () => 0, applyDeterministicFieldOrder() {},
    applyAiHeadingDraft: async () => {
      const id = state.accessibility.headingCandidates[0].candidateId;
      state.accessibility.headingDecisions[id] = {status: 'approved', tag: 'H1'};
      return 1;
    },
    headingReviewSignature: () => '', updateHeadingReviewDirty() {}, renderHeadingReviewStatus() {},
    runAiAccessibilityReview: async () => [], draftMissingDocumentLanguage: () => '',
    draftFilenameLikeDocumentTitle: () => '', accessibilityTooltipPayload: () => ({}),
    accessibilityFieldOrderPayload: () => [], accessibilityContentDecisionPayload: () => [],
    accessibilityHeadingDecisionPayload: () => Object.entries(state.accessibility.headingDecisions).map(([candidateId, decision]) => ({candidateId, ...decision})),
    runAccessibilityRemediation: async (action, options) => {
      if (action === 'fonts') {
        state.accessibility.headingCandidates = [{candidateId: 'after-fonts'}];
        state.accessibility.headingDecisions = {};
      }
      if (action === 'draft_structure') { sent = options.heading_decisions; throw stop; }
      return {};
    },
  });
  const start = source.indexOf('async function persistAccessibilityAutoFixDrafts(');
  const end = source.indexOf('\n}', source.indexOf('async function runAccessibilityAutoFix()')) + 2;
  vm.runInContext(source.slice(start, end), context);
  await assert.rejects(context.runAccessibilityAutoFix(), error => error === stop);
  assert.deepEqual(sent, [{candidateId: 'after-fonts', status: 'approved', tag: 'H1'}]);
});

for (const preserve of [false, true]) {
  test(`inspection preserves workshop drafts: ${preserve}`, async () => {
    const state = {
      pdfBytes: [1],
      fields: [{id: 'a', name: 'first', tooltip: 'Pending edit'}, {id: 'b', name: 'second', tooltip: ''}],
      accessibility: {
        metadata: {}, fieldOrder: ['b', 'a'], contentDecisions: {}, headingDecisions: {},
        images: [{assetId: 'p1:Im1', altText: 'Pending image edit', decorative: true}],
      },
    };
    const context = vm.createContext({
      state, FormData: class { append() {} }, getPdfFileForRequests() {},
      apiUrl: x => x, fetch: async () => ({}),
      parseApiResponse: async () => ({success: true, data: {
        fields: [
          {name: 'first', tooltip: 'Existing PDF tooltip', has_custom_tooltip: true},
          {name: 'second', tooltip: 'second', has_custom_tooltip: false},
        ],
        images: [{assetId: 'p1:Im1', altText: 'Existing PDF alt'}],
        field_order: ['first', 'second'], readback: {announcements: ['refreshed']},
      }}),
      defaultTooltipFromFieldName: x => x, contentDecision() {}, headingDecision() {},
      refreshAccessibilityFromFields() {},
    });
    vm.runInContext(inspection, context);
    await context.inspectAccessibilityData(true, {preserveAccessibilityDrafts: preserve});
    assert.equal(state.accessibility.images[0].altText, preserve ? 'Pending image edit' : 'Existing PDF alt');
    assert.equal(state.accessibility.images[0].decorative, preserve);
    assert.equal(state.fields[0].tooltip, preserve ? 'Pending edit' : 'Existing PDF tooltip');
    assert.equal(state.fields[1].tooltip, 'second');
    assert.deepEqual(Array.from(state.accessibility.fieldOrder), preserve ? ['b', 'a'] : ['a', 'b']);
    assert.equal(state.accessibility.readback.announcements[0], 'refreshed');
  });
}

for (const preserve of [false, true]) {
  test(`loading PDF resets document-specific certification state: ${preserve}`, () => {
    const state = {
      fileName: 'old.pdf',
      accessibility: {marked: true, readback: {announcements: ['old document']},
        headingCandidates: ['heading'], contentBlocks: ['block']},
    };
    const context = vm.createContext({
      state,
      File: class {},
      clonePdfBytes: value => value,
      hideAccessibilityAutoFixStatus() {},
    });
    const sync = source.slice(
      source.indexOf('function updateRequestPdfFile('),
      source.indexOf('\nasync function refreshPdfDocumentFromState('),
    );
    vm.runInContext(sync, context);
    context.syncPdfState([2], 'new.pdf', undefined, {
      preserveAccessibilityDrafts: preserve,
    });
    assert.equal(state.accessibility.marked, preserve);
    assert.equal(state.accessibility.headingCandidates.length, preserve ? 1 : 0);
    assert.equal(state.accessibility.contentBlocks.length, preserve ? 1 : 0);
    assert.equal(
      state.accessibility.readback && state.accessibility.readback.announcements[0],
      preserve ? 'old document' : null,
    );
  });
}

test('remediation inspection requests draft preservation', () => {
  const remediation = source.slice(source.indexOf('async function runAccessibilityRemediation('));
  assert.match(remediation, /await inspectAccessibilityData\(true, \{ preserveAccessibilityDrafts: true \}\)/);
  assert.ok(remediation.indexOf('mergeRemediatedTooltips(result)') < remediation.indexOf('await refreshPdfDocumentFromState()'));
});

test('repaired tooltips survive export while unrelated drafts stay intact', () => {
  const state = {
    fields: [
      {id: 'a', name: 'signature', pageIndex: 0, tooltip: 'Old signature'},
      {id: 'b', name: 'signature', pageIndex: 0, tooltip: 'Pending same-name widget'},
      {id: 'c', name: 'address', pageIndex: 0, tooltip: 'Pending address'},
    ],
    accessibility: {
      enabled: true, fieldOrder: ['c', 'a', 'b'], images: [], metadata: {},
      readback: {announcements: [
        {index: 4, kind: 'field', name: 'signature', page: 0},
        {index: 9, kind: 'field', name: 'signature', page: 0},
      ]},
    },
  };
  const context = vm.createContext({state, defaultTooltipFromFieldName: x => x});
  vm.runInContext(source.slice(source.indexOf('function mergeRemediatedTooltips('), source.indexOf('async function runAccessibilityRemediation(')), context);
  vm.runInContext(source.slice(source.indexOf('function buildAccessibilityPayload('), source.indexOf('function invalidateBulkRenamePreview(')), context);
  context.mergeRemediatedTooltips({tooltip_updates: [{fieldName: 'signature', page: 0, announcedIndex: 9, tooltip: 'Co-applicant signature'}]});
  const exported = context.buildAccessibilityPayload(new Map([['a', 'signature'], ['b', 'signature__1'], ['c', 'address']]));
  assert.equal(exported.field_tooltips.signature, 'Old signature');
  assert.equal(exported.field_tooltips.signature__1, 'Co-applicant signature');
  assert.equal(exported.field_tooltips.address, 'Pending address');
  assert.deepEqual(Array.from(exported.field_order), ['address', 'signature', 'signature__1']);
});

test('browser tooltip defaults mirror camel-case server defaults', () => {
  const context = vm.createContext({});
  vm.runInContext(source.slice(source.indexOf('function defaultTooltipFromFieldName('), source.indexOf('\nfunction sortedFieldIdsByDefaultOrder(')), context);
  assert.equal(context.defaultTooltipFromFieldName('HadFelonyYes'), 'Had Felony Yes');
  assert.equal(context.defaultTooltipFromFieldName('SSNNumber_otherValue'), 'SSN Number other Value');
});

function loadFunction(context, name, async = false) {
  const start = source.indexOf(`${async ? 'async ' : ''}function ${name}(`);
  const end = source.indexOf('\n}', start) + 2;
  vm.runInContext(source.slice(start, end), context);
}

for (const existingTree of [false, true]) {
  test(`one auto-fix run persists every supported proposal and refreshes readback (existing tree: ${existingTree})`, async () => {
    const calls = [];
    let stored = {};
    let reviews = 0;
    const state = {pdfBytes: [1], auth: {aiEnabled: true}, fields: [{id: 'f', tooltip: 'Old'}], accessibility: {
      metadata: {title: 'Old', language: 'en'}, images: [{assetId: 'p1:Im1', altText: ''}],
      headingCandidates: [{candidateId: 'h'}], headingDecisions: {}, fieldOrder: ['f'],
      tagStructure: {present: existingTree}, aiReview: {findings: []},
      readback: {findings: []}, report: {issues: []},
    }};
    const context = vm.createContext({
      state, updateAccessibilityMetadataFromInputs() {}, showLoading() {},
      draftTooltipsFromNearbyText: () => 0, applyDeterministicFieldOrder() {},
      applyAiTooltipDraft: async () => 1, applyAiHeadingDraft: async () => 1,
      headingReviewSignature: () => '', updateHeadingReviewDirty() {}, renderHeadingReviewStatus() {},
      draftMissingDocumentLanguage: () => '', draftFilenameLikeDocumentTitle: () => '',
      accessibilityTooltipPayload: () => ({field: state.fields[0].tooltip}),
      accessibilityFieldOrderPayload: () => state.accessibility.fieldOrder,
      accessibilityContentDecisionPayload: () => [], accessibilityHeadingDecisionPayload: () => [],
      setAiReviewExpanded() {}, renderAiAccessibilityReview() {}, accessibilityAiReviewSignature: () => '',
      setDirty() {}, hideToasts() {}, showAccessibilityAutoFixStatus() {}, renderAccessibilityModal() {},
      requestAiAccessibilityReview: async () => {
        reviews += 1;
        if (reviews === 2) {
          assert.equal(stored.metadata.title, 'New title');
          assert.equal(stored.image_alt_text['p1:Im1'], 'Court seal');
          assert.equal(stored.field_tooltips.field, 'New label');
          assert.deepEqual(stored.field_order, ['rtl-field']);
        }
        if (reviews === 3) {
          assert.ok(calls.some(([action]) => action === 'readback_text'));
          assert.ok(calls.some(([action]) => action === 'field_names'));
          assert.equal(state.accessibility.readback.findings.length, 0);
        }
        if (reviews === 1 || reviews === 3) return [{status: 'pending', change: {}}];
        return [];
      },
      applyAiAccessibilityFinding: finding => {
        state.accessibility.metadata.title = 'New title';
        state.accessibility.images[0].altText = 'Court seal';
        state.fields[0].tooltip = 'New label';
        state.accessibility.fieldOrder = ['rtl-field'];
        finding.status = 'accepted';
        return true;
      },
      runAccessibilityRemediation: async (action, options) => {
        calls.push([action, structuredClone(options)]);
        if (action === 'metadata') {
          stored = structuredClone(options);
          if (options.set_structure_tab_order && !calls.some(([a]) => a === 'field_names')) {
            state.accessibility.readback.findings = [
              {id: 'text', suggestion: 'Correct text', confident: true},
              {id: 'names', category: 'field-names', suggestions: [{suggested: 'Name 1'}]},
            ];
          }
        }
        if (action === 'draft_structure') {
          state.accessibility.tagStructure.present = true;
          state.accessibility.structureDrafted = true;
        }
        if (action === 'readback_text') state.accessibility.readback.findings.shift();
        if (action === 'field_names') state.accessibility.readback.findings = [];
        return {};
      },
    });
    for (const name of ['persistAccessibilityAutoFixDrafts', 'runAccessibilityAutoFix', 'runAiAccessibilityReview']) loadFunction(context, name, true);
    for (const name of ['unresolvedAiAccessibilityFindings', 'remainingAccessibilityIssues']) loadFunction(context, name);
    await context.runAccessibilityAutoFix();
    assert.equal(reviews, 4);
    assert.equal(calls.filter(([action]) => action === 'draft_structure').length, existingTree ? 0 : 1);
    assert.equal(calls.at(-1)[0], 'metadata');
    assert.equal(stored.image_alt_text['p1:Im1'], 'Court seal');
    assert.equal(stored.mark_untagged_as_artifacts, !existingTree);
    assert.equal(state.accessibility.aiReview.findings.length, 0);
    assert.ok(calls.every(([, options]) => !options.marked && !options.mark_as_tagged));
  });
}

test('failed persistence stops AI feedback before another review can use stale evidence', async () => {
  let requests = 0;
  const context = vm.createContext({
    state: {auth: {aiEnabled: true}, accessibility: {aiReview: {findings: []}}},
    setAiReviewExpanded() {}, renderAiAccessibilityReview() {},
    requestAiAccessibilityReview: async () => { requests += 1; return [{status: 'pending'}]; },
    applyAiAccessibilityFinding: () => true,
  });
  loadFunction(context, 'runAiAccessibilityReview', true);
  await assert.rejects(context.runAiAccessibilityReview({applyDrafts: true, persistDrafts: async () => { throw new Error('write failed'); }}), /write failed/);
  assert.equal(requests, 1);
  assert.equal(context.state.accessibility.aiReview.running, false);
});
