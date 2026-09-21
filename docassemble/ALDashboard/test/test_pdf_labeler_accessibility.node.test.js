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

test('remediation inspection requests draft preservation', () => {
  const remediation = source.slice(source.indexOf('async function runAccessibilityRemediation('));
  assert.match(remediation, /await inspectAccessibilityData\(true, \{ preserveAccessibilityDrafts: true \}\)/);
});
