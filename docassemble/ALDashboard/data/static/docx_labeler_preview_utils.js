(function(root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.DocxLabelerPreviewUtils = factory();
    }
}(typeof self !== 'undefined' ? self : this, function() {
    function defaultEscapeHtml(str) {
        return String(str || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function applyExistingLabelHighlightsByOccurrence(html, existingLabels, escapeHtmlFn) {
        var esc = escapeHtmlFn || defaultEscapeHtml;
        var output = String(html || '');
        var byOriginal = {};
        (existingLabels || []).forEach(function(label) {
            if (!label || typeof label.original !== 'string') return;
            if (!byOriginal[label.original]) byOriginal[label.original] = [];
            byOriginal[label.original].push(label);
        });
        Object.keys(byOriginal).forEach(function(original) {
            var labels = byOriginal[original];
            var encoded = esc(original);
            var offset = 0;
            labels.forEach(function(label) {
                var cls = label.current !== label.original ? 'highlight-accepted' : 'highlight-existing';
                var span = '<span class="' + cls + ' existing-inline-label" data-label-id="' + esc(label.id) + '">' + esc(label.current) + '</span>';
                var pos = output.indexOf(encoded, offset);
                if (pos === -1) return;
                output = output.substring(0, pos) + span + output.substring(pos + encoded.length);
                offset = pos + span.length;
            });
        });
        return output;
    }

    function shouldSuppressSelectionPopoverFromTarget(target) {
        if (!target) return false;
        var element = null;
        if (typeof target.closest === 'function') {
            element = target;
        } else if (target.parentElement && typeof target.parentElement.closest === 'function') {
            // Mouse events can target a text node inside the highlighted span.
            element = target.parentElement;
        }
        if (!element) return false;
        return !!element.closest('.existing-inline-label');
    }

    function normalizeReplaceAllFlag(value) {
        return value === true;
    }

    function formatManualWrapPreviewDisplay(manualKind, conditionExpression, selectedText, escapeHtmlFn) {
        var esc = escapeHtmlFn || defaultEscapeHtml;
        var condition = String(conditionExpression || '').trim();
        var content = String(selectedText || '');
        var openTag = manualKind === 'ifp_wrap'
            ? '{%p if ' + condition + ' %}'
            : '{% if ' + condition + ' %}';
        var closeTag = manualKind === 'ifp_wrap'
            ? '{%p endif %}'
            : '{% endif %}';

        if (manualKind === 'ifp_wrap') {
            return '<span class="dl-p-wrap-line">' + esc(openTag) + '</span>'
                + '<span class="dl-p-wrap-line">' + esc(content) + '</span>'
                + '<span class="dl-p-wrap-line">' + esc(closeTag) + '</span>';
        }

        return esc(openTag + content + closeTag);
    }

    function buildRunPatchLabelsFromExistingEdits(existingLabels, runs) {
        var runByKey = {};
        (runs || []).forEach(function(run) {
            runByKey[run[0] + ',' + run[1]] = String(run[2] || '');
        });

        var editedByRun = {};
        (existingLabels || []).forEach(function(label) {
            if (!label || label.current === label.original) return;
            if (typeof label.paragraph !== 'number' || typeof label.run !== 'number') return;
            if (typeof label.start !== 'number' || typeof label.end !== 'number') return;
            var key = label.paragraph + ',' + label.run;
            if (!editedByRun[key]) editedByRun[key] = [];
            editedByRun[key].push(label);
        });

        var patches = [];
        Object.keys(editedByRun).forEach(function(key) {
            var edits = editedByRun[key].slice().sort(function(a, b) {
                return b.start - a.start;
            });
            var originalRunText = runByKey[key];
            if (typeof originalRunText !== 'string') return;
            var patched = originalRunText;
            edits.forEach(function(edit) {
                patched = patched.substring(0, edit.start) + edit.current + patched.substring(edit.end);
            });
            var first = edits[0];
            patches.push({
                paragraph: first.paragraph,
                run: first.run,
                text: patched,
                new_paragraph: 0
            });
        });

        return patches;
    }

    return {
        applyExistingLabelHighlightsByOccurrence: applyExistingLabelHighlightsByOccurrence,
        shouldSuppressSelectionPopoverFromTarget: shouldSuppressSelectionPopoverFromTarget,
        formatManualWrapPreviewDisplay: formatManualWrapPreviewDisplay,
        buildRunPatchLabelsFromExistingEdits: buildRunPatchLabelsFromExistingEdits,
        normalizeReplaceAllFlag: normalizeReplaceAllFlag
    };
}));
