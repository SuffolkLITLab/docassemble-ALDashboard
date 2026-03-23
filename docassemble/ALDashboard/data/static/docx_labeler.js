    (function() {
        const DOCX_LABELER_CONFIG = (typeof window !== 'undefined' && window.DOCX_LABELER_CONFIG)
            ? window.DOCX_LABELER_CONFIG
            : {};
        const DOCX_LABELER_API = DOCX_LABELER_CONFIG.api || {};
        const DOCX_LABELER_UI = DOCX_LABELER_CONFIG.ui || {};

        function endpointPath(key, fallback) {
            var override = DOCX_LABELER_API[key];
            return (typeof override === 'string' && override.trim()) ? override.trim() : fallback;
        }

        function shouldHideInterviewPicker() {
            return !!DOCX_LABELER_UI.hideInterviewPicker;
        }

        function cloneVariableTree(value) {
            if (Array.isArray(value)) {
                return value.map(cloneVariableTree);
            }
            if (!value || typeof value !== 'object') {
                return value;
            }
            var clone = {};
            Object.keys(value).forEach(function(key) {
                clone[key] = cloneVariableTree(value[key]);
            });
            return clone;
        }

        function mergeVariableTree(baseTree, extraTree) {
            var merged = cloneVariableTree(baseTree || {});
            if (!extraTree || typeof extraTree !== 'object') {
                return merged;
            }
            Object.keys(extraTree).forEach(function(key) {
                var extraValue = extraTree[key];
                var mergedValue = merged[key];
                if (
                    mergedValue && extraValue
                    && typeof mergedValue === 'object'
                    && typeof extraValue === 'object'
                    && !Array.isArray(mergedValue)
                    && !Array.isArray(extraValue)
                ) {
                    merged[key] = mergeVariableTree(mergedValue, extraValue);
                } else {
                    merged[key] = cloneVariableTree(extraValue);
                }
            });
            return merged;
        }

        function getSingletonListChild(value) {
            if (!value || typeof value !== 'object' || Array.isArray(value)) {
                return null;
            }
            var childKeys = Object.keys(value).filter(function(key) { return !key.startsWith('_'); });
            if (childKeys.length !== 1 || childKeys[0] !== '[0]') {
                return null;
            }
            var child = value['[0]'];
            return child && typeof child === 'object' && !Array.isArray(child) ? child : null;
        }

        const DOCX_LABELER_VARIABLE_TREE_EXTRAS = DOCX_LABELER_CONFIG.variableTreeExtras || {};

        // ================================================================
        // AssemblyLine Variable Tree Structure
        // ================================================================
        const PERSON_ATTRIBUTES = {
            'name': {
                _description: 'Name components',
                'first': 'First name',
                'middle': 'Middle name',
                'middle_initial()': 'Middle initial',
                'last': 'Last name',
                'suffix': 'Suffix (Jr., Sr., III, etc.)',
                'full()': 'Full name'
            },
            'address': {
                _description: 'Address components',
                'block()': 'Full address (multiple lines)',
                'on_one_line()': 'Full address (single line)',
                'line_one()': 'Street + unit',
                'line_two()': 'City, state, zip',
                'address': 'Street address',
                'unit': 'Unit/Apt/Suite',
                'city': 'City',
                'state': 'State',
                'zip': 'ZIP/Postal code',
                'county': 'County',
                'country': 'Country'
            },
            'birthdate': 'Date of birth',
            'age_in_years()': 'Age (calculated)',
            'gender': 'Gender',
            'gender_female': 'Is female (checkbox)',
            'gender_male': 'Is male (checkbox)',
            'gender_other': 'Other gender (checkbox)',
            'gender_nonbinary': 'Nonbinary (checkbox)',
            'gender_undisclosed': 'Undisclosed (checkbox)',
            'phone_number': 'Phone number',
            'mobile_number': 'Mobile phone',
            'phone_numbers()': 'All phone numbers',
            'email': 'Email address',
            'signature': 'Signature'
        };

        const ATTORNEY_ATTRIBUTES = {
            ...PERSON_ATTRIBUTES,
            'bar_number': 'Bar/License number'
        };

        const AL_VARIABLE_TREE = mergeVariableTree({
            'users': { _description: 'People benefiting from the form (pro se filers)', '[0]': PERSON_ATTRIBUTES },
            'other_parties': { _description: 'Opposing/transactional parties', '[0]': PERSON_ATTRIBUTES },
            'plaintiffs': { _description: 'Plaintiffs in lawsuit', '[0]': PERSON_ATTRIBUTES },
            'defendants': { _description: 'Defendants in lawsuit', '[0]': PERSON_ATTRIBUTES },
            'petitioners': { _description: 'Petitioners', '[0]': PERSON_ATTRIBUTES },
            'respondents': { _description: 'Respondents', '[0]': PERSON_ATTRIBUTES },
            'children': { _description: 'Children involved', '[0]': PERSON_ATTRIBUTES },
            'spouses': { _description: 'Spouses', '[0]': PERSON_ATTRIBUTES },
            'parents': { _description: 'Parents', '[0]': PERSON_ATTRIBUTES },
            'caregivers': { _description: 'Caregivers', '[0]': PERSON_ATTRIBUTES },
            'guardians': { _description: 'Guardians', '[0]': PERSON_ATTRIBUTES },
            'guardians_ad_litem': { _description: 'Guardians ad litem', '[0]': PERSON_ATTRIBUTES },
            'witnesses': { _description: 'Witnesses', '[0]': PERSON_ATTRIBUTES },
            'attorneys': { _description: 'Attorneys', '[0]': ATTORNEY_ATTRIBUTES },
            'translators': { _description: 'Translators/Interpreters', '[0]': PERSON_ATTRIBUTES },
            'creditors': { _description: 'Creditors', '[0]': PERSON_ATTRIBUTES },
            'debt_collectors': { _description: 'Debt collectors', '[0]': PERSON_ATTRIBUTES },
            'decedents': { _description: 'Deceased persons', '[0]': PERSON_ATTRIBUTES },
            'interested_parties': { _description: 'Other interested parties', '[0]': PERSON_ATTRIBUTES },
            'trial_court': {
                _description: 'Court information',
                'name': 'Court name',
                'address': {
                    'county': 'County',
                    'address': 'Street address',
                    'city': 'City',
                    'state': 'State'
                },
                'division': 'Division',
                'department': 'Department'
            },
            'docket_number': 'Case/Docket number',
            'docket_numbers': 'Multiple docket numbers (comma-separated)',
            'case_name': 'Case name/caption',
            'signature_date': 'Date form is signed',
            'user_needs_interpreter': 'User needs interpreter (checkbox)',
            'user_preferred_language': 'User\'s preferred language'
        }, DOCX_LABELER_VARIABLE_TREE_EXTRAS);

        // ================================================================
        // State
        // ================================================================
        var previewUtils = (typeof window !== 'undefined' && window.DocxLabelerPreviewUtils)
            ? window.DocxLabelerPreviewUtils
            : null;

        let state = {
            file: null,
            fileContent: null,
            originalHtml: '',
            playgroundSource: null,
            runs: [],          // original runs from extract-runs endpoint
            existingLabels: [],
            suggestions: [],
            labelRenames: {},
            defaultModel: 'gpt-5-mini',
            recommendedModels: ['gpt-5-mini'],
            availableModels: [],
            auth: {
                isAuthenticated: false,
                email: '',
                loginUrl: '/user/sign-in',
                logoutUrl: '/user/sign-out',
                aiEnabled: false
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
            ui: {
                editInterviewSourceInSettings: false
            },
            settings: {
                additionalInstructions: '',
                contextText: '',
                customPeople: '',
                promptProfile: 'standard',
                model: 'gpt-5-mini',
                judgeModel: '',
                generationMethod: 'multi_run',
                generatorModels: '',
                defragmentRuns: true,
                usePlaygroundVariables: false,
                showLowConfidence: false
            },
            activeTab: 'existing',
            editingLabelId: null,
            validation: null,
            syntaxValidation: null,
            syntaxValidationPending: false,
            syntaxValidationTimer: null,
            syntaxValidationRequestToken: 0
        };

        // ================================================================
        // DOM Elements
        // ================================================================
        const fileInput = document.getElementById('file-input');
        const fileName = document.getElementById('file-name');
        const openPlaygroundBtn = document.getElementById('open-playground-btn');
        const savePlaygroundBtn = document.getElementById('save-playground-btn');
        const openPlaygroundModalEl = document.getElementById('open-playground-modal');
        const savePlaygroundModalEl = document.getElementById('save-playground-modal');
        const interviewPickerPanel = document.getElementById('interview-picker-panel');
        const interviewPickerSidebarHost = document.getElementById('interview-picker-sidebar-host');
        const settingsInterviewPickerHost = document.getElementById('settings-interview-picker-host');
        const changeInterviewSourceBtn = document.getElementById('change-interview-source-btn');
        const interviewSourceModeInputs = Array.from(document.querySelectorAll('input[name="interview-source-mode"]'));
        const playgroundFields = document.getElementById('playground-fields');
        const installedFields = document.getElementById('installed-fields');
        const playgroundProjectSelect = document.getElementById('playground-project');
        const playgroundYamlFileSelect = document.getElementById('playground-yaml-file');
        const installedPackageSelect = document.getElementById('installed-package');
        const installedYamlFileSelect = document.getElementById('installed-yaml-file');
        const interviewVariableSummary = document.getElementById('interview-variable-summary');
        const loadingState = document.getElementById('loading-state');
        const loadingMessage = document.getElementById('loading-message');
        const errorState = document.getElementById('error-state');
        const errorMessage = document.getElementById('error-message');
        const emptyState = document.getElementById('empty-state');
        const mainPanel = document.getElementById('main-panel');
        const previewEmpty = document.getElementById('preview-empty');
        const previewContent = document.getElementById('preview-content');
        const applyHighlightsBtn = document.getElementById('apply-highlights-btn');
        const downloadBtn = document.getElementById('download-btn');
        const downloadStatus = document.getElementById('download-status');
        const syntaxValidationBanner = document.getElementById('syntax-validation');
        const existingPanel = document.getElementById('existing-panel');
        const manualPanel = document.getElementById('manual-panel');
        const suggestionsPanel = document.getElementById('suggestions-panel');
        const suggestionsList = document.getElementById('suggestions-list');
        const manualLabelsList = document.getElementById('manual-labels-list');
        const existingLabelsTree = document.getElementById('existing-labels-tree');
        const existingCount = document.getElementById('existing-count');
        const manualCount = document.getElementById('manual-count');
        const suggestionsCount = document.getElementById('suggestions-count');
        const tabExisting = document.getElementById('tab-existing');
        const tabManual = document.getElementById('tab-manual');
        const tabSuggestions = document.getElementById('tab-suggestions');
        const variableTree = document.getElementById('variable-tree');
        const variableSearch = document.getElementById('variable-search');
        const settingsModal = document.getElementById('settings-modal');
        const bulkReplaceModal = document.getElementById('bulk-replace-modal');
        const editLabelModal = document.getElementById('edit-label-modal');
        const selPopover = document.getElementById('sel-popover');
        const selOriginalText = document.getElementById('sel-original-text');
        const selVarInput = document.getElementById('sel-var-input');
        const selVarPanel = document.getElementById('sel-var-panel');
        const selVarTree = document.getElementById('sel-var-tree');
        const selVarSearch = document.getElementById('sel-var-search');
        const aiModelInput = document.getElementById('ai-model');
        const aiModelSuggestions = document.getElementById('ai-model-suggestions');
        const promptProfileInput = document.getElementById('prompt-profile');
        const generationMethodInput = document.getElementById('generation-method');
        const generatorModelsGroup = document.getElementById('generator-models-group');
        const generatorModelsInput = document.getElementById('generator-models');
        const judgeModelInput = document.getElementById('judge-model');
        const usePlaygroundVariablesInput = document.getElementById('use-playground-variables');
        const usePlaygroundVarsGroup = document.getElementById('use-playground-vars-group');
        const playgroundSettingsSummary = document.getElementById('playground-settings-summary');
        const authControls = document.getElementById('auth-controls');
        const utilitiesBtn = document.getElementById('utilities-btn');
        const repairBtn = document.getElementById('repair-btn');
        const aiAuthNotice = document.getElementById('ai-auth-notice');
        const suggestionsValidation = document.getElementById('suggestions-validation');
        const toggleLowConfidence = document.getElementById('toggle-low-confidence');
        const lowConfidenceSummary = document.getElementById('low-confidence-summary');
        const utilitiesModal = document.getElementById('utilities-modal');
        const repairModal = document.getElementById('repair-modal');
        const utilityFileInput = document.getElementById('utility-file-input');
        const repairFileInput = document.getElementById('repair-file-input');
        const utilitySourceActive = document.getElementById('utility-source-active');
        const utilitySourceUpload = document.getElementById('utility-source-upload');
        const repairSourceActive = document.getElementById('repair-source-active');
        const repairSourceUpload = document.getElementById('repair-source-upload');
        const utilitiesResult = document.getElementById('utilities-result');
        const repairResult = document.getElementById('repair-result');
        const utilitySourceStatus = document.getElementById('utility-source-status');
        const repairSourceStatus = document.getElementById('repair-source-status');

        // ================================================================
        // Helpers
        // ================================================================
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function generateId() {
            return 'id-' + Math.random().toString(36).substr(2, 9);
        }

        function getConfidenceMeta(tier) {
            if (tier === 'high') {
                return { label: 'High confidence', badge: 'bg-success-subtle text-success-emphasis border border-success-subtle' };
            }
            if (tier === 'medium') {
                return { label: 'Medium confidence', badge: 'bg-warning-subtle text-warning-emphasis border border-warning-subtle' };
            }
            return { label: 'Low confidence', badge: 'bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle' };
        }

        function summarizeSuggestionSources(suggestion) {
            var sources = Array.isArray(suggestion.sources) ? suggestion.sources : [];
            var models = [];
            sources.forEach(function(source) {
                if (source && source.model && models.indexOf(source.model) === -1) {
                    models.push(source.model);
                }
            });
            return models.join(', ');
        }

        function isManualSuggestion(suggestion) {
            return !!(suggestion && suggestion._isManual);
        }

        function getManualLabelGroups() {
            return state.suggestions.filter(function(suggestion) {
                return isManualSuggestion(suggestion) && !suggestion._isCompanion;
            });
        }

        function applyManualLabelText(groupId, variableName) {
            state.suggestions.forEach(function(suggestion) {
                if (suggestion.group !== groupId) return;
                suggestion._displayLabel = variableName;
                var manualKind = suggestion._manualKind || 'replace';
                if (manualKind === 'if_wrap' || manualKind === 'ifp_wrap') {
                    var openTag = manualKind === 'ifp_wrap'
                        ? '{%p if ' + variableName + ' %}'
                        : '{% if ' + variableName + ' %}';
                    var closeTag = manualKind === 'ifp_wrap'
                        ? '{%p endif %}'
                        : '{% endif %}';
                    var selectedFragment = suggestion._selectedFragment || '';
                    if (suggestion._manualRole === 'single') {
                        suggestion.text =
                            (suggestion._manualPrefix || '') +
                            openTag +
                            selectedFragment +
                            closeTag +
                            (suggestion._manualSuffix || '');
                    } else if (suggestion._manualRole === 'first') {
                        suggestion.text =
                            (suggestion._manualPrefix || '') + openTag + selectedFragment;
                    } else if (suggestion._manualRole === 'last') {
                        suggestion.text = selectedFragment + closeTag + (suggestion._manualSuffix || '');
                    } else if (suggestion._manualRole === 'middle') {
                        suggestion.text = selectedFragment;
                    }
                    return;
                }
                if (suggestion._manualRole === 'single') {
                    suggestion.text = (suggestion._manualPrefix || '') + variableName + (suggestion._manualSuffix || '');
                } else if (suggestion._manualRole === 'first') {
                    suggestion.text = (suggestion._manualPrefix || '') + variableName;
                } else if (suggestion._manualRole === 'last') {
                    suggestion.text = suggestion._manualSuffix || '';
                } else if (suggestion._manualRole === 'middle') {
                    suggestion.text = '';
                }
            });
        }

        function collectRenamePayload() {
            // Existing-label edits are sent as run-level patches via the labels payload.
            return [];
        }

        function collectAcceptedLabels() {
            var acceptedByKey = {};
            state.suggestions.filter(function(s) { return s.status === 'accepted'; }).forEach(function(s) {
                var key = s.paragraph + ',' + s.run + ',' + (s.new_paragraph || 0);
                acceptedByKey[key] = { paragraph: s.paragraph, run: s.run, text: s.text, new_paragraph: s.new_paragraph };
            });

            var existingPatches = [];
            if (previewUtils && previewUtils.buildRunPatchLabelsFromExistingEdits) {
                existingPatches = previewUtils.buildRunPatchLabelsFromExistingEdits(state.existingLabels, state.runs);
            }
            existingPatches.forEach(function(patch) {
                var key = patch.paragraph + ',' + patch.run + ',0';
                acceptedByKey[key] = patch;
            });

            return Object.keys(acceptedByKey).map(function(key) {
                return acceptedByKey[key];
            });
        }

        function renderSyntaxValidation() {
            if (!syntaxValidationBanner) return;
            var validation = state.syntaxValidation;
            var errors = validation && Array.isArray(validation.errors) ? validation.errors : [];
            var warnings = validation && Array.isArray(validation.warnings) ? validation.warnings : [];

            if (!state.syntaxValidationPending && errors.length === 0 && warnings.length === 0) {
                syntaxValidationBanner.className = 'hidden alert small mb-2';
                syntaxValidationBanner.textContent = '';
                return;
            }

            var className = 'alert small mb-2';
            var html = '';
            if (errors.length > 0) {
                className += ' alert-danger';
                html += '<div class="fw-semibold">Jinja syntax errors detected</div>';
                errors.forEach(function(issue) {
                    var label = issue.paragraph !== undefined ? 'Paragraph ' + (issue.paragraph + 1) : 'Template';
                    html += '<div>' + escapeHtml(label + ': ' + (issue.message || issue.code || 'Syntax error')) + '</div>';
                });
            } else if (warnings.length > 0) {
                className += ' alert-warning';
                html += '<div class="fw-semibold">Jinja validation warnings</div>';
                warnings.forEach(function(issue) {
                    html += '<div>' + escapeHtml(issue.message || issue.code || 'Warning') + '</div>';
                });
            } else {
                className += ' alert-info';
                html = '<div>Checking Jinja syntax...</div>';
            }

            if (state.syntaxValidationPending && (errors.length > 0 || warnings.length > 0)) {
                html += '<div class="mt-1 text-muted">Rechecking...</div>';
            }

            syntaxValidationBanner.className = className;
            syntaxValidationBanner.innerHTML = html;
        }

        async function validateCurrentSyntax(options) {
            options = options || {};
            if (!state.file) {
                state.syntaxValidation = null;
                state.syntaxValidationPending = false;
                renderSyntaxValidation();
                return null;
            }

            var renames = collectRenamePayload();
            var acceptedLabels = collectAcceptedLabels();
            if (!options.force && renames.length === 0 && acceptedLabels.length === 0) {
                state.syntaxValidation = null;
                state.syntaxValidationPending = false;
                renderSyntaxValidation();
                return null;
            }

            var requestToken = state.syntaxValidationRequestToken + 1;
            state.syntaxValidationRequestToken = requestToken;
            state.syntaxValidationPending = true;
            renderSyntaxValidation();

            try {
                var formData = new FormData();
                formData.append('file', state.file);
                formData.append('defragment_runs', state.settings.defragmentRuns ? 'true' : 'false');
                if (renames.length > 0) formData.append('renames', JSON.stringify(renames));
                if (acceptedLabels.length > 0) formData.append('labels', JSON.stringify(acceptedLabels));
                var response = await fetch('/al/docx-labeler/api/validate-syntax', { method: 'POST', body: formData });
                var data = await parseApiResponse(response);
                if (requestToken === state.syntaxValidationRequestToken) {
                    state.syntaxValidation = data.data && data.data.validation ? data.data.validation : null;
                }
            } catch (error) {
                if (requestToken === state.syntaxValidationRequestToken) {
                    state.syntaxValidation = {
                        valid: false,
                        errors: [{ code: 'validation_request_failed', message: error.message || 'Syntax validation failed.' }],
                        warnings: []
                    };
                }
            } finally {
                if (requestToken === state.syntaxValidationRequestToken) {
                    state.syntaxValidationPending = false;
                    renderSyntaxValidation();
                    updateDownloadButton();
                }
            }

            return state.syntaxValidation;
        }

        function scheduleSyntaxValidation(delay) {
            if (state.syntaxValidationTimer) {
                window.clearTimeout(state.syntaxValidationTimer);
            }
            state.syntaxValidationTimer = window.setTimeout(function() {
                validateCurrentSyntax();
            }, typeof delay === 'number' ? delay : 400);
        }

        async function ensureSyntaxValidationBeforeWrite(actionLabel) {
            var validation = await validateCurrentSyntax({ force: true });
            var errors = validation && Array.isArray(validation.errors) ? validation.errors : [];
            if (errors.length === 0) {
                return { allowed: true, validation: validation, allowInvalidSyntax: false };
            }

            var summary = errors.slice(0, 3).map(function(issue) {
                return '- ' + (issue.message || issue.code || 'Syntax error');
            }).join('\n');
            var proceed = window.confirm(
                'The Jinja syntax validator found errors before ' + actionLabel + ':\n\n'
                + summary
                + '\n\nSave anyway?'
            );
            return { allowed: proceed, validation: validation, allowInvalidSyntax: proceed };
        }

        function swapSuggestionWithAlternate(suggestion, alternateIndex) {
            if (!suggestion || !Array.isArray(suggestion.alternates)) return;
            var alternate = suggestion.alternates[alternateIndex];
            if (!alternate) return;
            var currentPrimary = {
                text: suggestion.text,
                paragraph: suggestion.paragraph,
                run: suggestion.run,
                new_paragraph: suggestion.new_paragraph,
                validation_flags: suggestion.validation_flags || [],
                judge_review: suggestion.judge_review || null,
                confidence: suggestion.confidence || 'low',
                vote_count: suggestion.vote_count || 0,
                clean_vote_count: suggestion.clean_vote_count || 0,
                vote_total: suggestion.vote_total || 0,
                sources: suggestion.sources || []
            };
            suggestion.text = alternate.text;
            suggestion.validation_flags = alternate.validation_flags || [];
            suggestion.judge_review = alternate.judge_review || null;
            suggestion.confidence = alternate.confidence || 'low';
            suggestion.vote_count = alternate.vote_count || 0;
            suggestion.clean_vote_count = alternate.clean_vote_count || 0;
            suggestion.vote_total = alternate.vote_total || suggestion.vote_total || 0;
            suggestion.sources = alternate.sources || [];
            suggestion.alternates.splice(alternateIndex, 1, currentPrimary);
        }

        async function fetchModelCatalog() {
            try {
                var response = await fetch(endpointPath('models', '/al/labeler/api/models'), { method: 'GET' });
                var data = await response.json();
                if (!data.success || !data.data) return;
                state.defaultModel = data.data.default_model || state.defaultModel;
                state.recommendedModels = Array.isArray(data.data.recommended_models) && data.data.recommended_models.length
                    ? data.data.recommended_models
                    : [state.defaultModel];
                state.availableModels = Array.isArray(data.data.available_models) ? data.data.available_models : [];
                state.settings.model = state.defaultModel;
                renderModelSuggestions('');
            } catch (_error) {
                renderModelSuggestions('');
            }
        }

        function renderAuthControls() {
            if (!authControls) return;
            if (state.auth.isAuthenticated) {
                var emailText = state.auth.email || 'Account';
                authControls.innerHTML =
                    '<button id="auth-menu-btn" class="btn btn-outline-light btn-sm dropdown-toggle" type="button" aria-expanded="false">' + escapeHtml(emailText) + '</button>' +
                    '<div id="auth-menu" class="dropdown-menu dropdown-menu-end">' +
                        '<a class="dropdown-item" href="' + escapeHtml(state.auth.logoutUrl || '/user/sign-out') + '">Log out</a>' +
                    '</div>';
                var menuBtn = document.getElementById('auth-menu-btn');
                var menu = document.getElementById('auth-menu');
                menuBtn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    menu.classList.toggle('show');
                });
            } else {
                authControls.innerHTML =
                    '<a class="btn btn-outline-light btn-sm" href="' + escapeHtml(state.auth.loginUrl || '/user/sign-in') + '">Log in</a>';
            }
        }

        function updateAiUiState() {
            var aiEnabled = !!state.auth.aiEnabled;
            var regenerateBtn = document.getElementById('regenerate-btn');
            if (regenerateBtn) {
                regenerateBtn.disabled = !aiEnabled;
            }
            if (!aiEnabled) {
                aiAuthNotice.classList.remove('hidden');
                aiAuthNotice.innerHTML = 'AI labeling requires login. <a href="' + escapeHtml(state.auth.loginUrl || '/user/sign-in') + '">Log in</a> to enable suggestions.';
            } else {
                aiAuthNotice.classList.add('hidden');
            }
            // Show playground buttons only when authenticated
            if (state.auth.isAuthenticated) {
                openPlaygroundBtn.classList.remove('hidden');
            } else {
                openPlaygroundBtn.classList.add('hidden');
                savePlaygroundBtn.classList.add('hidden');
            }
        }

        function getActiveInterviewSourceState() {
            return state.interviewSourceMode === 'installed' ? state.installed : state.playground;
        }

        function moveInterviewPickerTo(hostElement) {
            if (!hostElement || !interviewPickerPanel) return;
            if (interviewPickerPanel.parentElement !== hostElement) {
                hostElement.appendChild(interviewPickerPanel);
            }
        }

        function getActiveInterviewSourceLabel() {
            var sourceState = getActiveInterviewSourceState();
            if (state.interviewSourceMode === 'installed') {
                if (!sourceState.selectedPackage || !sourceState.selectedFile) return '';
                return sourceState.selectedPackage + '/' + sourceState.selectedFile;
            }
            if (!sourceState.selectedProject || !sourceState.selectedFile) return '';
            return sourceState.selectedProject + '/' + sourceState.selectedFile;
        }

        function renderInterviewSourceSummary() {
            if (!interviewVariableSummary || !playgroundSettingsSummary) return;
            var sourceLabel = getActiveInterviewSourceLabel();
            var sourceState = getActiveInterviewSourceState();
            var sourceText = state.interviewSourceMode === 'installed' ? 'installed interview' : 'Playground interview';
            var count = sourceState.variables.length;
            if (sourceLabel) {
                interviewVariableSummary.textContent =
                    sourceLabel
                    + (count ? ' (' + count + ' vars, ' + sourceState.topLevelNames.length + ' top-level)' : ' (no variables detected yet)');
                interviewVariableSummary.classList.remove('hidden');
                playgroundSettingsSummary.textContent =
                    'Selected ' + sourceText + ': ' + sourceLabel
                    + (count ? '. The prompt will prefer its existing variable names.' : '.');
            } else {
                interviewVariableSummary.classList.add('hidden');
                playgroundSettingsSummary.textContent =
                    'Select a Playground or installed interview from the sidebar to use its variable list in the prompt.';
            }
        }

        function renderInterviewPicker() {
            if (!interviewPickerPanel || !interviewPickerSidebarHost || !settingsInterviewPickerHost || !usePlaygroundVarsGroup) return;
            if (shouldHideInterviewPicker()) {
                usePlaygroundVarsGroup.classList.add('hidden');
                if (changeInterviewSourceBtn) changeInterviewSourceBtn.classList.add('hidden');
                interviewPickerPanel.classList.add('hidden');
                interviewPickerSidebarHost.classList.add('hidden');
                settingsInterviewPickerHost.classList.add('hidden');
                state.settings.usePlaygroundVariables = false;
                usePlaygroundVariablesInput.checked = false;
                usePlaygroundVariablesInput.disabled = true;
                return;
            }
            var showPicker = !!state.auth.isAuthenticated;
            usePlaygroundVarsGroup.classList.toggle('hidden', !showPicker);
            if (changeInterviewSourceBtn) {
                changeInterviewSourceBtn.classList.toggle('hidden', !showPicker);
                changeInterviewSourceBtn.textContent = state.ui.editInterviewSourceInSettings ? 'Done' : 'Change';
            }
            if (showPicker && state.ui.editInterviewSourceInSettings) {
                moveInterviewPickerTo(settingsInterviewPickerHost);
            } else {
                moveInterviewPickerTo(interviewPickerSidebarHost);
            }
            interviewPickerPanel.classList.toggle('hidden', !showPicker);
            interviewPickerSidebarHost.classList.toggle('hidden', !showPicker || state.ui.editInterviewSourceInSettings);
            settingsInterviewPickerHost.classList.toggle('hidden', !showPicker || !state.ui.editInterviewSourceInSettings);
            playgroundFields.classList.toggle('hidden', state.interviewSourceMode !== 'playground');
            installedFields.classList.toggle('hidden', state.interviewSourceMode !== 'installed');
            interviewSourceModeInputs.forEach(function(input) {
                input.checked = input.value === state.interviewSourceMode;
            });

            playgroundProjectSelect.innerHTML = '';
            state.playground.projects.forEach(function(projectName) {
                var option = document.createElement('option');
                option.value = projectName;
                option.textContent = projectName;
                if (projectName === state.playground.selectedProject) option.selected = true;
                playgroundProjectSelect.appendChild(option);
            });

            playgroundYamlFileSelect.innerHTML = '';
            if (!state.playground.files.length) {
                var emptyPlaygroundOption = document.createElement('option');
                emptyPlaygroundOption.value = '';
                emptyPlaygroundOption.textContent = 'No YAML files found';
                playgroundYamlFileSelect.appendChild(emptyPlaygroundOption);
            } else {
                state.playground.files.forEach(function(fileInfo) {
                    var option = document.createElement('option');
                    option.value = fileInfo.filename;
                    option.textContent = fileInfo.label || fileInfo.filename;
                    if (fileInfo.filename === state.playground.selectedFile) option.selected = true;
                    playgroundYamlFileSelect.appendChild(option);
                });
            }

            installedPackageSelect.innerHTML = '';
            state.installed.packages.forEach(function(packageName) {
                var option = document.createElement('option');
                option.value = packageName;
                option.textContent = packageName;
                if (packageName === state.installed.selectedPackage) option.selected = true;
                installedPackageSelect.appendChild(option);
            });

            installedYamlFileSelect.innerHTML = '';
            if (!state.installed.files.length) {
                var emptyInstalledOption = document.createElement('option');
                emptyInstalledOption.value = '';
                emptyInstalledOption.textContent = 'No YAML files found';
                installedYamlFileSelect.appendChild(emptyInstalledOption);
            } else {
                state.installed.files.forEach(function(fileInfo) {
                    var option = document.createElement('option');
                    option.value = fileInfo.filename;
                    option.textContent = fileInfo.label || fileInfo.filename;
                    if (fileInfo.filename === state.installed.selectedFile) option.selected = true;
                    installedYamlFileSelect.appendChild(option);
                });
            }

            var activeSourceState = getActiveInterviewSourceState();
            usePlaygroundVariablesInput.disabled = !showPicker || !activeSourceState.selectedFile;
            if (usePlaygroundVariablesInput.disabled) {
                state.settings.usePlaygroundVariables = false;
                usePlaygroundVariablesInput.checked = false;
            }
            renderInterviewSourceSummary();
        }

        function deriveTopLevelNames(variableNames) {
            return Array.from(new Set(
                (Array.isArray(variableNames) ? variableNames : [])
                    .map(function(name) { return String(name || '').trim(); })
                    .filter(function(name) { return !!name; })
                    .map(function(name) { return name.split('.', 1)[0].split('[', 1)[0]; })
                    .filter(function(name) { return !!name; })
            )).sort(function(a, b) { return a.localeCompare(b); });
        }

        function splitVariablePathSegments(variableName) {
            var raw = String(variableName || '').trim();
            if (!raw) return [];
            var matches = raw.match(/([^[.\]]+|\[[^\]]*\])/g);
            return Array.isArray(matches) ? matches.filter(function(part) { return !!part; }) : [raw];
        }

        function buildInterviewVariableTree(variableNames) {
            var root = {
                _description: 'Variables detected from the selected interview'
            };
            (Array.isArray(variableNames) ? variableNames : []).forEach(function(variableName) {
                var segments = splitVariablePathSegments(variableName);
                if (!segments.length) return;
                var node = root;
                segments.forEach(function(segment, index) {
                    var isLeaf = index === segments.length - 1;
                    if (!node[segment] || typeof node[segment] !== 'object') {
                        node[segment] = {};
                    }
                    if (isLeaf) {
                        node[segment]._variable = true;
                        node[segment]._description = 'Selected interview variable';
                    }
                    node = node[segment];
                });
            });
            return root;
        }

        function getEffectiveVariableTree() {
            var sourceState = getActiveInterviewSourceState();
            if (!sourceState.variables.length) {
                return AL_VARIABLE_TREE;
            }
            return Object.assign(
                {
                    'Selected interview variables': buildInterviewVariableTree(sourceState.variables)
                },
                AL_VARIABLE_TREE
            );
        }

        async function fetchJsonOrThrow(url) {
            var response = await fetch(url, {
                method: 'GET',
                credentials: 'same-origin'
            });
            if (!response.ok) {
                throw new Error('Request failed with status ' + response.status);
            }
            return response.json();
        }

        async function parseApiResponse(response) {
            var contentType = response.headers.get('content-type') || '';
            var data;
            if (contentType.indexOf('application/json') !== -1) {
                data = await response.json();
            } else {
                var text = await response.text();
                if (!response.ok) {
                    throw new Error(text || ('Request failed (' + response.status + ').'));
                }
                throw new Error('The server returned a non-JSON response.');
            }
            if (!response.ok) {
                throw new Error((data && data.error && data.error.message) ? data.error.message : ('Request failed (' + response.status + ').'));
            }
            return data;
        }

        function updateModalSourceStatus() {
            var hasActiveFile = !!state.file;
            utilityFileInput.classList.toggle('hidden', utilitySourceActive.checked);
            repairFileInput.classList.toggle('hidden', repairSourceActive.checked);
            utilitySourceStatus.textContent = utilitySourceActive.checked
                ? (hasActiveFile ? 'The active DOCX will be used.' : 'No active DOCX is loaded. Choose a separate DOCX upload instead.')
                : (utilityFileInput.files[0] ? utilityFileInput.files[0].name : 'Choose a DOCX file for utilities.');
            repairSourceStatus.textContent = repairSourceActive.checked
                ? (hasActiveFile ? 'The active DOCX will be used.' : 'No active DOCX is loaded. Choose a separate DOCX upload instead.')
                : (repairFileInput.files[0] ? repairFileInput.files[0].name : 'Choose a DOCX file for repair.');
        }

        function getModalSourceFile(kind) {
            if (kind === 'utility') {
                return utilitySourceActive.checked ? state.file : utilityFileInput.files[0];
            }
            return repairSourceActive.checked ? state.file : repairFileInput.files[0];
        }

        function downloadBase64Docx(docxBase64, filename) {
            var binaryString = atob(docxBase64);
            var bytes = new Uint8Array(binaryString.length);
            for (var i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
            var blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
            var url = URL.createObjectURL(blob);
            var link = document.createElement('a');
            link.href = url;
            link.download = filename || 'output.docx';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        }

        function formatReportAsHtml(report) {
            if (!report) return '<div class="text-muted">No report returned.</div>';
            if (typeof report === 'string') return '<div>' + escapeHtml(report) + '</div>';
            if (Array.isArray(report)) return '<pre class="bg-light rounded p-2 small mb-0">' + escapeHtml(JSON.stringify(report, null, 2)) + '</pre>';

            var html = '';
            if (Array.isArray(report.errors) && report.errors.length) {
                html += '<div class="alert alert-danger small"><div class="fw-semibold mb-1">Errors</div>';
                report.errors.forEach(function(issue) {
                    html += '<div>' + escapeHtml(issue.message || issue.error || JSON.stringify(issue)) + '</div>';
                });
                html += '</div>';
            }
            if (Array.isArray(report.warning_details) && report.warning_details.length) {
                html += '<div class="alert alert-warning small"><div class="fw-semibold mb-1">Warnings</div>';
                report.warning_details.forEach(function(issue) {
                    html += '<div>' + escapeHtml(issue.message || issue.code || JSON.stringify(issue)) + '</div>';
                });
                html += '</div>';
            } else if (Array.isArray(report.warnings) && report.warnings.length) {
                html += '<div class="alert alert-warning small"><div class="fw-semibold mb-1">Warnings</div>';
                report.warnings.forEach(function(issue) {
                    html += '<div>' + escapeHtml(typeof issue === 'string' ? issue : (issue.message || issue.code || JSON.stringify(issue))) + '</div>';
                });
                html += '</div>';
            }
            if (Array.isArray(report.xml_parse_errors) && report.xml_parse_errors.length) {
                html += '<div class="alert alert-danger small"><div class="fw-semibold mb-1">XML Parse Errors</div>';
                report.xml_parse_errors.forEach(function(issue) {
                    html += '<div>' + escapeHtml((issue.part || 'part') + ': ' + (issue.error || 'Parse error')) + '</div>';
                });
                html += '</div>';
            }
            if (Array.isArray(report.schema_errors) && report.schema_errors.length) {
                html += '<div class="alert alert-danger small"><div class="fw-semibold mb-1">Schema Errors</div>';
                report.schema_errors.forEach(function(issue) {
                    html += '<div>' + escapeHtml((issue.part || 'part') + ': ' + (issue.error || 'Schema error')) + '</div>';
                });
                html += '</div>';
            }
            if (report.message) {
                html += '<div class="alert alert-info small">' + escapeHtml(report.message) + '</div>';
            }
            html += '<pre class="bg-light rounded p-2 small mb-0">' + escapeHtml(JSON.stringify(report, null, 2)) + '</pre>';
            return html;
        }

        async function runDocxOperation(kind, action, progressMessage) {
            var sourceFile = getModalSourceFile(kind);
            if (!sourceFile) {
                showError('Choose a DOCX file or load one into the editor first.');
                return;
            }

            var resultContainer = kind === 'utility' ? utilitiesResult : repairResult;
            var endpoint = kind === 'utility' ? '/al/docx-labeler/api/utilities' : '/al/docx-labeler/api/repair';
            resultContainer.innerHTML = '<div class="text-muted">Running...</div>';
            showLoading(progressMessage);
            try {
                var formData = new FormData();
                formData.append('file', sourceFile);
                formData.append('action', action);
                var response = await fetch(endpoint, { method: 'POST', body: formData });
                var payload = await parseApiResponse(response);
                var data = payload.data || {};
                var html = formatReportAsHtml(data.report);
                if (data.docx_base64) {
                    html += '<div class="mt-3"><button class="btn btn-primary btn-sm" id="' + kind + '-download-result">Download Result</button></div>';
                }
                resultContainer.innerHTML = html;
                if (data.docx_base64) {
                    document.getElementById(kind + '-download-result').addEventListener('click', function() {
                        downloadBase64Docx(data.docx_base64, data.filename);
                    });
                }
            } catch (error) {
                resultContainer.innerHTML = '<div class="alert alert-danger small mb-0">' + escapeHtml(error.message || 'Operation failed.') + '</div>';
            } finally {
                hideLoading();
            }
        }

        function sleep(milliseconds) {
            return new Promise(function(resolve) {
                window.setTimeout(resolve, milliseconds);
            });
        }

        async function pollLabelerJob(jobUrl, progressMessage) {
            var startedAt = Date.now();
            var timeoutMs = 15 * 60 * 1000;
            while ((Date.now() - startedAt) < timeoutMs) {
                await sleep(1500);
                var response = await fetch(jobUrl, {
                    method: 'GET',
                    headers: { 'Accept': 'application/json' }
                });
                var payload = await parseApiResponse(response);
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
            var response = await fetch(endpointPath, {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: formData
            });
            var payload = await parseApiResponse(response);
            if (payload.status === 'queued' && payload.job_url) {
                showLoading(progressMessage);
                return pollLabelerJob(payload.job_url, progressMessage);
            }
            return (payload && payload.data) ? payload.data : {};
        }

        async function fetchPlaygroundProjects() {
            if (!state.auth.isAuthenticated) {
                state.playground.projects = [];
                state.playground.files = [];
                state.playground.selectedFile = '';
                state.playground.variables = [];
                state.playground.topLevelNames = [];
                renderInterviewPicker();
                return;
            }
            try {
                var data = await fetchJsonOrThrow(endpointPath('playgroundProjects', '/al/labeler/api/playground-projects'));
                state.playground.projects =
                    data && data.success && data.data && Array.isArray(data.data.projects)
                        ? data.data.projects
                        : [];
                if (state.playground.projects.indexOf(state.playground.selectedProject) === -1) {
                    state.playground.selectedProject = state.playground.projects[0] || 'default';
                }
                await fetchPlaygroundFiles();
                return;
            } catch (_error) {}
            state.playground.projects = [];
            state.playground.files = [];
            state.playground.selectedFile = '';
            state.playground.variables = [];
            state.playground.topLevelNames = [];
            renderInterviewPicker();
        }

        async function fetchPlaygroundFiles() {
            if (!state.auth.isAuthenticated || !state.playground.selectedProject) {
                state.playground.files = [];
                state.playground.selectedFile = '';
                state.playground.variables = [];
                state.playground.topLevelNames = [];
                renderInterviewPicker();
                return;
            }
            try {
                var data = await fetchJsonOrThrow(
                    endpointPath('playgroundFiles', '/al/labeler/api/playground-files') + '?project='
                    + encodeURIComponent(state.playground.selectedProject)
                );
                var files =
                    data && data.success && data.data && Array.isArray(data.data.files)
                        ? data.data.files
                        : [];
                state.playground.files = files;
                var currentStillExists = files.some(function(item) { return item.filename === state.playground.selectedFile; });
                state.playground.selectedFile = currentStillExists ? state.playground.selectedFile : (files[0] ? files[0].filename : '');
                renderInterviewPicker();
                await fetchPlaygroundVariables();
                return;
            } catch (_error) {}
            state.playground.files = [];
            state.playground.selectedFile = '';
            state.playground.variables = [];
            state.playground.topLevelNames = [];
            renderInterviewPicker();
        }

        async function fetchPlaygroundVariables() {
            if (!state.auth.isAuthenticated || !state.playground.selectedProject || !state.playground.selectedFile) {
                state.playground.variables = [];
                state.playground.topLevelNames = [];
                renderInterviewPicker();
                return;
            }
            try {
                var data = await fetchJsonOrThrow(
                    endpointPath('playgroundVariables', '/al/labeler/api/playground-variables') + '?project='
                    + encodeURIComponent(state.playground.selectedProject)
                    + '&filename=' + encodeURIComponent(state.playground.selectedFile)
                );
                state.playground.variables =
                    data && data.success && data.data && Array.isArray(data.data.all_names)
                        ? data.data.all_names
                        : [];
                state.playground.topLevelNames =
                    data && data.success && data.data && Array.isArray(data.data.top_level_names)
                        ? data.data.top_level_names
                        : deriveTopLevelNames(state.playground.variables);
            } catch (_error) {
                state.playground.variables = [];
                state.playground.topLevelNames = [];
            }
            renderInterviewPicker();
        }

        async function fetchInstalledPackages() {
            if (!state.auth.isAuthenticated) {
                state.installed.packages = [];
                state.installed.files = [];
                state.installed.selectedPackage = '';
                state.installed.selectedFile = '';
                state.installed.variables = [];
                state.installed.topLevelNames = [];
                renderInterviewPicker();
                return;
            }
            try {
                var data = await fetchJsonOrThrow(endpointPath('installedPackages', '/al/labeler/api/installed-packages'));
                state.installed.packages =
                    data && data.success && data.data && Array.isArray(data.data.packages)
                        ? data.data.packages
                        : [];
                if (state.installed.packages.indexOf(state.installed.selectedPackage) === -1) {
                    state.installed.selectedPackage = state.installed.packages[0] || '';
                }
                await fetchInstalledFiles();
                return;
            } catch (_error) {}
            state.installed.packages = [];
            state.installed.files = [];
            state.installed.selectedPackage = '';
            state.installed.selectedFile = '';
            state.installed.variables = [];
            state.installed.topLevelNames = [];
            renderInterviewPicker();
        }

        async function fetchInstalledFiles() {
            if (!state.auth.isAuthenticated || !state.installed.selectedPackage) {
                state.installed.files = [];
                state.installed.selectedFile = '';
                state.installed.variables = [];
                state.installed.topLevelNames = [];
                renderInterviewPicker();
                return;
            }
            try {
                var data = await fetchJsonOrThrow(
                    endpointPath('installedFiles', '/al/labeler/api/installed-files') + '?package='
                    + encodeURIComponent(state.installed.selectedPackage)
                );
                var files =
                    data && data.success && data.data && Array.isArray(data.data.files)
                        ? data.data.files
                        : [];
                state.installed.files = files;
                var currentStillExists = files.some(function(item) { return item.filename === state.installed.selectedFile; });
                state.installed.selectedFile = currentStillExists ? state.installed.selectedFile : (files[0] ? files[0].filename : '');
                renderInterviewPicker();
                await fetchInstalledVariables();
                return;
            } catch (_error) {}
            state.installed.files = [];
            state.installed.selectedFile = '';
            state.installed.variables = [];
            state.installed.topLevelNames = [];
            renderInterviewPicker();
        }

        async function fetchInstalledVariables() {
            if (!state.auth.isAuthenticated || !state.installed.selectedPackage || !state.installed.selectedFile) {
                state.installed.variables = [];
                state.installed.topLevelNames = [];
                renderInterviewPicker();
                return;
            }
            try {
                var interviewPath = state.installed.selectedPackage + ':' + state.installed.selectedFile;
                var data = await fetchJsonOrThrow(
                    endpointPath('installedVariables', '/al/labeler/api/installed-variables') + '?interview_path='
                    + encodeURIComponent(interviewPath)
                );
                state.installed.variables =
                    data && data.success && data.data && Array.isArray(data.data.all_names)
                        ? data.data.all_names
                        : [];
                state.installed.topLevelNames =
                    data && data.success && data.data && Array.isArray(data.data.top_level_names)
                        ? data.data.top_level_names
                        : deriveTopLevelNames(state.installed.variables);
            } catch (_error) {
                state.installed.variables = [];
                state.installed.topLevelNames = [];
            }
            renderInterviewPicker();
        }

        async function fetchAuthStatus() {
            try {
                var nextTarget = window.location.pathname + window.location.search;
                var response = await fetch(endpointPath('authStatus', '/al/labeler/api/auth-status') + '?next=' + encodeURIComponent(nextTarget), { method: 'GET' });
                var data = await response.json();
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
            if (!shouldHideInterviewPicker()) {
                await fetchPlaygroundProjects();
                await fetchInstalledPackages();
            }
        }

        function renderModelSuggestions(filterText) {
            var filter = (filterText || '').trim().toLowerCase();
            var modelChoices = [];
            if (filter) {
                var source = state.availableModels.length ? state.availableModels : state.recommendedModels;
                modelChoices = source.filter(function(model) {
                    return model.toLowerCase().includes(filter);
                }).slice(0, 20);
            } else {
                modelChoices = state.recommendedModels.slice(0, 8);
            }
            aiModelSuggestions.innerHTML = '';
            modelChoices.forEach(function(modelName) {
                var button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-sm btn-outline-secondary font-monospace';
                button.textContent = modelName;
                button.addEventListener('click', function() {
                    aiModelInput.value = modelName;
                    state.settings.model = modelName;
                    renderModelSuggestions(modelName);
                });
                aiModelSuggestions.appendChild(button);
            });
        }

        function renderGenerationMethodFields() {
            var method = generationMethodInput.value || state.settings.generationMethod || 'multi_run';
            generatorModelsGroup.classList.toggle('hidden', method !== 'multi_model');
        }

        function parseGeneratorModelsInput(rawValue) {
            return (rawValue || '')
                .split(/[\n,]/)
                .map(function(item) { return item.trim(); })
                .filter(function(item) { return item.length > 0; });
        }

        // ================================================================
        // UI state transitions
        // ================================================================
        function showLoading(message) {
            loadingMessage.textContent = message;
            loadingState.classList.remove('hidden');
            emptyState.classList.add('hidden');
            mainPanel.classList.add('hidden');
            errorState.classList.add('hidden');
        }

        function hideLoading() {
            loadingState.classList.add('hidden');
        }

        function showError(message) {
            errorMessage.textContent = message;
            errorState.classList.remove('hidden');
            emptyState.classList.add('hidden');
            mainPanel.classList.add('hidden');
        }

        function showMainPanel() {
            mainPanel.classList.remove('hidden');
            emptyState.classList.add('hidden');
            errorState.classList.add('hidden');
        }

        // ================================================================
        // Label extraction from mammoth HTML
        // ================================================================
        function extractExistingLabels(html) {
            const labels = [];
            // Combined scan in document order: {{ }} and {% %}
            const allPattern = /\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/g;
            let match;
            while ((match = allPattern.exec(html)) !== null) {
                const text = match[0];
                labels.push({
                    id: generateId(),
                    original: text,
                    current: text,
                    isControl: text.startsWith('{%')
                });
            }
            return labels;
        }

        function extractExistingLabelsFromRuns(runs) {
            var labels = [];
            var pattern = /\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/g;
            (runs || []).forEach(function(run) {
                var paragraph = run[0];
                var runIndex = run[1];
                var text = String(run[2] || '');
                pattern.lastIndex = 0;
                var match;
                while ((match = pattern.exec(text)) !== null) {
                    var labelText = match[0];
                    labels.push({
                        id: generateId(),
                        original: labelText,
                        current: labelText,
                        isControl: labelText.startsWith('{%'),
                        paragraph: paragraph,
                        run: runIndex,
                        start: match.index,
                        end: match.index + labelText.length
                    });
                }
            });
            return labels;
        }

        // ================================================================
        // Paragraph / run index for mapping selections to runs
        // ================================================================
        function buildParagraphIndex() {
            var index = {};
            state.runs.forEach(function(r) {
                var para = r[0], run = r[1], text = r[2];
                if (!index[para]) index[para] = { runs: [], fullText: '' };
                index[para].runs.push({ runIdx: run, text: text, start: index[para].fullText.length, end: 0 });
                index[para].fullText += text;
            });
            Object.keys(index).forEach(function(p) {
                index[p].runs.forEach(function(r) { r.end = r.start + r.text.length; });
            });
            return index;
        }

        function buildGlobalRunIndex() {
            var sortedRuns = state.runs.slice().sort(function(a, b) {
                if (a[0] !== b[0]) return a[0] - b[0];
                return a[1] - b[1];
            });
            var entries = [];
            var globalOffset = 0;
            var lastParagraph = null;
            sortedRuns.forEach(function(r) {
                var paragraph = r[0];
                var run = r[1];
                var text = String(r[2] || '');
                if (lastParagraph !== null && paragraph !== lastParagraph) {
                    globalOffset += 1;
                }
                entries.push({
                    paragraph: paragraph,
                    run: run,
                    text: text,
                    start: globalOffset,
                    end: globalOffset + text.length,
                });
                globalOffset += text.length;
                lastParagraph = paragraph;
            });
            return entries;
        }

        /**
         * Given a user-selected text string, find the paragraph and set of
         * consecutive runs whose concatenated text contains the selection.
         * Returns null when no match is found.
         */
        function findRunsForSelection(selectedText) {
            if (!selectedText || !selectedText.trim()) return null;
            var paraIndex = buildParagraphIndex();
            var trimmed = selectedText.trim();

            for (var paraNum in paraIndex) {
                var para = paraIndex[paraNum];
                // Direct substring lookup in this paragraph’s concatenated text
                var pos = para.fullText.indexOf(trimmed);
                if (pos === -1) {
                    // Normalised whitespace fallback
                    var normFull = para.fullText.replace(/\s+/g, ' ');
                    var normSel  = trimmed.replace(/\s+/g, ' ');
                    pos = normFull.indexOf(normSel);
                    if (pos === -1) continue;
                }
                var end = pos + trimmed.length;

                var matchedRuns = [];
                para.runs.forEach(function(r) {
                    if (r.end > pos && r.start < end) {
                        matchedRuns.push({
                            paragraph: parseInt(paraNum),
                            run: r.runIdx,
                            originalText: r.text,
                            selStart: Math.max(0, pos - r.start),
                            selEnd:   Math.min(r.text.length, end - r.start),
                            isFullySelected: pos <= r.start && end >= r.end
                        });
                    }
                });
                if (matchedRuns.length > 0) {
                    return { paraNum: parseInt(paraNum), runs: matchedRuns, selectedText: trimmed };
                }
            }
            return null;
        }

        /**
         * Build suggestion entries that replace the matched runs with a Jinja2
         * variable.  The first run keeps any unselected prefix and gets the
         * label; the last run keeps any unselected suffix; middle runs are
         * blanked.  All entries share a `group` id so the UI and preview can
         * treat them as one logical operation.
         */
        function createLabelFromSelection(variableName, match) {
            var groupId = generateId();
            var labels = [];
            match.runs.forEach(function(r, i) {
                var newText;
                var manualRole;
                var manualPrefix = '';
                var manualSuffix = '';
                if (match.runs.length === 1) {
                    // Only run: prefix + label + suffix
                    manualPrefix = r.originalText.substring(0, r.selStart);
                    manualSuffix = r.originalText.substring(r.selEnd);
                    newText = manualPrefix + variableName + manualSuffix;
                    manualRole = 'single';
                } else if (i === 0) {
                    manualPrefix = r.originalText.substring(0, r.selStart);
                    newText = manualPrefix + variableName;
                    manualRole = 'first';
                } else if (i === match.runs.length - 1) {
                    manualSuffix = r.originalText.substring(r.selEnd);
                    newText = manualSuffix;
                    manualRole = 'last';
                } else {
                    newText = '';
                    manualRole = 'middle';
                }
                labels.push({
                    paragraph: r.paragraph,
                    run: r.run,
                    text: newText,
                    new_paragraph: 0,
                    id: generateId(),
                    status: 'accepted',
                    group: groupId,
                    _isCompanion: i > 0,
                    _isManual: true,
                    _selectedText: match.selectedText,
                    _displayLabel: variableName,
                    _manualKind: 'replace',
                    _manualRole: manualRole,
                    _manualPrefix: manualPrefix,
                    _manualSuffix: manualSuffix
                });
            });
            return labels;
        }

        function createWrappedLabelFromSelection(conditionExpression, match, wrapperKind) {
            var groupId = generateId();
            var labels = [];
            var openTag = wrapperKind === 'ifp_wrap'
                ? '{%p if ' + conditionExpression + ' %}'
                : '{% if ' + conditionExpression + ' %}';
            var closeTag = wrapperKind === 'ifp_wrap'
                ? '{%p endif %}'
                : '{% endif %}';
            match.runs.forEach(function(r, i) {
                var selectedFragment = r.originalText.substring(r.selStart, r.selEnd);
                var manualPrefix = '';
                var manualSuffix = '';
                var manualRole = 'middle';
                var newText = selectedFragment;
                if (match.runs.length === 1) {
                    manualRole = 'single';
                    manualPrefix = r.originalText.substring(0, r.selStart);
                    manualSuffix = r.originalText.substring(r.selEnd);
                    newText = manualPrefix + openTag + selectedFragment + closeTag + manualSuffix;
                } else if (i === 0) {
                    manualRole = 'first';
                    manualPrefix = r.originalText.substring(0, r.selStart);
                    newText = manualPrefix + openTag + selectedFragment;
                } else if (i === match.runs.length - 1) {
                    manualRole = 'last';
                    manualSuffix = r.originalText.substring(r.selEnd);
                    newText = selectedFragment + closeTag + manualSuffix;
                }
                labels.push({
                    paragraph: r.paragraph,
                    run: r.run,
                    text: newText,
                    new_paragraph: 0,
                    id: generateId(),
                    status: 'accepted',
                    group: groupId,
                    _isCompanion: i > 0,
                    _isManual: true,
                    _selectedText: match.selectedText,
                    _displayLabel: conditionExpression,
                    _manualKind: wrapperKind,
                    _manualRole: manualRole,
                    _manualPrefix: manualPrefix,
                    _manualSuffix: manualSuffix,
                    _selectedFragment: selectedFragment
                });
            });
            return labels;
        }

        // ================================================================
        // Selection popover logic
        // ================================================================
        var _selMatch = null;   // stashed findRunsForSelection() result
        var _selectionMode = 'replace';

        function ensureSelectionModeControls() {
            if (document.getElementById('sel-mode-group')) return;
            var controls = document.createElement('div');
            controls.className = 'mb-2';
            controls.innerHTML =
                '<div id="sel-mode-group" class="btn-group btn-group-sm w-100" role="group" aria-label="Selection mode">' +
                    '<button type="button" class="btn btn-outline-secondary sel-mode-btn active" data-mode="replace">Replace</button>' +
                    '<button type="button" class="btn btn-outline-secondary sel-mode-btn" data-mode="insert">Insert</button>' +
                    '<button type="button" class="btn btn-outline-secondary sel-mode-btn" data-mode="if_wrap">{% if %}</button>' +
                    '<button type="button" class="btn btn-outline-secondary sel-mode-btn" data-mode="ifp_wrap">{%p if %}</button>' +
                '</div>';
            var body = selPopover.querySelector('.dl-sel-popover-body');
            if (body) {
                body.insertBefore(controls, body.firstChild);
                var filterBtn = document.createElement('button');
                filterBtn.type = 'button';
                filterBtn.id = 'sel-catchall-btn';
                filterBtn.className = 'btn btn-sm btn-outline-primary mt-2 w-100';
                filterBtn.textContent = 'Catchall / Request Filters';
                filterBtn.addEventListener('click', function() {
                    openCatchallDialog(selVarInput);
                });
                body.appendChild(filterBtn);
            }
            controls.querySelectorAll('.sel-mode-btn').forEach(function(button) {
                button.addEventListener('click', function() {
                    _selectionMode = button.dataset.mode || 'replace';
                    updateSelectionModeButtons();
                    updateSelectionModeInputHints();
                });
            });
            updateSelectionModeButtons();
            updateSelectionModeInputHints();
        }

        function updateSelectionModeButtons() {
            document.querySelectorAll('.sel-mode-btn').forEach(function(button) {
                var isActive = button.dataset.mode === _selectionMode;
                button.classList.toggle('active', isActive);
                button.classList.toggle('btn-primary', isActive);
                button.classList.toggle('btn-outline-secondary', !isActive);
            });
        }

        function updateSelectionModeInputHints() {
            if (_selectionMode === 'replace' || _selectionMode === 'insert') {
                selVarInput.placeholder = '{{ users[0].name.first }}';
            } else {
                selVarInput.placeholder = 'users[0].is_active';
            }
            if (_selMatch && _selMatch.isInsertion) {
                selOriginalText.textContent = '(insert at cursor)';
            }
        }

        function normalizeSelectionInput(raw) {
            if (_selectionMode === 'replace' || _selectionMode === 'insert') {
                var varName = raw;
                if (!/^\{[{%]/.test(varName)) {
                    varName = '{{ ' + varName + ' }}';
                }
                return varName;
            }
            return raw
                .replace(/^\{%\s*p?\s*if\s*/i, '')
                .replace(/\s*%\}$/, '')
                .replace(/^\{\{\s*/, '')
                .replace(/\s*\}\}$/, '')
                .trim();
        }

        function findRunMatchForCaret(caretRange) {
            if (!caretRange) return null;
            var prefixRange = document.createRange();
            prefixRange.selectNodeContents(previewContent);
            prefixRange.setEnd(caretRange.startContainer, caretRange.startOffset);
            var globalOffset = prefixRange.toString().length;
            var runIndex = buildGlobalRunIndex();
            if (!runIndex.length) return null;
            var target = runIndex.find(function(entry) {
                return globalOffset >= entry.start && globalOffset <= entry.end;
            });
            if (!target) {
                target = runIndex[runIndex.length - 1];
            }
            var offsetInRun = Math.max(0, Math.min(target.text.length, globalOffset - target.start));
            return {
                paraNum: target.paragraph,
                runs: [{
                    paragraph: target.paragraph,
                    run: target.run,
                    originalText: target.text,
                    selStart: offsetInRun,
                    selEnd: offsetInRun,
                    isFullySelected: false
                }],
                selectedText: '',
                isInsertion: true
            };
        }

        function showSelectionPopover(match, mouseX, mouseY) {
            ensureSelectionModeControls();
            _selMatch = match;
            selOriginalText.textContent = match.isInsertion ? '(insert at cursor)' : match.selectedText;
            selVarInput.value = '';
            selVarPanel.classList.add('hidden');
            selVarSearch.value = '';
            if (match.isInsertion) {
                _selectionMode = 'insert';
            } else if (_selectionMode === 'insert') {
                _selectionMode = 'replace';
            }
            updateSelectionModeButtons();
            updateSelectionModeInputHints();

            // Position near the cursor, clamped to viewport
            var pw = 380, ph = 260;
            var vw = window.innerWidth, vh = window.innerHeight;
            var left = Math.min(mouseX + 4, vw - pw - 12);
            var top  = mouseY + 12;
            if (top + ph > vh) top = Math.max(8, mouseY - ph - 8);
            selPopover.style.left = Math.max(4, left) + 'px';
            selPopover.style.top  = top + 'px';
            selPopover.classList.remove('hidden');
            selVarInput.focus();
        }

        function hideSelectionPopover() {
            selPopover.classList.add('hidden');
            _selMatch = null;
        }

        function saveSelectionLabel() {
            var raw = selVarInput.value.trim();
            if (!raw || !_selMatch) return;
            if ((_selectionMode === 'if_wrap' || _selectionMode === 'ifp_wrap') && _selMatch.isInsertion) {
                showError('Select text first when adding conditional wrappers.');
                return;
            }
            var value = normalizeSelectionInput(raw);
            var newLabels;
            if (_selectionMode === 'if_wrap' || _selectionMode === 'ifp_wrap') {
                newLabels = createWrappedLabelFromSelection(value, _selMatch, _selectionMode);
            } else {
                newLabels = createLabelFromSelection(value, _selMatch);
            }
            state.suggestions = state.suggestions.concat(newLabels);
            hideSelectionPopover();
            renderSuggestions();
            updatePreview();
            updateDownloadButton();
            switchTab('manual');
        }

        function onPreviewMouseUp(e) {
            if (previewUtils && previewUtils.shouldSuppressSelectionPopoverFromTarget) {
                if (previewUtils.shouldSuppressSelectionPopoverFromTarget(e.target)) return;
            } else if (e.target && e.target.closest && e.target.closest('.existing-inline-label')) {
                return;
            }
            // Ignore if popover is already open or no document loaded
            if (!selPopover.classList.contains('hidden')) return;
            if (!state.file || state.runs.length === 0) return;

            var sel = window.getSelection();
            if (!sel) return;

            // Make sure the selection is inside the preview area
            var anchor = sel.anchorNode, focus = sel.focusNode;
            if (!previewContent.contains(anchor) || !previewContent.contains(focus)) return;

            var match = null;
            if (sel.isCollapsed) {
                if (document.caretRangeFromPoint) {
                    match = findRunMatchForCaret(document.caretRangeFromPoint(e.clientX, e.clientY));
                } else if (document.caretPositionFromPoint) {
                    var caretPos = document.caretPositionFromPoint(e.clientX, e.clientY);
                    if (caretPos) {
                        var fallbackRange = document.createRange();
                        fallbackRange.setStart(caretPos.offsetNode, caretPos.offset);
                        fallbackRange.setEnd(caretPos.offsetNode, caretPos.offset);
                        match = findRunMatchForCaret(fallbackRange);
                    }
                }
                if (!match) return;
            } else {
                var text = sel.toString();
                if (!text || !text.trim()) return;
                match = findRunsForSelection(text);
                if (!match) return;
            }

            showSelectionPopover(match, e.clientX, e.clientY);
        }

        // Render the variable tree inside the popover (reuses the same builder)
        function renderSelPopoverTree(filter) {
            selVarTree.innerHTML = '';
            renderVariableTree(getEffectiveVariableTree(), '', selVarTree, filter || '', function(varPath) {
                selVarInput.value = '{{ ' + varPath + ' }}';
                selVarInput.focus();
            });
        }

        // ================================================================
        // Preview rendering with highlights
        // ================================================================
        function updatePreview() {
            let html = state.originalHtml;

            // --- Grouped selection labels (multi-run) ---
            // Replace the full selected text span with the display label for
            // each unique group, before handling single-run suggestions.
            var processedGroups = {};
            state.suggestions.forEach(function(s) {
                if (s.status === 'accepted' && s.group && !processedGroups[s.group]) {
                    processedGroups[s.group] = true;
                    var selectedText = s._selectedText;
                    if (s._manualKind === 'if_wrap' || s._manualKind === 'ifp_wrap') {
                        // Show the fully wrapped form in the preview
                        var wrapDisplay = previewUtils && previewUtils.formatManualWrapPreviewDisplay
                            ? previewUtils.formatManualWrapPreviewDisplay(
                                s._manualKind,
                                s._displayLabel || '',
                                selectedText || '',
                                escapeHtml
                            )
                            : escapeHtml(
                                (s._manualKind === 'ifp_wrap' ? '{%p if ' : '{% if ')
                                + (s._displayLabel || '')
                                + ' %}'
                                + (selectedText || '')
                                + (s._manualKind === 'ifp_wrap' ? '{%p endif %}' : '{% endif %}')
                            );
                        if (selectedText) {
                            var wrapEncoded = escapeHtml(selectedText);
                            var wrapPos = html.indexOf(wrapEncoded);
                            if (wrapPos !== -1) {
                                html = html.substring(0, wrapPos)
                                    + '<span class="highlight-accepted">' + wrapDisplay + '</span>'
                                    + html.substring(wrapPos + wrapEncoded.length);
                            }
                        }
                    } else if (!selectedText && s._isManual) {
                        // Insert mode: no selected text — locate the run and replace its full text
                        var insertRun = state.runs.find(function(r) {
                            return r[0] === s.paragraph && r[1] === s.run;
                        });
                        if (insertRun && insertRun[2]) {
                            var insertOrig = escapeHtml(String(insertRun[2]));
                            var insertPos = html.indexOf(insertOrig);
                            if (insertPos !== -1) {
                                html = html.substring(0, insertPos)
                                    + '<span class="highlight-accepted">' + escapeHtml(s.text) + '</span>'
                                    + html.substring(insertPos + insertOrig.length);
                            }
                        }
                    } else {
                        var displayLabel = s._displayLabel || s.text;
                        if (selectedText) {
                            var encoded = escapeHtml(selectedText);
                            var pos = html.indexOf(encoded);
                            if (pos !== -1) {
                                html = html.substring(0, pos)
                                    + '<span class="highlight-accepted">' + escapeHtml(displayLabel) + '</span>'
                                    + html.substring(pos + encoded.length);
                            }
                        }
                    }
                }
            });

            // --- Single-run AI suggestions (no group) ---
            const acceptedByKey = {};
            state.suggestions.forEach(function(s) {
                if (s.status === 'accepted' && s.new_paragraph === 0 && !s.group) {
                    acceptedByKey[s.paragraph + ',' + s.run] = s;
                }
            });

            const replacements = [];
            for (const key of Object.keys(acceptedByKey)) {
                const s = acceptedByKey[key];
                const run = state.runs.find(function(r) {
                    return r[0] === s.paragraph && r[1] === s.run;
                });
                if (run && run[2] && run[2].trim()) {
                    replacements.push({ original: run[2], replacement: s.text });
                }
            }
            replacements.sort(function(a, b) { return b.original.length - a.original.length; });

            for (const r of replacements) {
                const encoded = escapeHtml(r.original);
                const pos = html.indexOf(encoded);
                if (pos !== -1) {
                    html = html.substring(0, pos)
                        + '<span class="highlight-accepted">' + escapeHtml(r.replacement) + '</span>'
                        + html.substring(pos + encoded.length);
                }
            }

            // Highlight existing labels (with any renames applied) — per-occurrence
            if (previewUtils && previewUtils.applyExistingLabelHighlightsByOccurrence) {
                html = previewUtils.applyExistingLabelHighlightsByOccurrence(html, state.existingLabels, escapeHtml);
            } else {
                var existingByOriginal = {};
                state.existingLabels.forEach(function(label) {
                    if (!existingByOriginal[label.original]) existingByOriginal[label.original] = [];
                    existingByOriginal[label.original].push(label);
                });
                Object.keys(existingByOriginal).forEach(function(original) {
                    var entries = existingByOriginal[original];
                    var encoded = escapeHtml(original);
                    var offset = 0;
                    entries.forEach(function(label) {
                        var cls = label.current !== label.original ? 'highlight-accepted' : 'highlight-existing';
                        var span = '<span class="' + cls + ' existing-inline-label" data-label-id="' + escapeHtml(label.id) + '">' + escapeHtml(label.current) + '</span>';
                        var pos = html.indexOf(encoded, offset);
                        if (pos === -1) return;
                        html = html.substring(0, pos) + span + html.substring(pos + encoded.length);
                        offset = pos + span.length;
                    });
                });
            }

            previewContent.innerHTML = html;
            previewContent.querySelectorAll('.existing-inline-label').forEach(function(node) {
                node.addEventListener('click', function(evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    var labelId = node.getAttribute('data-label-id');
                    var label = state.existingLabels.find(function(item) { return item.id === labelId; });
                    if (label) openEditLabelModal(label);
                });
            });
        }

        // ================================================================
        // Existing-labels tree
        // ================================================================
        function renderExistingLabelsTree() {
            existingLabelsTree.innerHTML = '';
            // Build unique-entry list and occurrence counts from per-occurrence data
            var seenOriginals = {};
            var uniqueEntries = [];
            var occurrenceCounts = {};
            state.existingLabels.forEach(function(label) {
                occurrenceCounts[label.original] = (occurrenceCounts[label.original] || 0) + 1;
                if (!seenOriginals[label.original]) {
                    seenOriginals[label.original] = true;
                    uniqueEntries.push(label);
                }
            });
            existingCount.textContent = uniqueEntries.length;
            if (uniqueEntries.length === 0) {
                existingLabelsTree.innerHTML = '<div class="text-center py-5 text-muted"><p>No existing labels found.</p><p class="small mt-1">Use the AI Suggestions tab to add labels.</p></div>';
                return;
            }
            const groups = {};
            uniqueEntries.forEach(function(label) {
                const inner = label.current.replace(/^\{\{\s*|\s*\}\}$/g, '').replace(/^\{%\s*|\s*%\}$/g, '').trim();
                const base = inner.split('.')[0].split('[')[0];
                if (!groups[base]) groups[base] = [];
                groups[base].push(label);
            });
            Object.keys(groups).sort().forEach(function(groupName) {
                const groupDiv = document.createElement('div');
                groupDiv.className = 'mb-3';
                const header = document.createElement('div');
                header.className = 'tree-item d-flex align-items-center gap-2 px-2 py-1 rounded fw-medium';
                header.innerHTML = '<span class="tree-toggle">\u25BC</span><span>' + escapeHtml(groupName) + '</span><span class="badge bg-secondary">' + groups[groupName].length + '</span>';
                const children = document.createElement('div');
                children.className = 'tree-children mt-1';
                groups[groupName].forEach(function(label) {
                    var count = occurrenceCounts[label.original] || 1;
                    const item = document.createElement('div');
                    item.className = 'existing-label p-2 rounded mb-1 cursor-pointer';
                    item.innerHTML = '<div class="font-monospace small break-all' + (label.original !== label.current ? ' text-primary' : '') + '">' + escapeHtml(label.current) + '</div>' + (label.original !== label.current ? '<div class="small text-muted mt-1 text-decoration-line-through">' + escapeHtml(label.original) + '</div>' : '') + '<div class="small text-muted mt-1">' + count + ' occurrence' + (count > 1 ? 's' : '') + '</div>';
                    item.addEventListener('click', function() { openEditLabelModal(label); });
                    children.appendChild(item);
                });
                header.addEventListener('click', function() {
                    var toggle = header.querySelector('.tree-toggle');
                    if (children.style.display === 'none') { children.style.display = 'block'; toggle.textContent = '\u25BC'; }
                    else { children.style.display = 'none'; toggle.textContent = '\u25B6'; }
                });
                groupDiv.appendChild(header);
                groupDiv.appendChild(children);
                existingLabelsTree.appendChild(groupDiv);
            });
            updateDownloadButton();
        }

        // ================================================================
        // Variable-picker tree
        // ================================================================
        function renderVariableTree(tree, prefix, container, filter, onSelectVariable) {
            tree = tree || getEffectiveVariableTree();
            prefix = prefix || '';
            container = container || variableTree;
            filter = filter || '';
            onSelectVariable = onSelectVariable || insertVariable;
            if (container === variableTree) container.innerHTML = '';
            Object.keys(tree).forEach(function(key) {
                if (key.startsWith('_')) return;
                var value = tree[key];
                var isSelectedInterviewGroup = !prefix && key === 'Selected interview variables';
                var fullPath = prefix ? prefix + '.' + key : key;
                var displayPath = fullPath.replace(/\.\[/g, '[');
                var singletonListChild = getSingletonListChild(value);
                var renderTarget = singletonListChild || value;
                var childKeys = renderTarget && typeof renderTarget === 'object'
                    ? Object.keys(renderTarget).filter(function(childKey) { return !childKey.startsWith('_'); })
                    : [];
                var hasChildren = childKeys.length > 0;
                var directSelectPath = singletonListChild ? displayPath + '[0]' : null;
                var labelPath = directSelectPath || displayPath;
                if (filter && !labelPath.toLowerCase().includes(filter.toLowerCase())) {
                    if (typeof renderTarget !== 'object') return;
                    var hasMatch = JSON.stringify(renderTarget).toLowerCase().includes(filter.toLowerCase());
                    if (!hasMatch) return;
                }
                var item = document.createElement('div');
                item.className = 'mb-1';
                var header = document.createElement('div');
                header.className = 'tree-item d-flex align-items-center gap-1 px-2 py-1 rounded small';
                var leafValue = renderTarget;
                var leafDescription = typeof leafValue === 'string' ? leafValue : (leafValue && leafValue._description ? leafValue._description : '');
                var isSelectableLeaf = typeof value === 'string' || (typeof value === 'object' && !!value._variable) || (directSelectPath && !hasChildren);
                if (isSelectableLeaf) {
                    header.innerHTML = '<span class="tree-toggle text-muted">\u00B7</span><span class="font-monospace text-primary cursor-pointer" data-var="' + labelPath + '">' + labelPath + '</span><span class="text-muted small ms-1 text-truncate">' + leafDescription + '</span>';
                    header.querySelector('[data-var]').addEventListener('click', function(e) { e.stopPropagation(); onSelectVariable(labelPath); });
                } else if (directSelectPath) {
                    header.innerHTML = '<span class="tree-toggle cursor-pointer">\u25B6</span><span class="font-monospace text-primary cursor-pointer" data-var="' + directSelectPath + '">' + directSelectPath + '</span>' + (leafDescription ? '<span class="text-muted small ms-1">' + leafDescription + '</span>' : '');
                    header.querySelector('[data-var]').addEventListener('click', function(e) { e.stopPropagation(); onSelectVariable(directSelectPath); });
                } else {
                    header.innerHTML = '<span class="tree-toggle cursor-pointer">\u25B6</span><span class="fw-medium">' + key + '</span>' + (leafDescription ? '<span class="text-muted small ms-1">' + leafDescription + '</span>' : '');
                }
                item.appendChild(header);
                if (hasChildren) {
                    var children = document.createElement('div');
                    children.className = 'tree-children hidden';
                    renderVariableTree(renderTarget, isSelectedInterviewGroup ? prefix : (directSelectPath || fullPath), children, filter, onSelectVariable);
                    item.appendChild(children);
                    header.addEventListener('click', function(e) {
                        if (e.target.dataset && e.target.dataset.var) return;
                        var toggle = header.querySelector('.tree-toggle');
                        if (children.classList.contains('hidden')) { children.classList.remove('hidden'); toggle.textContent = '\u25BC'; }
                        else { children.classList.add('hidden'); toggle.textContent = '\u25B6'; }
                    });
                }
                container.appendChild(item);
            });
        }

        function insertVariable(varPath) {
            var input = document.getElementById('edit-label-input');
            if (input) { input.value = '{{ ' + varPath + ' }}'; input.focus(); }
        }

        // ================================================================
        // File processing
        // ================================================================
        async function processFile(file) {
            if (!file) return;
            state.file = file;
            savePlaygroundBtn.classList.remove('hidden');
            updateModalSourceStatus();
            fileName.textContent = file.name;
            fileName.classList.remove('hidden');
            showLoading('Reading document...');
            try {
                var arrayBuffer = await file.arrayBuffer();
                state.fileContent = new Uint8Array(arrayBuffer);

                // Step 1: mammoth HTML preview
                showLoading('Converting document to HTML...');
                var result = await mammoth.convertToHtml({ arrayBuffer: arrayBuffer });
                state.originalHtml = result.value;
                previewContent.innerHTML = state.originalHtml;
                previewContent.classList.remove('hidden');
                previewEmpty.classList.add('hidden');

                // Step 2: extract runs via API (for preview mapping)
                showLoading('Extracting document structure...');
                var runsFormData = new FormData();
                runsFormData.append('file', file);
                runsFormData.append('defragment_runs', state.settings.defragmentRuns ? 'true' : 'false');
                try {
                    var runsResp = await fetch('/al/docx-labeler/api/extract-runs', { method: 'POST', body: runsFormData });
                    var runsData = await runsResp.json();
                    if (runsData.success) {
                        state.runs = runsData.data.runs;
                    }
                } catch (runErr) {
                    console.warn('Could not extract runs for preview mapping:', runErr);
                }

                state.existingLabels = extractExistingLabelsFromRuns(state.runs);
                if (state.existingLabels.length === 0) {
                    state.existingLabels = extractExistingLabels(state.originalHtml);
                }
                state.labelRenames = {};
                state.suggestions = [];
                state.validation = null;
                state.syntaxValidation = null;
                state.syntaxValidationPending = false;
                renderSyntaxValidation();

                hideLoading();
                showMainPanel();

                if (state.existingLabels.length > 0) {
                    switchTab('existing');
                    renderExistingLabelsTree();
                    updatePreview();
                } else {
                    switchTab('suggestions');
                    if (state.auth.aiEnabled) {
                        showLoading('Generating AI suggestions...');
                        await fetchSuggestions();
                    } else {
                        suggestionsList.innerHTML = '<div class="alert alert-warning">AI suggestions are unavailable until you log in.</div>';
                    }
                    hideLoading();
                    showMainPanel();    // <-- was missing; caused the "spin forever" bug
                }
            } catch (error) {
                console.error('Error processing file:', error);
                showError('Failed to process document: ' + error.message);
            } finally {
                hideLoading();
            }
        }

        // ================================================================
        // Tab switching
        // ================================================================
        function switchTab(tab) {
            state.activeTab = tab;
            tabExisting.classList.toggle('tab-active', tab === 'existing');
            tabManual.classList.toggle('tab-active', tab === 'manual');
            tabSuggestions.classList.toggle('tab-active', tab === 'suggestions');
            existingPanel.classList.toggle('hidden', tab !== 'existing');
            manualPanel.classList.toggle('hidden', tab !== 'manual');
            suggestionsPanel.classList.toggle('hidden', tab !== 'suggestions');
        }

        function renderManualLabels() {
            var manualLabels = getManualLabelGroups();
            var hasManualLabels = manualLabels.length > 0;
            tabManual.classList.toggle('hidden', !hasManualLabels);
            manualCount.textContent = manualLabels.length;

            if (!hasManualLabels) {
                manualLabelsList.innerHTML = '<div class="text-center py-5 text-muted"><p>No manual labels yet.</p><p class="small mt-1">Select text in the preview to add one.</p></div>';
                if (state.activeTab === 'manual') {
                    switchTab(state.existingLabels.length > 0 ? 'existing' : 'suggestions');
                }
                return;
            }

            manualLabelsList.innerHTML = '';
            manualLabels.forEach(function(label) {
                var div = document.createElement('div');
                div.className = 'p-3 rounded border border-2 mb-2 suggestion-accepted';
                div.innerHTML =
                    '<div class="d-flex align-items-start justify-content-between gap-2 mb-2">' +
                        '<span class="small text-muted">Selection</span>' +
                        '<button class="manual-remove-btn btn btn-sm btn-outline-danger" data-group="' + escapeHtml(label.group) + '" title="Remove">\u2716</button>' +
                    '</div>' +
                    '<div class="small text-muted mb-2">\u201C' + escapeHtml(label._selectedText || '') + '\u201D</div>' +
                    '<label class="form-label small fw-medium mb-1">Label</label>' +
                    '<input type="text" class="manual-edit-input form-control form-control-sm font-monospace mb-2" value="' + escapeHtml(label._displayLabel || label.text || '') + '" data-group="' + escapeHtml(label.group) + '">' +
                    '<div class="small text-muted">Para ' + escapeHtml(String(label.paragraph)) + ', Run ' + escapeHtml(String(label.run)) + '</div>';
                manualLabelsList.appendChild(div);
            });

            manualLabelsList.querySelectorAll('.manual-edit-input').forEach(function(input) {
                input.addEventListener('input', function() {
                    var value = input.value.trim();
                    if (!value) return;
                    applyManualLabelText(input.dataset.group, value);
                    updatePreview();
                    scheduleSyntaxValidation(500);
                });
            });

            manualLabelsList.querySelectorAll('.manual-remove-btn').forEach(function(button) {
                button.addEventListener('click', function() {
                    var groupId = button.dataset.group;
                    state.suggestions = state.suggestions.filter(function(suggestion) {
                        return suggestion.group !== groupId;
                    });
                    renderSuggestions();
                    updatePreview();
                    updateDownloadButton();
                    scheduleSyntaxValidation();
                });
            });
        }

        // ================================================================
        // Edit label modal
        // ================================================================
        function openEditLabelModal(label) {
            state.editingLabelId = label.id;
            document.getElementById('edit-label-original').textContent = label.original;
            document.getElementById('edit-label-input').value = label.current;
            document.getElementById('variable-search').value = '';
            renderVariableTree(getEffectiveVariableTree());
            // Show occurrence position in title when multiple exist
            var allSameOriginal = state.existingLabels.filter(function(l) { return l.original === label.original; });
            var occurrenceIndex = allSameOriginal.indexOf(label) + 1;
            var totalCount = allSameOriginal.length;
            document.getElementById('edit-label-title').textContent = totalCount > 1
                ? 'Edit Label (occurrence ' + occurrenceIndex + ' of ' + totalCount + ')'
                : 'Edit Label';
            if (!document.getElementById('edit-catchall-btn')) {
                var footer = editLabelModal.querySelector('.dl-modal-footer');
                if (footer) {
                    var catchallBtn = document.createElement('button');
                    catchallBtn.id = 'edit-catchall-btn';
                    catchallBtn.type = 'button';
                    catchallBtn.className = 'btn btn-outline-primary me-auto';
                    catchallBtn.textContent = 'Catchall / Request Filters';
                    catchallBtn.addEventListener('click', function() {
                        openCatchallDialog(document.getElementById('edit-label-input'));
                    });
                    footer.insertBefore(catchallBtn, footer.firstChild);
                }
            }
            // Add/update "Replace all" button depending on occurrence count
            var replaceAllBtn = document.getElementById('edit-replace-all-btn');
            if (totalCount > 1) {
                if (!replaceAllBtn) {
                    replaceAllBtn = document.createElement('button');
                    replaceAllBtn.id = 'edit-replace-all-btn';
                    replaceAllBtn.type = 'button';
                    replaceAllBtn.className = 'btn btn-outline-secondary';
                    replaceAllBtn.addEventListener('click', function() { saveEditLabel(true); });
                    var saveBtn = document.getElementById('edit-save');
                    var modalFooter = editLabelModal.querySelector('.dl-modal-footer');
                    if (saveBtn && modalFooter) modalFooter.insertBefore(replaceAllBtn, saveBtn);
                    else if (modalFooter) modalFooter.appendChild(replaceAllBtn);
                }
                replaceAllBtn.textContent = 'Save for all ' + totalCount + ' occurrences';
            } else if (replaceAllBtn) {
                replaceAllBtn.remove();
            }
            editLabelModal.classList.remove('hidden');
            document.getElementById('edit-label-input').focus();
        }

        function saveEditLabel(replaceAll) {
            var input = document.getElementById('edit-label-input').value.trim();
            if (!input || !state.editingLabelId) return;
            var replaceAllMode = replaceAll === true;
            var label = state.existingLabels.find(function(l) { return l.id === state.editingLabelId; });
            if (label) {
                if (replaceAllMode) {
                    var orig = label.original;
                    state.existingLabels.forEach(function(l) {
                        if (l.original === orig) l.current = input;
                    });
                } else {
                    label.current = input;
                }
            }
            editLabelModal.classList.add('hidden');
            state.editingLabelId = null;
            renderExistingLabelsTree();
            updatePreview();
            scheduleSyntaxValidation();
        }

        function quoteFilterArg(value) {
            return '"' + String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
        }

        function expressionInsideBraces(rawValue) {
            var text = String(rawValue || '').trim();
            if (text.startsWith('{{') && text.endsWith('}}')) {
                return text.replace(/^\{\{\s*/, '').replace(/\s*\}\}$/, '').trim();
            }
            return text;
        }

        function splitExpressionFilters(expression) {
            var parts = String(expression || '')
                .split('|')
                .map(function(part) { return part.trim(); })
                .filter(Boolean);
            return {
                base: parts.shift() || '',
                filters: parts
            };
        }

        function normalizeFilterSnippet(snippet) {
            var text = String(snippet || '').trim();
            if (!text) return '';
            if (text.startsWith('|')) text = text.slice(1).trim();
            return text;
        }

        function parseFilterChain(rawChain) {
            return String(rawChain || '')
                .split(/[\n|]/)
                .map(function(part) { return normalizeFilterSnippet(part); })
                .filter(Boolean);
        }

        function parseQuotedFilterValue(rawValue) {
            var text = String(rawValue || '').trim();
            if (!text) return '';
            if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) {
                text = text.slice(1, -1);
            }
            return text.replace(/\\"/g, '"').replace(/\\'/g, "'").replace(/\\\\/g, '\\');
        }

        function parseCatchallInvocation(filterText) {
            var match = String(filterText || '').trim().match(/^catchall_(complete|question|subquestion|label|datatype|options)\s*\((.*)\)$/i);
            if (!match) return null;

            var kind = match[1].toLowerCase();
            var argsText = match[2].trim();
            var result = {};

            if (kind === 'complete') {
                var argPattern = /(question|subquestion|label|datatype|options)\s*=\s*((?:\[[\s\S]*?\])|(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'))/gi;
                var argMatch;
                while ((argMatch = argPattern.exec(argsText))) {
                    var argName = argMatch[1].toLowerCase();
                    var argValue = argMatch[2].trim();
                    if (argName === 'options') {
                        var optionText = argValue;
                        if (optionText.startsWith('[') && optionText.endsWith(']')) {
                            optionText = optionText.slice(1, -1);
                        }
                        result.options = optionText
                            .split(',')
                            .map(function(item) { return parseQuotedFilterValue(item.trim()); })
                            .filter(Boolean)
                            .join(', ');
                    } else {
                        result[argName] = parseQuotedFilterValue(argValue);
                    }
                }
                return result;
            }

            result[kind] = parseQuotedFilterValue(argsText);
            return result;
        }

        function getCatchallEditorState(rawExpression) {
            var parts = splitExpressionFilters(rawExpression);
            var base = parts.base;
            var catchall = {
                question: '',
                subquestion: '',
                label: '',
                datatype: '',
                options: ''
            };
            var extraFilters = [];

            parts.filters.forEach(function(filterText) {
                var normalized = normalizeFilterSnippet(filterText);
                if (!normalized) return;
                if (normalized.toLowerCase().startsWith('catchall_')) {
                    var parsed = parseCatchallInvocation(normalized);
                    if (parsed) {
                        Object.keys(parsed).forEach(function(key) {
                            if (Object.prototype.hasOwnProperty.call(catchall, key) && parsed[key]) {
                                catchall[key] = parsed[key];
                            }
                        });
                    }
                    return;
                }
                extraFilters.push(normalized);
            });

            return {
                base: base,
                catchall: catchall,
                extraFilters: extraFilters
            };
        }

        const COMMON_FILTER_SNIPPETS = [
            { label: 'Docassemble: currency', value: 'currency' },
            { label: 'Docassemble: comma_and_list', value: 'comma_and_list' },
            { label: 'Docassemble: comma_list', value: 'comma_list' },
            { label: 'Docassemble: add_separators', value: 'add_separators' },
            { label: 'Docassemble: phone_number_formatted', value: 'phone_number_formatted' },
            { label: 'Docassemble: phone_number_in_e164', value: 'phone_number_in_e164' },
            { label: 'Docassemble: manual_line_breaks', value: 'manual_line_breaks' },
            { label: 'Docassemble: inline_markdown', value: 'inline_markdown' },
            { label: 'Docassemble: markdown', value: 'markdown' },
            { label: 'Docassemble: paragraphs', value: 'paragraphs' },
            { label: 'Docassemble: fix_punctuation', value: 'fix_punctuation' },
            { label: 'Docassemble: nice_number', value: 'nice_number' },
            { label: 'Docassemble: ordinal', value: 'ordinal' },
            { label: 'Docassemble: ordinal_number', value: 'ordinal_number' },
            { label: 'Docassemble: title_case', value: 'title_case' },
            { label: 'Docassemble: verbatim', value: 'verbatim' },
            { label: 'Docassemble: country_name', value: 'country_name' },
            { label: 'Docassemble: redact', value: 'redact' },
            { label: 'Jinja2: default("")', value: 'default("")' },
            { label: 'Jinja2: length', value: 'length' },
            { label: 'Jinja2: lower', value: 'lower' },
            { label: 'Jinja2: upper', value: 'upper' },
            { label: 'Jinja2: capitalize', value: 'capitalize' },
            { label: 'Jinja2: title', value: 'title' },
            { label: 'Jinja2: trim', value: 'trim' },
            { label: 'Jinja2: replace("", "")', value: 'replace("", "")' },
            { label: 'Jinja2: join(", ")', value: 'join(", ")' },
            { label: 'Jinja2: round', value: 'round' },
            { label: 'Jinja2: int', value: 'int' },
            { label: 'Jinja2: float', value: 'float' },
            { label: 'Jinja2: list', value: 'list' },
            { label: 'Jinja2: safe', value: 'safe' },
            { label: 'Jinja2: escape', value: 'escape' },
            { label: 'Jinja2: urlize', value: 'urlize' },
            { label: 'Jinja2: selectattr("attr")', value: 'selectattr("attr")' },
            { label: 'Jinja2: rejectattr("attr")', value: 'rejectattr("attr")' },
            { label: 'Jinja2: map(attribute="attr")', value: 'map(attribute="attr")' },
            { label: 'Jinja2: sort(attribute="attr")', value: 'sort(attribute="attr")' },
            { label: 'Jinja2: sum(attribute="attr")', value: 'sum(attribute="attr")' }
        ];

        function insertFilterSnippet(target, snippet) {
            if (!target) return;
            var text = normalizeFilterSnippet(snippet);
            if (!text) return;
            var prefix = target.value && target.value.trim() ? ' | ' : '| ';
            var insertText = prefix + text;
            if (typeof target.selectionStart === 'number' && typeof target.selectionEnd === 'number') {
                var start = target.selectionStart;
                var end = target.selectionEnd;
                var current = target.value || '';
                var before = current.slice(0, start);
                var after = current.slice(end);
                target.value = before + insertText + after;
                var caret = before.length + insertText.length;
                target.selectionStart = caret;
                target.selectionEnd = caret;
            } else {
                target.value = (target.value || '').trim();
                target.value += insertText;
            }
            target.dispatchEvent(new Event('input', { bubbles: true }));
        }

        function buildCatchallExpression(baseExpression, config) {
            var expression = String(baseExpression || '').trim();
            if (!expression) return '';
            var filters = [];
            var catchallArgs = [];
            if (config.question) catchallArgs.push('question=' + quoteFilterArg(config.question));
            if (config.subquestion) catchallArgs.push('subquestion=' + quoteFilterArg(config.subquestion));
            if (config.label) catchallArgs.push('label=' + quoteFilterArg(config.label));
            if (config.datatype) catchallArgs.push('datatype=' + quoteFilterArg(config.datatype));
            if (config.options) {
                var list = config.options
                    .split('\n')
                    .join(',')
                    .split(',')
                    .map(function(item) { return item.trim(); })
                    .filter(Boolean)
                    .map(function(item) { return quoteFilterArg(item); });
                if (list.length) catchallArgs.push('options=[' + list.join(', ') + ']');
            }
            if (catchallArgs.length) {
                filters.push('catchall_complete(' + catchallArgs.join(', ') + ')');
            }
            parseFilterChain(config.additionalFilters).forEach(function(filterText) {
                if (filterText) filters.push(filterText);
            });
            if (filters.length) {
                expression += ' | ' + filters.join(' | ');
            }
            return '{{ ' + expression + ' }}';
        }

        function ensureCatchallDialog() {
            var existing = document.getElementById('catchall-filter-modal');
            if (existing) return existing;
            var modal = document.createElement('div');
            modal.id = 'catchall-filter-modal';
            modal.className = 'hidden dl-modal-overlay';
            modal.innerHTML =
                '<div class="dl-modal dl-modal-md">' +
                    '<div class="dl-modal-header">' +
                        '<h2 class="h5 fw-semibold mb-0">Catchall / Request Filters</h2>' +
                        '<button type="button" id="catchall-close" class="btn btn-sm btn-light rounded">Close</button>' +
                    '</div>' +
                    '<div class="dl-modal-body">' +
                        '<div class="mb-2"><label class="form-label small mb-1" for="catchall-question">Question</label><input id="catchall-question" class="form-control form-control-sm" type="text"></div>' +
                        '<div class="mb-2"><label class="form-label small mb-1" for="catchall-subquestion">Subquestion</label><textarea id="catchall-subquestion" class="form-control form-control-sm" rows="2"></textarea></div>' +
                        '<div class="mb-2"><label class="form-label small mb-1" for="catchall-label">Label</label><input id="catchall-label" class="form-control form-control-sm" type="text"></div>' +
                        '<div class="mb-2"><label class="form-label small mb-1" for="catchall-datatype">Datatype</label><select id="catchall-datatype" class="form-select form-select-sm"><option value="">(none)</option><option value="text">text</option><option value="area">area</option><option value="yesno">yesno</option><option value="radio">radio</option><option value="checkboxes">checkboxes</option><option value="date">date</option><option value="email">email</option><option value="currency">currency</option><option value="integer">integer</option><option value="number">number</option></select></div>' +
                        '<div><label class="form-label small mb-1" for="catchall-options">Options (comma or one per line)</label><textarea id="catchall-options" class="form-control form-control-sm" rows="2"></textarea></div>' +
                        '<div class="mt-3 pt-2 border-top"><label class="form-label small mb-1" for="catchall-extra-filters">Additional filters</label><textarea id="catchall-extra-filters" class="form-control form-control-sm font-monospace" rows="4" placeholder="| default(\"\") | title"></textarea><div class="d-flex gap-2 mt-2"><select id="catchall-filter-pick" class="form-select form-select-sm flex-grow-1"><option value="">Common filters</option></select><button type="button" id="catchall-filter-add" class="btn btn-sm btn-outline-secondary">Add</button></div><div class="form-text mt-1">Add filters in order. Example: <span class="font-monospace">| default(\"\") | upper</span></div></div>' +
                    '</div>' +
                    '<div class="dl-modal-footer d-flex justify-content-end gap-2">' +
                        '<button type="button" id="catchall-cancel" class="btn btn-outline-secondary">Cancel</button>' +
                        '<button type="button" id="catchall-apply" class="btn btn-primary">Apply Catchall</button>' +
                    '</div>' +
                '</div>';
            document.body.appendChild(modal);
            modal.addEventListener('click', function(evt) {
                if (evt.target === modal) modal.classList.add('hidden');
            });
            document.getElementById('catchall-close').addEventListener('click', function() {
                modal.classList.add('hidden');
            });
            document.getElementById('catchall-cancel').addEventListener('click', function() {
                modal.classList.add('hidden');
            });
            return modal;
        }

        function openCatchallDialog(targetInput) {
            var input = targetInput;
            if (!input) return;
            var raw = String(input.value || '').trim();
            var innerExpression = expressionInsideBraces(raw);
            if (!innerExpression || raw.startsWith('{%')) {
                showError('Catchall filters apply to variable expressions like {{ variable_name }}.');
                return;
            }
            var parsed = getCatchallEditorState(innerExpression);
            var modal = ensureCatchallDialog();
            modal.classList.remove('hidden');
            document.getElementById('catchall-question').value = parsed.catchall.question || '';
            document.getElementById('catchall-subquestion').value = parsed.catchall.subquestion || '';
            document.getElementById('catchall-label').value = parsed.catchall.label || '';
            document.getElementById('catchall-datatype').value = parsed.catchall.datatype || '';
            document.getElementById('catchall-options').value = parsed.catchall.options || '';
            document.getElementById('catchall-extra-filters').value = parsed.extraFilters.join(' | ');
            var pick = document.getElementById('catchall-filter-pick');
            if (pick) {
                pick.innerHTML = '<option value="">Common filters</option>';
                COMMON_FILTER_SNIPPETS.forEach(function(item) {
                    var option = document.createElement('option');
                    option.value = item.value;
                    option.textContent = item.label;
                    pick.appendChild(option);
                });
                pick.value = '';
            }
            var addBtn = document.getElementById('catchall-filter-add');
            if (addBtn && pick) {
                addBtn.onclick = function() {
                    insertFilterSnippet(document.getElementById('catchall-extra-filters'), pick.value);
                    pick.value = '';
                };
            }
            document.getElementById('catchall-apply').onclick = function() {
                var updated = buildCatchallExpression(innerExpression, {
                    question: document.getElementById('catchall-question').value.trim(),
                    subquestion: document.getElementById('catchall-subquestion').value.trim(),
                    label: document.getElementById('catchall-label').value.trim(),
                    datatype: document.getElementById('catchall-datatype').value.trim(),
                    options: document.getElementById('catchall-options').value.trim(),
                    additionalFilters: document.getElementById('catchall-extra-filters').value.trim(),
                });
                if (!updated) return;
                input.value = updated;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                modal.classList.add('hidden');
                input.focus();
            };
        }

        // ================================================================
        // Bulk find & replace
        // ================================================================
        function previewBulkReplace() {
            var find = document.getElementById('bulk-find').value;
            var replace = document.getElementById('bulk-replace').value;
            if (!find) { document.getElementById('bulk-preview').classList.add('hidden'); return; }
            try {
                var regex = new RegExp(find, 'g');
                var previewList = document.getElementById('bulk-preview-list');
                previewList.innerHTML = '';
                var matchCount = 0;
                state.existingLabels.forEach(function(label) {
                    var inner = label.current.replace(/^\{\{\s*|\s*\}\}$/g, '');
                    if (regex.test(inner)) {
                        regex.lastIndex = 0;
                        var newInner = inner.replace(regex, replace);
                        var newLabel = '{{ ' + newInner + ' }}';
                        var div = document.createElement('div');
                        div.className = 'font-monospace';
                        div.innerHTML = '<span class="text-danger text-decoration-line-through">' + escapeHtml(label.current) + '</span><span class="text-muted mx-1">\u2192</span><span class="text-success">' + escapeHtml(newLabel) + '</span>';
                        previewList.appendChild(div);
                        matchCount++;
                    }
                });
                if (matchCount > 0) { document.getElementById('bulk-preview').classList.remove('hidden'); }
                else { previewList.innerHTML = '<span class="text-muted">No matches found</span>'; document.getElementById('bulk-preview').classList.remove('hidden'); }
            } catch (e) {
                document.getElementById('bulk-preview-list').innerHTML = '<span class="text-danger">Invalid regex: ' + e.message + '</span>';
                document.getElementById('bulk-preview').classList.remove('hidden');
            }
        }

        function applyBulkReplace() {
            var find = document.getElementById('bulk-find').value;
            var replace = document.getElementById('bulk-replace').value;
            if (!find) return;
            try {
                var regex = new RegExp(find, 'g');
                state.existingLabels.forEach(function(label) {
                    var inner = label.current.replace(/^\{\{\s*|\s*\}\}$/g, '');
                    if (regex.test(inner)) {
                        regex.lastIndex = 0;
                        var newInner = inner.replace(regex, replace);
                        label.current = '{{ ' + newInner + ' }}';
                    }
                });
                bulkReplaceModal.classList.add('hidden');
                renderExistingLabelsTree();
                updatePreview();
                scheduleSyntaxValidation();
            } catch (e) { alert('Invalid regex: ' + e.message); }
        }

        // ================================================================
        // AI suggestions
        // ================================================================
        async function fetchSuggestions() {
            if (!state.auth.aiEnabled) {
                suggestionsList.innerHTML = '<div class="alert alert-warning">AI suggestions require login.</div>';
                return;
            }
            var selectedSourceState = getActiveInterviewSourceState();
            if (state.settings.usePlaygroundVariables && !selectedSourceState.selectedFile) {
                suggestionsList.innerHTML = '<div class="alert alert-warning">Select an interview before using interview variable names.</div>';
                return;
            }
            var formData = new FormData();
            formData.append('file', state.file);
            formData.append('defragment_runs', state.settings.defragmentRuns ? 'true' : 'false');
            if (state.settings.additionalInstructions) formData.append('additional_instructions', state.settings.additionalInstructions);
            if (state.settings.contextText) formData.append('context_text', state.settings.contextText);
            if (state.settings.customPeople) formData.append('custom_people_names', state.settings.customPeople);
            formData.append('prompt_profile', state.settings.promptProfile || 'standard');
            formData.append('model', state.settings.model);
            if (state.settings.judgeModel) formData.append('judge_model', state.settings.judgeModel);
            if (state.settings.usePlaygroundVariables) {
                if (!selectedSourceState.variables.length) {
                    suggestionsList.innerHTML = '<div class="alert alert-warning">No interview variables were found for the selected interview.</div>';
                    return;
                }
                formData.append('use_playground_variables', 'true');
                formData.append('interview_source_mode', state.interviewSourceMode);
                formData.append('preferred_variable_names', JSON.stringify(selectedSourceState.variables));
                if (state.interviewSourceMode === 'installed') {
                    formData.append('installed_package', state.installed.selectedPackage);
                    formData.append('installed_yaml_file', state.installed.selectedFile);
                    formData.append('installed_interview_path', state.installed.selectedPackage + ':' + state.installed.selectedFile);
                } else {
                    formData.append('playground_project', state.playground.selectedProject);
                    formData.append('playground_yaml_file', state.playground.selectedFile);
                }
            }
            if (state.settings.generationMethod === 'single') {
                formData.append('generator_models', JSON.stringify([state.settings.model]));
            } else if (state.settings.generationMethod === 'multi_model') {
                var generatorModels = parseGeneratorModelsInput(state.settings.generatorModels);
                if (generatorModels.length === 0) generatorModels = [state.settings.model];
                formData.append('generator_models', JSON.stringify(generatorModels));
            }
            try {
                var manualSuggestions = state.suggestions.filter(function(suggestion) {
                    return isManualSuggestion(suggestion);
                });
                var data = await requestAsyncLabelerAction(
                    '/al/docx-labeler/api/suggest-labels',
                    formData,
                    'AI labeling is running in the background...'
                );
                state.suggestions = manualSuggestions.concat((data.suggestions || []).map(function(s) {
                    return Object.assign({}, s, { id: s.id || generateId(), status: 'pending' });
                }));
                state.validation = data.validation || null;
                suggestionsCount.textContent = state.suggestions.length;
                renderSuggestions();
            } catch (error) {
                console.error('Error fetching suggestions:', error);
                state.validation = null;
                suggestionsList.innerHTML = '<div class="alert alert-danger">' + escapeHtml(error.message) + '</div>';
            }
        }

        function renderSuggestions() {
            suggestionsList.innerHTML = '';
            renderManualLabels();
            var deterministic = state.validation && state.validation.deterministic ? state.validation.deterministic : null;
            var syntaxValidation = deterministic && deterministic.syntax_validation ? deterministic.syntax_validation : null;
            var documentWarnings = state.validation && Array.isArray(state.validation.document_warnings) ? state.validation.document_warnings : [];
            var aggregation = state.validation && state.validation.aggregation ? state.validation.aggregation : null;
            var timings = state.validation && state.validation.timings ? state.validation.timings : null;
            var aiSuggestions = state.suggestions.filter(function(suggestion) { return !isManualSuggestion(suggestion); });
            if ((deterministic && deterministic.flagged_count > 0) || documentWarnings.length > 0 || aggregation || (syntaxValidation && ((syntaxValidation.error_count || 0) > 0 || (syntaxValidation.warning_count || 0) > 0))) {
                var aiReview = state.validation && state.validation.ai_review ? state.validation.ai_review : {};
                var reviewedCount = Array.isArray(aiReview.reviews) ? aiReview.reviews.length : 0;
                suggestionsValidation.classList.remove('hidden');
                var summaryHtml = '';
                if (aggregation) {
                    var confidenceCounts = aggregation.confidence_counts || {};
                    summaryHtml +=
                        '<div><strong>Aggregated ' + aiSuggestions.length + '</strong> ranked suggestion' + (aiSuggestions.length === 1 ? '' : 's') +
                        ' from ' + escapeHtml(String(aggregation.generator_runs || 0)) + ' generation run' + ((aggregation.generator_runs || 0) === 1 ? '' : 's') + '.</div>' +
                        '<div class="mt-1">Confidence tiers: ' +
                        '<span class="badge bg-success-subtle text-success-emphasis border border-success-subtle me-1">High ' + escapeHtml(String(confidenceCounts.high || 0)) + '</span>' +
                        '<span class="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle me-1">Medium ' + escapeHtml(String(confidenceCounts.medium || 0)) + '</span>' +
                        '<span class="badge bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle">Low ' + escapeHtml(String(confidenceCounts.low || 0)) + '</span>' +
                        '</div>';
                    if (aggregation.ambiguous_group_count) {
                        summaryHtml += '<div class="mt-1">Judge reviewed ' + escapeHtml(String(reviewedCount)) + ' ambiguous group' + (reviewedCount === 1 ? '' : 's') + '.</div>';
                    }
                }
                if (deterministic && deterministic.flagged_count > 0) {
                    summaryHtml +=
                        '<div class="mt-1"><strong>Validator flagged ' + deterministic.flagged_count + '</strong> selected suggestion' + (deterministic.flagged_count === 1 ? '' : 's') + '.</div>';
                }
                if (syntaxValidation && (syntaxValidation.error_count || 0) > 0) {
                    summaryHtml += '<div class="mt-1"><strong>Final template syntax errors:</strong></div>';
                    syntaxValidation.errors.forEach(function(issue) {
                        summaryHtml += '<div>' + escapeHtml(issue.message || issue.code || 'Syntax error') + '</div>';
                    });
                } else if (syntaxValidation && (syntaxValidation.warning_count || 0) > 0) {
                    summaryHtml += '<div class="mt-1"><strong>Final template warnings:</strong></div>';
                    syntaxValidation.warnings.forEach(function(issue) {
                        summaryHtml += '<div>' + escapeHtml(issue.message || issue.code || 'Warning') + '</div>';
                    });
                }
                if (documentWarnings.length > 0) {
                    summaryHtml += '<div class="mt-1"><strong>Source document warnings:</strong></div>';
                    documentWarnings.forEach(function(warning) {
                        summaryHtml += '<div>' + escapeHtml(warning.message || warning.code || 'Document warning') + '</div>';
                    });
                }                
                suggestionsValidation.innerHTML = summaryHtml;
            } else {
                suggestionsValidation.classList.add('hidden');
                suggestionsValidation.textContent = '';
            }
            var hiddenLowConfidenceCount = 0;
            // Count only visible (non-companion) suggestions
            var visibleCount = aiSuggestions.filter(function(s) {
                if (s._isCompanion) return false;
                if (!state.settings.showLowConfidence && (s.confidence || 'low') === 'low') {
                    hiddenLowConfidenceCount += 1;
                    return false;
                }
                return true;
            }).length;
            suggestionsCount.textContent = visibleCount;
            lowConfidenceSummary.textContent = hiddenLowConfidenceCount > 0
                ? hiddenLowConfidenceCount + ' low confidence suggestion' + (hiddenLowConfidenceCount === 1 ? '' : 's') + ' hidden'
                : '';
            if (visibleCount === 0) {
                suggestionsList.innerHTML = hiddenLowConfidenceCount > 0
                    ? '<p class="text-muted text-center py-5">Only low confidence suggestions are available. Turn on “Show low confidence” to review them.</p>'
                    : '<p class="text-muted text-center py-5">No suggestions available.</p>';
                return;
            }
            state.suggestions.forEach(function(suggestion, index) {
                // Skip companion entries – they’re part of a multi-run group
                if (suggestion._isCompanion) return;
                if (isManualSuggestion(suggestion)) return;
                if (!state.settings.showLowConfidence && (suggestion.confidence || 'low') === 'low') return;

                var div = document.createElement('div');
                var stateClass = suggestion.status === 'accepted' ? 'suggestion-accepted' : suggestion.status === 'rejected' ? 'suggestion-rejected' : 'suggestion-pending';
                div.className = 'p-3 rounded border border-2 mb-2 ' + stateClass;

                var locationLabel = suggestion.group
                    ? '<span class="small text-muted">Selection \u2192 ' + escapeHtml(suggestion._displayLabel || suggestion.text) + '</span>'
                    : '<span class="small text-muted">Para ' + suggestion.paragraph + ', Run ' + suggestion.run + '</span>';
                var validationFlags = Array.isArray(suggestion.validation_flags) ? suggestion.validation_flags : [];
                var judgeReview = suggestion.judge_review || null;
                var confidenceMeta = getConfidenceMeta(suggestion.confidence || 'low');
                var supportHtml = '';
                if (!suggestion.group && suggestion.vote_total) {
                    supportHtml =
                        '<span class="badge ' + confidenceMeta.badge + '">' + escapeHtml(confidenceMeta.label) + '</span>' +
                        '<span class="badge text-bg-light border ms-1">' + escapeHtml(String(suggestion.clean_vote_count || 0)) + '/' + escapeHtml(String(suggestion.vote_total || 0)) + ' clean votes</span>';
                    var sourceSummary = summarizeSuggestionSources(suggestion);
                    if (sourceSummary) {
                        supportHtml += '<div class="small text-muted mt-1">Sources: ' + escapeHtml(sourceSummary) + '</div>';
                    }
                }
                var reviewHtml = '';
                if (validationFlags.length > 0) {
                    reviewHtml += '<div class="alert alert-warning small mt-2 mb-2 py-2">';
                    reviewHtml += '<div class="fw-semibold mb-1">Validator flags</div>';
                    validationFlags.forEach(function(flag) {
                        reviewHtml += '<div>' + escapeHtml(flag.message || flag.code || 'Flagged') + '</div>';
                    });
                    if (judgeReview && judgeReview.reason) {
                        reviewHtml += '<div class="mt-2"><span class="fw-semibold">Judge:</span> ' + escapeHtml(judgeReview.reason) + '</div>';
                    }
                    reviewHtml += '</div>';
                } else if (judgeReview && judgeReview.reason) {
                    reviewHtml += '<div class="small text-muted mt-2">Judge note: ' + escapeHtml(judgeReview.reason) + '</div>';
                }

                var alternates = Array.isArray(suggestion.alternates) ? suggestion.alternates : [];
                var alternatesHtml = '';
                if (!suggestion.group && alternates.length > 0) {
                    alternatesHtml += '<details class="mt-2"><summary class="small fw-semibold">Alternates (' + alternates.length + ')</summary>';
                    alternates.forEach(function(alternate, altIndex) {
                        var alternateMeta = getConfidenceMeta(alternate.confidence || 'low');
                        var alternateFlags = Array.isArray(alternate.validation_flags) ? alternate.validation_flags : [];
                        alternatesHtml +=
                            '<div class="border rounded p-2 mt-2 bg-light-subtle">' +
                                '<div class="d-flex justify-content-between align-items-start gap-2">' +
                                    '<div class="font-monospace small break-all flex-grow-1">' + escapeHtml(alternate.text || '') + '</div>' +
                                    '<button type="button" class="btn btn-sm btn-outline-primary use-alternate-btn" data-index="' + index + '" data-alt-index="' + altIndex + '">Use</button>' +
                                '</div>' +
                                '<div class="mt-2">' +
                                    '<span class="badge ' + alternateMeta.badge + '">' + escapeHtml(alternateMeta.label) + '</span>' +
                                    '<span class="badge text-bg-light border ms-1">' + escapeHtml(String(alternate.clean_vote_count || 0)) + '/' + escapeHtml(String(alternate.vote_total || suggestion.vote_total || 0)) + ' clean votes</span>' +
                                '</div>';
                        if (alternateFlags.length > 0) {
                            alternatesHtml += '<div class="small text-warning-emphasis mt-1">';
                            alternateFlags.forEach(function(flag) {
                                alternatesHtml += '<div>' + escapeHtml(flag.message || flag.code || 'Flagged') + '</div>';
                            });
                            alternatesHtml += '</div>';
                        }
                        alternatesHtml += '</div>';
                    });
                    alternatesHtml += '</details>';
                }

                div.innerHTML =
                    '<div class="d-flex align-items-start justify-content-between gap-2 mb-2">' +
                        locationLabel +
                        '<div class="d-flex gap-1">' +
                            '<button class="accept-btn btn btn-sm ' + (suggestion.status === 'accepted' ? 'btn-success' : 'btn-outline-secondary') + '" data-index="' + index + '">\u2713</button>' +
                            '<button class="reject-btn btn btn-sm ' + (suggestion.status === 'rejected' ? 'btn-danger' : 'btn-outline-secondary') + '" data-index="' + index + '">\u2717</button>' +
                            (suggestion.group ? '<button class="remove-btn btn btn-sm btn-outline-danger" data-index="' + index + '" title="Remove">\u2716</button>' : '') +
                        '</div>' +
                    '</div>' +
                    (suggestion._selectedText ? '<div class="small text-muted mb-1">\u201C' + escapeHtml(suggestion._selectedText) + '\u201D</div>' : '') +
                    (supportHtml ? '<div class="mb-2">' + supportHtml + '</div>' : '') +
                    '<div class="font-monospace small break-all">' + escapeHtml(suggestion._displayLabel || suggestion.text) + '</div>' +
                    reviewHtml +
                    alternatesHtml +
                    (suggestion.group ? '' : '<input type="text" class="edit-input form-control form-control-sm font-monospace mt-2' + (suggestion.status === 'rejected' ? ' hidden' : '') + '" value="' + escapeHtml(suggestion.text) + '" data-index="' + index + '">');
                suggestionsList.appendChild(div);
            });
            suggestionsList.querySelectorAll('.accept-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var index = parseInt(btn.dataset.index);
                    var s = state.suggestions[index];
                    var newStatus = s.status === 'accepted' ? 'pending' : 'accepted';
                    s.status = newStatus;
                    // Propagate to companions in same group
                    if (s.group) {
                        state.suggestions.forEach(function(c) {
                            if (c.group === s.group) c.status = newStatus;
                        });
                    }
                    renderSuggestions();
                    updatePreview();
                    updateDownloadButton();
                    scheduleSyntaxValidation();
                });
            });
            suggestionsList.querySelectorAll('.reject-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var index = parseInt(btn.dataset.index);
                    var s = state.suggestions[index];
                    var newStatus = s.status === 'rejected' ? 'pending' : 'rejected';
                    s.status = newStatus;
                    if (s.group) {
                        state.suggestions.forEach(function(c) {
                            if (c.group === s.group) c.status = newStatus;
                        });
                    }
                    renderSuggestions();
                    updatePreview();
                    updateDownloadButton();
                    scheduleSyntaxValidation();
                });
            });
            // Remove button deletes grouped selection labels entirely
            suggestionsList.querySelectorAll('.remove-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var index = parseInt(btn.dataset.index);
                    var grp = state.suggestions[index].group;
                    if (grp) {
                        state.suggestions = state.suggestions.filter(function(c) { return c.group !== grp; });
                    } else {
                        state.suggestions.splice(index, 1);
                    }
                    renderSuggestions();
                    updatePreview();
                    updateDownloadButton();
                    scheduleSyntaxValidation();
                });
            });
            suggestionsList.querySelectorAll('.edit-input').forEach(function(input) {
                input.addEventListener('input', function() {
                    var index = parseInt(input.dataset.index);
                    state.suggestions[index].text = input.value;
                    updatePreview();
                    scheduleSyntaxValidation(500);
                });
            });
            suggestionsList.querySelectorAll('.use-alternate-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var index = parseInt(btn.dataset.index);
                    var altIndex = parseInt(btn.dataset.altIndex);
                    swapSuggestionWithAlternate(state.suggestions[index], altIndex);
                    renderSuggestions();
                    updatePreview();
                    updateDownloadButton();
                    scheduleSyntaxValidation();
                });
            });
            updateDownloadButton();
        }

        // ================================================================
        // Playground open / save
        // ================================================================
        async function openDocxPlaygroundModal() {
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
                    await fetchPlaygroundProjects();
                    openPgProject.innerHTML = '';
                    state.playground.projects.forEach(function(proj) {
                        var opt = document.createElement('option');
                        opt.value = proj; opt.textContent = proj;
                        opt.selected = proj === state.playground.selectedProject;
                        openPgProject.appendChild(opt);
                    });
                    await fetchDocxOpenPlaygroundTemplates();
                } catch (e) {
                    console.error('Error loading playground data:', e);
                    openPgProject.innerHTML = '<option value="">(error loading projects)</option>';
                    openPgTemplate.innerHTML = '<option value="">(error loading templates)</option>';
                }
            })();
        }

        async function fetchDocxOpenPlaygroundTemplates() {
            var openPgProject = document.getElementById('open-pg-project');
            var openPgTemplate = document.getElementById('open-pg-template');
            var project = openPgProject.value || 'default';
            openPgTemplate.innerHTML = '<option value="">Loading...</option>';
            try {
                var data = await fetchJsonOrThrow(
                    endpointPath('playgroundTemplates', '/al/labeler/api/playground-templates') + '?project=' + encodeURIComponent(project) + '&type=docx'
                );
                var templates = data && data.success && data.data && Array.isArray(data.data.templates) ? data.data.templates : [];
                openPgTemplate.innerHTML = '';
                if (templates.length === 0) {
                    var opt = document.createElement('option');
                    opt.value = ''; opt.textContent = '(no DOCX templates found)';
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
                var data = await fetchJsonOrThrow(
                    endpointPath('playgroundTemplatesLoad', '/al/labeler/api/playground-templates/load') + '?project=' + encodeURIComponent(project) + '&filename=' + encodeURIComponent(filename)
                );
                if (!data.success || !data.data || !data.data.file_content_base64) {
                    throw new Error((data.error && data.error.message) || 'Failed to load template.');
                }
                var binaryString = atob(data.data.file_content_base64);
                var bytes = new Uint8Array(binaryString.length);
                for (var i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
                var blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
                var file = new File([blob], filename, { type: blob.type });
                state.playgroundSource = { project: project, filename: filename };
                savePlaygroundBtn.classList.remove('hidden');
                await processFile(file);
            } catch (error) {
                showError('Failed to load from Playground: ' + (error.message || 'Unknown error.'));
            } finally { hideLoading(); }
        }

        async function openSavePlaygroundModal() {
            if (!state.auth.isAuthenticated) { showError('Login required to save to Playground.'); return; }
            if (!state.file) { showError('No DOCX file loaded.'); return; }
            // Show modal immediately
            var savePgProject = document.getElementById('save-pg-project');
            var savePgFilename = document.getElementById('save-pg-filename');
            var savePgStatus = document.getElementById('save-pg-status');
            savePgProject.innerHTML = '<option value="">Loading projects...</option>';
            savePgFilename.value = (state.playgroundSource && state.playgroundSource.filename) || (state.file && state.file.name) || 'template.docx';
            if (!savePgFilename.value.toLowerCase().endsWith('.docx'))
                savePgFilename.value += '.docx';
            savePgStatus.classList.add('hidden');
            savePlaygroundModalEl.classList.remove('hidden');
            // Load projects in background
            (async function() {
                try {
                    await fetchPlaygroundProjects();
                    savePgProject.innerHTML = '';
                    state.playground.projects.forEach(function(proj) {
                        var opt = document.createElement('option');
                        opt.value = proj; opt.textContent = proj;
                        opt.selected = proj === ((state.playgroundSource && state.playgroundSource.project) || state.playground.selectedProject);
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
            if (!filename || !filename.toLowerCase().endsWith('.docx')) {
                savePgStatus.textContent = 'Filename must end with .docx'; savePgStatus.classList.remove('hidden'); return;
            }
            savePlaygroundModalEl.classList.add('hidden');
            showLoading('Saving to Playground...');
            try {
                var syntaxCheck = await ensureSyntaxValidationBeforeWrite('saving to Playground');
                if (!syntaxCheck.allowed) {
                    hideLoading();
                    return;
                }
                // First apply any pending changes via the apply-labels endpoint
                var renames = collectRenamePayload();
                var acceptedLabels = collectAcceptedLabels();

                var docxBase64;
                var applyHighlights = acceptedLabels.length > 0;
                if (renames.length > 0 || acceptedLabels.length > 0) {
                    var formData = new FormData();
                    formData.append('file', state.file);
                    formData.append('defragment_runs', state.settings.defragmentRuns ? 'true' : 'false');
                    if (renames.length > 0) formData.append('renames', JSON.stringify(renames));
                    if (acceptedLabels.length > 0) formData.append('labels', JSON.stringify(acceptedLabels));
                    if (applyHighlights) formData.append('apply_highlights', 'true');
                    if (syntaxCheck.allowInvalidSyntax) formData.append('allow_invalid_syntax', 'true');
                    var applyResponse = await fetch('/al/docx-labeler/api/apply-labels', { method: 'POST', body: formData });
                    var applyData = await parseApiResponse(applyResponse);
                    docxBase64 = applyData.data.docx_base64;
                } else {
                    // No changes - send the original file as base64
                    var arrayBuffer = await state.file.arrayBuffer();
                    var bytes = new Uint8Array(arrayBuffer);
                    var raw = '';
                    for (var i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
                    docxBase64 = window.btoa(raw);
                }

                var response = await fetch(endpointPath('playgroundTemplatesSave', '/al/labeler/api/playground-templates/save'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-WFD-Action': '1' },
                    body: JSON.stringify({ project: project, filename: filename, file_content_base64: docxBase64 }),
                    credentials: 'same-origin'
                });
                var data = await response.json();
                if (!data.success) throw new Error((data.error && data.error.message) || 'Save failed.');
                state.playgroundSource = { project: project, filename: filename };
                savePlaygroundBtn.classList.remove('hidden');
                downloadStatus.textContent = (data.data && data.data.created ? 'Created' : 'Updated') + ' ' + filename + ' in Playground.';
                downloadStatus.classList.remove('hidden');
            } catch (error) {
                showError('Save to Playground failed: ' + (error.message || 'Unknown error.'));
            } finally { hideLoading(); }
        }

        // ================================================================
        // Download
        // ================================================================
        function updateDownloadButton() {
            var hasChanges = collectRenamePayload().length > 0 || collectAcceptedLabels().length > 0;
            downloadBtn.disabled = !hasChanges;
            applyHighlightsBtn.disabled = !state.file;
            // Save to Playground is enabled when a file is loaded (changes or not, the user may want to save the original)
            savePlaygroundBtn.disabled = !state.file;
        }

        async function downloadDocument(options) {
            options = options || {};
            if (!state.file) return;
            var renames = collectRenamePayload();
            var acceptedLabels = collectAcceptedLabels();
            var applyHighlights = !!options.forceHighlights || acceptedLabels.length > 0;
            if (renames.length === 0 && acceptedLabels.length === 0 && !applyHighlights) { alert('No changes to apply.'); return; }
            downloadStatus.textContent = 'Processing...';
            downloadStatus.classList.remove('hidden');
            downloadBtn.disabled = true;
            applyHighlightsBtn.disabled = true;
            try {
                var syntaxCheck = await ensureSyntaxValidationBeforeWrite('downloading this DOCX');
                if (!syntaxCheck.allowed) {
                    downloadStatus.classList.add('hidden');
                    return;
                }
                var formData = new FormData();
                formData.append('file', state.file);
                formData.append('defragment_runs', state.settings.defragmentRuns ? 'true' : 'false');
                if (renames.length > 0) formData.append('renames', JSON.stringify(renames));
                if (acceptedLabels.length > 0) formData.append('labels', JSON.stringify(acceptedLabels));
                if (applyHighlights) formData.append('apply_highlights', 'true');
                if (syntaxCheck.allowInvalidSyntax) formData.append('allow_invalid_syntax', 'true');
                var response = await fetch('/al/docx-labeler/api/apply-labels', { method: 'POST', body: formData });
                var data = await parseApiResponse(response);
                var binaryString = atob(data.data.docx_base64);
                var bytes = new Uint8Array(binaryString.length);
                for (var i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
                var blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = data.data.filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                if (data.data.defragmented_before_apply && data.data.defragmentation) {
                    var paragraphCount = data.data.defragmentation.paragraphs_defragmented || 0;
                    var runsRemoved = data.data.defragmentation.runs_removed || 0;
                    downloadStatus.textContent = 'Download complete. Consolidated fragmented runs in ' + paragraphCount + ' paragraph' + (paragraphCount === 1 ? '' : 's') + ' before applying labels (' + runsRemoved + ' runs merged).';
                } else if (data.data.apply_highlights) {
                    downloadStatus.textContent = 'Download complete with Jinja highlights.';
                } else {
                    downloadStatus.textContent = 'Download complete!';
                    setTimeout(function() { downloadStatus.classList.add('hidden'); }, 2000);
                }
            } catch (error) {
                console.error('Error downloading:', error);
                downloadStatus.textContent = 'Error: ' + error.message;
            } finally { updateDownloadButton(); }
        }

        // ================================================================
        // Event listeners
        // ================================================================
        fileInput.addEventListener('change', function(e) { var file = e.target.files[0]; if (file) processFile(file); });

        // Playground open / save listeners
        openPlaygroundBtn.addEventListener('click', openDocxPlaygroundModal);
        savePlaygroundBtn.addEventListener('click', openSavePlaygroundModal);
        document.getElementById('open-pg-project').addEventListener('change', fetchDocxOpenPlaygroundTemplates);
        document.getElementById('open-pg-confirm').addEventListener('click', confirmOpenFromPlayground);
        document.getElementById('open-pg-cancel').addEventListener('click', function() { openPlaygroundModalEl.classList.add('hidden'); });
        document.getElementById('close-open-playground').addEventListener('click', function() { openPlaygroundModalEl.classList.add('hidden'); });
        document.getElementById('save-pg-confirm').addEventListener('click', confirmSaveToPlayground);
        document.getElementById('save-pg-cancel').addEventListener('click', function() { savePlaygroundModalEl.classList.add('hidden'); });
        document.getElementById('close-save-playground').addEventListener('click', function() { savePlaygroundModalEl.classList.add('hidden'); });
        // Close playground modals on backdrop click
        [openPlaygroundModalEl, savePlaygroundModalEl].forEach(function(modal) {
            modal.addEventListener('click', function(e) { if (e.target === modal) modal.classList.add('hidden'); });
        });
        utilitiesBtn.addEventListener('click', function() {
            updateModalSourceStatus();
            utilitiesResult.innerHTML = '';
            utilitiesModal.classList.remove('hidden');
        });
        repairBtn.addEventListener('click', function() {
            updateModalSourceStatus();
            repairResult.innerHTML = '';
            repairModal.classList.remove('hidden');
        });
        document.getElementById('utilities-close').addEventListener('click', function() { utilitiesModal.classList.add('hidden'); });
        document.getElementById('repair-close').addEventListener('click', function() { repairModal.classList.add('hidden'); });
        [utilitySourceActive, utilitySourceUpload, repairSourceActive, repairSourceUpload].forEach(function(input) {
            input.addEventListener('change', updateModalSourceStatus);
        });
        utilityFileInput.addEventListener('change', updateModalSourceStatus);
        repairFileInput.addEventListener('change', updateModalSourceStatus);
        document.querySelectorAll('.utility-action-btn').forEach(function(button) {
            button.addEventListener('click', function() {
                runDocxOperation('utility', button.dataset.action, 'Running DOCX utility...');
            });
        });
        document.querySelectorAll('.repair-action-btn').forEach(function(button) {
            button.addEventListener('click', function() {
                runDocxOperation('repair', button.dataset.action, 'Running DOCX repair...');
            });
        });
        tabExisting.addEventListener('click', function() { switchTab('existing'); });
        tabManual.addEventListener('click', function() { switchTab('manual'); });
        tabSuggestions.addEventListener('click', function() { switchTab('suggestions'); });
        document.getElementById('accept-all-btn').addEventListener('click', function() {
            state.suggestions.forEach(function(s) { if (s.status === 'pending') s.status = 'accepted'; });
            renderSuggestions();
            updatePreview();
        });
        document.getElementById('regenerate-btn').addEventListener('click', async function() {
            if (!state.auth.aiEnabled) {
                showError('AI suggestions require login.');
                return;
            }
            if (state.file) { showLoading('Regenerating suggestions...'); await fetchSuggestions(); hideLoading(); showMainPanel(); }
        });
        document.getElementById('bulk-replace-btn').addEventListener('click', function() {
            document.getElementById('bulk-find').value = '';
            document.getElementById('bulk-replace').value = '';
            document.getElementById('bulk-preview').classList.add('hidden');
            bulkReplaceModal.classList.remove('hidden');
        });
        document.getElementById('ai-relabel-btn').addEventListener('click', function() {
            alert('AI Relabeling coming soon! This will use AI to suggest better AssemblyLine-compatible names for your existing labels.');
        });
        document.getElementById('bulk-preview-btn').addEventListener('click', previewBulkReplace);
        document.getElementById('bulk-apply').addEventListener('click', applyBulkReplace);
        document.getElementById('bulk-cancel').addEventListener('click', function() { bulkReplaceModal.classList.add('hidden'); });
        document.getElementById('edit-save').addEventListener('click', saveEditLabel);
        document.getElementById('edit-cancel').addEventListener('click', function() { editLabelModal.classList.add('hidden'); state.editingLabelId = null; });
        document.getElementById('edit-close').addEventListener('click', function() { editLabelModal.classList.add('hidden'); state.editingLabelId = null; });
        variableSearch.addEventListener('input', function(e) { renderVariableTree(getEffectiveVariableTree(), '', variableTree, e.target.value); });
        downloadBtn.addEventListener('click', function() { downloadDocument(); });
        applyHighlightsBtn.addEventListener('click', function() { downloadDocument({ forceHighlights: true }); });
        document.getElementById('settings-btn').addEventListener('click', function() {
            document.getElementById('additional-instructions').value = state.settings.additionalInstructions;
            document.getElementById('context-text').value = state.settings.contextText || '';
            document.getElementById('custom-people').value = state.settings.customPeople;
            promptProfileInput.value = state.settings.promptProfile || 'standard';
            generationMethodInput.value = state.settings.generationMethod || 'multi_run';
            generatorModelsInput.value = state.settings.generatorModels || '';
            judgeModelInput.value = state.settings.judgeModel || '';
            document.getElementById('defragment-runs').checked = !!state.settings.defragmentRuns;
            usePlaygroundVariablesInput.checked = !!state.settings.usePlaygroundVariables;
            aiModelInput.value = state.settings.model;
            state.ui.editInterviewSourceInSettings = false;
            renderGenerationMethodFields();
            renderInterviewPicker();
            renderModelSuggestions('');
            settingsModal.classList.remove('hidden');
        });
        document.getElementById('close-settings').addEventListener('click', function() {
            state.ui.editInterviewSourceInSettings = false;
            renderInterviewPicker();
            settingsModal.classList.add('hidden');
        });
        document.getElementById('save-settings').addEventListener('click', function() {
            state.settings.additionalInstructions = document.getElementById('additional-instructions').value;
            state.settings.contextText = document.getElementById('context-text').value;
            state.settings.customPeople = document.getElementById('custom-people').value;
            state.settings.promptProfile = promptProfileInput.value || 'standard';
            state.settings.generationMethod = generationMethodInput.value || 'multi_run';
            state.settings.generatorModels = generatorModelsInput.value;
            state.settings.judgeModel = judgeModelInput.value.trim();
            state.settings.defragmentRuns = !!document.getElementById('defragment-runs').checked;
            state.settings.usePlaygroundVariables = !!usePlaygroundVariablesInput.checked;
            state.settings.model = aiModelInput.value.trim() || state.defaultModel;
            state.ui.editInterviewSourceInSettings = false;
            renderInterviewPicker();
            settingsModal.classList.add('hidden');
        });
        document.getElementById('reset-settings').addEventListener('click', function() {
            document.getElementById('additional-instructions').value = '';
            document.getElementById('context-text').value = '';
            document.getElementById('custom-people').value = '';
            promptProfileInput.value = 'standard';
            generationMethodInput.value = 'multi_run';
            generatorModelsInput.value = '';
            judgeModelInput.value = '';
            document.getElementById('defragment-runs').checked = true;
            usePlaygroundVariablesInput.checked = false;
            aiModelInput.value = state.defaultModel;
            renderGenerationMethodFields();
            renderInterviewPicker();
            renderModelSuggestions('');
        });
        if (changeInterviewSourceBtn) {
            changeInterviewSourceBtn.addEventListener('click', function() {
                state.ui.editInterviewSourceInSettings = !state.ui.editInterviewSourceInSettings;
                renderInterviewPicker();
            });
        }
        generationMethodInput.addEventListener('change', renderGenerationMethodFields);
        aiModelInput.addEventListener('input', function() { renderModelSuggestions(aiModelInput.value); });
        aiModelInput.addEventListener('change', function() {
            state.settings.model = aiModelInput.value.trim() || state.defaultModel;
        });
        interviewSourceModeInputs.forEach(function(input) {
            input.addEventListener('change', async function() {
                if (!input.checked) return;
                state.interviewSourceMode = input.value === 'installed' ? 'installed' : 'playground';
                renderInterviewPicker();
                if (state.interviewSourceMode === 'installed') {
                    if (!state.installed.packages.length) {
                        await fetchInstalledPackages();
                    } else if (state.installed.selectedFile) {
                        await fetchInstalledVariables();
                    }
                } else {
                    if (!state.playground.projects.length) {
                        await fetchPlaygroundProjects();
                    } else if (state.playground.selectedFile) {
                        await fetchPlaygroundVariables();
                    }
                }
            });
        });
        playgroundProjectSelect.addEventListener('change', async function() {
            state.playground.selectedProject = playgroundProjectSelect.value || 'default';
            state.playground.selectedFile = '';
            await fetchPlaygroundFiles();
        });
        playgroundYamlFileSelect.addEventListener('change', async function() {
            state.playground.selectedFile = playgroundYamlFileSelect.value || '';
            await fetchPlaygroundVariables();
        });
        installedPackageSelect.addEventListener('change', async function() {
            state.installed.selectedPackage = installedPackageSelect.value || '';
            state.installed.selectedFile = '';
            await fetchInstalledFiles();
        });
        installedYamlFileSelect.addEventListener('change', async function() {
            state.installed.selectedFile = installedYamlFileSelect.value || '';
            await fetchInstalledVariables();
        });
        usePlaygroundVariablesInput.addEventListener('change', function() {
            state.settings.usePlaygroundVariables = !!usePlaygroundVariablesInput.checked;
        });
        toggleLowConfidence.checked = !!state.settings.showLowConfidence;
        toggleLowConfidence.addEventListener('change', function() {
            state.settings.showLowConfidence = !!toggleLowConfidence.checked;
            renderSuggestions();
        });
        [settingsModal, bulkReplaceModal, editLabelModal, utilitiesModal, repairModal].forEach(function(modal) {
            modal.addEventListener('click', function(e) {
                if (e.target === modal) {
                    if (modal === settingsModal) {
                        state.ui.editInterviewSourceInSettings = false;
                        renderInterviewPicker();
                    }
                    modal.classList.add('hidden');
                }
            });
        });

        // Selection popover events
        previewContent.addEventListener('mouseup', onPreviewMouseUp);
        document.getElementById('sel-close').addEventListener('click', hideSelectionPopover);
        document.getElementById('sel-cancel').addEventListener('click', hideSelectionPopover);
        document.getElementById('sel-save').addEventListener('click', saveSelectionLabel);
        selVarInput.addEventListener('keydown', function(e) { if (e.key === 'Enter') { e.preventDefault(); saveSelectionLabel(); } });
        document.getElementById('sel-tree-toggle').addEventListener('click', function() {
            if (selVarPanel.classList.contains('hidden')) {
                selVarPanel.classList.remove('hidden');
                renderSelPopoverTree();
            } else {
                selVarPanel.classList.add('hidden');
            }
        });
        selVarSearch.addEventListener('input', function(e) { renderSelPopoverTree(e.target.value); });
        // Close popover on outside click
        document.addEventListener('mousedown', function(e) {
            if (!selPopover.classList.contains('hidden') && !selPopover.contains(e.target) && !previewContent.contains(e.target)) {
                hideSelectionPopover();
            }
            var authMenu = document.getElementById('auth-menu');
            var authMenuBtn = document.getElementById('auth-menu-btn');
            if (authMenu && authMenuBtn && !authControls.contains(e.target)) {
                authMenu.classList.remove('show');
            }
        });

        // Drag & drop
        var dropZone = document.querySelector('aside');
        dropZone.addEventListener('dragover', function(e) { e.preventDefault(); dropZone.classList.add('drag-over'); });
        dropZone.addEventListener('dragleave', function() { dropZone.classList.remove('drag-over'); });
        dropZone.addEventListener('drop', function(e) {
            e.preventDefault();
            dropZone.classList.remove('drag-over');
            var file = e.dataTransfer.files[0];
            if (file && file.name.endsWith('.docx')) processFile(file);
        });

        fetchModelCatalog();
        fetchAuthStatus();
        renderGenerationMethodFields();
        updateModalSourceStatus();
    })();
