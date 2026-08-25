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
// package.json's `lint` script pins --max-warnings=192, NOT 0. The
// number is a RATCHET: it fails the moment a new warning appears,
// without demanding the backlog be cleared in one pass. Drive it down;
// never raise it.
//
// It was 52 for a long time, and the number stopped being true — the
// real count reached 267, so `npm run lint` had been exiting 1 on a
// clean checkout, and nothing ran it: CI type-checks, tests and builds
// the dashboard but has no lint step. A ratchet nobody pulls is a
// number, not a ratchet, so lint now runs in CI beside tsc.
//
// The 192 that remain, measured rather than estimated:
//   - no-restricted-syntax (88): every one is a native `title=`
//     attribute. These clear themselves as the <Tip> migration lands;
//     src/components/ui/chrome.test.ts tracks the same 34 files.
//   - react-refresh/only-export-components (34): fires on the
//     intentional shadcn/ui pattern (component + its variants/constant
//     from one module). "Fixing" it means splitting widely-used
//     primitives for an HMR nicety, not a bug.
//   - @typescript-eslint/no-unused-vars (34): what survived the dead-
//     import sweep — destructured props a component accepts but does
//     not read, and state left behind by a component split. Each needs
//     a judgement (delete it, or `_`-prefix it to say "deliberately
//     unused"), not a blind removal.
//   - react-hooks/exhaustive-deps (30): the plugin's suggested fix can
//     introduce infinite-render loops if the "missing" dep isn't
//     memoized upstream; each needs the component's actual behaviour
//     checked, not an automatic dep-array edit.

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
      }, {
        // A hand-rolled modal backdrop.  TWELVE of these existed, and
        // every one was missing the same four things: focus trap, Escape,
        // aria-modal, and a background scroll lock — so the page kept
        // scrolling under the open panel and Tab walked straight out of
        // it.  They are all gone; this keeps the thirteenth from being
        // written.  <Sheet> for a side drawer, <Dialog> for a centred
        // one; both are Base UI dialogs and bring all four.
        selector: "JSXAttribute[name.name='className'] Literal[value=/fixed inset-0[^\"]*bg-black\\//]",
        message: 'Hand-rolled modal backdrop — use <Sheet> (side drawer) or <Dialog> (centred) from components/ui. A bare backdrop has no focus trap, no Escape, no aria-modal and no background scroll lock.',
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

  // ── The layering chain: scrolling → datagrid → features ───────────
  //
  //   components/scrolling   owns HOW a surface scrolls
  //   components/datagrid    consumes it, and owns how a TABLE scrolls
  //   features/*             consume DataGrid — and reach scrolling
  //                          directly ONLY for their own panes
  //
  // The point of the chain is that a change to table scrolling is made
  // in ONE place and travels: scrolling → DataGrid → all 40 grids. A
  // feature that wires table machinery itself steps outside that, so
  // the next change reaches every grid except that one — silently.
  //
  // Only the three things that are ALWAYS wrong are banned here. The
  // general-purpose region contract (ScrollRegion / useScrollRegion) is
  // deliberately NOT restricted: a feature's own drawer or list pane is
  // exactly what it is for, and Chat + NotificationsPanel use it
  // correctly today. Banning the primitive that is usually right is how
  // a rule gets disabled wholesale — see scrolling/CLAUDE.md.
  {
    files: ['src/features/**/*.{ts,tsx}', 'src/pages/**/*.{ts,tsx}', 'src/shells/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [
          {
            group: ['**/components/scrolling/*'],
            message:
              'Import from the components/scrolling barrel, not a file inside it. '
              + 'The barrel is what lets the module move things around without a sweep.',
          },
          {
            group: ['**/components/scrolling'],
            importNames: [
              'ScrollbarH', 'ScrollbarV', 'useOverflow',
              'useWheelToHorizontal', 'useFittedHeight', 'HIDE_NATIVE_SCROLLBAR',
            ],
            message:
              'Table machinery — it belongs to whoever PAINTS a table, which is '
              + 'components/datagrid, not a feature. Needing it here means a grid is '
              + 'being hand-rolled: use <DataGrid>, and the behaviour arrives through it. '
              + 'ScrollRegion / useScrollRegion are unrestricted — those are for your own panes.',
          },
        ],
      }],
    },
  },

  // No cycle: the bottom of the chain must not know about the top.
  {
    files: ['src/components/scrolling/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['**/datagrid', '**/datagrid/**'],
          message:
            'components/scrolling sits BELOW datagrid and must not import it. '
            + 'If something is needed in both, it belongs in scrolling and datagrid takes it.',
        }],
      }],
    },
  },
];
