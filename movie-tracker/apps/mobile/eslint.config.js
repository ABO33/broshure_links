const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ['dist/**', '.expo/**'],
    rules: {
      'import/order': ['warn', { alphabetize: { order: 'asc' }, 'newlines-between': 'always' }],
    },
  },
]);
