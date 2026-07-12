// Flat ESLint config for the 4truck dashboard.
//
// Goal: catch the bugs `tsc --noEmit` doesn't (missing hook deps, hooks
// rules-of-hooks, unused vars — this app's tsconfig has noUnusedLocals/
// noUnusedParameters OFF, so tsc lets those through entirely — and
// react-refresh boundaries) without forcing stylistic noise.  Type-aware
// rules are intentionally off — we already run `tsc --noEmit` for that.
// Mirrors interfaces/miniapp/eslint.config.js; keep the two in sync when
// tuning rules.
//
// package.json's `lint` script pins --max-warnings=52, NOT 0: this was
// the FIRST run on a pre-existing 358-file app, and the leftover 52 are
// two categories that need real review, not a blind sweep —
//   - react-hooks/exhaustive-deps (~23): the plugin's suggested fix can
//     introduce infinite-render loops if the "missing" dep isn't
//     memoized upstream; each needs the component's actual behavior
//     checked, not an automatic dep-array edit.
//   - react-refresh/only-export-components (~18): fires on the
//     intentional shadcn/ui pattern (component + its variants/constant
//     from one module); "fixing" it means splitting widely-used
//     primitives for an HMR nicety, not a bug.
// 52 is a ratchet, not a target: it fails the build the moment a NEW
// warning is introduced, without demanding the legacy backlog be
// cleared in one pass. Drive it down over time; don't raise it.

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
