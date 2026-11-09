// Exporta docs/presentacion.html a docs/presentacion.pdf (1920×1080 por diapositiva)
// y saca el diagrama de arquitectura a assets/img/arquitectura.png.
// Cada diapositiva va al PDF como imagen JPEG: los brillos y degradados del estilo HUD,
// en vectorial, hacían un PDF de 9–13 MB; así pesa unos 3 MB y se ve igual en GitHub.
// Requisitos: npm i playwright  (usa el Google Chrome instalado: channel 'chrome')
// Uso: node scripts/exportar-presentacion.cjs [--slides]   (--slides: también un PNG por diapositiva)
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');

const ROOT = path.resolve(__dirname, '..');
const HTML = pathToFileURL(path.join(ROOT, 'docs', 'presentacion.html')).href;
const SCALE = 1.5; // nitidez de cada diapositiva en el PDF (2880×1620)

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: SCALE });
  await page.goto(HTML, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);

  await page.locator('#arquitectura').screenshot({ path: path.join(ROOT, 'assets', 'img', 'arquitectura.png'), scale: 'css' });

  const slides = page.locator('section.slide');
  const count = await slides.count();
  const images = [];
  for (let i = 0; i < count; i++) {
    const jpeg = await slides.nth(i).screenshot({ type: 'jpeg', quality: 86 });
    images.push(jpeg.toString('base64'));
    if (process.argv.includes('--slides')) {
      const dir = path.join(ROOT, 'assets', 'img', 'slides');
      fs.mkdirSync(dir, { recursive: true });
      await slides.nth(i).screenshot({ path: path.join(dir, `slide-${String(i + 1).padStart(2, '0')}.png`), scale: 'css' });
    }
  }

  const deck = await browser.newPage();
  await deck.setContent(`<!doctype html><html><head><style>
    @page { size: 1920px 1080px; margin: 0 } * { margin: 0; padding: 0 }
    img { display: block; width: 1920px; height: 1080px; page-break-after: always }
  </style></head><body>${images.map((b) => `<img src="data:image/jpeg;base64,${b}">`).join('')}</body></html>`);
  await deck.pdf({
    path: path.join(ROOT, 'docs', 'presentacion.pdf'),
    width: '1920px', height: '1080px', printBackground: true, preferCSSPageSize: true,
  });
  await browser.close();
  console.log(`ok: presentacion.pdf (${count} diapositivas) y arquitectura.png`);
})().catch((e) => { console.error(e); process.exit(1); });
