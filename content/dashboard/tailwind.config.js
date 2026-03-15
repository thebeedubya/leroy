/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,jsx,ts,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Cascadia Code', 'ui-monospace', 'monospace'],
      },
      colors: {
        forge: {
          bg: '#080b12',
          surface: '#0f1420',
          card: '#141928',
          border: '#1e2535',
          muted: '#2a3347',
        },
      },
    },
  },
  plugins: [],
}
