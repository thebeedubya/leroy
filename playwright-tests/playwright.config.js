const { defineConfig } = require('@playwright/test')

module.exports = defineConfig({
  testDir: '.',
  timeout: 30000,
  use: {
    headless: true,
    baseURL: 'http://localhost:5173',
  },
  reporter: [['list'], ['json', { outputFile: '../test-results/ops-tab.json' }]],
})
