// Run against a running panel with Playwright installed.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1520, height: 940}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/music', route => route.fulfill({json: {
      auth: {logged_in: false}, source: 'library', library_available: true,
      library: {phase: 'stopped', track: null},
      glagol: {connected: true, state: {playing: false}, updated: Date.now() / 1000}
    }}));
    await page.goto((process.env.PANEL_TEST_URL || 'http://127.0.0.1:8765/') + '#player');
    await page.locator('#tab-player').waitFor({state: 'visible'});
    await page.waitForFunction(() => document.body.dataset.musicSource === 'library');
    assert.equal(await page.locator('#pl-body').isVisible(), true);
    assert.equal(await page.locator('#pl-login').isVisible(), false);
    assert.equal(await page.locator('#pl-zvuk').isVisible(), false);
    assert.equal(await page.locator('#hook-warn').isVisible(), false);
    assert.equal(await page.locator('#hook-warn').evaluate(e => e.parentElement.id), 'tab-settings');
    assert.deepEqual(errors, []);
    console.log('Local player is visible without login; cloud form and boot banner are absent');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
