#!/usr/bin/env python
"""
Kaynak sayfasindan CANLI ekran goruntusu — AYRI SUREC olarak calisir.

NEDEN AYRI SUREC
----------------
Playwright bot surecine SOKULMAZ: bir cokme dinleyiciyi de dusururdu ve
launchd yeniden baslatana kadar mesajlara cevap veremezdik. Ayni gerekce
`veri_topla`'nin tarayicili collector'lari alt surecte calistirmasinin
gerekcesi.

NE ISE YARAR
------------
Grafik BIZIM veritabanimizin ne dusundugunu gosterir; bu ise KAYNAGIN ne
gosterdigini. Ikisi ayrilirsa veri hatasi vardir. Projedeki en buyuk hata
(17 pozisyonun 14'unde yanlis para birimi) tam olarak boyle bir
karsilastirmayla yakalanmisti — o zaman elle yapmistik, artik arac var.

KULLANIM
    python scripts/kaynak_goruntusu.py SEMBOL VENUE CIKTI_YOLU
Cikti: tek satir JSON (stdout) — {"ok":bool,"yol":str,"kaynak":str,...}
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK / "src"))

# Sembol -> hangi sayfada aranacak. Kaynak, enstrumanin GERCEKTEN islem
# gordugu yer olmali: BIST hissesini Yahoo'da aramak yanlis kotasyona
# gotururdu (RBOT vakasi).
SAYFALAR = {
    "BIST": ("https://www.getmidas.com/canli-borsa/", "tablo"),
    "BINANCE": ("https://www.binance.com/en/trade/{sembol}_USDT", "tam"),
    "BUX": ("https://finance.yahoo.com/quote/{sembol}/", "tam"),
    "INDEX": ("https://finance.yahoo.com/quote/{sembol}/", "tam"),
    # IBKR evreni ABD/global hisse (Borsa Istanbul IBKR'de yok — piyasa
    # verisi fiyat sayfasindaki dunya borsa listesinde Turkiye hic
    # gecmiyor, Bukres/Budapeste/Ljubljana gecerken). Yani BUX ile ayni
    # kaynak dogru: Yahoo.
    "IBKR": ("https://finance.yahoo.com/quote/{sembol}/", "tam"),
}


def _cerez_bandini_kapat(pg) -> bool:
    """
    Cerez bandi sayfayi KILITLEMIYOR ama alt tarafta SABIT durup tablo
    satirlarini ortuyor. Playwright, bandin arkasina denk gelen elementi
    "gorunur degil" sayiyor ve `scroll_into_view_if_needed` zaman asimina
    ugruyordu (olculdu: THYAO'da 30 sn timeout).
    """
    for sec in ("button:has-text('Onayla')", "button:has-text('Kabul')",
                "[class*=cookie] button", "[class*=consent] button"):
        try:
            d = pg.locator(sec).first
            if d.count() and d.is_visible():
                d.click(timeout=4000)
                pg.wait_for_timeout(700)
                return True
        except Exception:                              # noqa: BLE001
            continue
    return False


def _tablo_satiri(pg, sembol: str, hedef: Path) -> dict:
    """
    Midas tablosunda sembolun SATIRINI kirp.

    ARAMA KUTUSU KULLANILIYOR, kaydirma degil: sayfada 626 satir var ve
    dogru satira kaydirip bandin/sticky basligin arkasina denk
    gelmemesini ummak kirilgan. Arama tabloyu tek satira indiriyor.
    """
    pg.wait_for_selector("table tr", timeout=25000)

    # 1) Arama kutusuyla suz — en saglam yol.
    aradi = False
    for sec in ("input[placeholder*='Ara']", "input[type=search]",
                "input[placeholder*='ara']"):
        try:
            kutu = pg.locator(sec).first
            if kutu.count() and kutu.is_visible():
                kutu.fill(sembol)
                pg.wait_for_timeout(1200)
                aradi = True
                break
        except Exception:                              # noqa: BLE001
            continue
    # Sembol `<a class="stock-code">` icinde; ayni `td` bir SVG ikon da
    # tasiyor, bu yuzden `td:text-is()` ESLESMIYOR (olculdu). Secici
    # dogrudan baglantiya sabitleniyor; sinif degisirse metne duser.
    # Yon ikonu (kirmizi/yesil ok) EN KRITIK sayinin uzerine biniyor:
    # "Son" hucresinde 307,75'in ilk hanelerini ortuyordu. Ekran
    # goruntusunun tek amaci fiyati okutmak oldugu icin ikon gizleniyor.
    try:
        pg.add_style_tag(content=".icon-container{display:none !important}")
    except Exception:                                  # noqa: BLE001
        pass

    # 2) Satiri bul. Sembol `<a class="stock-code">` icinde; ayni `td`
    #    bir SVG ikon da tasidigi icin `td:text-is()` ESLESMIYOR.
    adaylar = [
        f"a.stock-code:text-is('{sembol}')",
        f"a:text-is('{sembol}')",
        f"td:has-text('{sembol}')",
    ]
    satir = None
    for sec in adaylar:
        aday = pg.locator("table tr").filter(has=pg.locator(sec)).first
        if aday.count():
            satir = aday
            break
    if satir is None:
        return {"ok": False, "hata": f"{sembol} tabloda bulunamadi",
                "arama_kullanildi": aradi, "denenen_secici": adaylar}
    # Basligi da al ki kolonlarin ne oldugu anlasilsin.
    baslik = pg.locator("table tr").first
    try:
        baslik.screenshot(path=str(hedef.with_name("_baslik.png")))
    except Exception:                                  # noqa: BLE001
        pass
    try:
        satir.scroll_into_view_if_needed(timeout=8000)
    except Exception:                                  # noqa: BLE001
        # Kaydirma basarisiz olsa bile ekran goruntusu denenir; Playwright
        # gerekirse kendisi kaydirir. Burada durmak, sirf bant yuzunden
        # calisabilecek bir istegi reddetmek olurdu.
        pass
    satir.screenshot(path=str(hedef), timeout=15000)

    # Basligi ve satiri dikey birlestir — tek gorselde okunur olsun.
    try:
        from PIL import Image
        b = Image.open(hedef.with_name("_baslik.png"))
        s = Image.open(hedef)
        g = max(b.width, s.width)
        birlesik = Image.new("RGB", (g, b.height + s.height), "white")
        birlesik.paste(b, (0, 0))
        birlesik.paste(s, (0, b.height))
        birlesik.save(hedef)
        hedef.with_name("_baslik.png").unlink(missing_ok=True)
    except Exception:                                  # noqa: BLE001
        pass
    return {"ok": True, "kirpma": "tablo satiri + baslik"}


def main() -> int:
    if len(sys.argv) < 4:
        print(json.dumps({"ok": False, "hata": "kullanim: SEMBOL VENUE CIKTI"}))
        return 2
    sembol, venue, cikti = sys.argv[1].upper(), sys.argv[2].upper(), Path(sys.argv[3])
    cikti.parent.mkdir(parents=True, exist_ok=True)

    tanim = SAYFALAR.get(venue)
    if not tanim:
        print(json.dumps({"ok": False,
                          "hata": f"{venue} icin tanimli kaynak sayfasi yok",
                          "tanimli": sorted(SAYFALAR)}))
        return 1
    url_kalip, kip = tanim
    url = url_kalip.format(sembol=sembol)

    from finagent.browser.session import BrowserSession
    from finagent.config import load_settings

    try:
        with BrowserSession(load_settings()) as bs:
            with bs.page() as pg:
                pg.set_viewport_size({"width": 1280, "height": 900})
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3000)      # geç yuklenen fiyat alanlari
                kapatildi = _cerez_bandini_kapat(pg)
                if kip == "tablo":
                    sonuc = _tablo_satiri(pg, sembol, cikti)
                else:
                    pg.screenshot(path=str(cikti), full_page=False)
                    sonuc = {"ok": True, "kirpma": "gorunur alan"}
    except Exception as e:                             # noqa: BLE001
        print(json.dumps({"ok": False, "hata": f"{type(e).__name__}: {e}"[:200],
                          "url": url}))
        return 1

    sonuc.update({"yol": str(cikti), "url": url, "venue": venue,
                  "sembol": sembol, "cerez_bandi_kapatildi": kapatildi})
    print(json.dumps(sonuc, ensure_ascii=False))
    return 0 if sonuc.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
