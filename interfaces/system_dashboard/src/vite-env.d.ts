/// <reference types="vite/client" />

// Tell TypeScript that side-effect CSS imports are fine — Vite handles
// them at build time.  Without this the bundler works but ``tsc
// --noEmit`` complains.
declare module '*.css';
