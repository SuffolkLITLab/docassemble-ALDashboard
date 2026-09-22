const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../data/static/pdf_labeler.js'), 'utf8');
const inspection = source.slice(source.indexOf('async function inspectAccessibilityData('), source.indexOf('\nfunction buildAccessibilityPayload('));

for (const preserve of [false, true]) {
  test(`inspection preserves workshop drafts: ${preserve}`, async () => {
    const state = {
      pdfBytes: [1],
      fields: [{id: 'a', name: 'first', tooltip: 'Pending edit'}, {id: 'b', name: 'second', tooltip: ''}],
      accessibility: {
        metadata: {}, fieldOrder: ['b', 'a'], contentDecisions: {}, headingDecisions: {},
      },
    };
    const context = vm.createContext({
      state, FormData: class { append() {} }, getPdfFileForRequests() {},
      apiUrl: x => x, fetch: async () => ({}),
      parseApiResponse: async () => ({success: true, data: {
        fields: [{name: 'first', tooltip: 'Existing PDF tooltip', has_custom_tooltip: true}],
        field_order: ['first', 'second'], readback: {announcements: ['refreshed']},
      }}),
      defaultTooltipFromFieldName: x => x, contentDecision() {}, headingDecision() {},
      refreshAccessibilityFromFields() {},
    });
    vm.runInContext(inspection, context);
    await context.inspectAccessibilityData(true, {preserveAccessibilityDrafts: preserve});
    assert.equal(state.fields[0].tooltip, preserve ? 'Pending edit' : 'Existing PDF tooltip');
    assert.deepEqual(Array.from(state.accessibility.fieldOrder), preserve ? ['b', 'a'] : ['a', 'b']);
    assert.equal(state.accessibility.readback.announcements[0], 'refreshed');
  });
}

for (const preserve of [false, true]) {
  test(`loading PDF resets document-specific certification state: ${preserve}`, () => {
    const state = {
      fileName: 'old.pdf',
      accessibility: {marked: true, readback: {announcements: ['old document']}},
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
      {id: 'b', name: 'signature', pageIndex: 1, tooltip: 'Pending on another page'},
      {id: 'c', name: 'address', pageIndex: 0, tooltip: 'Pending address'},
    ],
    accessibility: {enabled: true, fieldOrder: ['c', 'a', 'b'], images: [], metadata: {}},
  };
  const context = vm.createContext({state, defaultTooltipFromFieldName: x => x});
  vm.runInContext(source.slice(source.indexOf('function mergeRemediatedTooltips('), source.indexOf('async function runAccessibilityRemediation(')), context);
  vm.runInContext(source.slice(source.indexOf('function buildAccessibilityPayload('), source.indexOf('function invalidateBulkRenamePreview(')), context);
  context.mergeRemediatedTooltips({tooltip_updates: [{fieldName: 'signature', page: 0, tooltip: 'Applicant signature'}]});
  const exported = context.buildAccessibilityPayload(new Map([['a', 'signature'], ['b', 'signature__1'], ['c', 'address']]));
  assert.equal(exported.field_tooltips.signature, 'Applicant signature');
  assert.equal(exported.field_tooltips.signature__1, 'Pending on another page');
  assert.equal(exported.field_tooltips.address, 'Pending address');
  assert.deepEqual(Array.from(exported.field_order), ['address', 'signature', 'signature__1']);
});
