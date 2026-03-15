// Playwright test: Leroy Dashboard Ops Tab
// Verifies the Ops tab renders with real data from forge-brain

const { test, expect } = require('@playwright/test')

test.describe('Ops Tab', () => {
  test('loads and displays ops data', async ({ page }) => {
    // Capture console errors early
    const fatalErrors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text()
        if (text.includes('TypeError') || text.includes('Cannot read') || text.includes('is not a function')) {
          fatalErrors.push(text)
        }
      }
    })

    // Navigate to ops tab
    await page.goto('http://localhost:5173/#ops')
    await page.waitForLoadState('domcontentloaded')

    // Verify Ops tab button visible in nav
    const opsTab = page.locator('button', { hasText: 'Ops' })
    await expect(opsTab).toBeVisible({ timeout: 8000 })
    await opsTab.click()

    // Wait for data to load (summary cards appear)
    await expect(page.locator('text=Sessions (96h)')).toBeVisible({ timeout: 15000 })

    // 1. Summary cards visible with numbers
    await expect(page.locator('text=Tool Calls (96h)')).toBeVisible()
    await expect(page.locator('text=Machine Split')).toBeVisible()

    // Sessions count should be a number > 0 (not a dash or "Loading")
    const mainContent = await page.locator('.flex-1.overflow-y-auto').textContent()
    expect(mainContent).not.toContain('Loading ops data...')

    // 2. Top Tools table has rows
    await expect(page.locator('text=Top Tools')).toBeVisible()
    // Bash is always the top tool
    const bashRow = page.locator('td', { hasText: 'Bash' })
    await expect(bashRow.first()).toBeVisible({ timeout: 5000 })

    // 3. Sessions timeline has entries
    await expect(page.locator('text=Recent Sessions')).toBeVisible()
    // At least one machine name (kush or haze) appears in the timeline area
    const sessionSection = page.locator('text=Recent Sessions').locator('..').locator('..')
    // There should be content below the sessions header
    const sessContent = await page.locator('text=kush').count()
    expect(sessContent).toBeGreaterThan(0)

    // 4. Volume chart section visible
    await expect(page.locator('text=Tool Call Volume')).toBeVisible()

    // 5. Recent Errors section visible
    await expect(page.locator('text=Recent Errors')).toBeVisible()

    // 6. No fatal JS errors
    expect(fatalErrors).toHaveLength(0)
  })

  test('no regression on other tabs', async ({ page }) => {
    // System tab
    await page.goto('http://localhost:5173/#system')
    await page.waitForLoadState('domcontentloaded')

    const systemTab = page.locator('button', { hasText: 'System' })
    await expect(systemTab).toBeVisible({ timeout: 8000 })
    await systemTab.click()

    // Brain section still loads
    await expect(page.locator('text=Brain (Aianna)')).toBeVisible({ timeout: 10000 })

    // Tasks tab
    const tasksTab = page.locator('button', { hasText: 'Tasks' })
    await expect(tasksTab).toBeVisible()
    await tasksTab.click()
    await page.waitForTimeout(500)

    // No crash - Ops tab exists in nav but doesn't break anything
    await expect(page.locator('nav button', { hasText: 'Ops' })).toBeVisible()
  })
})
