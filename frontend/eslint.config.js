import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      // ── THE RULE THAT WOULD HAVE CAUGHT A LIVE CRASH ──────────────────────
      // A reordering in BoardTab left `pages` reading `groups` above its own
      // `const`, which is a temporal dead zone error. The BUILD PASSED — it is
      // legal syntax and only throws when the component runs — so the whole app
      // shipped showing "can't access lexical declaration 'w' before
      // initialization" and nothing else.
      //
      // js.configs.recommended does not include this. `functions: false` keeps
      // hoisted function declarations legal, which is how every component file
      // here is written; it is the const/let case that breaks.
      'no-use-before-define': ['error', {
        functions: false, classes: true, variables: true,
      }],
    },
  },
])
