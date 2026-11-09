// Run with Playwright installed: PANEL_TEST_URL=http://127.0.0.1:8765 node tests/test_layout_browser.cjs
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
  try {
    for (const [width, rail] of [[1520, false], [1280, true], [390, false]]) {
      const page = await browser.newPage({viewport: {width, height: 940}, colorScheme: 'dark'});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('https://fonts.googleapis.com/**', route => route.abort());
      if (rail) await page.addInitScript(() => localStorage.setItem('ui.nav', 'rail'));
      await page.goto(process.env.PANEL_TEST_URL || 'http://127.0.0.1:8765/');
      await page.locator('#tab-overview').waitFor({state: 'visible'});
      const layout = await page.evaluate(() => {
        const rect = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
        return {aside: rect('aside'), main: rect('#tab-overview'),
          bar: getComputedStyle(document.querySelector('.mbar')).display,
          icon: rect('.mbar svg'), overflow: document.documentElement.scrollWidth > innerWidth};
      });
      assert.deepEqual(errors, [], 'page JavaScript errors');
      assert.equal(layout.overflow, false, `horizontal overflow at ${width}`);
      if (width > 760) {
        assert.equal(layout.bar, 'none', 'mobile navigation must be hidden on desktop');
        assert(layout.aside.width < 300, 'sidebar must not fill the desktop');
        assert(layout.main.x >= layout.aside.right, 'content must sit beside the sidebar');
        assert(layout.main.y < 100, 'content must start near the top');
      } else {
        assert.equal(layout.bar, 'grid', 'mobile navigation must be visible');
        assert(layout.icon.width < 60 && layout.icon.height < 40, 'mobile icons must be bounded');
        await page.locator('#mbar-more').click();
        await page.locator('body.nav-open').waitFor();
        assert.equal(await page.locator('nav.side').isVisible(), true);
      }
      await page.close();
    }
    console.log('Desktop, sidebar and mobile layout passed; no page JavaScript errors');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
