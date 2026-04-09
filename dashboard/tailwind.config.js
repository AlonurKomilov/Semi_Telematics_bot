/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: { 50: '#eef7ff', 100: '#d9edff', 200: '#bce0ff', 300: '#8eccff', 400: '#59b0ff', 500: '#338cff', 600: '#1a6bf5', 700: '#1355e1', 800: '#1646b6', 900: '#183d8f', 500: '#338cff' },
      },
    },
  },
  plugins: [],
};
