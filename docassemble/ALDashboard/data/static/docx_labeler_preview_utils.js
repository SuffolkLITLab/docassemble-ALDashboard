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

    function extractLabelsFromRuns(runs, createIdFn) {
        var labels = [];
        var pattern = /\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/g;
        var nextId = typeof createIdFn === 'function'
            ? createIdFn
            : function() {
                return 'label-' + Math.random().toString(36).slice(2);
            };
        var paragraphs = {};

        (runs || []).forEach(function(run) {
            var paragraph = Number(run[0]);
            if (!paragraphs[paragraph]) paragraphs[paragraph] = [];
            paragraphs[paragraph].push(run);
        });

        Object.keys(paragraphs).sort(function(a, b) {
            return Number(a) - Number(b);
        }).forEach(function(paragraphKey) {
            var paragraph = Number(paragraphKey);
            var fullText = '';
            var entries = [];
            paragraphs[paragraph].slice().sort(function(a, b) {
                return Number(a[1]) - Number(b[1]);
            }).forEach(function(run) {
                var text = String(run[2] || '');
                entries.push({
                    paragraph: paragraph,
                    run: Number(run[1]),
                    text: text,
                    start: fullText.length,
                    end: fullText.length + text.length,
                });
                fullText += text;
            });

            pattern.lastIndex = 0;
            var match;
            while ((match = pattern.exec(fullText)) !== null) {
                var labelText = match[0];
                var labelStart = match.index;
                var labelEnd = labelStart + labelText.length;
                var overlapping = entries.filter(function(entry) {
                    return entry.end > labelStart && entry.start < labelEnd;
                });
                if (!overlapping.length) continue;

                var label = {
                    id: nextId(),
                    original: labelText,
                    current: labelText,
                    isControl: labelText.startsWith('{%'),
                };

                if (overlapping.length === 1) {
                    label.paragraph = paragraph;
                    label.run = overlapping[0].run;
                    label.start = labelStart - overlapping[0].start;
                    label.end = labelEnd - overlapping[0].start;
                } else {
                    label.paragraph = paragraph;
                    label.segments = overlapping.map(function(entry) {
                        return {
                            paragraph: paragraph,
                            run: entry.run,
                            start: Math.max(0, labelStart - entry.start),
                            end: Math.min(entry.text.length, labelEnd - entry.start),
                        };
                    });
                }

                labels.push(label);
            }
        });

        return labels;
    }

    function getOccurrenceIndex(text, original, start) {
        if (typeof text !== 'string' || typeof original !== 'string' || !original) {
            return 0;
        }
        var occurrence = 0;
        var searchFrom = 0;
        var foundAt;
        while ((foundAt = text.indexOf(original, searchFrom)) !== -1) {
            if (foundAt === start) return occurrence;
            occurrence += 1;
            searchFrom = foundAt + Math.max(original.length, 1);
        }
        return 0;
    }

    function applyRunPatchEdits(baseText, edits) {
        var output = String(baseText || '');
        (edits || []).slice().sort(function(a, b) {
            if (a.start !== b.start) return b.start - a.start;
            return b.end - a.end;
        }).forEach(function(edit) {
            if (!edit || typeof edit.replacement !== 'string') return;
            var start = typeof edit.start === 'number' ? edit.start : null;
            var end = typeof edit.end === 'number' ? edit.end : null;

            if (start !== null && end !== null && start >= 0 && end >= start && end <= output.length) {
                var currentSlice = output.substring(start, end);
                if (typeof edit.original === 'string' && currentSlice === edit.original) {
                    output = output.substring(0, start) + edit.replacement + output.substring(end);
                    return;
                }
            }

            if (typeof edit.original !== 'string' || !edit.original) return;

            var desiredOccurrence = typeof edit.occurrenceIndex === 'number' ? edit.occurrenceIndex : 0;
            var searchFrom = 0;
            var foundAt = -1;
            var seen = 0;
            while ((foundAt = output.indexOf(edit.original, searchFrom)) !== -1) {
                if (seen === desiredOccurrence) break;
                seen += 1;
                searchFrom = foundAt + Math.max(edit.original.length, 1);
            }
            if (foundAt === -1) return;
            output = output.substring(0, foundAt) + edit.replacement + output.substring(foundAt + edit.original.length);
        });
        return output;
    }

    function buildRunPatchLabelsFromExistingEdits(existingLabels, runs) {
        var runByKey = {};
        (runs || []).forEach(function(run) {
            runByKey[run[0] + ',' + run[1]] = String(run[2] || '');
        });

        var editedByRun = {};
        function pushEdit(runKey, edit) {
            if (!editedByRun[runKey]) editedByRun[runKey] = [];
            editedByRun[runKey].push(edit);
        }

        (existingLabels || []).forEach(function(label) {
            if (!label || label.current === label.original) return;
            if (Array.isArray(label.segments) && label.segments.length > 0) {
                label.segments.forEach(function(segment, segmentIndex) {
                    if (typeof segment.paragraph !== 'number' || typeof segment.run !== 'number') return;
                    if (typeof segment.start !== 'number' || typeof segment.end !== 'number') return;
                    var segmentKey = segment.paragraph + ',' + segment.run;
                    var runText = runByKey[segmentKey];
                    if (typeof runText !== 'string') return;
                    var originalSlice = runText.substring(segment.start, segment.end);
                    pushEdit(segmentKey, {
                        paragraph: segment.paragraph,
                        run: segment.run,
                        start: segment.start,
                        end: segment.end,
                        original: originalSlice,
                        replacement: segmentIndex === 0 ? String(label.current || '') : '',
                        occurrenceIndex: getOccurrenceIndex(runText, originalSlice, segment.start),
                    });
                });
                return;
            }
            if (typeof label.paragraph !== 'number' || typeof label.run !== 'number') return;
            if (typeof label.start !== 'number' || typeof label.end !== 'number') return;
            var key = label.paragraph + ',' + label.run;
            var originalRunText = runByKey[key];
            if (typeof originalRunText !== 'string') return;
            var originalSlice = originalRunText.substring(label.start, label.end);
            pushEdit(key, {
                paragraph: label.paragraph,
                run: label.run,
                start: label.start,
                end: label.end,
                original: originalSlice,
                replacement: String(label.current || ''),
                occurrenceIndex: getOccurrenceIndex(originalRunText, originalSlice, label.start),
            });
        });

        var patches = [];
        Object.keys(editedByRun).forEach(function(key) {
            var edits = editedByRun[key].slice();
            var originalRunText = runByKey[key];
            if (typeof originalRunText !== 'string') return;
            var patched = applyRunPatchEdits(originalRunText, edits);
            var first = edits[0];
            patches.push({
                paragraph: first.paragraph,
                run: first.run,
                text: patched,
                new_paragraph: 0,
                _edits: edits,
            });
        });

        return patches;
    }

    function buildAcceptedRunPatchLookup(runs, existingLabels, suggestions) {
        var acceptedByKey = {};

        (suggestions || []).forEach(function(suggestion) {
            if (!suggestion || suggestion.status !== 'accepted') return;
            var newParagraph = suggestion.new_paragraph || 0;
            var key = suggestion.paragraph + ',' + suggestion.run + ',' + newParagraph;
            acceptedByKey[key] = {
                paragraph: suggestion.paragraph,
                run: suggestion.run,
                text: String(suggestion.text || ''),
                new_paragraph: newParagraph,
            };
        });

        buildRunPatchLabelsFromExistingEdits(existingLabels, runs).forEach(function(patch) {
            var key = patch.paragraph + ',' + patch.run + ',0';
            if (acceptedByKey[key] && Array.isArray(patch._edits)) {
                acceptedByKey[key].text = applyRunPatchEdits(
                    acceptedByKey[key].text,
                    patch._edits
                );
                return;
            }
            if (!acceptedByKey[key]) {
                acceptedByKey[key] = {
                    paragraph: patch.paragraph,
                    run: patch.run,
                    text: String(patch.text || ''),
                    new_paragraph: patch.new_paragraph || 0,
                };
            }
        });

        return acceptedByKey;
    }

    return {
        applyExistingLabelHighlightsByOccurrence: applyExistingLabelHighlightsByOccurrence,
        applyRunPatchEdits: applyRunPatchEdits,
        extractLabelsFromRuns: extractLabelsFromRuns,
        shouldSuppressSelectionPopoverFromTarget: shouldSuppressSelectionPopoverFromTarget,
        formatManualWrapPreviewDisplay: formatManualWrapPreviewDisplay,
        buildRunPatchLabelsFromExistingEdits: buildRunPatchLabelsFromExistingEdits,
        buildAcceptedRunPatchLookup: buildAcceptedRunPatchLookup,
        normalizeReplaceAllFlag: normalizeReplaceAllFlag
    };
}));
