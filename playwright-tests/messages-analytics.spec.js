// Playwright test: Leroy Dashboard Messages + Analytics Tabs
// Verifies both new tabs render without errors and display data

const { test, expect } = require('@playwright/test')

test.describe('Messages Tab', () => {
  test('loads and displays mailbox sidebar and messages', async ({ page }) => {
    const fatalErrors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text()
        if (text.includes('TypeError') || text.includes('Cannot read') || text.includes('is not a function')) {
          fatalErrors.push(text)
        }
      }
    })

    await page.goto('/#messages')
    await page.waitForLoadState('domcontentloaded')

    // Verify Messages tab button visible in nav
    const messagesTab = page.locator('button', { hasText: 'Messages' })
    await expect(messagesTab).toBeVisible({ timeout: 8000 })
    await messagesTab.click()

    // Wait for mailbox sidebar to render
    await expect(page.locator('text=Mailboxes')).toBeVisible({ timeout: 10000 })

    // "All" and "Needs Response" filter buttons should exist
    await expect(page.locator('button', { hasText: /All \(/ })).toBeVisible()
    await expect(page.locator('button', { hasText: /Needs Response/ })).toBeVisible()

    // Should show agent names in sidebar (at least pm and ops exist from bus messages)
    const sidebar = page.locator('.w-48')
    await expect(sidebar).toBeVisible()

    // Message list area should be present (either messages or "No messages" text)
    const mainArea = page.locator('.flex-1.overflow-y-auto')
    await expect(mainArea).toBeVisible()
    const content = await mainArea.textContent()
    expect(content.length).toBeGreaterThan(0)

    // Click "Needs Response" filter
    await page.locator('button', { hasText: /Needs Response/ }).click()
    await page.waitForTimeout(500)

    // No fatal JS errors
    expect(fatalErrors).toEqual([])
  })

  test('can filter by agent inbox', async ({ page }) => {
    await page.goto('/#messages')
    await page.waitForLoadState('domcontentloaded')

    // Wait for sidebar
    await expect(page.locator('text=Mailboxes')).toBeVisible({ timeout: 10000 })

    // Agent buttons appear after the divider in sidebar
    // They have "flex justify-between" class unlike the filter buttons
    const agentButtons = page.locator('.w-48 .border-t ~ button')
    const count = await agentButtons.count()
    if (count > 0) {
      const firstAgent = agentButtons.first()
      await firstAgent.click()
      await page.waitForTimeout(500)
      // After clicking an agent, header should show "<agent> inbox"
      await expect(page.locator('h2:has-text("inbox")')).toBeVisible()
    }
  })
})

test.describe('Analytics Tab', () => {
  test('loads and displays plan report with v1 baseline', async ({ page }) => {
    const fatalErrors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text()
        if (text.includes('TypeError') || text.includes('Cannot read') || text.includes('is not a function')) {
          fatalErrors.push(text)
        }
      }
    })

    await page.goto('/#analytics')
    await page.waitForLoadState('domcontentloaded')

    // Verify Analytics tab button visible in nav
    const analyticsTab = page.locator('button', { hasText: 'Analytics' })
    await expect(analyticsTab).toBeVisible({ timeout: 8000 })
    await analyticsTab.click()

    // Wait for plan report section to render
    await expect(page.locator('text=Plan Report')).toBeVisible({ timeout: 10000 })

    // v1 Baseline stat card should show (we migrated 179 v1 plans)
    await expect(page.locator('text=v1 Baseline')).toBeVisible()

    // v2 Plans card should exist (may be 0)
    await expect(page.getByText('v2 Plans', { exact: true })).toBeVisible()

    // Total Plans card
    await expect(page.locator('text=Total Plans')).toBeVisible()

    // v2 Pipeline Analytics header
    await expect(page.locator('text=v2 Pipeline Analytics')).toBeVisible()

    // No fatal JS errors
    expect(fatalErrors).toEqual([])
  })

  test('displays subsystem health section', async ({ page }) => {
    await page.goto('/#analytics')
    await page.waitForLoadState('domcontentloaded')

    await expect(page.locator('text=Subsystem Health')).toBeVisible({ timeout: 10000 })
  })

  test('displays cost breakdown section', async ({ page }) => {
    await page.goto('/#analytics')
    await page.waitForLoadState('domcontentloaded')

    await expect(page.locator('text=Cost Breakdown')).toBeVisible({ timeout: 10000 })
  })

  test('analytics page loads without JS errors', async ({ page }) => {
    const fatalErrors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text()
        if (text.includes('TypeError') || text.includes('Cannot read') || text.includes('is not a function')) {
          fatalErrors.push(text)
        }
      }
    })

    await page.goto('/#analytics')
    await page.waitForLoadState('domcontentloaded')

    // Wait for analytics to fully render
    await expect(page.locator('text=v2 Pipeline Analytics')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('text=Plan Report')).toBeVisible()

    // If v2 plans exist, Recent Plans table should render
    const recentPlans = page.getByText('Recent Plans')
    if (await recentPlans.isVisible({ timeout: 2000 }).catch(() => false)) {
      await expect(page.locator('th', { hasText: 'Subject' })).toBeVisible()
    }

    expect(fatalErrors).toEqual([])
  })
})
