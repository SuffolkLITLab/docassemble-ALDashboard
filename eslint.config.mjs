import js from '@eslint/js';
import globals from 'globals';
import sonarjs from 'eslint-plugin-sonarjs';

const labelerFiles = [
  'docassemble/ALDashboard/data/static/labeler_dialogs.js',
  'docassemble/ALDashboard/data/static/docx_labeler.js',
  'docassemble/ALDashboard/data/static/docx_labeler_preview_utils.js',
  'docassemble/ALDashboard/data/static/pdf_labeler.js',
  'docassemble/ALDashboard/data/static/interview_linter.js',
  '.github/scripts/labeler_a11y.js',
];

export default [
  {
    ignores: ['node_modules/**'],
  },
  {
    files: labelerFiles,
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
        mammoth: 'readonly',
      },
    },
    plugins: {
      sonarjs,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...sonarjs.configs.recommended.rules,
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-unused-vars': [
        'error',
        { args: 'none', caughtErrors: 'none' },
      ],
      // These browser scripts intentionally use nested closures for stateful
      // controllers and promise callbacks.
      'sonarjs/no-nested-functions': 'off',
      'sonarjs/cognitive-complexity': 'off',
      // These scripts use browser-safe fallback IDs and deliberately complex
      // expressions/regular expressions for document-template parsing.
      'sonarjs/no-ignored-exceptions': 'off',
      'sonarjs/no-nested-conditional': 'off',
      'sonarjs/no-useless-escape': 'off',
      'sonarjs/pseudo-random': 'off',
      'sonarjs/regex-complexity': 'off',
      'sonarjs/super-linear-regex': 'off',
      'sonarjs/no-dead-store': 'off',
      'sonarjs/no-unused-vars': 'off',
    },
  },
];
