const path = require('path');
const { test, expect } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'laptop', width: 1024, height: 768 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'phone', width: 390, height: 844 },
];

for (const viewport of viewports) {
  test(`New Cut is usable without horizontal overflow on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/v2');
    await expect(page.getByRole('heading', { name: 'Start a new cut' })).toBeVisible();
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
    await expect(page.locator('.v2-button').first()).toHaveCSS('color', 'rgb(255, 255, 255)');
    await expect(page).toHaveScreenshot(`new-cut-${viewport.name}.png`, {
      fullPage: true,
      animations: 'disabled',
    });
  });
}

test('V1 remains available while V2 is isolated', async ({ page }) => {
  await page.goto('/v1');
  await expect(page).toHaveTitle('PCP');
  await page.goto('/v2');
  await expect(page).toHaveTitle('PCP V2');
  await expect(page.getByText('V2 Preview')).toBeVisible();
});

test('single SVG upload opens the exact guided workbench', async ({ page, request }) => {
  const fixture = path.join(__dirname, 'fixtures', 'ux-square.svg');
  await page.goto('/v2');
  await page.locator('input.dz-hidden-input').setInputFiles(fixture);
  await expect(page).toHaveURL(/\/v2\/workbench\?file=ux-square\.svg/);
  await expect(page.getByRole('heading', { name: /Cut workspace/i })).toBeVisible();
  await expect(page.locator('[data-workspace-phase="design"]')).toBeVisible();
  await expect(page.locator('#workspaceSvg')).toBeVisible();
  await expect(page.locator('#workspaceCutsLayer path').first()).toBeVisible();
  await expect(page).toHaveScreenshot('workbench-design-desktop.png', {
    animations: 'disabled',
  });
  await page.locator('[data-workspace-phase="cut"]').click();
  await expect(page.getByRole('heading', { name: 'Cut', exact: true })).toBeVisible();
  await expect(page.locator('#v2Readiness')).toContainText('Exact preview');
  await page.locator('#workspaceGenerate').click();
  await expect(page.locator('#v2GeneratedOutput')).toContainText('.hpgl');
  const generatedName = await page.locator('#fileName').inputValue();
  expect(generatedName).toMatch(/\.hpgl$/i);
  await expect(page.locator('#v2SendCut')).toBeDisabled();
  await page.locator('[data-workspace-phase="design"]').click();
  await page.locator('#workspaceWidth').fill('38.0');
  await page.locator('#workspaceWidth').dispatchEvent('change');
  await expect(page.locator('#v2GeneratedOutput')).toContainText('Workspace changed');
  await request.post('/delete_file', { data: { filename: generatedName } });
  await request.post('/delete_file', { data: { filename: 'ux-square.svg' } });
});

test('multiple SVGs form one deterministic import tray while HPGL stays separate', async ({ page, request }) => {
  const square = path.join(__dirname, 'fixtures', 'ux-square.svg');
  const circle = path.join(__dirname, 'fixtures', 'ux-circle.svg');
  const hpgl = path.join(__dirname, 'fixtures', 'ux-exact.hpgl');
  await page.goto('/v2');
  await page.locator('input.dz-hidden-input').setInputFiles([square, circle, hpgl]);
  await expect(page.locator('#v2ImportTray')).toContainText('2 SVG files can be arranged together');
  await expect(page.locator('#v2ImportTray')).toContainText('1 HPGL file remain separate');
  await page.getByRole('button', { name: 'Create SVG layout' }).click();
  await expect(page).toHaveURL(/\/v2\/workbench/);
  await expect(page.locator('.workspace-design-row')).toHaveCount(2);
  await expect(page.locator('#workspaceReadOnly')).toBeHidden();
  for (const filename of ['ux-square.svg', 'ux-circle.svg', 'ux-exact.hpgl']) {
    await request.post('/delete_file', { data: { filename } });
  }
});

test('uploaded HPGL opens complete, exact, and read-only', async ({ page, request }) => {
  const fixture = path.join(__dirname, 'fixtures', 'ux-exact.hpgl');
  await page.goto('/v2');
  await page.locator('input.dz-hidden-input').setInputFiles(fixture);
  await expect(page).toHaveURL(/\/v2\/workbench\?file=ux-exact\.hpgl/);
  await expect(page.locator('#workspaceReadOnly')).toBeVisible();
  await expect(page.locator('#workspaceReadOnly')).toContainText('exact and read-only');
  await expect(page.locator('#workspaceProgress')).toHaveValue('1000');
  await expect(page.locator('#workspaceProgressLabel')).toHaveText('100%');
  expect(await page.locator('#workspaceCutsLayer path').count()).toBeGreaterThanOrEqual(2);
  await expect(page.locator('#workspaceGenerate')).toBeHidden();
  await request.post('/delete_file', { data: { filename: 'ux-exact.hpgl' } });
});

test('new SVG preparation defaults to safe simplification and throughput preflight', async ({ page, request }) => {
  const fixture = path.join(__dirname, 'fixtures', 'ux-circle.svg');
  await page.goto('/v2');
  await page.locator('input.dz-hidden-input').setInputFiles(fixture);
  await expect(page).toHaveURL(/\/v2\/workbench\?file=ux-circle\.svg/);
  await page.locator('[data-workspace-phase="prepare"]').click();
  await expect(page.locator('#workspaceSimplify')).toBeChecked();
  await expect(page.locator('#workspaceSimplifyTolerance')).toHaveValue('0.05');
  await expect(page.locator('#workspaceSimplifyTolerance')).toHaveAttribute('max', '0.1');
  await expect(page.locator('#workspaceOperatorSpeed')).toHaveValue('50');
  await expect(page.locator('#workspacePreflightStats')).toContainText('9600-baud throughput');
  await expect(page.locator('#workspacePreflightStats')).toContainText('Maximum simplification deviation: 0.050 mm');
  await request.post('/delete_file', { data: { filename: 'ux-circle.svg' } });
});

test('critical accessibility checks pass on the production shell', async ({ page }) => {
  await page.goto('/v2');
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  expect(results.violations).toEqual([]);
});
