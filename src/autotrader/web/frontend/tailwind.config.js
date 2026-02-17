/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#f0f9ff',
          100: '#e0f2fe',
          200: '#bae6fd',
          300: '#7dd3fc',
          400: '#38bdf8',
          500: '#0ea5e9',
          600: '#0284c7',
          700: '#0369a1',
          800: '#075985',
          900: '#0c4a6e',
        },
        buy: '#22c55e',
        sell: '#ef4444',
        hold: '#6b7280',
        confidence: {
          high: '#22c55e',
          medium: '#f59e0b',
          low: '#ef4444',
        },
      },
      animation: {
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
        'pnl-flash-green': 'pnlFlashGreen 0.6s ease-out',
        'pnl-flash-red': 'pnlFlashRed 0.6s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pnlFlashGreen: {
          '0%': { backgroundColor: 'rgb(34 197 94 / 0.2)' },
          '100%': { backgroundColor: 'transparent' },
        },
        pnlFlashRed: {
          '0%': { backgroundColor: 'rgb(239 68 68 / 0.2)' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
    },
  },
  plugins: [],
}
