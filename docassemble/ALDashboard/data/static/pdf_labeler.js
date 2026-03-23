    import * as pdfjsLib from 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.min.mjs';

    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.mjs';

    function parseBootstrapJson() {
        const bootstrapEl = document.getElementById('labeler-bootstrap');
        if (!bootstrapEl) return {};
        try {
            return JSON.parse(bootstrapEl.textContent || '{}');
        } catch (_error) {
            return {};
        }
    }

    const LABELER_BOOTSTRAP = parseBootstrapJson();
    const API_BASE_PATH = LABELER_BOOTSTRAP.apiBasePath || '/al';
    const BRANDING = LABELER_BOOTSTRAP.branding || {};
    const PDF_CONFIG = LABELER_BOOTSTRAP.pdf || {};
    const PDFLibGlobal = window.PDFLib || {};
    const JSZipGlobal = window.JSZip || null;

    const FIELD_TYPES = ['text', 'multiline', 'checkbox', 'signature', 'radio', 'dropdown', 'listbox'];
    const POINT_INSERT_TYPES = new Set(['checkbox', 'radio']);
    const OPTION_TYPES = new Set(['radio', 'dropdown', 'listbox']);
    const DEFAULT_OPTION_LIST = ['Option 1', 'Option 2'];
    const FONT_OPTIONS = [
        'Helvetica',
        'Helvetica-Bold',
        'Courier',
        'Courier-Bold',
        'Times-Roman',
        'Times-Bold'
    ];

    const FIELD_TYPE_META = {
        text: {
            label: 'Single-line Text',
            shortLabel: 'Text',
            createMode: 'draw',
            icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7V4h16v3M9 20h6M12 4v16"></path></svg>',
        },
        multiline: {
            label: 'Multi-line Text',
            shortLabel: 'Multi',
            createMode: 'draw',
            icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M3 10h18M3 14h18M3 18h10"></path></svg>',
        },
        checkbox: {
            label: 'Checkbox',
            shortLabel: 'Check',
            createMode: 'point',
            defaultWidth: 0.032,
            defaultHeight: 0.032,
            icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><path d="m9 12 2 2 4-4"></path></svg>',
        },
        signature: {
            label: 'Signature',
            shortLabel: 'Sign',
            createMode: 'draw',
            icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m14.5 3.5 6 6-11 11H3v-6.5l11-11Z"></path></svg>',
        },
        radio: {
            label: 'Radio Group',
            shortLabel: 'Radio',
            createMode: 'point',
            defaultWidth: 0.032,
            defaultHeight: 0.032,
            icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="3" fill="currentColor"></circle></svg>',
        },
        dropdown: {
            label: 'Dropdown',
            shortLabel: 'Drop',
            createMode: 'draw',
            icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2" ry="2"></rect><path d="m7 10 5 5 5-5"></path></svg>',
        },
        listbox: {
            label: 'List Box',
            shortLabel: 'List',
            createMode: 'draw',
            icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2" ry="2"></rect><path d="M7 8h10M7 12h10M7 16h4"></path></svg>',
        },
    };

    const DEFAULT_FIELD_NAME_LIBRARY = {
        text: [
            'users1_name_first',
            'users1_name_last',
            'users1_name_full',
            'users1_address_address',
            'users1_address_city',
            'users1_address_state',
            'users1_address_zip',
            'users1_phone_number',
            'users1_email',
            'other_parties1_name_full',
            'docket_number',
            'case_name'
        ],
        multiline: [
            'users1_mailing_address',
            'special_instructions',
            'facts_summary'
        ],
        signature: [
            'users1_signature',
            'other_parties1_signature',
            'attorney_signature'
        ],
        checkbox: [
            'user_agrees',
            'is_plaintiff',
            'is_defendant',
            'has_children'
        ],
        dropdown: ['court_division', 'selected_claim_type'],
        listbox: ['requested_relief', 'selected_issues'],
        radio: ['service_method']
    };

    const FIELD_NAME_LIBRARY = (PDF_CONFIG.fieldNameLibrary && Object.keys(PDF_CONFIG.fieldNameLibrary).length)
        ? PDF_CONFIG.fieldNameLibrary
        : ((PDF_CONFIG.field_name_library && Object.keys(PDF_CONFIG.field_name_library).length)
            ? PDF_CONFIG.field_name_library
            : DEFAULT_FIELD_NAME_LIBRARY);

    const state = {
        fileName: '',
        pdfBytes: null,
        requestPdfFile: null,
        quickEditFieldId: null,
        quickEditFocusPending: false,
        pdfDoc: null,
        pageCount: 0,
        pageSizes: [],
        pageTextBoxes: [],
        fields: [],
        selectedFieldId: null,
        selectedTool: null,
        previewMode: false,
        renderScale: 1.35,
        viewerZoom: 1,
        defaultModel: 'gpt-5-mini',
        recommendedModels: ['gpt-5-mini'],
        availableModels: [],
        model: 'gpt-5-mini',
        hasUnsavedChanges: false,
        fieldSearch: '',
        fieldTypeFilter: '',
        fieldSort: 'position',
        fieldsPanelMode: 'filter',
        bulkRenameType: 'pattern',
        bulkRenamePattern: '',
        bulkRenameReplacement: '',
        fieldNamesVersion: 0,
        bulkRenamePreviewCache: null,
        auth: {
            isAuthenticated: false,
            email: '',
            loginUrl: '/user/sign-in',
            logoutUrl: '/user/sign-out',
            aiEnabled: false
        },
        playgroundSource: {
            project: '',
            filename: '',
        },
        interviewSourceMode: 'playground',
        playground: {
            projects: [],
            files: [],
            selectedProject: 'default',
            selectedFile: '',
            variables: [],
            topLevelNames: []
        },
        installed: {
            packages: [],
            files: [],
            selectedPackage: '',
            selectedFile: '',
            variables: [],
            topLevelNames: []
        },
        usePlaygroundVariables: false
    };

    // --- Session-persisted defaults for font, fontSize, checkboxStyle ---
    const SESSION_DEFAULTS_KEY = 'pdfLabeler_sessionDefaults';
    const HARD_DEFAULTS = { font: 'Helvetica', fontSize: 10, checkboxStyle: 'cross', checkboxExportValue: 'Yes' };

    function _loadSessionDefaults() {
        try {
            const raw = sessionStorage.getItem(SESSION_DEFAULTS_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                return Object.assign({}, HARD_DEFAULTS, parsed);
            }
        } catch (_e) { /* ignore */ }
        return Object.assign({}, HARD_DEFAULTS);
    }
    function _saveSessionDefaults(defaults) {
        try {
            sessionStorage.setItem(SESSION_DEFAULTS_KEY, JSON.stringify(defaults));
        } catch (_e) { /* ignore */ }
    }
    function getSessionDefault(key) {
        return _loadSessionDefaults()[key] || HARD_DEFAULTS[key];
    }
    function setSessionDefault(key, value) {
        const defaults = _loadSessionDefaults();
        defaults[key] = value;
        _saveSessionDefaults(defaults);
    }

    let draftState = null;
    let dragState = null;
    let resizeState = null;
    let currentVisiblePageIndex = 0;
    let fieldsListSyncRaf = null;
    let fieldsListProgrammaticScroll = false;
    let fieldsListManualScrollUntil = 0;
    let pageManagerState = null;
    let pageManagerDragPageId = null;

    const fileInput = document.getElementById('file-input');
    const pdfContainer = document.getElementById('pdf-container');
    const pdfEmpty = document.getElementById('pdf-empty');
    const pdfLoading = document.getElementById('pdf-loading');
    const loadingMessage = document.getElementById('loading-message');
    const managePagesBtn = document.getElementById('manage-pages-btn');
    const pdfZoomOutBtn = document.getElementById('pdf-zoom-out');
    const pdfZoomInBtn = document.getElementById('pdf-zoom-in');
    const pdfZoomResetBtn = document.getElementById('pdf-zoom-reset');
    const pdfZoomLabel = document.getElementById('pdf-zoom-label');
    const floatingToolPicker = document.getElementById('floating-tool-picker');
    const pdfPages = document.getElementById('pdf-pages');
    const fieldsEmpty = document.getElementById('fields-empty');
    const fieldsList = document.getElementById('fields-list');
    const fieldCount = document.getElementById('field-count');
    const fieldSearchInput = document.getElementById('field-search');
    const fieldTypeFilterInput = document.getElementById('field-type-filter');
    const fieldSortInput = document.getElementById('field-sort');
    const fieldsPanel = document.getElementById('fields-panel');
    const fieldsFilterControls = document.getElementById('fields-filter-controls');
    const bulkRenameControls = document.getElementById('bulk-rename-controls');
    const fieldsModeInputs = Array.from(document.querySelectorAll('input[name="fields-panel-mode"]'));
    const bulkRenameTypeInput = document.getElementById('bulk-rename-type');
    const bulkRenamePatternInput = document.getElementById('bulk-rename-pattern');
    const bulkRenameReplacementInput = document.getElementById('bulk-rename-replacement');
    const bulkRenameStatus = document.getElementById('bulk-rename-status');
    const bulkRenameApplyBtn = document.getElementById('bulk-rename-apply');
    const autoDetectBtn = document.getElementById('auto-detect-btn');
    const relabelBtn = document.getElementById('relabel-btn');
    const sidebarRelabelBtn = document.getElementById('sidebar-relabel-btn');
    const exportBtn = document.getElementById('export-btn');
    const normalizePassBtn = document.getElementById('normalize-pass-btn');
    const errorToast = document.getElementById('error-toast');
    const errorToastMessage = document.getElementById('error-toast-message');
    const successToast = document.getElementById('success-toast');
    const successToastMessage = document.getElementById('success-toast-message');
    const aiAuthNotice = document.getElementById('ai-auth-notice');
    const authControls = document.getElementById('auth-controls');
    const settingsBtn = document.getElementById('settings-btn');
    const settingsModal = document.getElementById('settings-modal');
    const closeSettingsBtn = document.getElementById('close-settings');
    const saveSettingsBtn = document.getElementById('save-settings');
    const resetSettingsBtn = document.getElementById('reset-settings');
    const normalizationModal = document.getElementById('normalization-modal');
    const closeNormalizationBtn = document.getElementById('close-normalization');
    const applyNormalizationBtn = document.getElementById('apply-normalization');
    const repairBtn = document.getElementById('repair-btn');
    const repairModal = document.getElementById('repair-modal');
    const closeRepairBtn = document.getElementById('close-repair');
    const repairStatus = document.getElementById('repair-status');
    const repairStatusText = document.getElementById('repair-status-text');
    const utilitiesBtn = document.getElementById('utilities-btn');
    const previewBtn = document.getElementById('preview-btn');
    const utilitiesModal = document.getElementById('utilities-modal');
    const utilitiesCloseBtn = document.getElementById('utilities-close');
    const pageManagerModal = document.getElementById('page-manager-modal');
    const closePageManagerBtn = document.getElementById('close-page-manager');
    const pageManagerStatus = document.getElementById('page-manager-status');
    const pageManagerPages = document.getElementById('page-manager-pages');
    const pageManagerDropzone = document.getElementById('page-manager-dropzone');
    const pageManagerSummary = document.getElementById('page-manager-summary');
    const pageManagerInsertFileInput = document.getElementById('page-manager-insert-file');
    const pageManagerInsertFileName = document.getElementById('page-manager-insert-file-name');
    const pageManagerInsertPosition = document.getElementById('page-manager-insert-position');
    const pageManagerInsertSelectionSummary = document.getElementById('page-manager-insert-selection-summary');
    const pageManagerInsertPages = document.getElementById('page-manager-insert-pages');
    const pageManagerInsertRunBtn = document.getElementById('page-manager-insert-run');
    const pageManagerResetBtn = document.getElementById('page-manager-reset');
    const pageManagerDownloadSplitsBtn = document.getElementById('page-manager-download-splits');
    const pageManagerApplyBtn = document.getElementById('page-manager-apply');
    const passwordModal = document.getElementById('password-modal');
    const passwordInput = document.getElementById('password-input');
    const passwordError = document.getElementById('password-error');
    const passwordSubmitBtn = document.getElementById('password-submit');
    const passwordCancelBtn = document.getElementById('password-cancel');
    const repairPromptModal = document.getElementById('repair-prompt-modal');
    const repairPromptMessage = document.getElementById('repair-prompt-message');
    const repairPromptFixBtn = document.getElementById('repair-prompt-fix');
    const repairPromptCancelBtn = document.getElementById('repair-prompt-cancel');
    const aiModelInput = document.getElementById('ai-model');
    const aiModelSuggestions = document.getElementById('ai-model-suggestions');
    const pageTitle = document.getElementById('page-title');
    const pageFavicon = document.getElementById('page-favicon');
    const brandLogo = document.getElementById('brand-logo');
    const brandPdfTitle = document.getElementById('brand-pdf-title');
    const brandPdfSubtitle = document.getElementById('brand-pdf-subtitle');
    const documentName = document.getElementById('document-name');
    const dirtyIndicator = document.getElementById('dirty-indicator');
    const openPlaygroundBtn = document.getElementById('open-playground-btn');
    const savePlaygroundBtn = document.getElementById('save-playground-btn');
    const openPlaygroundModalEl = document.getElementById('open-playground-modal');
    const savePlaygroundModalEl = document.getElementById('save-playground-modal');
    const usePlaygroundVariablesInput = document.getElementById('use-playground-variables');
    const pdfInterviewPicker = document.getElementById('pdf-interview-picker');
    const pdfPlaygroundFields = document.getElementById('pdf-playground-fields');
    const pdfInstalledFields = document.getElementById('pdf-installed-fields');
    const pdfPlaygroundProjectSelect = document.getElementById('pdf-playground-project');
    const pdfPlaygroundYamlFileSelect = document.getElementById('pdf-playground-yaml-file');
    const pdfInstalledPackageSelect = document.getElementById('pdf-installed-package');
    const pdfInstalledYamlFileSelect = document.getElementById('pdf-installed-yaml-file');
    const pdfInterviewVariableSummary = document.getElementById('pdf-interview-variable-summary');
    const pdfInterviewSourceModeInputs = Array.from(document.querySelectorAll('input[name="pdf-interview-source-mode"]'));
    const quickFieldEditor = document.getElementById('quick-field-editor');
    const quickFieldTypeBadge = document.getElementById('quick-field-type-badge');
    const quickFieldNameInput = document.getElementById('quick-field-name');
    const quickFieldDeleteBtn = document.getElementById('quick-field-delete');
    const quickFieldCloseBtn = document.getElementById('quick-field-close');
    const quickFieldSuggestions = document.getElementById('quick-field-suggestions');

    const toolButtons = {
        text: document.getElementById('tool-text'),
        multiline: document.getElementById('tool-multiline'),
        checkbox: document.getElementById('tool-checkbox'),
        signature: document.getElementById('tool-signature'),
        dropdown: document.getElementById('tool-dropdown'),
        listbox: document.getElementById('tool-listbox'),
    };

    function generateId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return 'field-' + window.crypto.randomUUID();
        }
        return 'field-' + Math.random().toString(36).slice(2, 11);
    }

    function apiUrl(path) {
        return API_BASE_PATH + path;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    /** Map PDF standard font names to CSS font-family + weight. */
    function pdfFontToCss(pdfFont) {
        var f = String(pdfFont || 'Helvetica');
        if (f === 'Helvetica-Bold') return { family: 'Helvetica, Arial, sans-serif', weight: 'bold' };
        if (f === 'Courier') return { family: '"Courier New", Courier, monospace', weight: 'normal' };
        if (f === 'Courier-Bold') return { family: '"Courier New", Courier, monospace', weight: 'bold' };
        if (f === 'Times-Roman') return { family: '"Times New Roman", Times, serif', weight: 'normal' };
        if (f === 'Times-Bold') return { family: '"Times New Roman", Times, serif', weight: 'bold' };
        return { family: 'Helvetica, Arial, sans-serif', weight: 'normal' };
    }

    var CHECKBOX_PREVIEW_GLYPHS = {
        cross: '\u2717',    // ✗
        check: '\u2713',    // ✓
        circle: '\u25CF',   // ●
        star: '\u2605',     // ★
        diamond: '\u25C6',  // ◆
        square: '\u25A0'    // ■
    };

    var _signatureDataUrl = null;
    (function preloadSignature() {
        var img = new Image();
        img.onload = function () {
            var canvas = document.createElement('canvas');
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            canvas.getContext('2d').drawImage(img, 0, 0);
            _signatureDataUrl = canvas.toDataURL('image/png');
        };
        img.src = '/packagestatic/docassemble.ALDashboard/placeholder_signature.png';
    })();

    function setDirty(value) {
        state.hasUnsavedChanges = !!value;
        dirtyIndicator.classList.toggle('hidden', !state.hasUnsavedChanges);
    }

    function updateDocumentName() {
        documentName.textContent = state.fileName ? state.fileName : '';
    }

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    function getViewerRenderScale() {
        return state.renderScale * state.viewerZoom;
    }

    function updateZoomControls() {
        const hasPdf = !!state.pdfBytes;
        const zoomPercent = Math.round(state.viewerZoom * 100);
        pdfZoomLabel.textContent = zoomPercent + '%';
        pdfZoomOutBtn.disabled = !hasPdf || state.viewerZoom <= 0.6;
        pdfZoomInBtn.disabled = !hasPdf || state.viewerZoom >= 2.4;
        pdfZoomResetBtn.disabled = !hasPdf || Math.abs(state.viewerZoom - 1) < 0.001;
    }

    function captureViewerAnchor() {
        const shell = document.querySelector('.pdf-page-shell[data-page-index="' + currentVisiblePageIndex + '"]');
        if (!shell) {
            return { pageIndex: currentVisiblePageIndex || 0, offsetRatio: 0 };
        }
        const pageHeight = Math.max(shell.offsetHeight, 1);
        return {
            pageIndex: Number(shell.dataset.pageIndex || 0),
            offsetRatio: clamp((pdfContainer.scrollTop - shell.offsetTop) / pageHeight, 0, 1)
        };
    }

    function restoreViewerAnchor(anchor) {
        if (!anchor) return;
        const shell = document.querySelector('.pdf-page-shell[data-page-index="' + anchor.pageIndex + '"]');
        if (!shell) return;
        pdfContainer.scrollTop = shell.offsetTop + (shell.offsetHeight * clamp(anchor.offsetRatio || 0, 0, 1));
        currentVisiblePageIndex = anchor.pageIndex;
    }

    async function updateViewerZoom(nextZoom) {
        const clampedZoom = clamp(Number(nextZoom) || 1, 0.6, 2.4);
        if (!state.pdfDoc || Math.abs(clampedZoom - state.viewerZoom) < 0.001) {
            state.viewerZoom = clampedZoom;
            updateZoomControls();
            return;
        }
        const anchor = captureViewerAnchor();
        state.viewerZoom = clampedZoom;
        updateZoomControls();
        await renderPages({ anchor: anchor });
    }

    function normalizeFieldType(rawType) {
        const type = String(rawType || 'text').toLowerCase();
        return FIELD_TYPES.includes(type) ? type : 'text';
    }

    function normalizeOptionalFieldType(rawType) {
        const type = String(rawType || '').trim().toLowerCase();
        return FIELD_TYPES.includes(type) ? type : '';
    }

    function normalizePdfFieldName(rawName) {
        let name = String(rawName || '').trim();
        if (!name) return 'field';
        name = name.replace(/\(\)/g, '');
        name = name.replace(/['`’]/g, '');
        name = name.replace(/\[(\d+)\]/g, function (_match, index) {
            return String(Number(index) + 1);
        });
        name = name.replace(/\./g, '_');
        name = name.replace(/[^a-zA-Z0-9_]+/g, '_');
        name = name.replace(/_+/g, '_');
        name = name.replace(/^_+|_+$/g, '');
        name = name.toLowerCase();
        if (!name) name = 'field';
        if (/^[0-9]/.test(name)) {
            name = 'field_' + name;
        }
        return name;
    }

    function extractTextBoxesFromPage(pageProxy) {
        return pageProxy.getTextContent().then(function (textContent) {
            const viewport = pageProxy.getViewport({ scale: 1 });
            const textItems = [];
            textContent.items.forEach(function (item) {
                if (!item || !('str' in item) || !item.str || !item.str.trim() || !('transform' in item)) {
                    return;
                }
                const transform = item.transform;
                const x = Number(transform[4] || 0);
                const y = viewport.height - Number(transform[5] || 0);
                const fontSize = Math.max(Math.abs(Number(transform[3] || 0)), 6);
                const width = Math.max(Number(item.width || 0), item.str.trim().length * fontSize * 0.46);
                textItems.push({
                    text: item.str.trim(),
                    x: x,
                    y: y,
                    width: width,
                    height: fontSize,
                    fontSize: fontSize
                });
            });
            return groupTextItemsIntoBoxes(textItems).map(function (box) {
                return {
                    text: box.text,
                    x: clamp(box.x / viewport.width, 0, 1),
                    y: clamp(box.y / viewport.height, 0, 1),
                    width: clamp(box.width / viewport.width, 0, 1),
                    height: clamp(box.height / viewport.height, 0, 1),
                    fontSize: box.fontSize
                };
            });
        }).catch(function (error) {
            console.warn('Failed to extract PDF text for heuristic naming:', error);
            return [];
        });
    }

    function groupTextItemsIntoBoxes(textItems) {
        if (!Array.isArray(textItems) || textItems.length === 0) return [];
        const items = textItems.slice().sort(function (left, right) {
            const yDiff = Math.abs(left.y - right.y);
            if (yDiff < 3) {
                return left.x - right.x;
            }
            return left.y - right.y;
        });
        const boxes = [];
        let currentBox = null;
        items.forEach(function (item) {
            if (!currentBox) {
                currentBox = {
                    text: item.text,
                    x: item.x,
                    y: item.y,
                    width: item.width,
                    height: item.height,
                    fontSize: item.fontSize
                };
                return;
            }
            const sameLineThreshold = Math.max(currentBox.fontSize * 0.45, 4);
            const gap = item.x - (currentBox.x + currentBox.width);
            const maxGap = Math.max(currentBox.fontSize * 0.9, 12);
            if (Math.abs(item.y - currentBox.y) <= sameLineThreshold && gap <= maxGap) {
                currentBox.text += (gap > 2 ? ' ' : '') + item.text;
                currentBox.width = Math.max(currentBox.width, (item.x + item.width) - currentBox.x);
                currentBox.height = Math.max(currentBox.height, item.height);
                currentBox.fontSize = Math.max(currentBox.fontSize, item.fontSize);
                return;
            }
            boxes.push(currentBox);
            currentBox = {
                text: item.text,
                x: item.x,
                y: item.y,
                width: item.width,
                height: item.height,
                fontSize: item.fontSize
            };
        });
        if (currentBox) {
            boxes.push(currentBox);
        }
        return boxes.filter(function (box) {
            return box.text && box.text.trim() && !/^[_\s.\-:]{1,4}$/.test(box.text.trim());
        });
    }

    function getRectBounds(rect) {
        return {
            left: rect.x,
            right: rect.x + rect.width,
            top: rect.y,
            bottom: rect.y + rect.height,
            centerX: rect.x + (rect.width / 2),
            centerY: rect.y + (rect.height / 2)
        };
    }

    function getTextBoxBounds(box) {
        return {
            left: box.x,
            right: box.x + box.width,
            top: box.y,
            bottom: box.y + box.height,
            centerX: box.x + (box.width / 2),
            centerY: box.y + (box.height / 2)
        };
    }

    function cleanSuggestedLabelText(rawText) {
        let text = String(rawText || '').trim();
        if (!text) return '';
        text = text.replace(/\s+/g, ' ');
        text = text.replace(/^[\s:;.,\-–—_]+|[\s:;.,\-–—_]+$/g, '');
        if (!text) return '';
        const parts = text.split(/\s+/);
        if (parts.length > 10) {
            text = parts.slice(0, 10).join(' ');
        }
        return text;
    }

    function getNearbyFieldNameBases(pageIndex, rect, excludeFieldId) {
        const rectBounds = getRectBounds(rect);
        const nearby = new Set();
        state.fields.forEach(function (field) {
            if (field.pageIndex !== pageIndex || field.id === excludeFieldId) return;
            const fieldBounds = getRectBounds(field);
            const verticalDistance = Math.abs(fieldBounds.centerY - rectBounds.centerY);
            const horizontalGap = Math.min(
                Math.abs(rectBounds.left - fieldBounds.right),
                Math.abs(fieldBounds.left - rectBounds.right)
            );
            if (verticalDistance <= Math.max(rect.height * 1.6, field.height * 1.6, 0.05) && horizontalGap <= 0.1) {
                nearby.add(normalizePdfFieldName(field.name));
            }
        });
        return nearby;
    }

    // Contract: return up to 3 normalized field-name suggestions for a candidate
    // rect on a page, ordered by likely usefulness and already filtered against
    // immediate neighboring field names.
    function buildFieldNameSuggestions(pageIndex, rect, type, excludeFieldId) {
        const textBoxes = Array.isArray(state.pageTextBoxes[pageIndex]) ? state.pageTextBoxes[pageIndex] : [];
        if (!textBoxes.length) return [];
        const rectBounds = getRectBounds(rect);
        const rowTolerance = Math.max(rect.height * 1.6, 0.045);
        const sideGapLimit = Math.max(rect.width * 3.5, 0.22);
        const belowGapLimit = Math.max(rect.height * 2.4, 0.12);
        const nearbyNames = getNearbyFieldNameBases(pageIndex, rect, excludeFieldId);
        const matches = [];

        textBoxes.forEach(function (box) {
            const text = cleanSuggestedLabelText(box.text);
            if (!text) return;
            const boxBounds = getTextBoxBounds(box);
            const verticalDistance = Math.abs(boxBounds.centerY - rectBounds.centerY);
            const overlapWidth = Math.min(rectBounds.right, boxBounds.right) - Math.max(rectBounds.left, boxBounds.left);
            const overlapRatio = overlapWidth > 0 ? overlapWidth / Math.min(rect.width, box.width) : 0;

            if (boxBounds.right <= rectBounds.left + 0.01 && verticalDistance <= rowTolerance) {
                const gap = rectBounds.left - boxBounds.right;
                if (gap <= sideGapLimit) {
                    matches.push({
                        text: text,
                        source: 'left',
                        score: 320 - (gap * 1000) - (verticalDistance * 700)
                    });
                }
            }

            if (boxBounds.left >= rectBounds.right - 0.01 && verticalDistance <= rowTolerance) {
                const gap = boxBounds.left - rectBounds.right;
                if (gap <= sideGapLimit) {
                    matches.push({
                        text: text,
                        source: 'right',
                        score: 220 - (gap * 1000) - (verticalDistance * 700)
                    });
                }
            }

            if (boxBounds.top >= rectBounds.bottom - 0.01) {
                const gap = boxBounds.top - rectBounds.bottom;
                if (gap <= belowGapLimit && overlapRatio >= 0.18) {
                    matches.push({
                        text: text,
                        source: 'below',
                        score: 180 - (gap * 1200) + (overlapRatio * 100)
                    });
                }
            }
        });

        const leftMatches = matches
            .filter(function (candidate) { return candidate.source === 'left'; })
            .sort(function (left, right) { return right.score - left.score; });
        const rightMatches = matches
            .filter(function (candidate) { return candidate.source === 'right'; })
            .sort(function (left, right) { return right.score - left.score; });
        const belowMatches = matches
            .filter(function (candidate) { return candidate.source === 'below'; })
            .sort(function (left, right) { return right.score - left.score; });

        const ordered = [];
        if (leftMatches[0]) {
            ordered.push(leftMatches[0]);
            if (belowMatches[0]) ordered.push(belowMatches[0]);
            if (rightMatches[0]) ordered.push(rightMatches[0]);
        } else {
            if (rightMatches[0]) ordered.push(rightMatches[0]);
            if (belowMatches[0]) ordered.push(belowMatches[0]);
        }

        const seenNames = new Set();
        const suggestions = [];
        ordered.forEach(function (candidate) {
            if (!candidate || suggestions.length >= 3) return;
            const normalized = normalizePdfFieldName(candidate.text);
            if (!normalized || seenNames.has(normalized) || nearbyNames.has(normalized)) {
                return;
            }
            seenNames.add(normalized);
            suggestions.push({
                name: ensureUniqueFieldName(normalized, excludeFieldId || null),
                source: candidate.source,
                text: candidate.text
            });
        });

        return suggestions.slice(0, 3);
    }

    function ensureUniqueFieldName(baseName, excludeFieldId) {
        const used = new Set(
            state.fields
                .filter(function (field) { return field.id !== excludeFieldId; })
                .map(function (field) { return normalizePdfFieldName(field.name); })
        );
        const normalizedBase = normalizePdfFieldName(baseName);
        let candidate = normalizedBase;
        let suffix = 1;
        while (used.has(candidate)) {
            candidate = normalizedBase + '__' + suffix;
            suffix += 1;
        }
        return candidate;
    }

    function getDefaultFieldName(type) {
        const normalizedType = normalizeFieldType(type);
        const library = Array.isArray(FIELD_NAME_LIBRARY[normalizedType])
            ? FIELD_NAME_LIBRARY[normalizedType]
            : (normalizedType === 'multiline' ? FIELD_NAME_LIBRARY.text : []);
        for (let index = 0; index < library.length; index += 1) {
            const candidate = normalizePdfFieldName(library[index]);
            if (!state.fields.some(function (field) { return normalizePdfFieldName(field.name) === candidate; })) {
                return candidate;
            }
        }
        const fallbackBase = normalizedType === 'checkbox'
            ? 'checkbox'
            : normalizedType === 'signature'
                ? 'signature'
                : normalizedType === 'dropdown'
                    ? 'dropdown'
                    : normalizedType === 'listbox'
                        ? 'listbox'
                        : normalizedType === 'radio'
                            ? 'radio_group'
                            : normalizedType === 'multiline'
                                ? 'text_area'
                                : 'text';
        return ensureUniqueFieldName(fallbackBase, null);
    }

    function getFieldTypeMeta(type) {
        return FIELD_TYPE_META[normalizeFieldType(type)] || FIELD_TYPE_META.text;
    }

    function getCurrentField() {
        return state.fields.find(function (field) {
            return field.id === state.selectedFieldId;
        }) || null;
    }

    function getQuickEditField() {
        if (!state.quickEditFieldId) return null;
        return state.fields.find(function (field) {
            return field.id === state.quickEditFieldId;
        }) || null;
    }

    function getOverlayForPage(pageIndex) {
        return document.querySelector('.page-overlay[data-page-index="' + pageIndex + '"]');
    }

    function showLoading(message) {
        loadingMessage.textContent = message;
        pdfEmpty.classList.add('hidden');
        pdfLoading.classList.remove('hidden');
        pdfPages.classList.add('hidden');
    }

    function hideLoading() {
        pdfLoading.classList.add('hidden');
    }

    function showPdfWorkspace() {
        if (state.pdfBytes) {
            pdfPages.classList.remove('hidden');
            pdfEmpty.classList.add('hidden');
        }
        updateZoomControls();
    }

    function getCurrentVisiblePageIndex() {
        if (!state.pageCount) return 0;
        const containerRect = pdfContainer.getBoundingClientRect();
        const containerCenterY = containerRect.top + (containerRect.height / 2);
        let bestPageIndex = currentVisiblePageIndex || 0;
        let bestDistance = Number.POSITIVE_INFINITY;
        document.querySelectorAll('.pdf-page-shell').forEach(function (shell) {
            const rect = shell.getBoundingClientRect();
            if (rect.bottom < containerRect.top || rect.top > containerRect.bottom) {
                return;
            }
            const centerY = rect.top + (rect.height / 2);
            const distance = Math.abs(centerY - containerCenterY);
            if (distance < bestDistance) {
                bestDistance = distance;
                bestPageIndex = Number(shell.dataset.pageIndex || 0);
            }
        });
        return bestPageIndex;
    }

    function scrollFieldsListToElement(element, options) {
        if (!element || fieldsList.classList.contains('hidden')) return;
        const settings = options || {};
        const behavior = settings.behavior || 'smooth';
        const mode = settings.mode || 'nearest';
        const listRect = fieldsList.getBoundingClientRect();
        const itemRect = element.getBoundingClientRect();
        const topWithinList = itemRect.top - listRect.top + fieldsList.scrollTop;
        const bottomWithinList = itemRect.bottom - listRect.top + fieldsList.scrollTop;
        let targetTop = fieldsList.scrollTop;

        if (mode === 'start') {
            targetTop = topWithinList;
        } else if (mode === 'center') {
            targetTop = topWithinList - ((fieldsList.clientHeight - itemRect.height) / 2);
        } else {
            const visibleTop = fieldsList.scrollTop;
            const visibleBottom = visibleTop + fieldsList.clientHeight;
            if (topWithinList < visibleTop) {
                targetTop = topWithinList;
            } else if (bottomWithinList > visibleBottom) {
                targetTop = bottomWithinList - fieldsList.clientHeight;
            }
        }

        fieldsListProgrammaticScroll = true;
        fieldsList.scrollTo({
            top: Math.max(0, targetTop),
            behavior: behavior,
        });
        window.setTimeout(function () {
            fieldsListProgrammaticScroll = false;
        }, behavior === 'auto' ? 0 : 220);
    }

    function syncFieldsListToVisiblePage(force) {
        if (state.fieldSort !== 'position') return;
        if (state.pageCount <= 1 || fieldsList.classList.contains('hidden')) return;
        if (!force && Date.now() < fieldsListManualScrollUntil) return;
        const pageIndex = getCurrentVisiblePageIndex();
        currentVisiblePageIndex = pageIndex;
        const group = fieldsList.querySelector('.field-list-page[data-page-index="' + pageIndex + '"]');
        if (!group) return;
        scrollFieldsListToElement(group, {
            mode: 'start',
            behavior: force ? 'auto' : 'smooth'
        });
    }

    function scheduleFieldsListSync(force) {
        if (fieldsListSyncRaf) {
            window.cancelAnimationFrame(fieldsListSyncRaf);
        }
        fieldsListSyncRaf = window.requestAnimationFrame(function () {
            fieldsListSyncRaf = null;
            syncFieldsListToVisiblePage(force);
        });
    }

    function scrollSelectedFieldIntoView(fieldId) {
        if (!fieldId || fieldsList.classList.contains('hidden')) return;
        const item = fieldsList.querySelector('.field-list-item[data-field-id="' + fieldId + '"]');
        if (!item) return;
        scrollFieldsListToElement(item, { mode: 'nearest', behavior: 'smooth' });
    }

    function hideToasts() {
        errorToast.classList.add('hidden');
        successToast.classList.add('hidden');
    }

    function showError(message, timeoutMs) {
        hideToasts();
        errorToastMessage.textContent = message;
        errorToast.classList.remove('hidden');
        window.setTimeout(function () {
            errorToast.classList.add('hidden');
        }, typeof timeoutMs === 'number' ? timeoutMs : 6000);
    }

    function showSuccess(message, timeoutMs) {
        hideToasts();
        successToastMessage.textContent = message;
        successToast.classList.remove('hidden');
        window.setTimeout(function () {
            successToast.classList.add('hidden');
        }, typeof timeoutMs === 'number' ? timeoutMs : 3200);
    }

    function updateFieldCount() {
        const totalCount = state.fields.length;
        const filteredCount = totalCount === 0 ? 0 : getDisplayedFields().length;
        const filtersActive = state.fieldsPanelMode !== 'rename' && Boolean(String(state.fieldSearch || '').trim() || normalizeOptionalFieldType(state.fieldTypeFilter || ''));
        if (totalCount === 0) {
            fieldCount.textContent = 'No fields';
        } else if (filtersActive) {
            fieldCount.textContent = filteredCount + ' of ' + totalCount + ' fields';
        } else {
            fieldCount.textContent = totalCount + ' field' + (totalCount === 1 ? '' : 's');
        }
        fieldsEmpty.classList.toggle('hidden', totalCount !== 0);
        fieldsList.classList.toggle('hidden', totalCount === 0);
        floatingToolPicker.classList.toggle('hidden', !state.pdfBytes);
        exportBtn.disabled = !state.pdfBytes || totalCount === 0;
        savePlaygroundBtn.disabled = !state.pdfBytes || totalCount === 0;
        normalizePassBtn.disabled = !state.pdfBytes || totalCount === 0;
        repairBtn.disabled = !state.pdfBytes;
        if (relabelBtn) {
            relabelBtn.disabled = !state.pdfBytes || totalCount === 0 || !state.auth.aiEnabled;
        }
        if (sidebarRelabelBtn) {
            sidebarRelabelBtn.disabled = !state.pdfBytes || totalCount === 0 || !state.auth.aiEnabled;
        }
        previewBtn.disabled = !state.pdfBytes || totalCount === 0;
        managePagesBtn.disabled = !state.pdfBytes;
        if (!state.pdfBytes || totalCount === 0) {
            state.previewMode = false;
            previewBtn.classList.remove('active');
            document.getElementById('preview-icon-open').classList.remove('hidden');
            document.getElementById('preview-icon-closed').classList.add('hidden');
        }
        updateZoomControls();
    }

    function getDisplayedFields() {
        const inRenameMode = state.fieldsPanelMode === 'rename';
        const search = inRenameMode ? '' : String(state.fieldSearch || '').trim().toLowerCase();
        const typeFilter = inRenameMode ? '' : normalizeOptionalFieldType(state.fieldTypeFilter || '');
        const hasTypeFilter = !inRenameMode && FIELD_TYPES.includes(typeFilter);
        const filtered = state.fields.filter(function (field) {
            if (hasTypeFilter && field.type !== typeFilter) {
                return false;
            }
            if (search && String(field.name || '').toLowerCase().indexOf(search) === -1) {
                return false;
            }
            return true;
        });

        const sorted = filtered.slice();
        if (state.fieldSort === 'name_asc') {
            sorted.sort(function (left, right) {
                return String(left.name || '').localeCompare(String(right.name || ''), undefined, { sensitivity: 'base' });
            });
            return sorted;
        }
        if (state.fieldSort === 'name_desc') {
            sorted.sort(function (left, right) {
                return String(right.name || '').localeCompare(String(left.name || ''), undefined, { sensitivity: 'base' });
            });
            return sorted;
        }
        if (state.fieldSort === 'type_name') {
            sorted.sort(function (left, right) {
                const leftMeta = getFieldTypeMeta(left.type);
                const rightMeta = getFieldTypeMeta(right.type);
                const typeCompare = leftMeta.label.localeCompare(rightMeta.label, undefined, { sensitivity: 'base' });
                if (typeCompare !== 0) return typeCompare;
                return String(left.name || '').localeCompare(String(right.name || ''), undefined, { sensitivity: 'base' });
            });
            return sorted;
        }
        sorted.sort(function (left, right) {
            if (left.pageIndex !== right.pageIndex) return left.pageIndex - right.pageIndex;
            if (Math.abs(left.y - right.y) > 0.002) return left.y - right.y;
            return left.x - right.x;
        });
        return sorted;
    }

    function invalidateBulkRenamePreview() {
        state.bulkRenamePreviewCache = null;
    }

    function bumpFieldNamesVersion() {
        state.fieldNamesVersion = (Number(state.fieldNamesVersion) || 0) + 1;
        invalidateBulkRenamePreview();
    }

    function setFieldsPanelMode(nextMode) {
        state.fieldsPanelMode = (nextMode === 'rename') ? 'rename' : 'filter';
        fieldsModeInputs.forEach(function (input) {
            input.checked = input.value === state.fieldsPanelMode;
        });
        const showRename = state.fieldsPanelMode === 'rename';
        fieldsFilterControls.classList.toggle('hidden', showRename);
        bulkRenameControls.classList.toggle('hidden', !showRename);
        fieldsPanel.classList.toggle('bulk-rename-expanded', showRename);
        if (!showRename) {
            state.fieldSearch = fieldSearchInput.value || '';
            refreshFieldsListFromControls();
            return;
        }
        refreshFieldsListFromControls();
    }

    function escapeRegexPattern(text) {
        return String(text || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function buildWildcardRegex(patternText) {
        const escaped = String(patternText || '')
            .split('')
            .map(function (char) {
                if (char === '*') return '.*';
                if (char === '?') return '.';
                return escapeRegexPattern(char);
            })
            .join('');
        return new RegExp(escaped, 'g');
    }

    function compileBulkRenameTransform(renameType, findPattern, replacementText) {
        const replacement = String(replacementText || '');
        if (!findPattern) {
            return { error: '', transform: null };
        }
        if (renameType === 'regex') {
            try {
                const regex = new RegExp(findPattern, 'g');
                return {
                    error: '',
                    transform: function (name) {
                        return String(name || '').replace(regex, replacement);
                    }
                };
            } catch (error) {
                return { error: 'Invalid regex: ' + (error && error.message ? error.message : 'unknown error'), transform: null };
            }
        }
        const hasWildcard = /[*?]/.test(findPattern);
        if (hasWildcard) {
            try {
                const wildcardRegex = buildWildcardRegex(findPattern);
                return {
                    error: '',
                    transform: function (name) {
                        return String(name || '').replace(wildcardRegex, replacement);
                    }
                };
            } catch (error) {
                return { error: 'Invalid wildcard pattern.', transform: null };
            }
        }
        return {
            error: '',
            transform: function (name) {
                const source = String(name || '');
                if (!findPattern) return source;
                if (source.indexOf(findPattern) === -1) return source;
                return source.split(findPattern).join(replacement);
            }
        };
    }

    function getBulkRenamePreview() {
        if (state.fieldsPanelMode !== 'rename') {
            return { changedCount: 0, renamedById: new Map(), mapping: {}, error: '', duplicates: [] };
        }
        const renameType = state.bulkRenameType === 'regex' ? 'regex' : 'pattern';
        const findPattern = String(state.bulkRenamePattern || '');
        const replacementText = String(state.bulkRenameReplacement || '');
        const cacheKey = [
            state.fieldNamesVersion,
            renameType,
            findPattern,
            replacementText
        ].join('::');
        if (state.bulkRenamePreviewCache && state.bulkRenamePreviewCache.key === cacheKey) {
            return state.bulkRenamePreviewCache.value;
        }
        const compiled = compileBulkRenameTransform(renameType, findPattern, replacementText);
        if (!compiled.transform) {
            const emptyResult = {
                changedCount: 0,
                renamedById: new Map(),
                mapping: {},
                error: compiled.error || '',
                duplicates: []
            };
            state.bulkRenamePreviewCache = { key: cacheKey, value: emptyResult };
            return emptyResult;
        }

        const renamedById = new Map();
        const mapping = {};
        const resultingCounts = new Map();
        let changedCount = 0;

        state.fields.forEach(function (field) {
            const original = String(field.name || '');
            const renamed = String(compiled.transform(original));
            const finalName = renamed || original;
            resultingCounts.set(finalName, (resultingCounts.get(finalName) || 0) + 1);
            if (finalName !== original) {
                changedCount += 1;
                renamedById.set(field.id, {
                    oldName: original,
                    newName: finalName
                });
                mapping[original] = finalName;
            }
        });

        const duplicates = [];
        resultingCounts.forEach(function (count, name) {
            if (count > 1) duplicates.push(name);
        });
        const result = {
            changedCount: changedCount,
            renamedById: renamedById,
            mapping: mapping,
            error: '',
            duplicates: duplicates
        };
        state.bulkRenamePreviewCache = { key: cacheKey, value: result };
        return result;
    }

    function updateBulkRenameUiState() {
        const preview = getBulkRenamePreview();
        const hasPattern = String(state.bulkRenamePattern || '').length > 0;
        bulkRenameStatus.className = 'bulk-rename-status small text-muted';
        if (!hasPattern) {
            bulkRenameStatus.textContent = 'Live preview will appear in the list below.';
        } else if (preview.error) {
            bulkRenameStatus.classList.add('error');
            bulkRenameStatus.textContent = preview.error;
        } else if (preview.duplicates.length) {
            bulkRenameStatus.classList.add('warning');
            bulkRenameStatus.textContent = 'Rename would create duplicate names: ' + preview.duplicates.slice(0, 3).join(', ') + (preview.duplicates.length > 3 ? ' ...' : '');
        } else if (preview.changedCount === 0) {
            bulkRenameStatus.textContent = 'No matching fields for current pattern.';
        } else {
            bulkRenameStatus.textContent = 'Previewing ' + preview.changedCount + ' rename' + (preview.changedCount === 1 ? '' : 's') + '.';
        }
        bulkRenameApplyBtn.disabled = !hasPattern || !!preview.error || preview.changedCount === 0 || preview.duplicates.length > 0;
    }

    function applyBulkRename() {
        const preview = getBulkRenamePreview();
        if (preview.error) {
            showError(preview.error);
            return;
        }
        if (!preview.changedCount) {
            showError('No matching fields to rename.');
            return;
        }
        if (preview.duplicates.length) {
            showError('Bulk rename would create duplicate field names.');
            return;
        }
        state.fields.forEach(function (field) {
            const change = preview.renamedById.get(field.id);
            if (change) {
                field.name = change.newName;
            }
        });
        bumpFieldNamesVersion();
        setDirty(true);
        renderFieldsList();
        renderFieldsOnPages();
        updateFieldCount();
        showSuccess('Renamed ' + preview.changedCount + ' field' + (preview.changedCount === 1 ? '' : 's') + '.');
    }

    function refreshFieldsListFromControls() {
        renderFieldsList();
        if (state.selectedFieldId && fieldsList.querySelector('.field-list-item[data-field-id="' + state.selectedFieldId + '"]')) {
            scrollSelectedFieldIntoView(state.selectedFieldId);
            return;
        }
    }

    function updateToolHint() {
        return;
    }

    function hideQuickFieldEditor() {
        state.quickEditFieldId = null;
        state.quickEditFocusPending = false;
        quickFieldEditor.classList.add('hidden');
        quickFieldSuggestions.innerHTML = '';
        quickFieldSuggestions.classList.add('hidden');
    }

    function getVisiblePdfViewportRect() {
        const containerRect = pdfContainer.getBoundingClientRect();
        const left = Math.max(containerRect.left + 8, 8);
        const top = Math.max(containerRect.top + 8, 8);
        const right = Math.min(containerRect.right - 8, window.innerWidth - 8);
        const bottom = Math.min(containerRect.bottom - 8, window.innerHeight - 8);
        return {
            left: left,
            top: top,
            right: Math.max(right, left + 1),
            bottom: Math.max(bottom, top + 1)
        };
    }

    function getQuickFieldEditorWidth(_fieldEl, viewportRect) {
        const nameLength = String(quickFieldNameInput.value || '').length;
        const preferredWidth = Math.max(
            260,
            Math.min(720, 164 + (nameLength * 9))
        );
        return clamp(preferredWidth, 260, viewportRect.right - viewportRect.left);
    }

    function positionQuickFieldEditor() {
        if (quickFieldEditor.classList.contains('hidden')) return;
        const field = getQuickEditField();
        if (!field) {
            hideQuickFieldEditor();
            return;
        }
        const fieldEl = document.querySelector('.field-box[data-field-id="' + field.id + '"]');
        const viewportRect = getVisiblePdfViewportRect();
        const editorWidth = getQuickFieldEditorWidth(fieldEl, viewportRect);
        quickFieldEditor.style.width = Math.round(editorWidth) + 'px';
        const editorRect = quickFieldEditor.getBoundingClientRect();
        const margin = 12;
        let top = viewportRect.top + margin;
        let left = viewportRect.left + margin;

        if (fieldEl) {
            const fieldRect = fieldEl.getBoundingClientRect();
            const fieldVisible = fieldRect.bottom > viewportRect.top &&
                fieldRect.top < viewportRect.bottom &&
                fieldRect.right > viewportRect.left &&
                fieldRect.left < viewportRect.right;
            const preferredTop = fieldVisible
                ? fieldRect.top - 2
                : ((fieldRect.top + fieldRect.bottom) / 2) - (editorRect.height / 2);
            const alignedLeft = clamp(fieldRect.left, viewportRect.left, viewportRect.right - editorRect.width);

            top = clamp(preferredTop, viewportRect.top, viewportRect.bottom - editorRect.height);
            if (fieldVisible) {
                left = alignedLeft;
            } else {
                left = alignedLeft;
            }
        }

        quickFieldEditor.style.top = Math.round(top) + 'px';
        quickFieldEditor.style.left = Math.round(left) + 'px';
    }

    function updateRenderedFieldName(fieldId) {
        const field = state.fields.find(function (candidate) { return candidate.id === fieldId; });
        if (!field) return;
        const fieldEl = document.querySelector('.field-box[data-field-id="' + field.id + '"]');
        if (fieldEl) {
            const chip = fieldEl.querySelector('.field-chip');
            const chipName = chip && chip.querySelector('.field-chip-name');
            if (chipName) {
                chipName.textContent = field.name || '';
            }
            if (chip) {
                applyFieldChipLayout(fieldEl, chip);
            }
        }
        const listItem = fieldsList.querySelector('.field-list-item[data-field-id="' + field.id + '"]');
        if (listItem) {
            const title = listItem.querySelector('.field-list-name');
            if (title && !title.classList.contains('field-list-name-preview')) {
                title.textContent = field.name || '';
            }
            const sidebarInput = listItem.querySelector('[data-action="field-name"]');
            if (sidebarInput && document.activeElement !== sidebarInput) {
                sidebarInput.value = field.name || '';
            }
            const help = listItem.querySelector('.field-card-help');
            if (help) {
                const nameHelp = getFieldNameHelp(field);
                help.className = 'field-card-help ' + nameHelp.level;
                help.innerHTML = nameHelp.html;
            }
        }
    }

    function commitFieldNameChange(fieldId, nextValue, source) {
        const field = state.fields.find(function (candidate) { return candidate.id === fieldId; });
        if (!field) return;
        if (field.name === nextValue) return;
        field.name = nextValue;
        bumpFieldNamesVersion();
        setDirty(true);
        if (state.fieldsPanelMode === 'rename') {
            renderFieldsList();
        }
        updateRenderedFieldName(fieldId);
        if (source !== 'quick') {
            const quickField = getQuickEditField();
            if (quickField && quickField.id === fieldId && document.activeElement !== quickFieldNameInput) {
                quickFieldNameInput.value = field.name || '';
            }
        }
        syncQuickFieldEditor({ preserveInput: source === 'quick' });
    }

    function syncQuickFieldEditor(options) {
        const settings = options || {};
        const field = getQuickEditField();
        if (!field || field.id !== state.selectedFieldId) {
            hideQuickFieldEditor();
            return;
        }

        const meta = getFieldTypeMeta(field.type);
        quickFieldTypeBadge.innerHTML = meta.icon;
        if (!settings.preserveInput || document.activeElement !== quickFieldNameInput) {
            quickFieldNameInput.value = field.name || '';
        }
        quickFieldNameInput.dataset.fieldId = field.id;
        renderQuickFieldSuggestions(field);
        quickFieldEditor.classList.remove('hidden');
        positionQuickFieldEditor();

        if (state.quickEditFocusPending) {
            window.requestAnimationFrame(function () {
                quickFieldNameInput.focus();
                quickFieldNameInput.select();
            });
            state.quickEditFocusPending = false;
        }
    }

    function renderQuickFieldSuggestions(field) {
        const detectedSuggestions = Array.isArray(field && field.nameSuggestions) ? field.nameSuggestions : [];
        // Merge in playground/installed variable names that match the current input
        const varHints = getEffectivePdfVariables();
        const currentVal = (quickFieldNameInput.value || '').trim().toLowerCase();
        const usedNames = new Set(state.fields.map(function(f) { return f.name; }));
        const detectedNames = new Set(detectedSuggestions.map(function(s) { return s.name; }));
        const extraSuggestions = [];
        if (varHints.length > 0 && currentVal) {
            varHints.forEach(function(v) {
                if (!detectedNames.has(v) && v.toLowerCase().indexOf(currentVal) !== -1) {
                    extraSuggestions.push({ name: v, text: 'From interview variables' });
                }
            });
        }
        const allSuggestions = detectedSuggestions.concat(extraSuggestions.slice(0, 10));
        quickFieldSuggestions.innerHTML = '';
        if (!allSuggestions.length) {
            quickFieldSuggestions.classList.add('hidden');
            return;
        }
        allSuggestions.forEach(function (suggestion) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'quick-field-suggestion btn btn-outline-secondary btn-sm';
            button.dataset.suggestedName = suggestion.name;
            button.title = suggestion.text ? ('From nearby text: ' + suggestion.text) : suggestion.name;
            button.textContent = suggestion.name;
            if (normalizePdfFieldName(suggestion.name) === normalizePdfFieldName(field.name)) {
                button.classList.add('active');
            }
            quickFieldSuggestions.appendChild(button);
        });
        quickFieldSuggestions.classList.remove('hidden');
    }

    function updateOverlayCursorState() {
        document.querySelectorAll('.page-overlay').forEach(function (overlay) {
            overlay.classList.toggle('tool-active', !!state.selectedTool);
            overlay.classList.toggle('point-insert', !!state.selectedTool && POINT_INSERT_TYPES.has(state.selectedTool));
        });
    }

    function setSelectedTool(tool) {
        state.selectedTool = tool ? normalizeFieldType(tool) : null;
        Object.keys(toolButtons).forEach(function (key) {
            toolButtons[key].classList.toggle('active', key === state.selectedTool);
        });
        updateToolHint();
        updateOverlayCursorState();
    }

    function applyBranding() {
        if (pageTitle) {
            pageTitle.textContent = BRANDING.pdf_page_title || BRANDING.pdf_header_title || 'PDF Labeler';
            document.title = pageTitle.textContent;
        }
        if (pageFavicon && BRANDING.favicon_url) {
            pageFavicon.href = BRANDING.favicon_url;
        }
        if (brandLogo && BRANDING.logo_url) {
            brandLogo.src = BRANDING.logo_url;
        }
        if (brandLogo && BRANDING.logo_alt) {
            brandLogo.alt = BRANDING.logo_alt;
        }
        if (brandPdfTitle) {
            brandPdfTitle.textContent = BRANDING.pdf_header_title || 'PDF Labeler';
        }
        if (brandPdfSubtitle) {
            brandPdfSubtitle.textContent = BRANDING.pdf_header_subtitle || 'Add, inspect, rename, and export PDF form fields';
        }
    }

    function renderAuthControls() {
        if (!authControls) return;
        if (state.auth.isAuthenticated) {
            const emailText = state.auth.email || 'Account';
            authControls.innerHTML =
                '<button id="auth-menu-btn" class="btn btn-outline-light btn-sm dropdown-toggle" type="button" aria-expanded="false">' + escapeHtml(emailText) + '</button>' +
                '<div id="auth-menu" class="dropdown-menu dropdown-menu-end header-auth-menu">' +
                    '<a class="dropdown-item" href="' + escapeHtml(state.auth.logoutUrl || '/user/sign-out') + '">Log out</a>' +
                '</div>';
            const menuBtn = document.getElementById('auth-menu-btn');
            const menu = document.getElementById('auth-menu');
            menuBtn.addEventListener('click', function (event) {
                event.stopPropagation();
                menu.classList.toggle('show');
            });
        } else {
            authControls.innerHTML =
                '<a class="btn btn-outline-light btn-sm" href="' + escapeHtml(state.auth.loginUrl || '/user/sign-in') + '">Log in</a>';
        }
    }

    function updateAiUiState() {
        const aiEnabled = !!state.auth.aiEnabled;
        autoDetectBtn.disabled = !state.pdfBytes || !aiEnabled;
        if (relabelBtn) {
            relabelBtn.disabled = !state.pdfBytes || state.fields.length === 0 || !aiEnabled;
        }
        if (sidebarRelabelBtn) {
            sidebarRelabelBtn.disabled = !state.pdfBytes || state.fields.length === 0 || !aiEnabled;
        }
        if (!aiEnabled) {
            aiAuthNotice.classList.remove('hidden');
            aiAuthNotice.innerHTML = 'AI features require login. <a href="' + escapeHtml(state.auth.loginUrl || '/user/sign-in') + '">Log in</a> to use auto-detect and AI relabel.';
        } else {
            aiAuthNotice.classList.add('hidden');
        }
        savePlaygroundBtn.classList.toggle('hidden', !state.auth.isAuthenticated);
        openPlaygroundBtn.classList.toggle('hidden', !state.auth.isAuthenticated);
    }

    async function fetchModelCatalog() {
        try {
            const response = await fetch(apiUrl('/labeler/api/models'), { method: 'GET' });
            const data = await response.json();
            if (!data.success || !data.data) return;
            state.defaultModel = data.data.default_model || state.defaultModel;
            state.recommendedModels = Array.isArray(data.data.recommended_models) && data.data.recommended_models.length
                ? data.data.recommended_models
                : [state.defaultModel];
            state.availableModels = Array.isArray(data.data.available_models) ? data.data.available_models : [];
            state.model = state.defaultModel;
            aiModelInput.value = state.model;
        } catch (_error) {
            aiModelInput.value = state.model;
        }
    }

    async function fetchAuthStatus() {
        try {
            const nextTarget = window.location.pathname + window.location.search;
            const response = await fetch(apiUrl('/labeler/api/auth-status') + '?next=' + encodeURIComponent(nextTarget), { method: 'GET' });
            const data = await response.json();
            if (data.success && data.data) {
                state.auth.isAuthenticated = !!data.data.is_authenticated;
                state.auth.email = data.data.email || '';
                state.auth.loginUrl = data.data.login_url || '/user/sign-in';
                state.auth.logoutUrl = data.data.logout_url || '/user/sign-out';
                state.auth.aiEnabled = !!data.data.ai_enabled;
            }
        } catch (_error) {
            state.auth = {
                isAuthenticated: false,
                email: '',
                loginUrl: '/user/sign-in',
                logoutUrl: '/user/sign-out',
                aiEnabled: false
            };
        }
        renderAuthControls();
        updateAiUiState();
        if (state.auth.isAuthenticated) {
            fetchPdfPlaygroundProjects();
        }
    }

    // ================================================================
    // Playground integration (variable hints, open, save)
    // ================================================================

    async function fetchPdfPlaygroundProjects() {
        if (!state.auth.isAuthenticated) {
            state.playground.projects = [];
            state.playground.files = [];
            return;
        }
        try {
            var response = await fetch(apiUrl('/labeler/api/playground-projects'), { method: 'GET', credentials: 'same-origin' });
            var data = await response.json();
            state.playground.projects =
                data && data.success && data.data && Array.isArray(data.data.projects)
                    ? data.data.projects : [];
            if (state.playground.projects.indexOf(state.playground.selectedProject) === -1)
                state.playground.selectedProject = state.playground.projects[0] || 'default';
            await fetchPdfPlaygroundFiles();
        } catch (_e) {
            state.playground.projects = [];
        }
        renderPdfInterviewPicker();
    }

    async function fetchPdfPlaygroundFiles() {
        if (!state.playground.selectedProject) { state.playground.files = []; renderPdfInterviewPicker(); return; }
        try {
            var response = await fetch(apiUrl('/labeler/api/playground-files?project=' + encodeURIComponent(state.playground.selectedProject)), { method: 'GET', credentials: 'same-origin' });
            var data = await response.json();
            var files = data && data.success && data.data && Array.isArray(data.data.files) ? data.data.files : [];
            state.playground.files = files;
            var exists = files.some(function(f) { return f.filename === state.playground.selectedFile; });
            state.playground.selectedFile = exists ? state.playground.selectedFile : (files[0] ? files[0].filename : '');
            await fetchPdfPlaygroundVariables();
        } catch (_e) {
            state.playground.files = [];
            state.playground.variables = [];
            state.playground.topLevelNames = [];
        }
        renderPdfInterviewPicker();
    }

    async function fetchPdfPlaygroundVariables() {
        if (!state.playground.selectedProject || !state.playground.selectedFile) {
            state.playground.variables = [];
            state.playground.topLevelNames = [];
            renderPdfInterviewPicker();
            return;
        }
        try {
            var response = await fetch(apiUrl('/labeler/api/playground-variables?project=' + encodeURIComponent(state.playground.selectedProject) + '&filename=' + encodeURIComponent(state.playground.selectedFile)), { method: 'GET', credentials: 'same-origin' });
            var data = await response.json();
            state.playground.variables =
                data && data.success && data.data && Array.isArray(data.data.all_names) ? data.data.all_names : [];
            state.playground.topLevelNames =
                data && data.success && data.data && Array.isArray(data.data.top_level_names) ? data.data.top_level_names : [];
        } catch (_e) {
            state.playground.variables = [];
            state.playground.topLevelNames = [];
        }
        renderPdfInterviewPicker();
    }

    async function fetchPdfInstalledPackages() {
        if (!state.auth.isAuthenticated) { state.installed.packages = []; return; }
        try {
            var response = await fetch(apiUrl('/labeler/api/installed-packages'), { method: 'GET', credentials: 'same-origin' });
            var data = await response.json();
            state.installed.packages = data && data.success && data.data && Array.isArray(data.data.packages) ? data.data.packages : [];
            if (state.installed.packages.indexOf(state.installed.selectedPackage) === -1)
                state.installed.selectedPackage = state.installed.packages[0] || '';
            await fetchPdfInstalledFiles();
        } catch (_e) {
            state.installed.packages = [];
        }
        renderPdfInterviewPicker();
    }

    async function fetchPdfInstalledFiles() {
        if (!state.installed.selectedPackage) { state.installed.files = []; renderPdfInterviewPicker(); return; }
        try {
            var response = await fetch(apiUrl('/labeler/api/installed-files?package=' + encodeURIComponent(state.installed.selectedPackage)), { method: 'GET', credentials: 'same-origin' });
            var data = await response.json();
            var files = data && data.success && data.data && Array.isArray(data.data.files) ? data.data.files : [];
            state.installed.files = files;
            var exists = files.some(function(f) { return f.filename === state.installed.selectedFile; });
            state.installed.selectedFile = exists ? state.installed.selectedFile : (files[0] ? files[0].filename : '');
            await fetchPdfInstalledVariables();
        } catch (_e) { state.installed.files = []; state.installed.variables = []; state.installed.topLevelNames = []; }
        renderPdfInterviewPicker();
    }

    async function fetchPdfInstalledVariables() {
        if (!state.installed.selectedPackage || !state.installed.selectedFile) {
            state.installed.variables = []; state.installed.topLevelNames = []; renderPdfInterviewPicker(); return;
        }
        try {
            var path = state.installed.selectedPackage + ':' + state.installed.selectedFile;
            var response = await fetch(apiUrl('/labeler/api/installed-variables?interview_path=' + encodeURIComponent(path)), { method: 'GET', credentials: 'same-origin' });
            var data = await response.json();
            state.installed.variables = data && data.success && data.data && Array.isArray(data.data.all_names) ? data.data.all_names : [];
            state.installed.topLevelNames = data && data.success && data.data && Array.isArray(data.data.top_level_names) ? data.data.top_level_names : [];
        } catch (_e) { state.installed.variables = []; state.installed.topLevelNames = []; }
        renderPdfInterviewPicker();
    }

    function renderPdfInterviewPicker() {
        pdfInterviewPicker.classList.toggle('hidden', !state.usePlaygroundVariables);
        var isPlayground = state.interviewSourceMode === 'playground';
        pdfPlaygroundFields.classList.toggle('hidden', !isPlayground);
        pdfInstalledFields.classList.toggle('hidden', isPlayground);
        pdfInterviewSourceModeInputs.forEach(function(inp) { inp.checked = inp.value === state.interviewSourceMode; });

        // Populate playground selects
        pdfPlaygroundProjectSelect.innerHTML = '';
        state.playground.projects.forEach(function(proj) {
            var opt = document.createElement('option');
            opt.value = proj; opt.textContent = proj;
            opt.selected = proj === state.playground.selectedProject;
            pdfPlaygroundProjectSelect.appendChild(opt);
        });
        pdfPlaygroundYamlFileSelect.innerHTML = '';
        state.playground.files.forEach(function(f) {
            var opt = document.createElement('option');
            opt.value = f.filename; opt.textContent = f.filename;
            opt.selected = f.filename === state.playground.selectedFile;
            pdfPlaygroundYamlFileSelect.appendChild(opt);
        });

        // Populate installed selects
        pdfInstalledPackageSelect.innerHTML = '';
        state.installed.packages.forEach(function(pkg) {
            var opt = document.createElement('option');
            opt.value = pkg; opt.textContent = pkg;
            opt.selected = pkg === state.installed.selectedPackage;
            pdfInstalledPackageSelect.appendChild(opt);
        });
        pdfInstalledYamlFileSelect.innerHTML = '';
        state.installed.files.forEach(function(f) {
            var opt = document.createElement('option');
            opt.value = f.filename; opt.textContent = f.filename;
            opt.selected = f.filename === state.installed.selectedFile;
            pdfInstalledYamlFileSelect.appendChild(opt);
        });

        // Variable summary
        var vars = getEffectivePdfVariables();
        if (vars.length > 0) {
            pdfInterviewVariableSummary.textContent = vars.length + ' variable name' + (vars.length === 1 ? '' : 's') + ' loaded.';
            pdfInterviewVariableSummary.classList.remove('hidden');
        } else {
            pdfInterviewVariableSummary.classList.add('hidden');
        }
    }

    function getEffectivePdfVariables() {
        if (!state.usePlaygroundVariables) return [];
        if (state.interviewSourceMode === 'installed')
            return state.installed.variables || [];
        return state.playground.variables || [];
    }

    // Open from Playground modal
    async function openPlaygroundModal() {
        if (!state.auth.isAuthenticated) { showError('Login required to access Playground.'); return; }
        // Show modal immediately with loading states
        var openPgProject = document.getElementById('open-pg-project');
        var openPgTemplate = document.getElementById('open-pg-template');
        openPgProject.innerHTML = '<option value="">Loading projects...</option>';
        openPgTemplate.innerHTML = '<option value="">Waiting for projects...</option>';
        openPlaygroundModalEl.classList.remove('hidden');
        // Load data in background
        (async function() {
            try {
                await fetchPdfPlaygroundProjects();
                openPgProject.innerHTML = '';
                state.playground.projects.forEach(function(proj) {
                    var opt = document.createElement('option');
                    opt.value = proj; opt.textContent = proj;
                    opt.selected = proj === state.playground.selectedProject;
                    openPgProject.appendChild(opt);
                });
                await fetchOpenPlaygroundTemplates();
            } catch (e) {
                console.error('Error loading playground data:', e);
                openPgProject.innerHTML = '<option value="">(error loading projects)</option>';
                openPgTemplate.innerHTML = '<option value="">(error loading templates)</option>';
            }
        })();
    }

    async function fetchOpenPlaygroundTemplates() {
        var openPgProject = document.getElementById('open-pg-project');
        var openPgTemplate = document.getElementById('open-pg-template');
        var project = openPgProject.value || 'default';
        openPgTemplate.innerHTML = '<option value="">Loading...</option>';
        try {
            var response = await fetch(apiUrl('/labeler/api/playground-templates?project=' + encodeURIComponent(project) + '&type=pdf'), { method: 'GET', credentials: 'same-origin' });
            var data = await response.json();
            var templates = data && data.success && data.data && Array.isArray(data.data.templates) ? data.data.templates : [];
            openPgTemplate.innerHTML = '';
            if (templates.length === 0) {
                var opt = document.createElement('option');
                opt.value = ''; opt.textContent = '(no PDF templates found)';
                openPgTemplate.appendChild(opt);
                return;
            }
            templates.forEach(function(t) {
                var opt = document.createElement('option');
                opt.value = t.filename; opt.textContent = t.filename + (t.size ? ' (' + Math.round(t.size / 1024) + ' KB)' : '');
                openPgTemplate.appendChild(opt);
            });
        } catch (_e) {
            openPgTemplate.innerHTML = '<option value="">(error loading templates)</option>';
        }
    }

    async function confirmOpenFromPlayground() {
        var openPgProject = document.getElementById('open-pg-project');
        var openPgTemplate = document.getElementById('open-pg-template');
        var project = openPgProject.value || 'default';
        var filename = openPgTemplate.value;
        if (!filename) { showError('Select a template file.'); return; }
        openPlaygroundModalEl.classList.add('hidden');
        showLoading('Loading ' + filename + ' from Playground...');
        try {
            var response = await fetch(apiUrl('/labeler/api/playground-templates/load?project=' + encodeURIComponent(project) + '&filename=' + encodeURIComponent(filename)), { method: 'GET', credentials: 'same-origin' });
            var data = await response.json();
            if (!data.success || !data.data || !data.data.file_content_base64) {
                throw new Error((data.error && data.error.message) || 'Failed to load template.');
            }
            var bytes = base64ToUint8Array(data.data.file_content_base64);
            var file = new File([bytes], filename, { type: 'application/pdf' });
            state.playgroundSource = { project: project, filename: filename };
            savePlaygroundBtn.classList.remove('hidden');
            await loadPdf(file);
            showSuccess('Opened ' + filename + ' from Playground.');
        } catch (error) {
            showPdfWorkspace();
            showError('Failed to load from Playground: ' + (error.message || 'Unknown error.'));
        } finally { hideLoading(); }
    }

    // Save to Playground modal
    async function openSavePlaygroundModal() {
        if (!state.auth.isAuthenticated) { showError('Login required to save to Playground.'); return; }
        if (!state.pdfBytes) { showError('No PDF loaded.'); return; }
        // Show modal immediately
        var savePgProject = document.getElementById('save-pg-project');
        var savePgFilename = document.getElementById('save-pg-filename');
        var savePgStatus = document.getElementById('save-pg-status');
        savePgProject.innerHTML = '<option value="">Loading projects...</option>';
        savePgFilename.value = state.playgroundSource.filename || state.fileName || 'template.pdf';
        if (!savePgFilename.value.toLowerCase().endsWith('.pdf'))
            savePgFilename.value += '.pdf';
        savePgStatus.classList.add('hidden');
        savePlaygroundModalEl.classList.remove('hidden');
        // Load projects in background
        (async function() {
            try {
                await fetchPdfPlaygroundProjects();
                savePgProject.innerHTML = '';
                state.playground.projects.forEach(function(proj) {
                    var opt = document.createElement('option');
                    opt.value = proj; opt.textContent = proj;
                    opt.selected = proj === (state.playgroundSource.project || state.playground.selectedProject);
                    savePgProject.appendChild(opt);
                });
            } catch (e) {
                console.error('Error loading projects:', e);
                savePgProject.innerHTML = '<option value="">(error loading projects)</option>';
            }
        })();
    }

    async function confirmSaveToPlayground() {
        var savePgProject = document.getElementById('save-pg-project');
        var savePgFilename = document.getElementById('save-pg-filename');
        var savePgStatus = document.getElementById('save-pg-status');
        var project = savePgProject.value || 'default';
        var filename = savePgFilename.value.trim();
        if (!filename || !filename.toLowerCase().endsWith('.pdf')) {
            savePgStatus.textContent = 'Filename must end with .pdf'; savePgStatus.classList.remove('hidden'); return;
        }
        savePlaygroundModalEl.classList.add('hidden');
        showLoading('Saving to Playground...');
        try {
            // First export to get the latest PDF with field changes applied
            var pdfContent;
            if (state.hasUnsavedChanges && state.fields.length > 0) {
                var formData = new FormData();
                formData.append('file', getPdfFileForRequests());
                formData.append('fields', JSON.stringify(convertFieldsToAbsoluteCoordinates()));
                var exportResponse = await fetch(apiUrl('/pdf-labeler/api/apply-fields'), { method: 'POST', headers: { 'Accept': 'application/json' }, body: formData });
                var exportData = await parseApiResponse(exportResponse);
                if (!exportData.success || !exportData.data || !exportData.data.pdf_base64) {
                    throw new Error('Failed to apply fields before saving.');
                }
                pdfContent = exportData.data.pdf_base64;
            } else {
                // Use current PDF bytes as-is
                var raw = '';
                var bytes = new Uint8Array(state.pdfBytes);
                for (var i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
                pdfContent = window.btoa(raw);
            }

            var response = await fetch(apiUrl('/labeler/api/playground-templates/save'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ project: project, filename: filename, file_content_base64: pdfContent }),
                credentials: 'same-origin'
            });
            var data = await response.json();
            if (!data.success) throw new Error((data.error && data.error.message) || 'Save failed.');
            state.playgroundSource = { project: project, filename: filename };
            savePlaygroundBtn.classList.remove('hidden');
            setDirty(false);
            showPdfWorkspace();
            showSuccess((data.data && data.data.created ? 'Created' : 'Updated') + ' ' + filename + ' in Playground.');
        } catch (error) {
            showPdfWorkspace();
            showError('Save to Playground failed: ' + (error.message || 'Unknown error.'));
        } finally { hideLoading(); }
    }

    function renderModelSuggestions(filterText) {
        const filter = (filterText || '').trim().toLowerCase();
        const source = filter
            ? (state.availableModels.length ? state.availableModels : state.recommendedModels)
            : state.recommendedModels;
        const models = source.filter(function (name) {
            return !filter || name.toLowerCase().includes(filter);
        }).slice(0, 20);
        aiModelSuggestions.innerHTML = '';
        if (models.length === 0) {
            aiModelSuggestions.classList.add('hidden');
            return;
        }
        models.forEach(function (name) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'model-suggestion-btn font-monospace';
            button.textContent = name;
            button.addEventListener('click', function () {
                aiModelInput.value = name;
                state.model = name;
                aiModelSuggestions.classList.add('hidden');
            });
            aiModelSuggestions.appendChild(button);
        });
        aiModelSuggestions.classList.remove('hidden');
    }

    function arrayBufferToUint8Array(buffer) {
        return new Uint8Array(buffer);
    }

    function base64ToUint8Array(base64String) {
        const binaryString = window.atob(base64String);
        const bytes = new Uint8Array(binaryString.length);
        for (let index = 0; index < binaryString.length; index += 1) {
            bytes[index] = binaryString.charCodeAt(index);
        }
        return bytes;
    }

    function clonePdfBytes(pdfBytes) {
        if (!pdfBytes) return null;
        if (pdfBytes instanceof Uint8Array) {
            return new Uint8Array(pdfBytes);
        }
        if (ArrayBuffer.isView(pdfBytes)) {
            return new Uint8Array(pdfBytes.buffer.slice(pdfBytes.byteOffset, pdfBytes.byteOffset + pdfBytes.byteLength));
        }
        if (pdfBytes instanceof ArrayBuffer) {
            return new Uint8Array(pdfBytes.slice(0));
        }
        return new Uint8Array(pdfBytes);
    }

    function looksLikePdfBytes(pdfBytes) {
        if (!pdfBytes || pdfBytes.length < 5) return false;
        return pdfBytes[0] === 0x25 &&
            pdfBytes[1] === 0x50 &&
            pdfBytes[2] === 0x44 &&
            pdfBytes[3] === 0x46 &&
            pdfBytes[4] === 0x2d;
    }

    function applyReturnedPdfPayload(data, fallbackName) {
        if (!data || !data.pdf_base64) {
            return false;
        }
        const nextPdfBytes = base64ToUint8Array(data.pdf_base64);
        if (!looksLikePdfBytes(nextPdfBytes)) {
            throw new Error('AI relabel returned output that was not a valid PDF.');
        }
        syncPdfState(
            nextPdfBytes,
            data.output_filename || data.filename || fallbackName || state.fileName || 'edited-form.pdf'
        );
        return true;
    }

    function didRelabelChangeFieldNames(data) {
        const previous = Array.isArray(data && data.fields_old) ? data.fields_old : [];
        const next = Array.isArray(data && data.fields) ? data.fields : [];
        if (!previous.length || previous.length !== next.length) return null;
        return previous.some(function (name, index) {
            return String(name || '') !== String(next[index] || '');
        });
    }

    function getRelabelChangedCount(data) {
        const previous = Array.isArray(data && data.fields_old) ? data.fields_old : [];
        const next = Array.isArray(data && data.fields) ? data.fields : [];
        if (!previous.length || previous.length !== next.length) return null;
        let changedCount = 0;
        previous.forEach(function (name, index) {
            if (String(name || '') !== String(next[index] || '')) {
                changedCount += 1;
            }
        });
        return changedCount;
    }

    function reconcileRelabeledFields(fields, data) {
        const expectedNames = Array.isArray(data && data.fields)
            ? data.fields.map(function (name) { return String(name || ''); })
            : [];
        const previousNames = Array.isArray(data && data.fields_old)
            ? data.fields_old.map(function (name) { return String(name || ''); })
            : [];

        if (!expectedNames.length) {
            return Array.isArray(fields) ? fields : [];
        }

        const baseFields = Array.isArray(fields) && fields.length
            ? fields.map(function (field) { return Object.assign({}, field); })
            : state.fields.map(function (field) { return Object.assign({}, field); });

        if (baseFields.length !== expectedNames.length) {
            return baseFields;
        }

        if (previousNames.length === expectedNames.length) {
            const renameMap = new Map();
            previousNames.forEach(function (name, index) {
                renameMap.set(name, expectedNames[index]);
            });
            return baseFields.map(function (field, index) {
                const nextName = renameMap.get(String(field.name || '')) || expectedNames[index];
                return Object.assign({}, field, { name: nextName || field.name });
            });
        }

        return baseFields.map(function (field, index) {
            return Object.assign({}, field, { name: expectedNames[index] || field.name });
        });
    }

    function logRelabelDebug(data, detectedFields, relabeledFields) {
        const previousNames = Array.isArray(data && data.fields_old) ? data.fields_old.slice() : [];
        const nextNames = Array.isArray(data && data.fields) ? data.fields.slice() : [];
        const renamedCount = getRelabelChangedCount(data);
        const detectedNames = Array.isArray(detectedFields)
            ? detectedFields.map(function (field) { return field.name; })
            : [];
        const finalNames = Array.isArray(relabeledFields)
            ? relabeledFields.map(function (field) { return field.name; })
            : [];
        console.groupCollapsed('[pdf-labeler] AI relabel result');
        console.info('renamedCount:', renamedCount);
        console.info('worker fields_old:', previousNames);
        console.info('worker fields:', nextNames);
        console.info('detected field names after relabel:', detectedNames);
        console.info('final field names rendered in UI:', finalNames);
        console.info('raw relabel payload:', data);
        console.groupEnd();
    }

    function updateRequestPdfFile(pdfBytes, fileName, originalFile) {
        const nextName = fileName || state.fileName || 'edited-form.pdf';
        if (originalFile instanceof File && originalFile.size > 0) {
            if (originalFile.name === nextName) {
                state.requestPdfFile = originalFile;
            } else {
                state.requestPdfFile = new File([originalFile], nextName, { type: 'application/pdf' });
            }
            return;
        }
        if (pdfBytes && pdfBytes.length) {
            state.requestPdfFile = new File([pdfBytes], nextName, { type: 'application/pdf' });
            return;
        }
        state.requestPdfFile = null;
    }

    function syncPdfState(pdfBytes, fileName, originalFile) {
        state.pdfBytes = clonePdfBytes(pdfBytes);
        if (fileName) {
            state.fileName = fileName;
        }
        updateRequestPdfFile(state.pdfBytes, state.fileName, originalFile);
    }

    async function refreshPdfDocumentFromState(renderOptions) {
        state.pdfDoc = await pdfjsLib.getDocument({ data: clonePdfBytes(state.pdfBytes) }).promise;
        state.pageCount = state.pdfDoc.numPages;
        state.pageSizes = [];
        state.pageTextBoxes = [];

        for (let index = 1; index <= state.pageCount; index += 1) {
            const page = await state.pdfDoc.getPage(index);
            const viewport = page.getViewport({ scale: 1 });
            state.pageSizes.push({ width: viewport.width, height: viewport.height });
            state.pageTextBoxes.push(await extractTextBoxesFromPage(page));
        }

        await renderPages(renderOptions);
    }

    function stripPdfExtension(fileName) {
        return String(fileName || 'document').replace(/\.pdf$/i, '');
    }

    function sanitizeFilenameSegment(value, fallbackValue) {
        const cleaned = String(value || '')
            .replace(/[^a-z0-9._-]+/gi, '-')
            .replace(/-+/g, '-')
            .replace(/^-+|-+$/g, '');
        return cleaned || fallbackValue || 'document';
    }

    function downloadBlob(blob, fileName) {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = fileName;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        URL.revokeObjectURL(url);
    }

    function getPdfFileForRequests() {
        if (state.requestPdfFile && state.requestPdfFile.size > 0) {
            return state.requestPdfFile;
        }
        updateRequestPdfFile(state.pdfBytes, state.fileName);
        if (state.requestPdfFile && state.requestPdfFile.size > 0) {
            return state.requestPdfFile;
        }
        return new File([], state.fileName || 'edited-form.pdf', { type: 'application/pdf' });
    }

    async function parseApiResponse(response) {
        const text = await response.text();
        let data = null;
        try {
            data = text ? JSON.parse(text) : {};
        } catch (_error) {
            const returnedHtml = text.trim().startsWith('<');
            const statusText = response.status ? ('HTTP ' + response.status) : 'request';
            const finalUrl = response.url ? (' from ' + response.url) : '';
            if (returnedHtml && response.redirected && response.url && response.url.indexOf('/user/sign-in') !== -1) {
                throw new Error('Login required for this AI action.');
            }
            throw new Error(returnedHtml
                ? ('The server returned HTML instead of JSON (' + statusText + finalUrl + ').')
                : ('The server returned invalid JSON (' + statusText + finalUrl + ').'));
        }
        if (!response.ok) {
            throw new Error((data && data.error && data.error.message) ? data.error.message : ('Request failed (' + response.status + ').'));
        }
        return data;
    }

    function sleep(milliseconds) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, milliseconds);
        });
    }

    async function pollLabelerJob(jobUrl, progressMessage) {
        const startedAt = Date.now();
        const timeoutMs = 8 * 60 * 1000;
        while ((Date.now() - startedAt) < timeoutMs) {
            await sleep(1500);
            const response = await fetch(jobUrl, {
                method: 'GET',
                headers: { 'Accept': 'application/json' }
            });
            const payload = await parseApiResponse(response);
            if (payload.status === 'queued' || payload.status === 'running') {
                showLoading(progressMessage);
                continue;
            }
            if (payload.status === 'failed') {
                throw new Error((payload.error && payload.error.message) || 'Background job failed.');
            }
            if (payload.status === 'succeeded') {
                return payload.data || {};
            }
            throw new Error('Unexpected background job status.');
        }
        throw new Error('The background job timed out before it completed.');
    }

    async function requestAsyncLabelerAction(endpointPath, formData, progressMessage) {
        formData.append('mode', 'async');
        const response = await fetch(apiUrl(endpointPath), {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData
        });
        const payload = await parseApiResponse(response);
        if (payload.status === 'queued' && payload.job_url) {
            showLoading(progressMessage);
            return pollLabelerJob(payload.job_url, progressMessage);
        }
        return (payload && payload.data) ? payload.data : {};
    }

    function normalizeExistingRect(rect, pageSize) {
        const normalizedWidth = clamp(rect.width / pageSize.width, 0.01, 1);
        const normalizedHeight = clamp(rect.height / pageSize.height, 0.01, 1);
        const normalizedX = clamp(rect.x / pageSize.width, 0, 1 - normalizedWidth);
        const normalizedY = clamp(1 - (rect.y + rect.height) / pageSize.height, 0, 1 - normalizedHeight);
        return {
            x: normalizedX,
            y: normalizedY,
            width: normalizedWidth,
            height: normalizedHeight
        };
    }

    async function parseExistingFieldsLocally(pdfBytes, pageSizes) {
        if (!PDFLibGlobal.PDFDocument) {
            return [];
        }
        const fields = [];
        try {
            const pdfDoc = await PDFLibGlobal.PDFDocument.load(clonePdfBytes(pdfBytes));
            const form = pdfDoc.getForm();
            const pdfPages = pdfDoc.getPages();

            form.getFields().forEach(function (field) {
                let type = null;
                let options = undefined;

                if (PDFLibGlobal.PDFTextField && field instanceof PDFLibGlobal.PDFTextField) {
                    type = field.isMultiline() ? 'multiline' : 'text';
                } else if (PDFLibGlobal.PDFCheckBox && field instanceof PDFLibGlobal.PDFCheckBox) {
                    type = 'checkbox';
                } else if (PDFLibGlobal.PDFSignature && field instanceof PDFLibGlobal.PDFSignature) {
                    type = 'signature';
                } else if (PDFLibGlobal.PDFRadioGroup && field instanceof PDFLibGlobal.PDFRadioGroup) {
                    type = 'radio';
                    options = typeof field.getOptions === 'function' ? field.getOptions() : DEFAULT_OPTION_LIST.slice();
                } else if (PDFLibGlobal.PDFDropdown && field instanceof PDFLibGlobal.PDFDropdown) {
                    type = 'dropdown';
                    options = typeof field.getOptions === 'function' ? field.getOptions() : DEFAULT_OPTION_LIST.slice();
                } else if (PDFLibGlobal.PDFOptionList && field instanceof PDFLibGlobal.PDFOptionList) {
                    type = 'listbox';
                    options = typeof field.getOptions === 'function' ? field.getOptions() : DEFAULT_OPTION_LIST.slice();
                }

                if (!type) return;

                const widgets = field.acroField.getWidgets();
                widgets.forEach(function (widget) {
                    const rect = widget.getRectangle();
                    if (!rect) return;

                    const pageRef = widget.P();
                    let pageIndex = 0;
                    if (pageRef) {
                        const foundIndex = pdfPages.findIndex(function (page) {
                            return ((page.ref || null) === pageRef);
                        });
                        if (foundIndex >= 0) {
                            pageIndex = foundIndex;
                        }
                    }
                    const pageSize = pageSizes[pageIndex] || pdfPages[pageIndex].getSize();
                    const normalizedRect = normalizeExistingRect(rect, pageSize);
                    fields.push({
                        id: generateId(),
                        name: field.getName() || getDefaultFieldName(type),
                        type: type,
                        pageIndex: pageIndex,
                        x: normalizedRect.x,
                        y: normalizedRect.y,
                        width: normalizedRect.width,
                        height: normalizedRect.height,
                        font: getSessionDefault('font'),
                        fontSize: getSessionDefault('fontSize'),
                        autoSize: true,
                        options: Array.isArray(options) ? options.slice() : undefined
                    });
                });
            });
            return fields;
        } catch (error) {
            console.warn('Unable to parse existing PDF fields locally', error);
            return [];
        }
    }

    async function detectExistingFieldsWithServer() {
        if (!state.pdfBytes) return [];
        const formData = new FormData();
        formData.append('file', getPdfFileForRequests());
        const response = await fetch(apiUrl('/pdf-labeler/api/detect-fields'), {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData
        });
        const data = await parseApiResponse(response);
        const importedFields = Array.isArray(data.data && data.data.fields) ? data.data.fields : [];
        return importedFields.map(function (field) {
            const pageSize = state.pageSizes[field.pageIndex];
            const fieldType = normalizeFieldType(field.type);
            const width = pageSize ? clamp(field.width / pageSize.width, 0.01, 1) : 0.1;
            const height = pageSize ? clamp(field.height / pageSize.height, 0.01, 1) : 0.03;
            const x = pageSize ? clamp(field.x / pageSize.width, 0, 1 - width) : 0;
            const y = pageSize ? clamp(1 - ((field.y + field.height) / pageSize.height), 0, 1 - height) : 0;
            return {
                id: generateId(),
                name: field.name || getDefaultFieldName(fieldType),
                type: fieldType,
                pageIndex: field.pageIndex,
                x: x,
                y: y,
                width: width,
                height: height,
                font: field.font || getSessionDefault('font'),
                fontSize: field.fontSize || getSessionDefault('fontSize'),
                autoSize: field.autoSize !== false,
                options: Array.isArray(field.options) ? field.options.slice() : undefined
            };
        });
    }

    async function runRepairAction(action) {
        if (!state.pdfBytes) return;
        var allBtns = repairModal.querySelectorAll('.repair-run-btn');
        allBtns.forEach(function (b) { b.disabled = true; });
        repairStatus.className = 'alert alert-info small mb-3';
        repairStatusText.textContent = 'Running ' + action + '...';
        repairStatus.classList.remove('hidden');

        try {
            var formData = new FormData();
            formData.append('file', getPdfFileForRequests());
            formData.append('action', action);

            if (action === 'ghostscript_reprint') {
                var preserve = document.getElementById('repair-gs-preserve').checked;
                formData.append('preserve_fields', preserve ? 'true' : 'false');
                var pdfOpt = document.getElementById('repair-gs-optimization').value || 'prepress';
                formData.append('pdf_optimization', pdfOpt);
            } else if (action === 'unlock') {
                var pw = document.getElementById('repair-unlock-pw').value;
                if (pw) formData.append('password', pw);
            } else if (action === 'ocr') {
                var lang = document.getElementById('repair-ocr-lang').value.trim() || 'eng';
                formData.append('language', lang);
                var skip = document.getElementById('repair-ocr-skip').checked;
                formData.append('skip_text', skip ? 'true' : 'false');
            }

            var response = await fetch(apiUrl('/pdf-labeler/api/repair'), {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: formData
            });
            var result = await parseApiResponse(response);
            if (!result.success) {
                var errMsg = (result.error && result.error.message) || 'Repair failed.';
                throw new Error(errMsg);
            }

            var data = result.data || {};
            var pdfBase64 = data.pdf_base64;
            if (!pdfBase64) throw new Error('No repaired PDF was returned.');

            // Decode base64 to Uint8Array
            var binaryStr = atob(pdfBase64);
            var bytes = new Uint8Array(binaryStr.length);
            for (var i = 0; i < binaryStr.length; i++) {
                bytes[i] = binaryStr.charCodeAt(i);
            }

            // Reload the repaired PDF into the editor
            var repairedFile = new File([bytes], data.filename || state.fileName || 'repaired.pdf', { type: 'application/pdf' });
            repairModal.classList.add('hidden');
            await loadPdf(repairedFile);

            var repairResult = data.repair_result || {};
            var summaryParts = [action + ' completed'];
            if (repairResult.fields_restored) {
                summaryParts.push(repairResult.fields_restored + ' fields restored');
            }
            if (repairResult.warnings && repairResult.warnings.length) {
                summaryParts.push(repairResult.warnings.join('; '));
            }
            showSuccess(summaryParts.join('. ') + '.');
        } catch (err) {
            repairStatus.className = 'alert alert-danger small mb-3';
            repairStatusText.textContent = err.message || 'Repair failed.';
            allBtns.forEach(function (b) { b.disabled = false; });
        }
    }

    function getFieldNameHelp(field) {
        const rawName = String(field.name || '').trim();
        const normalized = normalizePdfFieldName(rawName);
        const duplicateExists = state.fields.some(function (candidate) {
            return candidate.id !== field.id && normalizePdfFieldName(candidate.name) === normalized;
        });

        if (!rawName) {
            return {
                level: 'warning',
                html: 'Field labels are required.'
            };
        }
        if (duplicateExists) {
            return {
                level: 'warning',
                html: 'Another field already uses this PDF label. Rename it before export to avoid automatic suffixes.'
            };
        }
        if (rawName !== normalized && /[\[\].()]/.test(rawName)) {
            return {
                level: 'info',
                html: 'This looks like DOCX/Jinja syntax. PDFs should use labels like <code>' + escapeHtml(normalized) + '</code>.'
            };
        }
        return {
            level: 'info',
            html: 'AssemblyLine PDF labels use underscores and inline list numbers, for example <code>users1_name_full</code>.'
        };
    }

    function renderFontOptions(selectedFont) {
        return FONT_OPTIONS.map(function (font) {
            return '<option value="' + escapeHtml(font) + '"' + (font === selectedFont ? ' selected' : '') + '>' + escapeHtml(font) + '</option>';
        }).join('');
    }

    function ptToNormalizedLength(points, pageIndex, axis) {
        const pageSize = state.pageSizes[pageIndex] || { width: 612, height: 792 };
        const dimension = axis === 'x' ? pageSize.width : pageSize.height;
        return clamp(Number(points) / dimension, 0.001, 1);
    }

    function looksLikeNameOrAddressField(fieldName) {
        return /(name|address|street|city|state|zip|postal|phone|phone_number|email|cell)/i.test(String(fieldName || ''));
    }

    function isYesNoOption(optionLabel) {
        const normalized = String(optionLabel || '').trim().toLowerCase();
        return normalized === 'yes' || normalized === 'true' || normalized === 'no' || normalized === 'false';
    }

    function getRadioOptionSuffixes(options) {
        const normalized = options.map(function (option) {
            return String(option || '').trim().toLowerCase();
        });
        if (normalized.length === 2) {
            const yesIndex = normalized.findIndex(function (option) {
                return option === 'yes' || option === 'true';
            });
            const noIndex = normalized.findIndex(function (option) {
                return option === 'no' || option === 'false';
            });
            if (yesIndex >= 0 && noIndex >= 0) {
                return normalized.map(function (_option, index) {
                    return index === yesIndex ? 'yes' : 'no';
                });
            }
        }
        return normalized.map(function (_option, index) {
            return index === 0 ? 'opt' : ('opt' + String(index + 1));
        });
    }

    function dedupeNormalizedFieldNames(fields) {
        const used = new Set();
        return fields.map(function (field) {
            const updated = Object.assign({}, field);
            const baseName = normalizePdfFieldName(updated.name);
            let candidate = baseName;
            let suffix = 1;
            while (used.has(candidate)) {
                candidate = baseName + '__' + suffix;
                suffix += 1;
            }
            used.add(candidate);
            updated.name = candidate;
            return updated;
        });
    }

    function alignFieldsByRow(fields, thresholdPoints) {
        const groupedByPage = {};
        fields.forEach(function (field) {
            if (!(field.type === 'text' || field.type === 'multiline' || field.type === 'checkbox')) return;
            if (!groupedByPage[field.pageIndex]) {
                groupedByPage[field.pageIndex] = [];
            }
            groupedByPage[field.pageIndex].push(field);
        });

        Object.keys(groupedByPage).forEach(function (pageKey) {
            const pageIndex = Number(pageKey);
            const threshold = ptToNormalizedLength(thresholdPoints, pageIndex, 'y');
            const pageFields = groupedByPage[pageKey].slice().sort(function (left, right) {
                return (left.y + (left.height / 2)) - (right.y + (right.height / 2));
            });
            let cluster = [];
            pageFields.forEach(function (field) {
                const centerY = field.y + (field.height / 2);
                if (!cluster.length) {
                    cluster = [field];
                    return;
                }
                const clusterCenter = cluster.reduce(function (sum, item) {
                    return sum + item.y + (item.height / 2);
                }, 0) / cluster.length;
                if (Math.abs(centerY - clusterCenter) <= threshold) {
                    cluster.push(field);
                    return;
                }
                if (cluster.length > 1) {
                    const targetCenter = cluster.reduce(function (sum, item) {
                        return sum + item.y + (item.height / 2);
                    }, 0) / cluster.length;
                    cluster.forEach(function (item) {
                        item.y = clamp(targetCenter - (item.height / 2), 0, 1 - item.height);
                    });
                }
                cluster = [field];
            });
            if (cluster.length > 1) {
                const targetCenter = cluster.reduce(function (sum, item) {
                    return sum + item.y + (item.height / 2);
                }, 0) / cluster.length;
                cluster.forEach(function (item) {
                    item.y = clamp(targetCenter - (item.height / 2), 0, 1 - item.height);
                });
            }
        });
    }

    function alignFieldsByColumn(fields, thresholdPoints) {
        const groupedByPage = {};
        fields.forEach(function (field) {
            if (!(field.type === 'text' || field.type === 'multiline' || field.type === 'checkbox')) return;
            if (!groupedByPage[field.pageIndex]) {
                groupedByPage[field.pageIndex] = [];
            }
            groupedByPage[field.pageIndex].push(field);
        });

        Object.keys(groupedByPage).forEach(function (pageKey) {
            const pageIndex = Number(pageKey);
            const threshold = ptToNormalizedLength(thresholdPoints, pageIndex, 'x');
            const pageFields = groupedByPage[pageKey].slice().sort(function (left, right) {
                return left.x - right.x;
            });
            let cluster = [];
            pageFields.forEach(function (field) {
                if (!cluster.length) {
                    cluster = [field];
                    return;
                }
                const clusterLeft = cluster.reduce(function (sum, item) {
                    return sum + item.x;
                }, 0) / cluster.length;
                if (Math.abs(field.x - clusterLeft) <= threshold) {
                    cluster.push(field);
                    return;
                }
                if (cluster.length > 1) {
                    var targetLeft = cluster.reduce(function (sum, item) {
                        return sum + item.x;
                    }, 0) / cluster.length;
                    cluster.forEach(function (item) {
                        item.x = clamp(targetLeft, 0, 1 - item.width);
                    });
                }
                cluster = [field];
            });
            if (cluster.length > 1) {
                var targetLeft = cluster.reduce(function (sum, item) {
                    return sum + item.x;
                }, 0) / cluster.length;
                cluster.forEach(function (item) {
                    item.x = clamp(targetLeft, 0, 1 - item.width);
                });
            }
        });
    }

    function readNormalizationSettings() {
        return {
            normalizeCheckboxStyle: document.getElementById('norm-checkbox-style-enable').checked,
            checkboxStyle: document.getElementById('norm-checkbox-style').value || '',
            checkboxExportValue: String(document.getElementById('norm-checkbox-export-value').value || '').trim() || 'Yes',
            uniformCheckboxSize: document.getElementById('norm-checkbox-uniform').checked,
            checkboxSizePt: Number(document.getElementById('norm-checkbox-size').value || 12),
            normalizeFont: document.getElementById('norm-font-normalize').checked,
            normalizeFontSize: document.getElementById('norm-fontsize-normalize').checked,
            fontName: document.getElementById('norm-font-name').value || getSessionDefault('font'),
            fontSizePt: Number(document.getElementById('norm-font-size').value || getSessionDefault('fontSize')),
            dropdownsToText: document.getElementById('norm-dropdowns-to-text').checked,
            radiosToCheckboxes: document.getElementById('norm-radios-to-checkboxes').checked,
            alignRows: document.getElementById('norm-align-rows').checked,
            alignThresholdPt: Number(document.getElementById('norm-align-threshold').value || 6),
            alignColumns: document.getElementById('norm-align-columns').checked,
            alignColumnThresholdPt: Number(document.getElementById('norm-align-col-threshold').value || 6),
            autoSizeNameAddress: document.getElementById('norm-autosize-name-address').checked,
            fixedTextHeightPt: Number(document.getElementById('norm-text-height').value || 14),
            disableAutoScroll: document.getElementById('norm-disable-scroll').checked,
            multilineTallFields: document.getElementById('norm-multiline-tall').checked,
            multilineThresholdLines: Number(document.getElementById('norm-multiline-lines').value || 2.2),
            multilineWhiteBackground: document.getElementById('norm-multiline-white').checked,
            removeEmbeddedFonts: document.getElementById('norm-remove-embedded-fonts').checked
        };
    }

    function buildNormalizedFields(settings) {
        const transformed = [];
        state.fields.forEach(function (field) {
            const nextField = Object.assign({}, field);
            nextField.font = nextField.font || getSessionDefault('font');
            nextField.fontSize = Number(nextField.fontSize) || getSessionDefault('fontSize');

            if (settings.dropdownsToText && nextField.type === 'dropdown') {
                nextField.type = 'text';
                delete nextField.options;
            }

            if (settings.radiosToCheckboxes && nextField.type === 'radio') {
                const options = Array.isArray(nextField.options) && nextField.options.length
                    ? nextField.options.slice()
                    : ['Yes', 'No'];
                const suffixes = getRadioOptionSuffixes(options);
                const boxWidth = ptToNormalizedLength(settings.checkboxSizePt, nextField.pageIndex, 'x');
                const boxHeight = ptToNormalizedLength(settings.checkboxSizePt, nextField.pageIndex, 'y');
                const gap = ptToNormalizedLength(Math.max(4, settings.checkboxSizePt * 0.35), nextField.pageIndex, 'x');
                const totalWidth = (options.length * boxWidth) + ((options.length - 1) * gap);
                const startX = clamp(nextField.x + Math.max(0, (nextField.width - totalWidth) / 2), 0, 1 - boxWidth);
                const targetY = clamp((nextField.y + (nextField.height / 2)) - (boxHeight / 2), 0, 1 - boxHeight);
                const baseName = normalizePdfFieldName(nextField.name);
                options.forEach(function (_option, index) {
                    transformed.push({
                        id: generateId(),
                        name: baseName + '_' + suffixes[index],
                        type: 'checkbox',
                        pageIndex: nextField.pageIndex,
                        x: clamp(startX + (index * (boxWidth + gap)), 0, 1 - boxWidth),
                        y: targetY,
                        width: boxWidth,
                        height: boxHeight,
                        font: settings.normalizeFont ? settings.fontName : nextField.font,
                        fontSize: settings.normalizeFontSize ? settings.fontSizePt : nextField.fontSize,
                        autoSize: false,
                        checkboxStyle: (settings.normalizeCheckboxStyle && settings.checkboxStyle) || nextField.checkboxStyle || 'check',
                        checkboxExportValue: (settings.normalizeCheckboxStyle && settings.checkboxExportValue) || nextField.checkboxExportValue || 'Yes',
                        allowScroll: false
                    });
                });
                return;
            }

            if (settings.normalizeFont && (nextField.type === 'text' || nextField.type === 'multiline' || nextField.type === 'signature')) {
                nextField.font = settings.fontName;
            }
            if (settings.normalizeFontSize && (nextField.type === 'text' || nextField.type === 'multiline' || nextField.type === 'signature')) {
                nextField.fontSize = settings.fontSizePt;
            }

            if (settings.normalizeCheckboxStyle && nextField.type === 'checkbox' && settings.checkboxStyle) {
                nextField.checkboxStyle = settings.checkboxStyle;
            }

            if (settings.normalizeCheckboxStyle && nextField.type === 'checkbox' && settings.checkboxExportValue) {
                nextField.checkboxExportValue = settings.checkboxExportValue;
            }

            if (settings.uniformCheckboxSize && nextField.type === 'checkbox') {
                nextField.width = ptToNormalizedLength(settings.checkboxSizePt, nextField.pageIndex, 'x');
                nextField.height = ptToNormalizedLength(settings.checkboxSizePt, nextField.pageIndex, 'y');
            }

            if (settings.disableAutoScroll && (nextField.type === 'text' || nextField.type === 'multiline')) {
                nextField.allowScroll = false;
            }

            if (settings.autoSizeNameAddress && nextField.type === 'text' && looksLikeNameOrAddressField(nextField.name)) {
                nextField.autoSize = true;
                nextField.height = ptToNormalizedLength(settings.fixedTextHeightPt, nextField.pageIndex, 'y');
            }

            if (settings.multilineTallFields && nextField.type === 'text') {
                const lineHeightPt = settings.fontSizePt * 1.2;
                const fieldHeightPt = nextField.height * (state.pageSizes[nextField.pageIndex] ? state.pageSizes[nextField.pageIndex].height : 792);
                if ((fieldHeightPt / lineHeightPt) >= settings.multilineThresholdLines) {
                    nextField.type = 'multiline';
                }
            }

            if (settings.multilineWhiteBackground && nextField.type === 'multiline') {
                nextField.backgroundColor = '#ffffff';
            }

            transformed.push(nextField);
        });

        if (settings.alignRows) {
            alignFieldsByRow(transformed, settings.alignThresholdPt);
        }
        if (settings.alignColumns) {
            alignFieldsByColumn(transformed, settings.alignColumnThresholdPt);
        }
        return dedupeNormalizedFieldNames(transformed);
    }

    function renderFieldEditor(field) {
        const meta = getFieldTypeMeta(field.type);
        const nameHelp = getFieldNameHelp(field);
        const optionsValue = Array.isArray(field.options) && field.options.length
            ? field.options.join(', ')
            : DEFAULT_OPTION_LIST.join(', ');
        const isTextLike = field.type === 'text' || field.type === 'multiline';
        const autoSize = field.autoSize !== false;
        return '' +
            '<div class="field-card-editor">' +
                '<div class="d-flex align-items-center justify-content-between gap-2 mb-3">' +
                    '<div class="field-type-badge">' + meta.icon + '<span>' + escapeHtml(meta.label) + '</span></div>' +
                    '<div class="field-card-subtext">Editing selected field</div>' +
                '</div>' +
                '<div class="mb-3">' +
                    '<label class="form-label small text-muted fw-semibold mb-1" for="field-name-' + escapeHtml(field.id) + '">PDF field label</label>' +
                    '<div class="d-flex gap-2">' +
                        '<input type="text" id="field-name-' + escapeHtml(field.id) + '" class="form-control form-control-sm font-monospace" data-action="field-name" data-field-id="' + escapeHtml(field.id) + '" value="' + escapeHtml(field.name) + '">' +
                        '<button type="button" class="btn btn-outline-secondary btn-sm" data-action="normalize-name" data-field-id="' + escapeHtml(field.id) + '">Normalize</button>' +
                    '</div>' +
                    '<div class="field-card-help ' + escapeHtml(nameHelp.level) + '">' + nameHelp.html + '</div>' +
                '</div>' +
                '<div class="mb-3">' +
                    '<label class="form-label small text-muted fw-semibold mb-1" for="field-type-' + escapeHtml(field.id) + '">Field type</label>' +
                    '<select id="field-type-' + escapeHtml(field.id) + '" class="form-select form-select-sm" data-action="field-type" data-field-id="' + escapeHtml(field.id) + '">' +
                        '<option value="text"' + (field.type === 'text' ? ' selected' : '') + '>Single-line Text</option>' +
                        '<option value="multiline"' + (field.type === 'multiline' ? ' selected' : '') + '>Multi-line Text</option>' +
                        '<option value="checkbox"' + (field.type === 'checkbox' ? ' selected' : '') + '>Checkbox</option>' +
                        '<option value="signature"' + (field.type === 'signature' ? ' selected' : '') + '>Signature</option>' +
                        '<option value="radio"' + (field.type === 'radio' ? ' selected' : '') + '>Radio Group</option>' +
                        '<option value="dropdown"' + (field.type === 'dropdown' ? ' selected' : '') + '>Dropdown</option>' +
                        '<option value="listbox"' + (field.type === 'listbox' ? ' selected' : '') + '>List Box</option>' +
                    '</select>' +
                '</div>' +
                (isTextLike
                    ? '<div class="mb-3">' +
                        '<label class="form-label small text-muted fw-semibold mb-1">Font and size</label>' +
                        '<div class="font-row">' +
                            '<div>' +
                                '<select class="form-select form-select-sm" data-action="field-font" data-field-id="' + escapeHtml(field.id) + '">' +
                                    renderFontOptions(field.font || getSessionDefault('font')) +
                                '</select>' +
                            '</div>' +
                            '<label class="auto-size-wrap form-check small mb-0 mt-1">' +
                                '<input class="form-check-input" type="checkbox" data-action="field-auto-size" data-field-id="' + escapeHtml(field.id) + '"' + (autoSize ? ' checked' : '') + '>' +
                                '<span class="form-check-label">Auto size</span>' +
                            '</label>' +
                            '<div>' +
                                '<input type="number" min="4" max="72" step="1" class="form-control form-control-sm" data-action="field-font-size" data-field-id="' + escapeHtml(field.id) + '" value="' + escapeHtml(String(field.fontSize || getSessionDefault('fontSize'))) + '"' + (autoSize ? ' disabled' : '') + '>' +
                            '</div>' +
                        '</div>' +
                    '</div>'
                    : '') +
                (field.type === 'checkbox'
                    ? '<div class="mb-3">' +
                        '<label class="form-label small text-muted fw-semibold mb-1">Checkbox settings</label>' +
                        '<div class="normalization-inline-2">' +
                            '<div>' +
                                '<label class="form-label small text-muted mb-1" for="field-checkbox-style-' + escapeHtml(field.id) + '">Style</label>' +
                                '<select id="field-checkbox-style-' + escapeHtml(field.id) + '" class="form-select form-select-sm" data-action="field-checkbox-style" data-field-id="' + escapeHtml(field.id) + '">' +
                                    '<option value="cross"' + ((field.checkboxStyle || 'check') === 'cross' ? ' selected' : '') + '>Cross</option>' +
                                    '<option value="check"' + ((field.checkboxStyle || 'check') === 'check' ? ' selected' : '') + '>Check</option>' +
                                    '<option value="circle"' + (field.checkboxStyle === 'circle' ? ' selected' : '') + '>Circle</option>' +
                                    '<option value="star"' + (field.checkboxStyle === 'star' ? ' selected' : '') + '>Star</option>' +
                                    '<option value="diamond"' + (field.checkboxStyle === 'diamond' ? ' selected' : '') + '>Diamond</option>' +
                                    '<option value="square"' + (field.checkboxStyle === 'square' ? ' selected' : '') + '>Square</option>' +
                                '</select>' +
                            '</div>' +
                            '<div>' +
                                '<label class="form-label small text-muted mb-1" for="field-checkbox-export-value-' + escapeHtml(field.id) + '">Export value</label>' +
                                '<input id="field-checkbox-export-value-' + escapeHtml(field.id) + '" type="text" class="form-control form-control-sm font-monospace" data-action="field-checkbox-export-value" data-field-id="' + escapeHtml(field.id) + '" value="' + escapeHtml(String(field.checkboxExportValue || 'Yes')) + '">' +
                            '</div>' +
                        '</div>' +
                    '</div>'
                    : '') +
                (OPTION_TYPES.has(field.type)
                    ? '<div class="mb-3">' +
                        '<label class="form-label small text-muted fw-semibold mb-1" for="field-options-' + escapeHtml(field.id) + '">Options</label>' +
                        '<textarea id="field-options-' + escapeHtml(field.id) + '" rows="3" class="form-control form-control-sm" data-action="field-options" data-field-id="' + escapeHtml(field.id) + '">' + escapeHtml(optionsValue) + '</textarea>' +
                        '<div class="form-text small">Separate each option with a comma.</div>' +
                    '</div>'
                    : '') +
                '<div class="field-card-actions">' +
                    '<button type="button" class="btn btn-outline-secondary btn-sm" data-action="duplicate-field" data-field-id="' + escapeHtml(field.id) + '">Duplicate</button>' +
                    '<button type="button" class="btn btn-outline-danger btn-sm" data-action="delete-field" data-field-id="' + escapeHtml(field.id) + '">Delete</button>' +
                '</div>' +
            '</div>';
    }

    function renderFieldsList() {
        updateBulkRenameUiState();
        updateFieldCount();
        if (state.fields.length === 0) {
            fieldsList.innerHTML = '';
            return;
        }
        const showPageGroups = state.pageCount > 1 && state.fieldSort === 'position';
        const orderedFields = getDisplayedFields();
        const renamePreview = state.fieldsPanelMode === 'rename' ? getBulkRenamePreview() : null;
        fieldsList.innerHTML = '';
        if (orderedFields.length === 0) {
            fieldsList.innerHTML = '<div class="text-muted small px-1 py-2">No fields match the current filter.</div>';
            return;
        }
        const fragment = document.createDocumentFragment();
        let currentGroup = null;
        let currentPage = null;

        orderedFields.forEach(function (field) {
            const meta = getFieldTypeMeta(field.type);
            if (!currentGroup || (showPageGroups && currentPage !== field.pageIndex)) {
                currentPage = field.pageIndex;
                currentGroup = document.createElement('section');
                currentGroup.className = 'field-list-page';
                if (showPageGroups) {
                    currentGroup.dataset.pageIndex = String(field.pageIndex);
                }
                if (showPageGroups) {
                    const divider = document.createElement('div');
                    divider.className = 'field-page-divider';
                    divider.textContent = 'Page ' + (field.pageIndex + 1);
                    currentGroup.appendChild(divider);
                }
                fragment.appendChild(currentGroup);
            }
            const item = document.createElement('div');
            item.className = 'field-list-item' + (field.id === state.selectedFieldId ? ' selected' : '');
            item.dataset.fieldId = field.id;
            const renameChange = renamePreview ? renamePreview.renamedById.get(field.id) : null;
            const displayNameHtml = renameChange
                ? (
                    '<div class="field-list-name field-list-name-preview" title="' + escapeHtml(renameChange.oldName + ' -> ' + renameChange.newName) + '">' +
                        '<span class="rename-old">' + escapeHtml(renameChange.oldName) + '</span>' +
                        '<span class="rename-arrow">-></span>' +
                        '<span class="rename-new">' + escapeHtml(renameChange.newName) + '</span>' +
                    '</div>'
                )
                : ('<div class="field-list-name" title="' + escapeHtml(field.name) + '">' + escapeHtml(field.name) + '</div>');
            item.innerHTML =
                '<div class="field-list-row">' +
                    '<div class="field-list-icon" aria-hidden="true">' + meta.icon + '</div>' +
                    displayNameHtml +
                '</div>' +
                (field.id === state.selectedFieldId ? renderFieldEditor(field) : '');
            item.addEventListener('click', function (event) {
                if (event.target.closest('input, select, textarea, button')) {
                    return;
                }
                selectField(field.id, { focusNameInput: false, scrollIntoView: true });
            });
            item.querySelectorAll('input, select, textarea').forEach(function (control) {
                control.addEventListener('click', function (event) {
                    event.stopPropagation();
                });
            });
            currentGroup.appendChild(item);
        });
        fieldsList.appendChild(fragment);
    }

    function renderDraftRect() {
        document.querySelectorAll('.draft-rect').forEach(function (node) { node.remove(); });
        if (!draftState) return;
        const overlay = getOverlayForPage(draftState.pageIndex);
        if (!overlay) return;
        const draftRect = document.createElement('div');
        draftRect.className = 'draft-rect';
        draftRect.style.left = (Math.min(draftState.startX, draftState.currentX) * 100) + '%';
        draftRect.style.top = (Math.min(draftState.startY, draftState.currentY) * 100) + '%';
        draftRect.style.width = (Math.abs(draftState.currentX - draftState.startX) * 100) + '%';
        draftRect.style.height = (Math.abs(draftState.currentY - draftState.startY) * 100) + '%';
        overlay.appendChild(draftRect);
    }

    function getFieldBoxClasses(field) {
        return 'field-box' + (field.id === state.selectedFieldId ? ' selected' : '');
    }

    function applyFieldChipLayout(fieldEl, labelEl) {
        const width = fieldEl.offsetWidth;
        const height = fieldEl.offsetHeight;
        labelEl.classList.remove('hide-name', 'icon-only', 'wrap-name', 'compact');

        if (width < 20 || height < 11) {
            labelEl.classList.add('hide-name', 'icon-only');
            return;
        }

        if (width < 34) {
            labelEl.classList.add('hide-name', 'icon-only');
            return;
        }

        if (height < 16) {
            if (width >= 60) {
                labelEl.classList.add('compact');
                return;
            }
            labelEl.classList.add('hide-name', 'icon-only');
            return;
        }

        if (width >= 96 && height >= 24) {
            labelEl.classList.add('wrap-name');
        }
    }

    function renderFieldsOnPages() {
        document.querySelectorAll('.field-box').forEach(function (node) {
            node.remove();
        });
        state.fields.forEach(function (field) {
            const overlay = getOverlayForPage(field.pageIndex);
            if (!overlay) return;

            const meta = getFieldTypeMeta(field.type);
            const fieldEl = document.createElement('div');
            fieldEl.className = getFieldBoxClasses(field);
            fieldEl.dataset.fieldId = field.id;
            fieldEl.dataset.fieldType = field.type;
            fieldEl.style.left = (field.x * 100) + '%';
            fieldEl.style.top = (field.y * 100) + '%';
            fieldEl.style.width = (field.width * 100) + '%';
            fieldEl.style.height = (field.height * 100) + '%';
            if (field.backgroundColor) {
                fieldEl.style.background = field.backgroundColor;
            }

            const label = document.createElement('div');
            label.className = 'field-chip';
            label.innerHTML =
                meta.icon +
                '<span class="field-chip-name">' + escapeHtml(field.name) + '</span>';
            fieldEl.appendChild(label);

            // --- Preview mode content ---
            if (state.previewMode) {
                fieldEl.classList.add('previewing');
                var previewDiv = document.createElement('div');
                previewDiv.className = 'field-preview-content';

                if (field.type === 'checkbox' || field.type === 'radio') {
                    previewDiv.classList.add('preview-checkbox');
                    var glyph = CHECKBOX_PREVIEW_GLYPHS[field.checkboxStyle || getSessionDefault('checkboxStyle')] || '\u2713';
                    previewDiv.textContent = glyph;
                    // Scale the glyph to fit the box
                    var overlayEl = getOverlayForPage(field.pageIndex);
                    var boxPx = overlayEl ? Math.min(field.width * overlayEl.offsetWidth, field.height * overlayEl.offsetHeight) : 20;
                    previewDiv.style.fontSize = Math.max(8, boxPx * 0.8) + 'px';
                    previewDiv.style.lineHeight = '1';
                    previewDiv.style.color = '#000';
                } else if (field.type === 'signature') {
                    previewDiv.classList.add('preview-signature');
                    if (_signatureDataUrl) {
                        var sigImg = document.createElement('img');
                        sigImg.src = _signatureDataUrl;
                        sigImg.alt = 'Signature preview';
                        previewDiv.appendChild(sigImg);
                    }
                } else {
                    // text, multiline, dropdown, listbox — render the field name
                    var css = pdfFontToCss(field.font || getSessionDefault('font'));
                    var ptSize = field.fontSize || getSessionDefault('fontSize') || 10;
                    // 1 PDF pt = renderScale CSS px on the scaled canvas overlay
                    var pxSize = ptSize * getViewerRenderScale();
                    previewDiv.style.fontFamily = css.family;
                    previewDiv.style.fontWeight = css.weight;
                    previewDiv.style.lineHeight = '1.15';
                    previewDiv.style.color = '#000';
                    previewDiv.style.whiteSpace = field.type === 'multiline' ? 'pre-wrap' : 'nowrap';
                    previewDiv.textContent = field.name || '';

                    // Apply auto-sizing: adjust font to fit field dimensions
                    if (field.autoSize && field.name && field.type === 'text') {
                        var pageSize = state.pageSizes[field.pageIndex] || { width: 612, height: 792 };
                        var fieldWidthPx = field.width * (pageSize.width * getViewerRenderScale());
                        var fieldHeightPx = field.height * (pageSize.height * getViewerRenderScale());
                        
                        // Max font size based on field height (single-line text, ~15% padding)
                        var maxPxSize = fieldHeightPx * 0.85 / 1.15;  // Account for line-height
                        
                        // Start with the smaller of declared size or height-based max
                        pxSize = Math.min(pxSize, maxPxSize);
                        
                        // Now check if text width fits; scale down if needed
                        var fontStr = css.weight + ' ' + Math.round(pxSize) + 'px ' + css.family;
                        var canvas = document.createElement('canvas');
                        var ctx = canvas.getContext('2d');
                        if (ctx) {
                            ctx.font = fontStr;
                            var textWidth = ctx.measureText(previewDiv.textContent).width;
                            var padding = 4;  // Left/right padding in PDF viewer
                            
                            // Scale down if text exceeds field width
                            if (textWidth + padding > fieldWidthPx && textWidth > 0) {
                                pxSize = pxSize * (fieldWidthPx - padding) / textWidth;
                            }
                        }
                    }

                    previewDiv.style.fontSize = pxSize + 'px';
                }
                fieldEl.appendChild(previewDiv);
            }

            if (field.id === state.selectedFieldId) {
                ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].forEach(function (handleName) {
                    const handle = document.createElement('div');
                    handle.className = 'resize-handle ' + handleName;
                    handle.dataset.handle = handleName;
                    fieldEl.appendChild(handle);
                });
            }

            fieldEl.addEventListener('pointerdown', function (event) {
                event.stopPropagation();
                setSelectedTool(null);

                const target = event.target;
                if (target && target.dataset && target.dataset.handle) {
                    selectField(field.id, { focusNameInput: false, scrollIntoView: false });
                    startResize(field.id, target.dataset.handle, event, overlay);
                    return;
                }
                startDrag(field.id, event, overlay);
            });

            fieldEl.addEventListener('click', function (event) {
                const target = event.target;
                if (target && target.dataset && target.dataset.handle) {
                    return;
                }
                event.stopPropagation();
                // First click selects (handles only); second click on
                // already-selected field opens the quick editor.
                if (state.selectedFieldId === field.id) {
                    selectField(field.id, { focusNameInput: true, scrollIntoView: false });
                } else {
                    selectField(field.id, { focusNameInput: false, scrollIntoView: false });
                }
            });

            fieldEl.addEventListener('dblclick', function (event) {
                event.stopPropagation();
                selectField(field.id, { focusNameInput: true, scrollIntoView: false });
            });

            overlay.appendChild(fieldEl);
            applyFieldChipLayout(fieldEl, label);
        });
        renderDraftRect();
        syncQuickFieldEditor({ preserveInput: true });
    }

    function selectField(fieldId, options) {
        const settings = options || {};
        state.selectedFieldId = fieldId;
        if (!fieldId) {
            hideQuickFieldEditor();
        } else if (settings.focusNameInput) {
            state.quickEditFieldId = fieldId;
            state.quickEditFocusPending = true;
        } else if (state.quickEditFieldId && state.quickEditFieldId !== fieldId) {
            hideQuickFieldEditor();
        }
        renderFieldsList();
        renderFieldsOnPages();
        if (fieldId && settings.scrollIntoView) {
            const fieldEl = document.querySelector('.field-box[data-field-id="' + fieldId + '"]');
            if (fieldEl) {
                fieldEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
        if (fieldId) {
            scrollSelectedFieldIntoView(fieldId);
        }
        syncQuickFieldEditor({ preserveInput: true });
    }

    function replaceFields(fields, options) {
        const settings = options || {};
        state.fields = fields.map(function (field) {
            const width = clamp(Number(field.width), 0.01, 1);
            const height = clamp(Number(field.height), 0.01, 1);
            return {
                id: field.id || generateId(),
                name: field.name,
                type: normalizeFieldType(field.type),
                pageIndex: Number(field.pageIndex) || 0,
                x: clamp(Number(field.x), 0, 1 - width),
                y: clamp(Number(field.y), 0, 1 - height),
                width: width,
                height: height,
                font: field.font || getSessionDefault('font'),
                fontSize: Number(field.fontSize) || getSessionDefault('fontSize'),
                autoSize: field.autoSize !== false,
                checkboxStyle: field.checkboxStyle || undefined,
                checkboxExportValue: field.checkboxExportValue || undefined,
                allowScroll: field.allowScroll !== false,
                backgroundColor: field.backgroundColor || undefined,
                options: Array.isArray(field.options) ? field.options.slice() : undefined
            };
        });
        bumpFieldNamesVersion();
        state.selectedFieldId = settings.preserveSelection && state.fields.some(function (field) {
            return field.id === state.selectedFieldId;
        }) ? state.selectedFieldId : (state.fields[0] ? state.fields[0].id : null);
        if (!state.fields.some(function (field) { return field.id === state.quickEditFieldId; })) {
            hideQuickFieldEditor();
        }
        renderFieldsList();
        renderFieldsOnPages();
        updateFieldCount();
    }

    function markDirtyAndRender(skipListRefresh) {
        setDirty(true);
        renderFieldsOnPages();
        if (!skipListRefresh) {
            renderFieldsList();
        }
        updateFieldCount();
        syncQuickFieldEditor({ preserveInput: true });
    }

    function createFieldFromTool(pageIndex, x, y, width, height) {
        if (!state.selectedTool) return;
        const type = normalizeFieldType(state.selectedTool);
        const rect = {
            x: clamp(x, 0, 1 - clamp(width, 0.01, 1)),
            y: clamp(y, 0, 1 - clamp(height, 0.01, 1)),
            width: clamp(width, 0.01, 1),
            height: clamp(height, 0.01, 1)
        };
        const nameSuggestions = buildFieldNameSuggestions(pageIndex, rect, type, null);
        const field = {
            id: generateId(),
            name: nameSuggestions[0] ? nameSuggestions[0].name : getDefaultFieldName(type),
            type: type,
            pageIndex: pageIndex,
            x: 0,
            y: 0,
            width: rect.width,
            height: rect.height,
            font: getSessionDefault('font'),
            fontSize: getSessionDefault('fontSize'),
            autoSize: type === 'text' || type === 'multiline',
            checkboxExportValue: type === 'checkbox' ? getSessionDefault('checkboxExportValue') : undefined,
            options: OPTION_TYPES.has(type) ? DEFAULT_OPTION_LIST.slice() : undefined,
            nameSuggestions: nameSuggestions
        };
        field.x = rect.x;
        field.y = rect.y;
        state.fields.push(field);
        bumpFieldNamesVersion();
        selectField(field.id, { focusNameInput: true, scrollIntoView: false });
        setDirty(true);
        updateFieldCount();
        renderFieldsList();
        renderFieldsOnPages();
    }

    function createPointField(pageIndex, pointX, pointY) {
        const meta = getFieldTypeMeta(state.selectedTool);
        const width = meta.defaultWidth || 0.032;
        const height = meta.defaultHeight || 0.032;
        const x = clamp(pointX - (width / 2), 0, 1 - width);
        const y = clamp(pointY - (height / 2), 0, 1 - height);
        createFieldFromTool(pageIndex, x, y, width, height);
    }

    function startDrag(fieldId, event, overlay) {
        const field = state.fields.find(function (candidate) { return candidate.id === fieldId; });
        if (!field) return;
        const rect = overlay.getBoundingClientRect();
        const pointerX = (event.clientX - rect.left) / rect.width;
        const pointerY = (event.clientY - rect.top) / rect.height;
        dragState = {
            fieldId: fieldId,
            pageIndex: field.pageIndex,
            startClientX: event.clientX,
            startClientY: event.clientY,
            startPointerX: pointerX,
            startPointerY: pointerY,
            originX: field.x,
            originY: field.y,
            moved: false
        };
    }

    function startResize(fieldId, handle, event, overlay) {
        const field = state.fields.find(function (candidate) { return candidate.id === fieldId; });
        if (!field) return;
        const rect = overlay.getBoundingClientRect();
        resizeState = {
            fieldId: fieldId,
            handle: handle,
            startX: (event.clientX - rect.left) / rect.width,
            startY: (event.clientY - rect.top) / rect.height,
            originX: field.x,
            originY: field.y,
            originWidth: field.width,
            originHeight: field.height
        };
    }

    function handleDragMove(event) {
        if (!dragState) return;
        const dragDistance = Math.hypot(
            event.clientX - dragState.startClientX,
            event.clientY - dragState.startClientY
        );
        if (!dragState.moved) {
            if (dragDistance < 4) {
                return;
            }
            dragState.moved = true;
            if (state.selectedFieldId !== dragState.fieldId) {
                selectField(dragState.fieldId, { focusNameInput: false, scrollIntoView: false });
            }
        }
        const overlay = getOverlayForPage(dragState.pageIndex);
        if (!overlay) return;
        const rect = overlay.getBoundingClientRect();
        const pointerX = (event.clientX - rect.left) / rect.width;
        const pointerY = (event.clientY - rect.top) / rect.height;
        const field = state.fields.find(function (candidate) { return candidate.id === dragState.fieldId; });
        if (!field) return;
        field.x = clamp(dragState.originX + (pointerX - dragState.startPointerX), 0, 1 - field.width);
        field.y = clamp(dragState.originY + (pointerY - dragState.startPointerY), 0, 1 - field.height);
        markDirtyAndRender(true);
    }

    function handleResizeMove(event) {
        if (!resizeState) return;
        const field = state.fields.find(function (candidate) { return candidate.id === resizeState.fieldId; });
        if (!field) return;
        const overlay = getOverlayForPage(field.pageIndex);
        if (!overlay) return;
        const rect = overlay.getBoundingClientRect();
        const pointerX = (event.clientX - rect.left) / rect.width;
        const pointerY = (event.clientY - rect.top) / rect.height;
        const deltaX = pointerX - resizeState.startX;
        const deltaY = pointerY - resizeState.startY;
        let newX = resizeState.originX;
        let newY = resizeState.originY;
        let newWidth = resizeState.originWidth;
        let newHeight = resizeState.originHeight;
        const minSize = 0.01;

        if (resizeState.handle.includes('e')) {
            newWidth = resizeState.originWidth + deltaX;
        }
        if (resizeState.handle.includes('s')) {
            newHeight = resizeState.originHeight + deltaY;
        }
        if (resizeState.handle.includes('w')) {
            newWidth = resizeState.originWidth - deltaX;
            newX = resizeState.originX + deltaX;
        }
        if (resizeState.handle.includes('n')) {
            newHeight = resizeState.originHeight - deltaY;
            newY = resizeState.originY + deltaY;
        }

        newWidth = Math.max(minSize, newWidth);
        newHeight = Math.max(minSize, newHeight);
        newX = clamp(newX, 0, 1 - newWidth);
        newY = clamp(newY, 0, 1 - newHeight);

        if (resizeState.handle.includes('w')) {
            newWidth = Math.min(newWidth, resizeState.originX + resizeState.originWidth - newX);
        }
        if (resizeState.handle.includes('n')) {
            newHeight = Math.min(newHeight, resizeState.originY + resizeState.originHeight - newY);
        }

        field.x = newX;
        field.y = newY;
        field.width = clamp(newWidth, minSize, 1 - field.x);
        field.height = clamp(newHeight, minSize, 1 - field.y);
        markDirtyAndRender(true);
    }

    function clearInteractionState() {
        draftState = null;
        dragState = null;
        resizeState = null;
        renderDraftRect();
    }

    function setupPageInteractions(overlay, pageIndex) {
        overlay.addEventListener('pointerdown', function (event) {
            if (event.button !== 0) return;
            const rect = overlay.getBoundingClientRect();
            const pointX = clamp((event.clientX - rect.left) / rect.width, 0, 1);
            const pointY = clamp((event.clientY - rect.top) / rect.height, 0, 1);

            if (!state.selectedTool) {
                if (event.target === overlay) {
                    selectField(null, { focusNameInput: false, scrollIntoView: false });
                }
                return;
            }

            if (event.target !== overlay) return;

            if (POINT_INSERT_TYPES.has(state.selectedTool)) {
                createPointField(pageIndex, pointX, pointY);
                return;
            }

            draftState = {
                pageIndex: pageIndex,
                startX: pointX,
                startY: pointY,
                currentX: pointX,
                currentY: pointY
            };
            selectField(null, { focusNameInput: false, scrollIntoView: false });
            renderDraftRect();
        });
    }

    function createPageShell(pageIndex) {
        const shell = document.createElement('div');
        shell.className = 'pdf-page-shell';
        shell.dataset.pageIndex = String(pageIndex);

        const label = document.createElement('div');
        label.className = 'pdf-page-label';
        label.textContent = 'Page ' + (pageIndex + 1);
        shell.appendChild(label);

        const page = document.createElement('div');
        page.className = 'pdf-page';
        page.dataset.pageIndex = String(pageIndex);
        shell.appendChild(page);
        return { shell: shell, page: page };
    }

    async function renderPages(options) {
        const settings = options || {};
        pdfPages.innerHTML = '';

        for (let pageIndex = 0; pageIndex < state.pageCount; pageIndex += 1) {
            const pageProxy = await state.pdfDoc.getPage(pageIndex + 1);
            const viewport = pageProxy.getViewport({ scale: getViewerRenderScale() });
            const pageShell = createPageShell(pageIndex);
            pageShell.page.style.width = viewport.width + 'px';
            pageShell.page.style.height = viewport.height + 'px';

            const canvas = document.createElement('canvas');
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            pageShell.page.appendChild(canvas);

            const context = canvas.getContext('2d');
            await pageProxy.render({
                canvasContext: context,
                viewport: viewport,
                annotationMode: 0
            }).promise;

            const overlay = document.createElement('div');
            overlay.className = 'page-overlay';
            overlay.dataset.pageIndex = String(pageIndex);
            pageShell.page.appendChild(overlay);
            setupPageInteractions(overlay, pageIndex);

            pdfPages.appendChild(pageShell.shell);
        }

        updateOverlayCursorState();
        renderFieldsOnPages();
        if (settings.anchor) {
            restoreViewerAnchor(settings.anchor);
        }
        currentVisiblePageIndex = getCurrentVisiblePageIndex();
    }

    async function loadPdf(file) {
        showLoading('Loading PDF...');
        hideToasts();
        clearInteractionState();
        setSelectedTool(null);

        try {
            const arrayBuffer = await file.arrayBuffer();
            syncPdfState(arrayBufferToUint8Array(arrayBuffer), file.name || 'edited-form.pdf', file);
            updateDocumentName();
            await refreshPdfDocumentFromState();
            showLoading('Detecting fields with FormFyxer...');

            const existingFields = await parseExistingFieldsLocally(state.pdfBytes, state.pageSizes);
            const detectedFields = await detectExistingFieldsWithServer().catch(function (_error) {
                return existingFields;
            });
            replaceFields(detectedFields);
            setDirty(false);
            updateAiUiState();
            floatingToolPicker.classList.remove('hidden');
            showPdfWorkspace();
            showSuccess(detectedFields.length ? ('Loaded ' + detectedFields.length + ' existing fields.') : 'PDF loaded.');
        } catch (error) {
            console.error('Error loading PDF:', error);
            hideLoading();

            if (error && error.name === 'PasswordException') {
                tryAutoUnlock(file);
                return;
            }

            showRepairPrompt(file, error);
            return;
        } finally {
            hideLoading();
        }
    }

    /**
     * Attempt to unlock the PDF without a password via the server-side
     * repair API.  If the blank-password unlock succeeds the repaired
     * file is reloaded immediately.  Otherwise the password dialog is shown.
     */
    async function tryAutoUnlock(file) {
        showLoading('Trying to unlock PDF...');
        try {
            var formData = new FormData();
            formData.append('file', file);
            formData.append('action', 'unlock');

            var response = await fetch(apiUrl('/pdf-labeler/api/repair'), {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: formData
            });
            var result = await parseApiResponse(response);
            if (result.success && result.data && result.data.pdf_base64) {
                var binaryStr = atob(result.data.pdf_base64);
                var bytes = new Uint8Array(binaryStr.length);
                for (var i = 0; i < binaryStr.length; i++) {
                    bytes[i] = binaryStr.charCodeAt(i);
                }
                var unlockedFile = new File([bytes], result.data.filename || state.fileName || 'unlocked.pdf', { type: 'application/pdf' });
                hideLoading();
                showSuccess('PDF unlocked successfully.');
                await loadPdf(unlockedFile);
                return;
            }
        } catch (_err) {
            // blank-password unlock failed — fall through to password dialog
        }
        hideLoading();
        showPasswordDialog(file);
    }

    function showPasswordDialog(file) {
        state._pendingPasswordFile = file;
        passwordInput.value = '';
        passwordError.classList.add('hidden');
        passwordError.textContent = '';
        passwordModal.classList.remove('hidden');
        passwordInput.focus();
    }

    async function submitPassword() {
        var file = state._pendingPasswordFile;
        if (!file) return;
        var pw = passwordInput.value;
        if (!pw) {
            passwordError.textContent = 'Please enter a password.';
            passwordError.classList.remove('hidden');
            return;
        }
        passwordSubmitBtn.disabled = true;
        passwordError.classList.add('hidden');
        try {
            var formData = new FormData();
            formData.append('file', file);
            formData.append('action', 'unlock');
            formData.append('password', pw);

            var response = await fetch(apiUrl('/pdf-labeler/api/repair'), {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: formData
            });
            var result = await parseApiResponse(response);
            if (!result.success) {
                var errMsg = (result.error && result.error.message) || 'Incorrect password or unlock failed.';
                throw new Error(errMsg);
            }
            var data = result.data || {};
            if (!data.pdf_base64) throw new Error('No unlocked PDF was returned.');

            var binaryStr = atob(data.pdf_base64);
            var bytes = new Uint8Array(binaryStr.length);
            for (var i = 0; i < binaryStr.length; i++) {
                bytes[i] = binaryStr.charCodeAt(i);
            }
            var unlockedFile = new File([bytes], data.filename || state.fileName || 'unlocked.pdf', { type: 'application/pdf' });
            passwordModal.classList.add('hidden');
            state._pendingPasswordFile = null;
            showSuccess('PDF unlocked successfully.');
            await loadPdf(unlockedFile);
        } catch (err) {
            passwordError.textContent = err.message || 'Failed to unlock PDF.';
            passwordError.classList.remove('hidden');
        } finally {
            passwordSubmitBtn.disabled = false;
        }
    }

    function showRepairPrompt(file, error) {
        state._pendingRepairFile = file;
        var msg = 'This PDF could not be rendered.';
        if (error && error.message) {
            msg += ' (' + error.message + ')';
        }
        msg += ' Would you like to try repairing it?';
        repairPromptMessage.textContent = msg;
        pdfEmpty.classList.add('hidden');
        repairPromptModal.classList.remove('hidden');
    }

    function deleteField(fieldId) {
        state.fields = state.fields.filter(function (field) { return field.id !== fieldId; });
        bumpFieldNamesVersion();
        if (state.selectedFieldId === fieldId) {
            state.selectedFieldId = state.fields[0] ? state.fields[0].id : null;
        }
        if (state.quickEditFieldId === fieldId) {
            hideQuickFieldEditor();
        }
        setDirty(true);
        renderFieldsList();
        renderFieldsOnPages();
        updateFieldCount();
    }

    function duplicateSelectedField() {
        const field = getCurrentField();
        if (!field) return;
        const duplicate = {
            id: generateId(),
            name: ensureUniqueFieldName(field.name + '_copy', field.id),
            type: field.type,
            pageIndex: field.pageIndex,
            x: clamp(field.x + 0.012, 0, 1 - field.width),
            y: clamp(field.y + 0.012, 0, 1 - field.height),
            width: field.width,
            height: field.height,
            font: field.font || getSessionDefault('font'),
            fontSize: field.fontSize,
            autoSize: field.autoSize !== false,
            checkboxStyle: field.checkboxStyle,
            checkboxExportValue: field.checkboxExportValue,
            allowScroll: field.allowScroll !== false,
            backgroundColor: field.backgroundColor,
            options: Array.isArray(field.options) ? field.options.slice() : undefined
        };
        state.fields.push(duplicate);
        bumpFieldNamesVersion();
        selectField(duplicate.id, { focusNameInput: false, scrollIntoView: false });
        setDirty(true);
        updateFieldCount();
    }

    function updateSelectedField(updates) {
        const field = getCurrentField();
        if (!field) return;
        Object.keys(updates).forEach(function (key) {
            field[key] = updates[key];
        });
        markDirtyAndRender();
    }

    function normalizeCurrentFieldName() {
        const field = getCurrentField();
        if (!field) return;
        const normalized = ensureUniqueFieldName(field.name, field.id);
        if (field.name === normalized) return;
        field.name = normalized;
        bumpFieldNamesVersion();
        setDirty(true);
        updateRenderedFieldName(field.id);
        syncQuickFieldEditor();
    }

    function convertFieldsToAbsoluteCoordinates() {
        return state.fields.map(function (field) {
            const pageSize = state.pageSizes[field.pageIndex];
            const safeName = ensureUniqueFieldName(field.name, field.id);
            return {
                name: safeName,
                type: field.type,
                pageIndex: field.pageIndex,
                x: field.x * pageSize.width,
                y: (1 - field.y - field.height) * pageSize.height,
                width: field.width * pageSize.width,
                height: field.height * pageSize.height,
                font: field.font || getSessionDefault('font'),
                fontSize: field.fontSize,
                autoSize: field.autoSize !== false,
                checkboxStyle: field.checkboxStyle,
                checkboxExportValue: field.checkboxExportValue,
                allowScroll: field.allowScroll !== false,
                backgroundColor: field.backgroundColor,
                options: Array.isArray(field.options) ? field.options.slice() : undefined
            };
        });
    }

    async function applyNormalizationPass() {
        if (!state.pdfBytes || state.fields.length === 0) {
            showError('Load a PDF with fields before running normalization.');
            return;
        }
        const settings = readNormalizationSettings();
        normalizationModal.classList.add('hidden');
        showLoading('Applying normalization pass...');
        try {
            await sleep(50);
            const normalizedFields = buildNormalizedFields(settings);
            replaceFields(normalizedFields, { preserveSelection: true });
            setDirty(true);

            if (settings.removeEmbeddedFonts && state.pdfBytes) {
                showLoading('Removing embedded fonts...');
                var formData = new FormData();
                formData.append('file', getPdfFileForRequests());
                var resp = await fetch(apiUrl('/pdf-labeler/api/strip-fonts'), {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                    body: formData
                });
                var data = await parseApiResponse(resp);
                if (data.success && data.data && data.data.pdf_base64) {
                    var strippedBytes = base64ToUint8Array(data.data.pdf_base64);
                    syncPdfState(strippedBytes, state.fileName || 'edited-form.pdf');
                    await refreshPdfDocumentFromState();
                    replaceFields(state.fields, { preserveSelection: true });
                }
            }

            showPdfWorkspace();
            showSuccess('Normalization pass applied.');
        } catch (error) {
            console.error('Error applying normalization pass:', error);
            showPdfWorkspace();
            showError('Normalization failed: ' + (error && error.message ? error.message : 'Unknown error.'));
        } finally {
            hideLoading();
        }
    }

    async function autoDetectFields() {
        if (!state.pdfBytes) return;
        if (!state.auth.aiEnabled) {
            showError('AI auto-detect requires login.');
            return;
        }

        showLoading('Auto-detecting fields with AI...');
        try {
            const formData = new FormData();
            formData.append('file', getPdfFileForRequests());
            formData.append('normalize_fields', 'true');
            formData.append('model', state.model || state.defaultModel);

            // Pass preferred variable names from Playground/installed interview
            var effectiveVars = getEffectivePdfVariables();
            if (state.usePlaygroundVariables && effectiveVars.length > 0) {
                formData.append('use_playground_variables', 'true');
                formData.append('preferred_variable_names', JSON.stringify(effectiveVars));
                formData.append('interview_source_mode', state.interviewSourceMode);
                if (state.interviewSourceMode === 'playground') {
                    formData.append('playground_project', state.playground.selectedProject);
                    formData.append('playground_yaml_file', state.playground.selectedFile);
                } else {
                    formData.append('installed_package', state.installed.selectedPackage);
                    formData.append('installed_yaml_file', state.installed.selectedFile);
                }
            }

            const data = await requestAsyncLabelerAction(
                '/pdf-labeler/api/auto-detect',
                formData,
                'Auto-detect is running in the background...'
            );

            applyReturnedPdfPayload(data, state.fileName || 'edited-form.pdf');

            const importedFields = Array.isArray(data && data.fields) ? data.fields : [];
            let normalizedFields = [];
            if (importedFields.length && typeof importedFields[0] === 'object' && importedFields[0] !== null && Object.prototype.hasOwnProperty.call(importedFields[0], 'x')) {
                normalizedFields = importedFields.map(function (field) {
                    const pageSize = state.pageSizes[field.pageIndex];
                    const fieldType = normalizeFieldType(field.type);
                    const width = pageSize ? clamp(field.width / pageSize.width, 0.01, 1) : 0.1;
                    const height = pageSize ? clamp(field.height / pageSize.height, 0.01, 1) : 0.03;
                    const x = pageSize ? clamp(field.x / pageSize.width, 0, 1 - width) : 0;
                    const y = pageSize ? clamp(1 - ((field.y + field.height) / pageSize.height), 0, 1 - height) : 0;
                    return {
                        id: generateId(),
                        name: ensureUniqueFieldName(field.name || getDefaultFieldName(fieldType), null),
                        type: fieldType,
                        pageIndex: field.pageIndex,
                        x: x,
                        y: y,
                        width: width,
                        height: height,
                        font: field.font || getSessionDefault('font'),
                        fontSize: field.fontSize || getSessionDefault('fontSize'),
                        autoSize: field.autoSize !== false,
                        options: Array.isArray(field.options) ? field.options.slice() : undefined
                    };
                });
            } else {
                normalizedFields = await detectExistingFieldsWithServer().catch(function () {
                    return parseExistingFieldsLocally(state.pdfBytes, state.pageSizes);
                });
            }

            replaceFields(normalizedFields);
            setDirty(false);
            showPdfWorkspace();
            const detectedCount = normalizedFields.length;
            showSuccess('Detected ' + detectedCount + ' field' + (detectedCount === 1 ? '' : 's') + '.');
        } catch (error) {
            console.error('Error auto-detecting:', error);
            showPdfWorkspace();
            showError('Auto-detection failed: ' + (error && error.message ? error.message : 'Unknown error.'));
        } finally {
            hideLoading();
        }
    }

    async function relabelFields() {
        if (!state.pdfBytes || state.fields.length === 0) return;
        if (!state.auth.aiEnabled) {
            showError('AI relabel requires login.');
            return;
        }
        if (state.hasUnsavedChanges) {
            const confirmed = window.confirm('AI relabel works on the PDF fields currently embedded in the document. Unsaved manual edits will be replaced. Continue?');
            if (!confirmed) return;
        }

        showLoading('Relabeling fields with AI... this can take 2-3 minutes.');
        try {
            const formData = new FormData();
            formData.append('file', getPdfFileForRequests());
            formData.append('model', state.model || state.defaultModel);

            const data = await requestAsyncLabelerAction(
                '/pdf-labeler/api/relabel',
                formData,
                'AI relabel is running in the background (this can take 2-3 minutes)...'
            );

            const pdfWasUpdated = applyReturnedPdfPayload(data, state.fileName || 'edited-form.pdf');
            const detectedRelabeledFields = await detectExistingFieldsWithServer().catch(function () {
                return parseExistingFieldsLocally(state.pdfBytes, state.pageSizes);
            });
            const relabeledFields = reconcileRelabeledFields(detectedRelabeledFields, data);
            logRelabelDebug(data, detectedRelabeledFields, relabeledFields);
            replaceFields(relabeledFields);
            setDirty(false);
            showPdfWorkspace();
            if (!pdfWasUpdated) {
                throw new Error('AI relabel completed without returning an updated PDF.');
            }
            const renamedCount = getRelabelChangedCount(data);
            if (renamedCount === 0) {
                showError('AI relabel completed, but no field names changed. See the browser console for details.', 15000);
                return;
            }
            showSuccess(
                typeof renamedCount === 'number'
                    ? ('Relabeled ' + renamedCount + ' field' + (renamedCount === 1 ? '' : 's') + '.')
                    : 'Fields relabeled successfully.',
                9000
            );
        } catch (error) {
            console.error('Error relabeling:', error);
            showPdfWorkspace();
            showError('Relabeling failed: ' + (error && error.message ? error.message : 'Unknown error.'), 15000);
        } finally {
            hideLoading();
        }
    }

    async function exportPdf() {
        if (!state.pdfBytes || state.fields.length === 0) {
            showError('There are no fields to export.');
            return;
        }

        showLoading('Exporting PDF...');
        try {
            const formData = new FormData();
            formData.append('file', getPdfFileForRequests());
            formData.append('fields', JSON.stringify(convertFieldsToAbsoluteCoordinates()));

            const response = await fetch(apiUrl('/pdf-labeler/api/apply-fields'), {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: formData
            });
            const data = await parseApiResponse(response);
            if (!data.success) {
                throw new Error((data.error && data.error.message) || 'Export failed.');
            }

            if (!data.data || !data.data.pdf_base64) {
                throw new Error('The export endpoint did not return a PDF.');
            }

            const outputBytes = base64ToUint8Array(data.data.pdf_base64);
            const filename = data.data.filename || ((state.fileName || 'edited-form').replace(/\.pdf$/i, '') + '-with-fields.pdf');

            const blob = new Blob([outputBytes], { type: 'application/pdf' });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = filename;
            document.body.appendChild(anchor);
            anchor.click();
            document.body.removeChild(anchor);
            URL.revokeObjectURL(url);

            syncPdfState(outputBytes, filename);
            updateDocumentName();
            setDirty(false);
            showPdfWorkspace();
            showSuccess('PDF exported successfully.');
        } catch (error) {
            console.error('Error exporting:', error);
            showPdfWorkspace();
            showError('Export failed: ' + (error && error.message ? error.message : 'Unknown error.'));
        } finally {
            hideLoading();
        }
    }

    function createPageManagerDraftPage(sourceId, sourcePageIndex) {
        return {
            id: 'page-' + generateId(),
            sourceId: sourceId,
            sourcePageIndex: Number(sourcePageIndex) || 0,
            splitBefore: false
        };
    }

    function getPageManagerSource(sourceId) {
        if (!pageManagerState || !pageManagerState.sources) return null;
        return pageManagerState.sources[sourceId] || null;
    }

    function setPageManagerStatus(message, tone) {
        if (!message) {
            pageManagerStatus.className = 'hidden alert small mb-3';
            pageManagerStatus.textContent = '';
            return;
        }
        const level = tone || 'info';
        pageManagerStatus.className = 'alert alert-' + level + ' small mb-3';
        pageManagerStatus.textContent = message;
        pageManagerStatus.classList.remove('hidden');
    }

    function hasPageManagerStructuralChanges() {
        if (!pageManagerState) return false;
        if (pageManagerState.draftPages.length !== state.pageCount) {
            return true;
        }
        return pageManagerState.draftPages.some(function (page, index) {
            return page.sourceId !== 'active' || page.sourcePageIndex !== index;
        });
    }

    function getPageManagerSplitGroups() {
        if (!pageManagerState || !pageManagerState.draftPages.length) return [];
        const groups = [];
        let currentGroup = [];
        pageManagerState.draftPages.forEach(function (page, index) {
            if (index > 0 && page.splitBefore && currentGroup.length) {
                groups.push(currentGroup);
                currentGroup = [];
            }
            currentGroup.push(page);
        });
        if (currentGroup.length) {
            groups.push(currentGroup);
        }
        return groups;
    }

    function syncPageManagerInsertPositionOptions() {
        if (!pageManagerState) return;
        const previousValue = pageManagerInsertPosition.value;
        pageManagerInsertPosition.innerHTML = '';
        const totalPages = pageManagerState.draftPages.length;
        if (!totalPages) {
            const option = document.createElement('option');
            option.value = '0';
            option.textContent = 'At beginning';
            pageManagerInsertPosition.appendChild(option);
            return;
        }
        for (let index = 0; index <= totalPages; index += 1) {
            const option = document.createElement('option');
            option.value = String(index);
            if (index === 0) {
                option.textContent = 'Before page 1';
            } else if (index === totalPages) {
                option.textContent = 'After page ' + totalPages;
            } else {
                option.textContent = 'Between pages ' + index + ' and ' + (index + 1);
            }
            pageManagerInsertPosition.appendChild(option);
        }
        if (Array.from(pageManagerInsertPosition.options).some(function (option) { return option.value === previousValue; })) {
            pageManagerInsertPosition.value = previousValue;
        } else {
            pageManagerInsertPosition.value = String(totalPages);
        }
    }

    function renderPageManagerSummary() {
        if (!pageManagerState) return;
        const totalPages = pageManagerState.draftPages.length;
        const insertedCount = pageManagerState.draftPages.filter(function (page) { return page.sourceId !== 'active'; }).length;
        const splitCount = Math.max(getPageManagerSplitGroups().length - 1, 0);
        const parts = [totalPages + ' page' + (totalPages === 1 ? '' : 's')];
        if (insertedCount) {
            parts.push(insertedCount + ' inserted');
        }
        if (splitCount) {
            parts.push(splitCount + ' split point' + (splitCount === 1 ? '' : 's'));
        }
        pageManagerSummary.textContent = parts.join(' • ');
    }

    function updatePageManagerControls() {
        if (!pageManagerState) return;
        const insertSource = getPageManagerSource(pageManagerState.insertSourceId);
        const selectedInsertCount = insertSource && pageManagerState.insertSelections
            ? pageManagerState.insertSelections.size
            : 0;
        if (!insertSource) {
            pageManagerInsertSelectionSummary.textContent = 'No insert PDF loaded.';
        } else if (!selectedInsertCount) {
            pageManagerInsertSelectionSummary.textContent = insertSource.name + ' loaded. Select page cards to insert.';
        } else {
            pageManagerInsertSelectionSummary.textContent = selectedInsertCount + ' page' + (selectedInsertCount === 1 ? '' : 's') + ' selected from ' + insertSource.name + '.';
        }
        pageManagerInsertRunBtn.disabled = !insertSource || selectedInsertCount === 0;
        pageManagerApplyBtn.disabled = !pageManagerState.draftPages.length || !hasPageManagerStructuralChanges();
        pageManagerDownloadSplitsBtn.disabled = getPageManagerSplitGroups().length < 2;
        renderPageManagerSummary();
        syncPageManagerInsertPositionOptions();
    }

    async function ensurePageManagerThumbnail(source, pageIndex) {
        if (!source) return '';
        if (!source.thumbCache) {
            source.thumbCache = {};
        }
        if (source.thumbCache[pageIndex]) {
            return source.thumbCache[pageIndex];
        }
        const page = await source.pdfDoc.getPage(pageIndex + 1);
        const viewport = page.getViewport({ scale: 0.23 });
        const canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        await page.render({
            canvasContext: canvas.getContext('2d'),
            viewport: viewport,
            annotationMode: 0
        }).promise;
        source.thumbCache[pageIndex] = canvas.toDataURL('image/png');
        return source.thumbCache[pageIndex];
    }

    async function hydratePageManagerThumb(thumbEl, sourceId, pageIndex) {
        const source = getPageManagerSource(sourceId);
        if (!thumbEl || !source || !source.pdfDoc) return;
        thumbEl.innerHTML = '<div class="page-manager-thumb-placeholder">Rendering preview…</div>';
        try {
            const imageUrl = await ensurePageManagerThumbnail(source, pageIndex);
            if (!thumbEl.isConnected) return;
            const image = document.createElement('img');
            image.src = imageUrl;
            image.alt = 'Page preview';
            thumbEl.innerHTML = '';
            thumbEl.appendChild(image);
        } catch (_error) {
            if (!thumbEl.isConnected) return;
            thumbEl.innerHTML = '<div class="page-manager-thumb-placeholder">Preview unavailable</div>';
        }
    }

    function clearPageManagerDropTargets() {
        pageManagerPages.querySelectorAll('.page-manager-page.drop-target').forEach(function (card) {
            card.classList.remove('drop-target');
        });
        pageManagerDropzone.classList.remove('drop-target');
    }

    function movePageManagerDraftPage(pageId, nextIndex) {
        if (!pageManagerState) return;
        const currentIndex = pageManagerState.draftPages.findIndex(function (page) {
            return page.id === pageId;
        });
        if (currentIndex < 0) return;
        const boundedIndex = clamp(nextIndex, 0, pageManagerState.draftPages.length);
        if (currentIndex === boundedIndex) return;
        const movedPage = pageManagerState.draftPages.splice(currentIndex, 1)[0];
        const adjustedIndex = boundedIndex > currentIndex ? boundedIndex - 1 : boundedIndex;
        pageManagerState.draftPages.splice(adjustedIndex, 0, movedPage);
        renderPageManagerPages();
    }

    function renderPageManagerPages() {
        if (!pageManagerState) return;
        pageManagerPages.innerHTML = '';
        pageManagerState.draftPages.forEach(function (page, index) {
            const source = getPageManagerSource(page.sourceId);
            const card = document.createElement('div');
            card.className = 'page-manager-page';
            card.draggable = true;
            card.dataset.pageId = page.id;

            const header = document.createElement('div');
            header.className = 'page-manager-page-header';
            header.innerHTML =
                '<div>' +
                    '<div class="page-manager-page-title">Page ' + (index + 1) + '</div>' +
                    '<div class="page-manager-page-subtitle">Source page ' + (page.sourcePageIndex + 1) + '</div>' +
                '</div>' +
                '<span class="page-manager-source-badge">' + escapeHtml(source && source.kind === 'active' ? 'Active PDF' : 'Inserted PDF') + '</span>';
            card.appendChild(header);

            const thumb = document.createElement('div');
            thumb.className = 'page-manager-thumb';
            thumb.innerHTML = '<div class="page-manager-thumb-placeholder">Rendering preview…</div>';
            card.appendChild(thumb);
            void hydratePageManagerThumb(thumb, page.sourceId, page.sourcePageIndex);

            const actions = document.createElement('div');
            actions.className = 'page-manager-page-actions';
            actions.innerHTML =
                '<div class="form-check mb-0">' +
                    '<input class="form-check-input page-manager-split-toggle"' +
                        (index === 0 ? ' disabled' : '') +
                        ' type="checkbox" data-page-id="' + escapeHtml(page.id) + '"' +
                        (page.splitBefore ? ' checked' : '') +
                        ' id="page-manager-split-' + escapeHtml(page.id) + '">' +
                    '<label class="form-check-label small" for="page-manager-split-' + escapeHtml(page.id) + '">Start new document here</label>' +
                '</div>' +
                '<button type="button" class="btn btn-outline-danger btn-sm" data-page-manager-action="remove" data-page-id="' + escapeHtml(page.id) + '">Remove</button>';
            card.appendChild(actions);

            pageManagerPages.appendChild(card);
        });
        updatePageManagerControls();
    }

    function renderPageManagerInsertPages() {
        pageManagerInsertPages.innerHTML = '';
        if (!pageManagerState) return;
        const insertSource = getPageManagerSource(pageManagerState.insertSourceId);
        if (!insertSource) {
            updatePageManagerControls();
            return;
        }
        for (let pageIndex = 0; pageIndex < insertSource.pdfDoc.numPages; pageIndex += 1) {
            const selected = pageManagerState.insertSelections.has(pageIndex);
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'insert-page-card text-start' + (selected ? ' selected' : '');
            card.dataset.insertPageIndex = String(pageIndex);
            card.innerHTML =
                '<div class="d-flex justify-content-between align-items-center gap-2 mb-2">' +
                    '<div class="small fw-semibold">Page ' + (pageIndex + 1) + '</div>' +
                    '<input type="checkbox" class="form-check-input m-0" tabindex="-1"' + (selected ? ' checked' : '') + '>' +
                '</div>';
            const thumb = document.createElement('div');
            thumb.className = 'page-manager-thumb mb-0';
            thumb.innerHTML = '<div class="page-manager-thumb-placeholder">Rendering preview…</div>';
            card.appendChild(thumb);
            void hydratePageManagerThumb(thumb, insertSource.id, pageIndex);
            pageManagerInsertPages.appendChild(card);
        }
        updatePageManagerControls();
    }

    function renderPageManager() {
        renderPageManagerPages();
        renderPageManagerInsertPages();
        updatePageManagerControls();
    }

    function buildFreshPageManagerState() {
        return {
            sources: {
                active: {
                    id: 'active',
                    kind: 'active',
                    name: state.fileName || 'active.pdf',
                    bytes: clonePdfBytes(state.pdfBytes),
                    pdfDoc: state.pdfDoc,
                    thumbCache: {}
                }
            },
            draftPages: Array.from({ length: state.pageCount }, function (_unused, index) {
                return createPageManagerDraftPage('active', index);
            }),
            insertSourceId: null,
            insertSelections: new Set()
        };
    }

    function openPageManager() {
        if (!state.pdfBytes || !state.pdfDoc) return;
        pageManagerState = buildFreshPageManagerState();
        pageManagerInsertFileInput.value = '';
        pageManagerInsertFileName.textContent = '';
        pageManagerDragPageId = null;
        setPageManagerStatus('', 'info');
        renderPageManager();
        pageManagerModal.classList.remove('hidden');
    }

    function closePageManager() {
        pageManagerModal.classList.add('hidden');
        pageManagerState = null;
        pageManagerDragPageId = null;
        setPageManagerStatus('', 'info');
    }

    async function loadPageManagerInsertSource(file) {
        if (!pageManagerState || !file) return;
        setPageManagerStatus('Loading insert PDF...', 'info');
        try {
            const bytes = clonePdfBytes(arrayBufferToUint8Array(await file.arrayBuffer()));
            const pdfDoc = await pdfjsLib.getDocument({ data: clonePdfBytes(bytes) }).promise;
            const sourceId = 'insert-' + Date.now();
            pageManagerState.sources[sourceId] = {
                id: sourceId,
                kind: 'insert',
                name: file.name || 'insert.pdf',
                bytes: bytes,
                pdfDoc: pdfDoc,
                thumbCache: {}
            };
            pageManagerState.insertSourceId = sourceId;
            pageManagerState.insertSelections = new Set();
            pageManagerInsertFileName.textContent = file.name || 'insert.pdf';
            renderPageManagerInsertPages();
            setPageManagerStatus('Insert PDF loaded.', 'success');
        } catch (error) {
            console.error('Error loading insert PDF:', error);
            setPageManagerStatus('Could not load insert PDF: ' + (error && error.message ? error.message : 'Unknown error.'), 'danger');
        }
    }

    function togglePageManagerInsertSelection(pageIndex) {
        if (!pageManagerState) return;
        if (pageManagerState.insertSelections.has(pageIndex)) {
            pageManagerState.insertSelections.delete(pageIndex);
        } else {
            pageManagerState.insertSelections.add(pageIndex);
        }
        renderPageManagerInsertPages();
    }

    function insertSelectedPagesIntoManager() {
        if (!pageManagerState) return;
        const source = getPageManagerSource(pageManagerState.insertSourceId);
        const selectedPages = Array.from(pageManagerState.insertSelections).sort(function (left, right) {
            return left - right;
        });
        if (!source || !selectedPages.length) return;
        const insertAt = clamp(Number(pageManagerInsertPosition.value || pageManagerState.draftPages.length), 0, pageManagerState.draftPages.length);
        const draftPages = selectedPages.map(function (pageIndex) {
            return createPageManagerDraftPage(source.id, pageIndex);
        });
        pageManagerState.draftPages.splice.apply(pageManagerState.draftPages, [insertAt, 0].concat(draftPages));
        pageManagerState.insertSelections = new Set();
        renderPageManager();
        setPageManagerStatus('Inserted ' + selectedPages.length + ' page' + (selectedPages.length === 1 ? '' : 's') + '.', 'success');
    }

    async function buildPdfBytesFromPageDescriptors(pages) {
        if (!PDFLibGlobal.PDFDocument) {
            throw new Error('Page management requires pdf-lib in the browser.');
        }
        const outputDoc = await PDFLibGlobal.PDFDocument.create();
        const sourceDocs = {};
        for (const page of pages) {
            const source = getPageManagerSource(page.sourceId);
            if (!source || !source.bytes) {
                throw new Error('A source PDF is missing for one or more pages.');
            }
            if (!sourceDocs[source.id]) {
                sourceDocs[source.id] = await PDFLibGlobal.PDFDocument.load(clonePdfBytes(source.bytes));
            }
            const copiedPages = await outputDoc.copyPages(sourceDocs[source.id], [page.sourcePageIndex]);
            outputDoc.addPage(copiedPages[0]);
        }
        return new Uint8Array(await outputDoc.save());
    }

    function remapFieldsForPageManagerDraft() {
        const fieldsByPageIndex = new Map();
        state.fields.forEach(function (field) {
            if (!fieldsByPageIndex.has(field.pageIndex)) {
                fieldsByPageIndex.set(field.pageIndex, []);
            }
            fieldsByPageIndex.get(field.pageIndex).push(field);
        });

        const nextFields = [];
        pageManagerState.draftPages.forEach(function (page, newPageIndex) {
            if (page.sourceId !== 'active') return;
            const sourceFields = fieldsByPageIndex.get(page.sourcePageIndex) || [];
            sourceFields.forEach(function (field) {
                nextFields.push(Object.assign({}, field, { pageIndex: newPageIndex }));
            });
        });
        return nextFields;
    }

    async function applyPageManagerChanges() {
        if (!pageManagerState || !pageManagerState.draftPages.length) {
            setPageManagerStatus('Keep at least one page in the document.', 'danger');
            return;
        }
        showLoading('Applying page changes...');
        try {
            const nextPdfBytes = await buildPdfBytesFromPageDescriptors(pageManagerState.draftPages);
            const nextFields = remapFieldsForPageManagerDraft();
            syncPdfState(nextPdfBytes, state.fileName || 'edited-form.pdf');
            updateDocumentName();
            await refreshPdfDocumentFromState();
            replaceFields(nextFields, { preserveSelection: true });
            setDirty(true);
            closePageManager();
            showPdfWorkspace();
            showSuccess('Page changes applied to the workspace.');
        } catch (error) {
            console.error('Error applying page changes:', error);
            setPageManagerStatus('Could not apply page changes: ' + (error && error.message ? error.message : 'Unknown error.'), 'danger');
        } finally {
            hideLoading();
        }
    }

    async function downloadSplitDocumentsFromManager() {
        if (!pageManagerState) return;
        const groups = getPageManagerSplitGroups();
        if (groups.length < 2) {
            setPageManagerStatus('Add at least one split point before downloading multiple documents.', 'warning');
            return;
        }
        if (state.fields.length > 0 && state.hasUnsavedChanges) {
            const confirmed = window.confirm('Split downloads use the current PDF pages only. Unsaved field overlay edits will not be embedded. Continue?');
            if (!confirmed) return;
        }
        showLoading('Preparing split PDFs...');
        try {
            const baseName = sanitizeFilenameSegment(stripPdfExtension(state.fileName || 'document'), 'document');
            const outputs = [];
            for (let index = 0; index < groups.length; index += 1) {
                const bytes = await buildPdfBytesFromPageDescriptors(groups[index]);
                outputs.push({
                    name: baseName + '-part-' + String(index + 1).padStart(2, '0') + '.pdf',
                    bytes: bytes
                });
            }
            if (JSZipGlobal) {
                const zip = new JSZipGlobal();
                outputs.forEach(function (output) {
                    zip.file(output.name, output.bytes);
                });
                const zipBlob = await zip.generateAsync({ type: 'blob' });
                downloadBlob(zipBlob, baseName + '-split-pdfs.zip');
            } else {
                outputs.forEach(function (output) {
                    downloadBlob(new Blob([output.bytes], { type: 'application/pdf' }), output.name);
                });
            }
            setPageManagerStatus('Downloaded ' + outputs.length + ' split PDF' + (outputs.length === 1 ? '' : 's') + '.', 'success');
            showSuccess('Split PDFs downloaded.');
        } catch (error) {
            console.error('Error downloading split PDFs:', error);
            setPageManagerStatus('Could not split the PDF: ' + (error && error.message ? error.message : 'Unknown error.'), 'danger');
        } finally {
            hideLoading();
        }
    }

    document.addEventListener('pointermove', function (event) {
        if (draftState) {
            const overlay = getOverlayForPage(draftState.pageIndex);
            if (!overlay) return;
            const rect = overlay.getBoundingClientRect();
            draftState.currentX = clamp((event.clientX - rect.left) / rect.width, 0, 1);
            draftState.currentY = clamp((event.clientY - rect.top) / rect.height, 0, 1);
            renderDraftRect();
            return;
        }
        if (dragState) {
            handleDragMove(event);
            return;
        }
        if (resizeState) {
            handleResizeMove(event);
        }
    });

    document.addEventListener('pointerup', function () {
        if (draftState && state.selectedTool) {
            const width = Math.abs(draftState.currentX - draftState.startX);
            const height = Math.abs(draftState.currentY - draftState.startY);
            if (width >= 0.01 && height >= 0.01) {
                createFieldFromTool(
                    draftState.pageIndex,
                    Math.min(draftState.startX, draftState.currentX),
                    Math.min(draftState.startY, draftState.currentY),
                    width,
                    height
                );
            }
        }
        clearInteractionState();
    });

    fileInput.addEventListener('change', function (event) {
        const file = event.target.files && event.target.files[0];
        if (file) {
            loadPdf(file);
        }
        fileInput.value = '';
    });

    pdfZoomOutBtn.addEventListener('click', function () {
        void updateViewerZoom(state.viewerZoom - 0.15);
    });
    pdfZoomInBtn.addEventListener('click', function () {
        void updateViewerZoom(state.viewerZoom + 0.15);
    });
    pdfZoomResetBtn.addEventListener('click', function () {
        void updateViewerZoom(1);
    });

    pdfContainer.addEventListener('dragover', function (event) {
        event.preventDefault();
        pdfContainer.classList.add('drag-over');
    });

    pdfContainer.addEventListener('dragleave', function () {
        pdfContainer.classList.remove('drag-over');
    });

    pdfContainer.addEventListener('drop', function (event) {
        event.preventDefault();
        pdfContainer.classList.remove('drag-over');
        const file = event.dataTransfer.files && event.dataTransfer.files[0];
        if (file && file.type === 'application/pdf') {
            loadPdf(file);
        }
    });
    pdfContainer.addEventListener('scroll', function () {
        currentVisiblePageIndex = getCurrentVisiblePageIndex();
        positionQuickFieldEditor();
    });
    pdfContainer.addEventListener('wheel', function (event) {
        if (!state.pdfBytes || !(event.ctrlKey || event.metaKey)) return;
        if (event.cancelable) {
            event.preventDefault();
        }
        void updateViewerZoom(state.viewerZoom + (event.deltaY < 0 ? 0.1 : -0.1));
    }, { passive: false });
    fieldsList.addEventListener('scroll', function () {
        if (fieldsListProgrammaticScroll) return;
        fieldsListManualScrollUntil = Date.now() + 1600;
    });
    fieldSearchInput.addEventListener('input', function () {
        state.fieldSearch = fieldSearchInput.value || '';
        refreshFieldsListFromControls();
    });
    fieldTypeFilterInput.addEventListener('change', function () {
        state.fieldTypeFilter = fieldTypeFilterInput.value || '';
        refreshFieldsListFromControls();
    });
    fieldSortInput.addEventListener('change', function () {
        state.fieldSort = fieldSortInput.value || 'position';
        refreshFieldsListFromControls();
    });
    fieldsModeInputs.forEach(function (input) {
        input.addEventListener('change', function () {
            setFieldsPanelMode(input.value);
        });
    });
    bulkRenameTypeInput.addEventListener('change', function () {
        state.bulkRenameType = bulkRenameTypeInput.value === 'regex' ? 'regex' : 'pattern';
        invalidateBulkRenamePreview();
        refreshFieldsListFromControls();
    });
    bulkRenamePatternInput.addEventListener('input', function () {
        state.bulkRenamePattern = bulkRenamePatternInput.value || '';
        invalidateBulkRenamePreview();
        refreshFieldsListFromControls();
    });
    bulkRenameReplacementInput.addEventListener('input', function () {
        state.bulkRenameReplacement = bulkRenameReplacementInput.value || '';
        invalidateBulkRenamePreview();
        refreshFieldsListFromControls();
    });
    bulkRenameApplyBtn.addEventListener('click', applyBulkRename);
    window.addEventListener('resize', function () {
        currentVisiblePageIndex = getCurrentVisiblePageIndex();
        positionQuickFieldEditor();
    });

    Object.keys(toolButtons).forEach(function (tool) {
        toolButtons[tool].addEventListener('click', function () {
            setSelectedTool(state.selectedTool === tool ? null : tool);
            if (state.selectedTool) {
                selectField(null, { focusNameInput: false, scrollIntoView: false });
            }
        });
    });

    normalizePassBtn.addEventListener('click', function () {
        if (!state.pdfBytes || state.fields.length === 0) return;
        // Pre-populate normalization modal with session defaults
        var sessionDefs = _loadSessionDefaults();
        document.getElementById('norm-font-name').value = sessionDefs.font || 'Helvetica';
        document.getElementById('norm-font-size').value = String(sessionDefs.fontSize || 10);
        document.getElementById('norm-checkbox-style').value = sessionDefs.checkboxStyle || 'cross';
        normalizationModal.classList.remove('hidden');
    });
    repairBtn.addEventListener('click', function () {
        if (!state.pdfBytes) return;
        repairStatus.classList.add('hidden');
        repairModal.querySelectorAll('.repair-run-btn').forEach(function (btn) { btn.disabled = false; });
        repairModal.classList.remove('hidden');
    });
    managePagesBtn.addEventListener('click', function () {
        openPageManager();
    });
    closePageManagerBtn.addEventListener('click', function () {
        closePageManager();
    });
    pageManagerResetBtn.addEventListener('click', function () {
        if (!state.pdfBytes) return;
        pageManagerState = buildFreshPageManagerState();
        pageManagerInsertFileInput.value = '';
        pageManagerInsertFileName.textContent = '';
        setPageManagerStatus('Page arrangement reset to the active PDF.', 'info');
        renderPageManager();
    });
    pageManagerInsertFileInput.addEventListener('change', function (event) {
        const file = event.target.files && event.target.files[0];
        if (file) {
            void loadPageManagerInsertSource(file);
        }
        pageManagerInsertFileInput.value = '';
    });
    pageManagerInsertRunBtn.addEventListener('click', function () {
        insertSelectedPagesIntoManager();
    });
    pageManagerApplyBtn.addEventListener('click', function () {
        void applyPageManagerChanges();
    });
    pageManagerDownloadSplitsBtn.addEventListener('click', function () {
        void downloadSplitDocumentsFromManager();
    });
    pageManagerInsertPages.addEventListener('click', function (event) {
        const card = event.target.closest('[data-insert-page-index]');
        if (!card || !pageManagerState) return;
        togglePageManagerInsertSelection(Number(card.dataset.insertPageIndex || 0));
    });
    pageManagerPages.addEventListener('click', function (event) {
        const removeBtn = event.target.closest('[data-page-manager-action="remove"][data-page-id]');
        if (!removeBtn || !pageManagerState) return;
        if (pageManagerState.draftPages.length <= 1) {
            setPageManagerStatus('The document must keep at least one page.', 'warning');
            return;
        }
        const pageId = removeBtn.dataset.pageId;
        pageManagerState.draftPages = pageManagerState.draftPages.filter(function (page) {
            return page.id !== pageId;
        });
        renderPageManagerPages();
    });
    pageManagerPages.addEventListener('change', function (event) {
        const toggle = event.target.closest('.page-manager-split-toggle[data-page-id]');
        if (!toggle || !pageManagerState) return;
        const page = pageManagerState.draftPages.find(function (candidate) {
            return candidate.id === toggle.dataset.pageId;
        });
        if (!page) return;
        page.splitBefore = !!toggle.checked;
        updatePageManagerControls();
    });
    pageManagerPages.addEventListener('dragstart', function (event) {
        const card = event.target.closest('.page-manager-page[data-page-id]');
        if (!card || !pageManagerState) return;
        pageManagerDragPageId = card.dataset.pageId;
        card.classList.add('dragging');
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = 'move';
        }
    });
    pageManagerPages.addEventListener('dragend', function () {
        pageManagerDragPageId = null;
        pageManagerPages.querySelectorAll('.page-manager-page.dragging').forEach(function (card) {
            card.classList.remove('dragging');
        });
        clearPageManagerDropTargets();
    });
    pageManagerPages.addEventListener('dragover', function (event) {
        const card = event.target.closest('.page-manager-page[data-page-id]');
        if (!card || !pageManagerState || !pageManagerDragPageId || card.dataset.pageId === pageManagerDragPageId) return;
        event.preventDefault();
        clearPageManagerDropTargets();
        card.classList.add('drop-target');
    });
    pageManagerPages.addEventListener('drop', function (event) {
        const card = event.target.closest('.page-manager-page[data-page-id]');
        if (!card || !pageManagerState || !pageManagerDragPageId || card.dataset.pageId === pageManagerDragPageId) return;
        event.preventDefault();
        const nextIndex = pageManagerState.draftPages.findIndex(function (page) {
            return page.id === card.dataset.pageId;
        });
        movePageManagerDraftPage(pageManagerDragPageId, nextIndex);
        clearPageManagerDropTargets();
    });
    pageManagerDropzone.addEventListener('dragover', function (event) {
        if (!pageManagerState || !pageManagerDragPageId) return;
        event.preventDefault();
        clearPageManagerDropTargets();
        pageManagerDropzone.classList.add('drop-target');
    });
    pageManagerDropzone.addEventListener('drop', function (event) {
        if (!pageManagerState || !pageManagerDragPageId) return;
        event.preventDefault();
        movePageManagerDraftPage(pageManagerDragPageId, pageManagerState.draftPages.length);
        clearPageManagerDropTargets();
    });
    closeRepairBtn.addEventListener('click', function () {
        repairModal.classList.add('hidden');
    });

    /* --------------- Utilities modal --------------- */
    var utilCopySourceBytes = null;
    var utilCopySourceName = '';
    var utilCopyDestBytes = null;
    var utilCopyDestName = '';

    function updateUtilCopyRunState() {
        document.getElementById('util-copy-run').disabled = !(utilCopySourceBytes && utilCopyDestBytes);
    }
    function updateUtilActiveButtons() {
        var hasPdf = !!state.pdfBytes;
        document.getElementById('util-copy-source-active').disabled = !hasPdf;
        document.getElementById('util-copy-dest-active').disabled = !hasPdf;
    }

    previewBtn.addEventListener('click', function () {
        state.previewMode = !state.previewMode;
        previewBtn.classList.toggle('active', state.previewMode);
        document.getElementById('preview-icon-open').classList.toggle('hidden', state.previewMode);
        document.getElementById('preview-icon-closed').classList.toggle('hidden', !state.previewMode);
        renderFieldsOnPages();
    });

    utilitiesBtn.addEventListener('click', function () {
        utilCopySourceBytes = null;
        utilCopySourceName = '';
        utilCopyDestBytes = null;
        utilCopyDestName = '';
        document.getElementById('util-copy-source-name').textContent = '';
        document.getElementById('util-copy-dest-name').textContent = '';
        document.getElementById('util-copy-source-file').value = '';
        document.getElementById('util-copy-dest-file').value = '';
        document.getElementById('util-copy-status').classList.add('hidden');
        document.getElementById('util-bulk-status').classList.add('hidden');
        document.getElementById('util-bulk-size-warning').classList.add('hidden');
        document.getElementById('util-bulk-files').value = '';
        bulkFiles = [];
        updateBulkState();
        updateUtilActiveButtons();
        updateUtilCopyRunState();
        utilitiesModal.classList.remove('hidden');
    });
    utilitiesCloseBtn.addEventListener('click', function () {
        utilitiesModal.classList.add('hidden');
    });

    document.getElementById('util-copy-source-file').addEventListener('change', function (e) {
        var file = e.target.files && e.target.files[0];
        if (!file) return;
        file.arrayBuffer().then(function (buf) {
            utilCopySourceBytes = new Uint8Array(buf);
            utilCopySourceName = file.name || 'source.pdf';
            document.getElementById('util-copy-source-name').textContent = utilCopySourceName;
            updateUtilCopyRunState();
        });
    });
    document.getElementById('util-copy-dest-file').addEventListener('change', function (e) {
        var file = e.target.files && e.target.files[0];
        if (!file) return;
        file.arrayBuffer().then(function (buf) {
            utilCopyDestBytes = new Uint8Array(buf);
            utilCopyDestName = file.name || 'destination.pdf';
            document.getElementById('util-copy-dest-name').textContent = utilCopyDestName;
            updateUtilCopyRunState();
        });
    });
    document.getElementById('util-copy-source-active').addEventListener('click', function () {
        if (!state.pdfBytes) return;
        utilCopySourceBytes = state.pdfBytes;
        utilCopySourceName = state.fileName || 'active.pdf';
        document.getElementById('util-copy-source-name').textContent = utilCopySourceName + ' (active)';
        updateUtilCopyRunState();
    });
    document.getElementById('util-copy-dest-active').addEventListener('click', function () {
        if (!state.pdfBytes) return;
        utilCopyDestBytes = state.pdfBytes;
        utilCopyDestName = state.fileName || 'active.pdf';
        document.getElementById('util-copy-dest-name').textContent = utilCopyDestName + ' (active)';
        updateUtilCopyRunState();
    });

    document.getElementById('util-copy-run').addEventListener('click', function () {
        if (!utilCopySourceBytes || !utilCopyDestBytes) return;
        var statusEl = document.getElementById('util-copy-status');
        statusEl.className = 'alert alert-info small mb-3';
        statusEl.textContent = 'Copying fields...';
        statusEl.classList.remove('hidden');
        document.getElementById('util-copy-run').disabled = true;

        var formData = new FormData();
        formData.append('source', new Blob([utilCopySourceBytes], { type: 'application/pdf' }), utilCopySourceName);
        formData.append('destination', new Blob([utilCopyDestBytes], { type: 'application/pdf' }), utilCopyDestName);

        fetch(apiUrl('/pdf-labeler/api/copy-fields'), {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData
        })
        .then(function (resp) { return parseApiResponse(resp); })
        .then(function (data) {
            if (!data.success) {
                throw new Error((data.error && data.error.message) || 'Copy failed.');
            }
            if (!data.data || !data.data.pdf_base64) {
                throw new Error('Server did not return a PDF.');
            }
            var outputBytes = base64ToUint8Array(data.data.pdf_base64);
            var outputName = data.data.filename || 'result-with-fields.pdf';

            statusEl.className = 'alert alert-success small mb-3';
            statusEl.textContent = 'Fields copied successfully.';

            var shouldLoad = document.getElementById('util-copy-load-result').checked;
            if (shouldLoad) {
                utilitiesModal.classList.add('hidden');
                var blob = new Blob([outputBytes], { type: 'application/pdf' });
                var file = new File([blob], outputName, { type: 'application/pdf' });
                loadPdf(file);
            } else {
                var blob = new Blob([outputBytes], { type: 'application/pdf' });
                var url = URL.createObjectURL(blob);
                var anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = outputName;
                document.body.appendChild(anchor);
                anchor.click();
                document.body.removeChild(anchor);
                URL.revokeObjectURL(url);
            }
        })
        .catch(function (err) {
            statusEl.className = 'alert alert-danger small mb-3';
            statusEl.textContent = 'Error: ' + (err && err.message ? err.message : 'Unknown error.');
        })
        .finally(function () {
            updateUtilCopyRunState();
        });
    });

    /* --------------- Bulk normalize --------------- */
    var bulkFiles = [];
    var BULK_WARN_BYTES = 16 * 1024 * 1024;

    function formatBytes(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function updateBulkState() {
        document.getElementById('util-bulk-run').disabled = bulkFiles.length === 0;
        document.getElementById('util-bulk-file-count').textContent =
            bulkFiles.length ? bulkFiles.length + ' file' + (bulkFiles.length === 1 ? '' : 's') + ' selected' : '';
        var totalSize = bulkFiles.reduce(function (sum, f) { return sum + f.size; }, 0);
        var sizeWarning = document.getElementById('util-bulk-size-warning');
        if (totalSize > BULK_WARN_BYTES) {
            document.getElementById('util-bulk-size-display').textContent = formatBytes(totalSize);
            sizeWarning.classList.remove('hidden');
        } else {
            sizeWarning.classList.add('hidden');
        }
    }

    document.getElementById('util-bulk-files').addEventListener('change', function (e) {
        bulkFiles = e.target.files ? Array.from(e.target.files) : [];
        updateBulkState();
    });

    document.getElementById('util-bulk-run').addEventListener('click', function () {
        if (!bulkFiles.length) return;
        var statusEl = document.getElementById('util-bulk-status');
        statusEl.className = 'alert alert-info small mb-3';
        statusEl.textContent = 'Normalizing ' + bulkFiles.length + ' PDF' + (bulkFiles.length === 1 ? '' : 's') + '...';
        statusEl.classList.remove('hidden');
        document.getElementById('util-bulk-run').disabled = true;

        var options = {
            normalizeFont: document.getElementById('util-bulk-norm-font').checked,
            fontName: document.getElementById('util-bulk-norm-font-name').value,
            normalizeFontSize: document.getElementById('util-bulk-norm-fontsize').checked,
            fontSizePt: Number(document.getElementById('util-bulk-norm-fontsize-val').value || 10),
            normalizeCheckboxStyle: document.getElementById('util-bulk-norm-checkbox-style').checked,
            checkboxStyle: document.getElementById('util-bulk-norm-checkbox-style-val').value,
            checkboxExportValue: 'Yes',
            uniformCheckboxSize: document.getElementById('util-bulk-norm-checkbox-size').checked,
            checkboxSizePt: Number(document.getElementById('util-bulk-norm-checkbox-size-val').value || 12),
            removeEmbeddedFonts: document.getElementById('util-bulk-norm-strip-fonts').checked
        };

        var formData = new FormData();
        formData.append('options', JSON.stringify(options));
        bulkFiles.forEach(function (file) {
            formData.append('files', file, file.name);
        });

        fetch(apiUrl('/pdf-labeler/api/bulk-normalize'), {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData
        })
        .then(function (resp) { return parseApiResponse(resp); })
        .then(function (data) {
            if (!data.success) {
                throw new Error((data.error && data.error.message) || 'Bulk normalize failed.');
            }
            if (!data.data || !data.data.zip_base64) {
                throw new Error('Server did not return a .zip file.');
            }
            var zipBytes = base64ToUint8Array(data.data.zip_base64);
            var zipName = data.data.filename || 'normalized-pdfs.zip';

            var summary = 'Normalized ' + data.data.processed + '/' + data.data.total + ' PDF' + (data.data.total === 1 ? '' : 's') + '.';
            if (data.data.errors && data.data.errors.length) {
                summary += ' Errors: ' + data.data.errors.join('; ');
                statusEl.className = 'alert alert-warning small mb-3';
            } else {
                statusEl.className = 'alert alert-success small mb-3';
            }
            statusEl.textContent = summary;

            var blob = new Blob([zipBytes], { type: 'application/zip' });
            var url = URL.createObjectURL(blob);
            var anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = zipName;
            document.body.appendChild(anchor);
            anchor.click();
            document.body.removeChild(anchor);
            URL.revokeObjectURL(url);
        })
        .catch(function (err) {
            statusEl.className = 'alert alert-danger small mb-3';
            statusEl.textContent = 'Error: ' + (err && err.message ? err.message : 'Unknown error.');
        })
        .finally(function () {
            updateBulkState();
        });
    });

    repairModal.querySelectorAll('.repair-run-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var action = btn.dataset.repairAction;
            if (!action || !state.pdfBytes) return;
            runRepairAction(action);
        });
    });
    passwordSubmitBtn.addEventListener('click', submitPassword);
    passwordInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') submitPassword();
    });
    passwordCancelBtn.addEventListener('click', function () {
        passwordModal.classList.add('hidden');
        state._pendingPasswordFile = null;
        pdfEmpty.classList.remove('hidden');
    });
    repairPromptFixBtn.addEventListener('click', function () {
        repairPromptModal.classList.add('hidden');
        if (state._pendingRepairFile || state.pdfBytes) {
            runRepairAction('auto');
        } else {
            repairStatus.classList.add('hidden');
            repairModal.querySelectorAll('.repair-run-btn').forEach(function (btn) { btn.disabled = false; });
            repairBtn.disabled = false;
            repairModal.classList.remove('hidden');
        }
    });
    repairPromptCancelBtn.addEventListener('click', function () {
        repairPromptModal.classList.add('hidden');
        state._pendingRepairFile = null;
        pdfEmpty.classList.remove('hidden');
    });
    settingsBtn.addEventListener('click', function () {
        aiModelInput.value = state.model || state.defaultModel;
        renderModelSuggestions('');
        // Load session defaults into settings UI
        var sessionDefs = _loadSessionDefaults();
        document.getElementById('settings-default-font').value = sessionDefs.font || 'Helvetica';
        document.getElementById('settings-default-font-size').value = String(sessionDefs.fontSize || 10);
        document.getElementById('settings-default-checkbox-style').value = sessionDefs.checkboxStyle || 'cross';
        settingsModal.classList.remove('hidden');
    });
    closeSettingsBtn.addEventListener('click', function () {
        settingsModal.classList.add('hidden');
        aiModelSuggestions.classList.add('hidden');
    });
    closeNormalizationBtn.addEventListener('click', function () {
        normalizationModal.classList.add('hidden');
    });
    applyNormalizationBtn.addEventListener('click', applyNormalizationPass);
    saveSettingsBtn.addEventListener('click', function () {
        state.model = aiModelInput.value.trim() || state.defaultModel;
        // Save session defaults
        setSessionDefault('font', document.getElementById('settings-default-font').value || 'Helvetica');
        setSessionDefault('fontSize', Number(document.getElementById('settings-default-font-size').value) || 10);
        setSessionDefault('checkboxStyle', document.getElementById('settings-default-checkbox-style').value || 'cross');
        settingsModal.classList.add('hidden');
        aiModelSuggestions.classList.add('hidden');
    });
    resetSettingsBtn.addEventListener('click', function () {
        state.model = state.defaultModel;
        aiModelInput.value = state.defaultModel;
        // Reset session defaults to hard defaults
        _saveSessionDefaults(Object.assign({}, HARD_DEFAULTS));
        document.getElementById('settings-default-font').value = HARD_DEFAULTS.font;
        document.getElementById('settings-default-font-size').value = String(HARD_DEFAULTS.fontSize);
        document.getElementById('settings-default-checkbox-style').value = HARD_DEFAULTS.checkboxStyle;
        renderModelSuggestions('');
    });
    aiModelInput.addEventListener('input', function () {
        state.model = aiModelInput.value.trim() || state.defaultModel;
        renderModelSuggestions(aiModelInput.value);
    });
    aiModelInput.addEventListener('focus', function () {
        renderModelSuggestions(aiModelInput.value);
    });
    quickFieldCloseBtn.addEventListener('click', function () {
        hideQuickFieldEditor();
    });

    // Close quick editor when clicking outside it, but keep the field selected
    document.addEventListener('mousedown', function (event) {
        if (state.quickEditFieldId &&
            !quickFieldEditor.contains(event.target) &&
            !event.target.closest('.field-box[data-field-id="' + state.quickEditFieldId + '"]')) {
            hideQuickFieldEditor();
        }
    }, true);
    quickFieldDeleteBtn.addEventListener('click', function () {
        const field = getQuickEditField();
        if (!field) return;
        deleteField(field.id);
    });
    quickFieldNameInput.addEventListener('input', function () {
        const field = getQuickEditField();
        if (!field) return;
        commitFieldNameChange(field.id, quickFieldNameInput.value, 'quick');
    });
    quickFieldNameInput.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            event.preventDefault();
            hideQuickFieldEditor();
            return;
        }
        if (event.key === 'Enter') {
            event.preventDefault();
            hideQuickFieldEditor();
        }
    });
    quickFieldSuggestions.addEventListener('click', function (event) {
        const button = event.target.closest('[data-suggested-name]');
        const field = getQuickEditField();
        if (!button || !field) return;
        const nextName = button.dataset.suggestedName || '';
        if (!nextName) return;
        event.preventDefault();
        commitFieldNameChange(field.id, nextName, 'quick');
        quickFieldNameInput.value = nextName;
        renderQuickFieldSuggestions(field);
        quickFieldNameInput.focus();
        quickFieldNameInput.select();
    });

    document.addEventListener('mousedown', function (event) {
        if (!aiModelSuggestions.contains(event.target) && event.target !== aiModelInput) {
            aiModelSuggestions.classList.add('hidden');
        }
        const authMenu = document.getElementById('auth-menu');
        if (authMenu && !authControls.contains(event.target)) {
            authMenu.classList.remove('show');
        }
        if (!settingsModal.classList.contains('hidden') && event.target === settingsModal) {
            settingsModal.classList.add('hidden');
            aiModelSuggestions.classList.add('hidden');
        }
        if (!normalizationModal.classList.contains('hidden') && event.target === normalizationModal) {
            normalizationModal.classList.add('hidden');
        }
        if (!repairModal.classList.contains('hidden') && event.target === repairModal) {
            repairModal.classList.add('hidden');
        }
        if (!pageManagerModal.classList.contains('hidden') && event.target === pageManagerModal) {
            closePageManager();
        }
    });

    fieldsList.addEventListener('input', function (event) {
        const target = event.target;
        if (!target || !target.dataset) return;
        const fieldId = target.dataset.fieldId;
        const field = state.fields.find(function (candidate) { return candidate.id === fieldId; });
        if (!field) return;
        if (target.dataset.action === 'field-name') {
            commitFieldNameChange(fieldId, target.value, 'sidebar');
            return;
        }
        if (target.dataset.action === 'field-options') {
            field.options = target.value.split(',').map(function (option) {
                return option.trim();
            }).filter(Boolean);
            if (!field.options.length) {
                field.options = DEFAULT_OPTION_LIST.slice();
            }
            markDirtyAndRender();
            return;
        }
        if (target.dataset.action === 'field-font-size') {
            const parsedValue = Number(target.value);
            if (Number.isFinite(parsedValue)) {
                field.fontSize = clamp(parsedValue, 4, 72);
                markDirtyAndRender();
            }
            return;
        }
        if (target.dataset.action === 'field-checkbox-export-value') {
            field.checkboxExportValue = String(target.value || '').trim() || 'Yes';
            markDirtyAndRender(true);
        }
    });

    fieldsList.addEventListener('change', function (event) {
        const target = event.target;
        if (!target || !target.dataset) return;
        const fieldId = target.dataset.fieldId;
        const field = state.fields.find(function (candidate) { return candidate.id === fieldId; });
        if (!field) return;
        if (target.dataset.action === 'field-type') {
            const newType = normalizeFieldType(target.value);
            field.type = newType;
            if (newType === 'text' || newType === 'multiline') {
                field.font = field.font || getSessionDefault('font');
                if (typeof field.autoSize !== 'boolean') {
                    field.autoSize = true;
                }
            }
            if (OPTION_TYPES.has(newType) && (!Array.isArray(field.options) || !field.options.length)) {
                field.options = DEFAULT_OPTION_LIST.slice();
            }
            if (!OPTION_TYPES.has(newType)) {
                delete field.options;
            }
            if (newType === 'checkbox') {
                field.checkboxStyle = field.checkboxStyle || 'check';
                field.checkboxExportValue = field.checkboxExportValue || 'Yes';
            }
            if (!(newType === 'text' || newType === 'multiline')) {
                field.autoSize = true;
            }
            markDirtyAndRender();
            return;
        }
        if (target.dataset.action === 'field-font') {
            field.font = target.value || getSessionDefault('font');
            markDirtyAndRender();
            return;
        }
        if (target.dataset.action === 'field-checkbox-style') {
            field.checkboxStyle = target.value || 'check';
            markDirtyAndRender();
            return;
        }
        if (target.dataset.action === 'field-auto-size') {
            field.autoSize = !!target.checked;
            markDirtyAndRender();
        }
    });

    fieldsList.addEventListener('click', function (event) {
        const actionTarget = event.target.closest('[data-action]');
        if (!actionTarget) return;
        const fieldId = actionTarget.dataset.fieldId;
        if (!fieldId) return;
        if (actionTarget.dataset.action === 'normalize-name') {
            event.preventDefault();
            event.stopPropagation();
            state.selectedFieldId = fieldId;
            normalizeCurrentFieldName();
            return;
        }
        if (actionTarget.dataset.action === 'duplicate-field') {
            event.preventDefault();
            event.stopPropagation();
            state.selectedFieldId = fieldId;
            duplicateSelectedField();
            return;
        }
        if (actionTarget.dataset.action === 'delete-field') {
            event.preventDefault();
            event.stopPropagation();
            deleteField(fieldId);
        }
    });

    autoDetectBtn.addEventListener('click', autoDetectFields);
    if (relabelBtn) {
        relabelBtn.addEventListener('click', relabelFields);
    }
    if (sidebarRelabelBtn) {
        sidebarRelabelBtn.addEventListener('click', relabelFields);
    }
    exportBtn.addEventListener('click', exportPdf);

    // Playground event listeners
    openPlaygroundBtn.addEventListener('click', openPlaygroundModal);
    savePlaygroundBtn.addEventListener('click', openSavePlaygroundModal);
    document.getElementById('close-open-playground').addEventListener('click', function() { openPlaygroundModalEl.classList.add('hidden'); });
    document.getElementById('open-pg-cancel').addEventListener('click', function() { openPlaygroundModalEl.classList.add('hidden'); });
    document.getElementById('open-pg-confirm').addEventListener('click', confirmOpenFromPlayground);
    document.getElementById('open-pg-project').addEventListener('change', fetchOpenPlaygroundTemplates);
    document.getElementById('close-save-playground').addEventListener('click', function() { savePlaygroundModalEl.classList.add('hidden'); });
    document.getElementById('save-pg-cancel').addEventListener('click', function() { savePlaygroundModalEl.classList.add('hidden'); });
    document.getElementById('save-pg-confirm').addEventListener('click', confirmSaveToPlayground);

    // Interview variable picker event listeners
    usePlaygroundVariablesInput.addEventListener('change', function() {
        state.usePlaygroundVariables = usePlaygroundVariablesInput.checked;
        renderPdfInterviewPicker();
    });
    pdfInterviewSourceModeInputs.forEach(function(inp) {
        inp.addEventListener('change', function() {
            state.interviewSourceMode = inp.value;
            if (inp.value === 'installed' && state.installed.packages.length === 0) fetchPdfInstalledPackages();
            renderPdfInterviewPicker();
        });
    });
    pdfPlaygroundProjectSelect.addEventListener('change', function() {
        state.playground.selectedProject = pdfPlaygroundProjectSelect.value;
        fetchPdfPlaygroundFiles();
    });
    pdfPlaygroundYamlFileSelect.addEventListener('change', function() {
        state.playground.selectedFile = pdfPlaygroundYamlFileSelect.value;
        fetchPdfPlaygroundVariables();
    });
    pdfInstalledPackageSelect.addEventListener('change', function() {
        state.installed.selectedPackage = pdfInstalledPackageSelect.value;
        fetchPdfInstalledFiles();
    });
    pdfInstalledYamlFileSelect.addEventListener('change', function() {
        state.installed.selectedFile = pdfInstalledYamlFileSelect.value;
        fetchPdfInstalledVariables();
    });

    document.addEventListener('keydown', function (event) {
        const targetTag = document.activeElement && document.activeElement.tagName ? document.activeElement.tagName.toUpperCase() : '';
        if ((event.key === 'Delete' || event.key === 'Backspace') && state.selectedFieldId && targetTag !== 'INPUT' && targetTag !== 'TEXTAREA') {
            event.preventDefault();
            deleteField(state.selectedFieldId);
            return;
        }
        if (event.key === 'Escape') {
            clearInteractionState();
            setSelectedTool(null);
            selectField(null, { focusNameInput: false, scrollIntoView: false });
            aiModelSuggestions.classList.add('hidden');
            settingsModal.classList.add('hidden');
            normalizationModal.classList.add('hidden');
            if (!pageManagerModal.classList.contains('hidden')) {
                closePageManager();
            }
            return;
        }
        if (!state.selectedFieldId || targetTag === 'INPUT' || targetTag === 'TEXTAREA') {
            return;
        }
        const field = getCurrentField();
        if (!field) return;
        const step = event.shiftKey ? 0.01 : 0.003;
        let moved = false;
        if (event.key === 'ArrowLeft') {
            field.x = clamp(field.x - step, 0, 1 - field.width);
            moved = true;
        } else if (event.key === 'ArrowRight') {
            field.x = clamp(field.x + step, 0, 1 - field.width);
            moved = true;
        } else if (event.key === 'ArrowUp') {
            field.y = clamp(field.y - step, 0, 1 - field.height);
            moved = true;
        } else if (event.key === 'ArrowDown') {
            field.y = clamp(field.y + step, 0, 1 - field.height);
            moved = true;
        }
        if (moved) {
            event.preventDefault();
            markDirtyAndRender();
        }
    });

    applyBranding();
    bulkRenameTypeInput.value = state.bulkRenameType;
    bulkRenamePatternInput.value = state.bulkRenamePattern;
    bulkRenameReplacementInput.value = state.bulkRenameReplacement;
    setFieldsPanelMode(state.fieldsPanelMode);
    updateDocumentName();
    updateToolHint();
    fetchModelCatalog();
    fetchAuthStatus();
    updateFieldCount();
    updateZoomControls();
