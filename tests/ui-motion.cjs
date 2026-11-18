// Exercise the actual animated WebGL surface, not only its static DOM shell.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const output = process.env.UI_OUTPUT_DIR || '/tmp/nodeseek-ui';
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8765';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'no-preference', colorScheme: 'light'});
  const page = await context.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  async function login(target) {
    await target.goto(base);
    await target.locator('#password').fill('preview-only-password');
    await target.locator('#login-form [type=submit]').click();
    await target.locator('#dashboard').waitFor({state: 'visible'});
  }
  async function frame() { return Number(await page.locator('#orbit-canvas').getAttribute('data-frame')); }
  async function pixelCheck() {
    const value = await page.locator('#orbit-canvas').evaluate(canvas => {
      const gl = canvas.getContext('webgl2');
      if (!gl) return {colored: 0, total: 1};
      // The existing motion event renders synchronously, before the GPU buffer clears.
      document.dispatchEvent(new Event('nodeseek:motion'));
        const pixels = new Uint8Array(gl.drawingBufferWidth * gl.drawingBufferHeight * 4);
        gl.readPixels(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        let colored = 0;
        for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 20) colored++;
        return {colored, total: pixels.length / 4};
    });
    assert.ok(value.colored / value.total > .015, `Canvas must render visible geometry: ${JSON.stringify(value)}`);
  }
  try {
    await login(page);
    await page.waitForFunction(() => document.querySelector('#scene-layer').dataset.ready === 'true');
    await page.waitForFunction(() => document.querySelector('#orbit-canvas').dataset.motion === 'running');
    const first = await frame(); await pause(300); assert.ok(await frame() > first, 'The 3D scene advances');
    await pixelCheck();
    const canvas = page.locator('#orbit-canvas');
    const before = await canvas.screenshot();
    await page.mouse.move(1060, 390); await pause(700);
    const after = await canvas.screenshot();
    assert.notDeepEqual(before, after, 'Pointer interaction and orbital movement change rendered pixels');
    await page.screenshot({path: path.join(output, 'kinetic-desktop.png')});
    await page.locator('.app-header .motion-toggle').click();
    await page.waitForFunction(() => document.querySelector('#orbit-canvas').dataset.motion === 'paused');
    const stopped = await frame(); await pause(250); assert.equal(await frame(), stopped, 'Pause freezes render loop');
    await page.reload();
    await page.locator('#dashboard').waitFor({state: 'visible'});
    assert.equal(await page.locator('body').getAttribute('data-motion'), 'off');
    await page.locator('.app-header .motion-toggle').click();
    await page.waitForFunction(() => document.querySelector('#orbit-canvas').dataset.motion === 'running');
    await page.locator('.primary-nav [data-view=account]').click();
    await page.locator('#account').waitFor({state: 'visible'});
    await pause(450);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'page-title', 'Animated navigation restores heading focus');
    assert.equal(await page.locator('#orbit-canvas').getAttribute('data-motion'), 'paused', 'Hidden views stop rendering');
    await page.locator('.primary-nav [data-view=overview]').click();
    await page.locator('#overview').waitFor({state: 'visible'});
    await page.waitForFunction(() => document.querySelector('#orbit-canvas').dataset.motion === 'running');
    await page.route('**/api/status', route => route.fulfill({json: {running: true, next_run: null, last_result: null, scheduler_alive: true}}));
    await page.locator('#overview-refresh').click();
    await page.waitForFunction(() => document.querySelector('#orbit-canvas').dataset.phase === 'running');
    await page.screenshot({path: path.join(output, 'kinetic-running.png')});
    await page.unroute('**/api/status');
    await page.locator('#overview-refresh').click();
    for (const width of [390, 320, 768, 1024]) {
      await page.setViewportSize({width, height: 900});
      await page.locator('#scene-layer').scrollIntoViewIfNeeded();
      await pause(350);
      await pixelCheck();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `No horizontal overflow at ${width}`);
      const bounds = await canvas.boundingBox();
      assert.ok(bounds.width > 150 && bounds.height >= 240, `Scene keeps useful framing at ${width}`);
      await page.screenshot({path: path.join(output, `kinetic-${width}.png`)});
    }
    await page.emulateMedia({reducedMotion: 'reduce'});
    await page.waitForFunction(() => document.body.dataset.motion === 'off');
    const reduced = await frame(); await pause(250); assert.equal(await frame(), reduced, 'Reduced motion freezes scene');
    await page.emulateMedia({reducedMotion: 'no-preference'});
    await page.waitForFunction(() => document.body.dataset.motion === 'on');
    const fallback = await context.newPage();
    await fallback.addInitScript(() => {
      const getContext = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function(type, ...args) { return String(type).startsWith('webgl') ? null : getContext.call(this, type, ...args); };
    });
    await fallback.goto(base);
    await fallback.locator('#dashboard').waitFor({state: 'visible'});
    await fallback.waitForFunction(() => document.querySelector('#scene-layer').dataset.fallback === 'true');
    assert.equal(await fallback.locator('.scene-fallback').isVisible(), true);
    assert.equal(await fallback.locator('#run').isEnabled(), true, 'Core controls work without WebGL');
    await fallback.close();
    assert.deepEqual(errors, []);
    console.log('PASS: nonblank WebGL, moving pixels, pointer response, pause persistence, reduced motion, animated navigation focus, hidden-view suspension, status updates, mobile framing, no-WebGL fallback');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
