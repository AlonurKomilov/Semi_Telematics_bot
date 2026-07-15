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
      // Native title= renders the browser's unthemed, delayed tooltip and
      // is invisible on touch — use <Tip> from components/tooltip (the
      // hover-info SSOT, same idea as DataGrid for tables).  Warn-level:
      // legacy usages migrate opportunistically as files are touched.
      'no-restricted-syntax': ['warn', {
        // Lowercase (DOM) elements only — <PageHeader title=…> and other
        // component props legitimately named "title" are not tooltips.
        selector: "JSXOpeningElement[name.name=/^[a-z]/] > JSXAttribute[name.name='title']",
        message: 'Use <Tip label="…"> from components/tooltip instead of the native title= tooltip (unthemed, delayed, no touch support).',
      }, {
        // Icon sizes are a scale (design.md §7): 12 · 14 · 16 · 18 · 20 · 24.
        // Off-step values fragment the visual rhythm one pixel at a time.
        // (Numeric equality — esquery regex only matches string values.)
        selector: "JSXAttribute[name.name='size'] JSXExpressionContainer > Literal:matches([value=10],[value=11],[value=13],[value=15],[value=17],[value=19],[value=21],[value=22],[value=23])",
        message: 'Off-step icon size — use the design.md §7 scale: 12 · 14 · 16 · 18 · 20 · 24.',
      }, {
        // Z-index is a ladder (design.md §7): 0–20 content · 30 sticky ·
        // 40 panels · 50 floating UI · z-[60] above-dialog · z-[100]
        // maintenance blocker.  Arbitrary values outside the ladder cause
        // stacking bugs.  Leaflet-pane-matching values (400/500/650/1000/
        // 2000/2100) are allowed for map components — keep them commented.
        selector: "JSXAttribute[name.name='className'] Literal[value=/z-\\[(?!60\\]|100\\]|400\\]|500\\]|650\\]|1000\\]|2000\\]|2100\\])/]",
        message: 'Arbitrary z-index outside the design.md §7 ladder (0–20/30/40/50, z-[60] above-dialog, z-[100] blocker; Leaflet pane values for maps).',
      }, {
        // Same z-ladder rule for template-literal classNames.
        selector: "JSXAttribute[name.name='className'] TemplateElement[value.raw=/z-\\[(?!60\\]|100\\]|400\\]|500\\]|650\\]|1000\\]|2000\\]|2100\\])/]",
        message: 'Arbitrary z-index outside the design.md §7 ladder (0–20/30/40/50, z-[60] above-dialog, z-[100] blocker; Leaflet pane values for maps).',
      }],

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
