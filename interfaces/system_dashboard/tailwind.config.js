/** @type {import('tailwindcss').Config} */
export default {
  // Match against the operator app's source only.  Kept tight so the
  // generated CSS stays small for a tool used by ~3 people.
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'media',  // follow OS preference — no theme picker in MVP
  theme: {
    extend: {
      colors: {
        // Operator UI uses a neutral palette plus a single accent.
        // No brand colors — we want it to feel like a tool, not a
        // marketing page.
        accent:  '#3b82f6',
        danger:  '#ef4444',
        warn:    '#f59e0b',
        ok:      '#10b981',
      },
    },
  },
  plugins: [],
};
