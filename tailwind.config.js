import forms from '@tailwindcss/forms'
import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './app/templates/**/*.html',
    './app/static/js/**/*.js',
  ],
  safelist: [
    // Grid layout classes (built dynamically in Jinja macros via dicts)
    'grid-cols-1', 'grid-cols-2', 'grid-cols-3', 'grid-cols-4', 'grid-cols-5', 'grid-cols-6',
    'sm:grid-cols-2', 'lg:grid-cols-2', 'lg:grid-cols-3', 'lg:grid-cols-4',
    'xl:grid-cols-5', 'xl:grid-cols-6',
    'gap-2', 'gap-3', 'gap-4', 'gap-5', 'gap-6', 'gap-8',
    'mb-6', 'mb-8', 'mt-4', 'mt-6',
    // Page widths
    'max-w-4xl', 'max-w-7xl', 'max-w-[1800px]', 'mx-auto',
    'max-w-md', 'max-w-lg', 'max-w-xl', 'max-w-2xl', 'max-w-3xl', 'max-w-5xl',
    // Dynamic color classes (passed as variables in Jinja macros)
    'text-brand-300', 'text-brand-400', 'text-brand-500',
    'bg-brand-300', 'bg-brand-500', 'bg-brand-300/10', 'bg-brand-500/10', 'bg-brand-500/20',
    'border-brand-300/30', 'border-brand-400/30', 'border-brand-500/30',
    // Stat card dynamic colors (icon_box, stat_card use color param)
    'text-blue-400', 'text-green-400', 'text-amber-400', 'text-red-400',
    'text-purple-400', 'text-cyan-400', 'text-orange-400',
    'bg-blue-500/10', 'bg-green-500/10', 'bg-amber-500/10', 'bg-red-500/10',
    'bg-purple-500/10', 'bg-cyan-500/10', 'bg-orange-500/10',
    'border-blue-500/20', 'border-green-500/20', 'border-amber-500/20', 'border-red-500/20',
    'border-purple-500/20', 'border-orange-500/20',
    // Status badge colors
    'bg-green-100', 'text-green-700', 'bg-red-100', 'text-red-700',
    'bg-yellow-100', 'text-yellow-700', 'bg-blue-100', 'text-blue-700',
    'bg-gray-100', 'text-gray-700',
    // Semantic status (from colorffy)
    'text-success-400', 'text-success-500', 'bg-success-500/10', 'border-success-500/30',
    'text-warning-400', 'text-warning-500', 'bg-warning-500/10', 'border-warning-500/30',
    'text-danger-400', 'text-danger-500', 'bg-danger-500/10', 'border-danger-500/30',
    'text-info-400', 'text-info-500', 'bg-info-500/10', 'border-info-500/30',
    // Surface colors (custom, used in templates)
    'bg-surface-0', 'bg-surface-10', 'bg-surface-20',
    'border-surface-20',
  ],
  theme: {
    extend: {
      colors: {
        // LuxAnalytics Brand: Orange
        // From colorffy palette — warm, flat, high-contrast on dark
        brand: {
          50:  '#FFF7ED',
          100: '#FFEDD5',
          200: '#FFD9A8',
          300: '#ffb952',        // a20 — light accent
          400: '#ffaf35',        // a10 — hover / secondary
          500: '#ffa500',        // a0  — PRIMARY hero color
          600: '#E08E00',        // pressed state
          700: '#B37200',        // dark text on light bg
          800: '#8A5800',
          900: '#6B4400',
          DEFAULT: '#ffa500',
        },

        // Dark surfaces — warm-tinted from colorffy
        surface: {
          0:   '#121212',        // a0  — deepest background
          10:  '#282828',        // a10 — elevated card
          20:  '#3f3f3f',        // a20 — borders, dividers
          // Tonal (warm orange-tinted surfaces for panels/headers)
          't0':  '#271f16',      // tonal-a0
          't10': '#3c342b',      // tonal-a10
          't20': '#514a42',      // tonal-a20
        },

        // Semantic status colors — flat, from colorffy
        success: {
          400: '#47d5a6',        // a10 — text on dark
          500: '#22946e',        // a0  — badges, icons
          600: '#1a7358',        // pressed
          light: '#9ae8ce',      // a20 — light mode text
        },
        warning: {
          400: '#d7ac61',        // a10
          500: '#a87a2a',        // a0
          600: '#8A6422',
          light: '#ecd7b2',      // a20
        },
        danger: {
          400: '#d94a4a',        // a10
          500: '#9c2121',        // a0
          600: '#7E1A1A',
          light: '#eb9e9e',      // a20
        },
        info: {
          400: '#4077d1',        // a10
          500: '#21498a',        // a0
          600: '#1A3A6E',
          light: '#92b2e5',      // a20
        },
      },

      // Glow effects use brand orange
      boxShadow: {
        'glow':        '0 4px 20px rgba(255, 165, 0, 0.12)',
        'glow-md':     '0 8px 30px rgba(255, 165, 0, 0.18)',
        'glow-lg':     '0 8px 40px rgba(255, 165, 0, 0.25)',
        'glow-focus':  '0 0 0 3px rgba(255, 165, 0, 0.30)',
        'glow-success':'0 4px 20px rgba(34, 148, 110, 0.30)',
        'glow-warning':'0 4px 20px rgba(168, 122, 42, 0.30)',
        'glow-error':  '0 4px 20px rgba(156, 33, 33, 0.30)',
      },

      backgroundImage: {
        // Body gradient — warm dark
        'surface-gradient': 'linear-gradient(135deg, #121212 0%, #000000 50%, #121212 100%)',
        // Panel header — subtle orange tint
        'panel-header': 'linear-gradient(135deg, rgba(255, 165, 0, 0.08), rgba(255, 175, 53, 0.03))',
      },

      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      animation: {
        'spin-slow': 'spin 3s linear infinite',
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [
    forms,
    typography,
  ],
}
