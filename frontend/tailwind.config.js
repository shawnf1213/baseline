/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        condensed: ['"Barlow Condensed"', 'sans-serif'],
        sans: ['Barlow', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
