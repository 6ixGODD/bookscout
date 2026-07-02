import css from '@eslint/css';
import js from '@eslint/js';
import json from '@eslint/json';
import markdown from '@eslint/markdown';
import pluginNext from '@next/eslint-plugin-next';
import pluginReact from 'eslint-plugin-react';
import pluginReactHooks from 'eslint-plugin-react-hooks';
import simpleImportSort from 'eslint-plugin-simple-import-sort';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default [
    // ========================================================================
    // Ignore patterns
    // ========================================================================
    {
        ignores: [
            '**/node_modules/**',
            '**/.next/**',
            '**/out/**',
            '**/dist/**',
            '**/build/**',
            '**/.turbo/**',
            '**/coverage/**',
            '**/__pycache__/**',
            '**/*.min.js',
            '**/public/**',
            'examples/**',
            'docs/reference/**',
            'deps/**',
            'data/**',
            'venv/**',
            '.venv/**',
            '**/package-lock.json',
            '.codepen/**',
            '**/openapi.json',
        ],
    },

    // ========================================================================
    // JavaScript/TypeScript files
    // ========================================================================
    {
        files: ['**/*.{js,mjs,cjs,ts,mts,cts,jsx,tsx}'],
        languageOptions: {
            ecmaVersion: 'latest',
            sourceType: 'module',
            globals: {
                ...globals.browser,
                ...globals.node,
                ...globals.es2021,
            },
            parserOptions: {
                ecmaFeatures: {
                    jsx: true,
                },
            },
        },
        plugins: {
            '@typescript-eslint': tseslint.plugin,
            'simple-import-sort': simpleImportSort,
        },
        rules: {
            ...js.configs.recommended.rules,
            // Customize rules here
            'no-console': ['warn', { allow: ['warn', 'error'] }],
            'no-unused-vars': 'off', // Handled by TypeScript
            'simple-import-sort/imports': 'error',
            'simple-import-sort/exports': 'error',
        },
    },

    // ========================================================================
    // TypeScript specific
    // ========================================================================
    ...tseslint.configs.recommended.map((config) => ({
        ...config,
        files: ['**/*.{ts,tsx,mts,cts}'],
    })),
    {
        files: ['**/*.{ts,tsx,mts,cts}'],
        rules: {
            '@typescript-eslint/no-unused-vars': [
                'warn',
                {
                    argsIgnorePattern: '^_',
                    varsIgnorePattern: '^_',
                },
            ],
            '@typescript-eslint/no-explicit-any': 'warn',
            '@typescript-eslint/explicit-module-boundary-types': 'off',
        },
    },

    // ========================================================================
    // React
    // ========================================================================
    {
        files: ['**/*.{jsx,tsx}'],
        plugins: {
            react: pluginReact,
            'react-hooks': pluginReactHooks,
        },
        settings: {
            react: {
                version: 'detect',
            },
        },
        rules: {
            ...pluginReact.configs.recommended.rules,
            ...pluginReact.configs['jsx-runtime'].rules,
            ...pluginReactHooks.configs.recommended.rules,
            'react/react-in-jsx-scope': 'off', // Not needed in Next.js
            'react/prop-types': 'off', // Using TypeScript
        },
    },

    // ========================================================================
    // Next.js specific
    // ========================================================================
    {
        files: ['**/*.{js,jsx,ts,tsx}'],
        plugins: {
            '@next/next': pluginNext,
        },
        rules: {
            ...pluginNext.configs.recommended.rules,
            ...pluginNext.configs['core-web-vitals'].rules,
        },
    },

    // ========================================================================
    // JSON files
    // ========================================================================
    {
        files: ['**/*.json'],
        ignores: ['package-lock.json', 'pnpm-lock.json', 'yarn.lock'],
        language: 'json/json',
        plugins: {
            json,
        },
        rules: {
            ...json.configs.recommended.rules,
        },
    },
    {
        files: ['**/*.jsonc', '.vscode/*.json'],
        language: 'json/jsonc',
        plugins: {
            json,
        },
        rules: {
            ...json.configs.recommended.rules,
        },
    },

    // ========================================================================
    // Markdown files
    // ========================================================================
    {
        files: ['**/*.md'],
        language: 'markdown/commonmark',
        plugins: {
            markdown,
        },
        rules: Object.assign({}, ...markdown.configs.recommended.map((config) => config.rules)),
    },

    // ========================================================================
    // CSS files
    // ========================================================================
    {
        files: ['**/*.css'],
        language: 'css/css',
        plugins: {
            css,
        },
        rules: {
            ...css.configs.recommended.rules,
        },
    },

    // ========================================================================
    // Config files - more lenient rules
    // ========================================================================
    {
        files: [
            '*.config.{js,mjs,cjs,ts}',
            '**/next.config.{js,mjs}',
            '**/postcss.config.{js,mjs}',
            '**/tailwind.config.{js,ts}',
        ],
        rules: {
            '@typescript-eslint/no-require-imports': 'off',
            'no-console': 'off',
        },
    },
];
