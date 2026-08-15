"""
Selector kesif modu.

Siteler (ozellikle BUX/Midas gibi SPA'lar) DOM'unu sik degistirir ve
bu selector'lari uzaktan tahmin etmek guvenilir degil. Bu modul,
hedef sayfayi ACIK OTURUMLA yukleyip:
  - tam HTML'i discovery/<site>_<ts>.html olarak kaydeder
  - ekran goruntusu alir
  - tablo/liste benzeri tekrarlayan yapilari ve data-testid'leri listeler
boylece selectors.yaml'i saniyeler icinde doldurabilirsin.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from .session import BrowserSession

log = logging.getLogger(__name__)

_PROBE_JS = r"""
() => {
  const out = { testids: [], repeated: [], tables: [], headings: [] };

  // 1) data-testid / data-cy / aria-label gibi stabil kancalar
  document.querySelectorAll('[data-testid],[data-test],[data-cy],[data-qa]').forEach(el => {
    const k = el.getAttribute('data-testid') || el.getAttribute('data-test')
           || el.getAttribute('data-cy') || el.getAttribute('data-qa');
    out.testids.push({ attr: k, tag: el.tagName.toLowerCase(),
                       text: (el.innerText || '').trim().slice(0, 80) });
  });

  // 2) Ayni class imzasindan >=3 kardes -> muhtemelen liste satiri
  const bySig = new Map();
  document.querySelectorAll('div,li,tr,article,section').forEach(el => {
    const cls = (el.className && typeof el.className === 'string')
      ? el.className.trim().split(/\s+/).slice(0, 4).join('.') : '';
    if (!cls) return;
    const sig = el.tagName.toLowerCase() + '.' + cls;
    if (!bySig.has(sig)) bySig.set(sig, []);
    bySig.get(sig).push(el);
  });
  [...bySig.entries()]
    .filter(([, els]) => els.length >= 3)
    .sort((a, b) => b[1].length - a[1].length)
    .slice(0, 25)
    .forEach(([sig, els]) => out.repeated.push({
      selector: sig, count: els.length,
      sample: (els[0].innerText || '').trim().slice(0, 160)
    }));

  // 3) Tablolar
  document.querySelectorAll('table').forEach((t, i) => {
    const head = [...t.querySelectorAll('th')].map(h => h.innerText.trim()).slice(0, 12);
    out.tables.push({ index: i, headers: head, rows: t.querySelectorAll('tbody tr').length });
  });

  // 4) Basliklar - sayfayi tanimak icin
  document.querySelectorAll('h1,h2,h3').forEach(h =>
    out.headings.push((h.innerText || '').trim().slice(0, 100)));

  return out;
}
"""


def discover_site(settings, site: str, url: str | None = None) -> dict:
    target = url or settings.sel(f"{site}.portfolio_url") or settings.sel(f"{site}.url") \
        or settings.get(f"sources.{site}.base_url")
    if not target:
        raise SystemExit(f"'{site}' icin URL bulunamadi.")

    outdir = settings.discovery_dir
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    with BrowserSession(settings, headless=False) as bs:
        with bs.page(target) as pg:
            log.info("Yukleniyor: %s", target)
            try:
                pg.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                log.warning("networkidle beklenemedi, mevcut DOM ile devam.")
            pg.wait_for_timeout(2500)  # SPA render payi

            html = pg.content()
            (outdir / f"{site}_{ts}.html").write_text(html, encoding="utf-8")
            pg.screenshot(path=str(outdir / f"{site}_{ts}.png"), full_page=True)
            probe = pg.evaluate(_PROBE_JS)

    (outdir / f"{site}_{ts}.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n  Kesif dosyalari: {outdir}/{site}_{ts}.[html|png|json]\n")
    print("  --- Basliklar ---")
    for h in probe["headings"][:8]:
        print("   ·", h)
    print("\n  --- Aday satir selector'lari (tekrar sayisina gore) ---")
    for r in probe["repeated"][:10]:
        print(f"   {r['count']:>3}x  {r['selector']}")
        if r["sample"]:
            print(f"        ornek: {r['sample'][:90]!r}")
    if probe["testids"]:
        print("\n  --- data-testid kancalari (en stabil secim) ---")
        seen = set()
        for t in probe["testids"]:
            if t["attr"] in seen:
                continue
            seen.add(t["attr"])
            print(f"   [data-testid='{t['attr']}']  <{t['tag']}>  {t['text'][:60]!r}")
            if len(seen) >= 20:
                break
    print("\n  Bunlari config/selectors.yaml icine yapistir.\n")
    return probe
