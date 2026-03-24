const test = require('node:test');
const assert = require('node:assert/strict');

const previewUtils = require('../data/static/docx_labeler_preview_utils.js');

test('per-occurrence existing-label highlight only updates changed occurrence', () => {
    const html = '<p>A {{ users[0].name.first }} and {{ users[0].name.first }}</p>';
    const existingLabels = [
        {
            id: 'first-occurrence',
            original: '{{ users[0].name.first }}',
            current: '{{ users[0].given_name }}'
        },
        {
            id: 'second-occurrence',
            original: '{{ users[0].name.first }}',
            current: '{{ users[0].name.first }}'
        }
    ];

    const result = previewUtils.applyExistingLabelHighlightsByOccurrence(html, existingLabels);

    assert.equal((result.match(/existing-inline-label/g) || []).length, 2);
    assert.match(result, /data-label-id="first-occurrence">\{\{ users\[0\]\.given_name \}\}<\/span>/);
    assert.match(result, /data-label-id="second-occurrence">\{\{ users\[0\]\.name\.first \}\}<\/span>/);
    assert.equal((result.match(/highlight-accepted/g) || []).length, 1);
    assert.equal((result.match(/highlight-existing/g) || []).length, 1);
});

test('suppresses selection popover when mouseup target is an existing inline label', () => {
    const target = {
        closest: (selector) => (selector === '.existing-inline-label' ? { tagName: 'SPAN' } : null)
    };
    assert.equal(previewUtils.shouldSuppressSelectionPopoverFromTarget(target), true);

    const nonLabelTarget = {
        closest: () => null
    };
    assert.equal(previewUtils.shouldSuppressSelectionPopoverFromTarget(nonLabelTarget), false);
});

test('suppresses selection popover for text-node-like targets inside existing labels', () => {
    const textNodeLikeTarget = {
        parentElement: {
            closest: (selector) => (selector === '.existing-inline-label' ? { tagName: 'SPAN' } : null)
        }
    };
    assert.equal(previewUtils.shouldSuppressSelectionPopoverFromTarget(textNodeLikeTarget), true);
});

test('%p if preview formatter renders wrapper and body on separate visual lines', () => {
    const result = previewUtils.formatManualWrapPreviewDisplay('ifp_wrap', 'users[0].is_active', 'Paragraph text');

    assert.equal((result.match(/dl-p-wrap-line/g) || []).length, 3);
    assert.match(result, /\{%p if users\[0\]\.is_active %\}/);
    assert.match(result, /Paragraph text/);
    assert.match(result, /\{%p endif %\}/);
});

test('{% if %} preview formatter remains inline', () => {
    const result = previewUtils.formatManualWrapPreviewDisplay('if_wrap', 'users[0].is_active', 'Inline text');

    assert.equal((result.match(/dl-p-wrap-line/g) || []).length, 0);
    assert.match(result, /\{% if users\[0\]\.is_active %\}Inline text\{% endif %\}/);
});

test('run patch builder only updates edited occurrence in labels payload', () => {
    const runs = [
        [0, 0, 'A {{ spouse_name }} and {{ spouse_name }} in one run']
    ];
    const first = '{{ spouse_name }}';
    const secondStart = runs[0][2].indexOf(first, runs[0][2].indexOf(first) + first.length);
    const labels = [
        {
            id: 'occ-1',
            original: first,
            current: '{{ spouse_full_name }}',
            paragraph: 0,
            run: 0,
            start: runs[0][2].indexOf(first),
            end: runs[0][2].indexOf(first) + first.length
        },
        {
            id: 'occ-2',
            original: first,
            current: first,
            paragraph: 0,
            run: 0,
            start: secondStart,
            end: secondStart + first.length
        }
    ];

    const patches = previewUtils.buildRunPatchLabelsFromExistingEdits(labels, runs);

    assert.equal(patches.length, 1);
    assert.equal(patches[0].paragraph, 0);
    assert.equal(patches[0].run, 0);
    assert.match(patches[0].text, /\{\{ spouse_full_name \}\}/);
    assert.equal((patches[0].text.match(/\{\{ spouse_name \}\}/g) || []).length, 1);
});

test('applyRunPatchEdits only updates targeted repeated occurrence', () => {
    const baseText = 'A {{ spouse_name }} and {{ spouse_name }} in one run';
    const firstStart = baseText.indexOf('{{ spouse_name }}');

    const merged = previewUtils.applyRunPatchEdits(baseText, [
        {
            start: firstStart,
            end: firstStart + '{{ spouse_name }}'.length,
            original: '{{ spouse_name }}',
            replacement: '{{ spouse_full_name }}',
            occurrenceIndex: 0,
        }
    ]);

    assert.match(merged, /A \{\{ spouse_full_name \}\} and \{\{ spouse_name \}\} in one run/);
});

test('extractLabelsFromRuns finds labels split across multiple runs', () => {
    const runs = [
        [0, 0, 'A {{ spouse_'],
        [0, 1, 'name }} and {{ other_name }}'],
    ];

    const labels = previewUtils.extractLabelsFromRuns(runs, () => 'generated-id');

    assert.equal(labels.length, 2);
    assert.equal(labels[0].original, '{{ spouse_name }}');
    assert.equal(Array.isArray(labels[0].segments), true);
    assert.equal(labels[0].segments.length, 2);
    assert.equal(labels[1].original, '{{ other_name }}');
    assert.equal(labels[1].run, 1);
});

test('run patch builder rewrites split label occurrence without global rename', () => {
    const runs = [
        [0, 0, 'A {{ spouse_'],
        [0, 1, 'name }} and {{ spouse_name }}'],
    ];
    const labels = previewUtils.extractLabelsFromRuns(runs, () => 'generated-id');

    labels[0].current = '{{ spouse_full_name }}';

    const patches = previewUtils.buildRunPatchLabelsFromExistingEdits(labels, runs);
    const firstRunPatch = patches.find((patch) => patch.run === 0);
    const secondRunPatch = patches.find((patch) => patch.run === 1);

    assert.equal(patches.length, 2);
    assert.match(firstRunPatch.text, /A \{\{ spouse_full_name \}\}/);
    assert.equal(secondRunPatch.text, ' and {{ spouse_name }}');
});

test('normalizeReplaceAllFlag only returns true for explicit true', () => {
    assert.equal(previewUtils.normalizeReplaceAllFlag(true), true);
    assert.equal(previewUtils.normalizeReplaceAllFlag(false), false);
    assert.equal(previewUtils.normalizeReplaceAllFlag(undefined), false);
    assert.equal(previewUtils.normalizeReplaceAllFlag({ type: 'click' }), false);
});
