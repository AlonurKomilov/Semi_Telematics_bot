// Flat ESLint config for the 4truck system (operator) dashboard.
//
// Goal: catch the bugs `tsc --noEmit` doesn't (missing hook deps, hooks
// rules-of-hooks, unused vars, react-refresh boundaries) without forcing
// stylistic noise.  Type-aware rules are intentionally off — we already
// run `tsc --noEmit` for that.  Mirrors interfaces/miniapp/eslint.config.js
// and interfaces/dashboard/eslint.config.js; keep the three in sync when
// tuning rules.
//
// package.json's `lint` script pins --max-warnings=2 (not 0): one
// exhaustive-deps warning whose suggested fix risks an infinite-render
// loop without checking whether `load` is memoized upstream, and one
// react-refresh warning on an intentional multi-export module — see
// interfaces/dashboard/eslint.config.js's longer note on why these two
// categories get reviewed individually rather than swept.

import js from '@eslint/js';
import tsParser from '@typescript-eslint/parser';
import tsPlugin from '@typescript-eslint/eslint-plugin';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';

export default [
  {
    ignores: ['dist/**', 'node_modules/**', '*.config.ts', '*.config.js'],
  },
  js.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: 'module',
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.es2022,
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // tsc owns these:
      'no-unused-vars': 'off',
      'no-undef': 'off',
      'no-redeclare': 'off',

      // Real bugs:
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
      'no-empty': ['warn', { allowEmptyCatch: true }],
      'prefer-const': 'warn',
    },
  },
];
