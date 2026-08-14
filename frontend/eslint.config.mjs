/**
 * Flat config, built from the plugins directly.
 *
 * `eslint-config-next` is deliberately not used: it loads
 * `@rushstack/eslint-patch`, which fails against ESLint 9 with "Failed to patch
 * ESLint because the calling module was not recognized". `next lint` is also
 * deprecated and removed in Next 16, so we call the ESLint CLI instead.
 *
 * The rule sets below are the same ones `next/core-web-vitals` pulls in.
 */

import nextPlugin from '@next/eslint-plugin-next'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import tsParser from '@typescript-eslint/parser'
import reactPlugin from 'eslint-plugin-react'
import hooksPlugin from 'eslint-plugin-react-hooks'

export default [
  {
    ignores: [
      '.next/**',
      'node_modules/**',
      'next-env.d.ts',
      // Generated from openapi.json — lint the contract, not its output.
      'src/lib/api/generated.ts',
    ],
  },
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
      react: reactPlugin,
      'react-hooks': hooksPlugin,
      '@next/next': nextPlugin,
    },
    settings: {
      react: { version: 'detect' },
    },
    rules: {
      ...tsPlugin.configs['flat/recommended'].reduce(
        (acc, c) => ({ ...acc, ...(c.rules ?? {}) }),
        {},
      ),
      ...reactPlugin.configs.flat['jsx-runtime'].rules,
      ...hooksPlugin.configs.recommended.rules,
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs['core-web-vitals'].rules,

      // `_unused` is the conventional way to say "required by the signature,
      // deliberately ignored" — most often a React prop or a route param.
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
]
