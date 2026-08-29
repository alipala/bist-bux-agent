"""
`endeks_hareketlileri` mutasyon turu (2026-08-29).

OLCULEN BOSLUK: kullanici "S&P 500'de %5+ hareket edenler" istedi;
botta ABD karsiligi YOKTU ve tek yol `fiyat_serisi`i sembol basina
cagirmakti — veritabani tarafinda 0,4 sn, LLM dongusunden 17-34 dakika
(sinir: arac suresi 420 sn, max_turns 40). Gorev YAPILAMAZDI.

EN KRITIK MUTASYONLAR:
  D — o gun bari olmayan kagidi "hareket etmedi" saymak (yanlis "yok")
  F — onceki bari TAKVIMDEN almak (seyrek islem goren kagitta yanlis gun)

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib, shutil, subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
TL = "src/finagent/bot/tools.py"

TARA = "test_endeks_hareketlileri_TUM_EVRENI_tek_cagride_tarar"
ONCEKI = "test_endeks_hareketlileri_ONCEKI_BARI_TAKVIMDEN_almaz"
REHBER = "test_rehber_her_araci_sade_dille_karsilar"
BOLUNME = "test_endeks_hareketlileri_BOLUNMEYI_hareket_saymaz"

M = [
    ("A) esik siki karsilastiriliyor (sinirdaki kagit duser)",
     TL, "                if yon.startswith(\"yuk\") and pct < esik:",
     "                if yon.startswith(\"yuk\") and pct <= esik:", TARA),

    ("B) yon yok sayiliyor — dusenler de 'yukari'da cikar",
     TL, "                if yon.startswith(\"asa\") and pct > -esik:",
     "                if False:", TARA),

    ("C) siralama ters (en buyuk hareket altta kalir)",
     TL, "            bulunan.sort(key=lambda r: r[\"degisim_pct\"],\n"
         "                         reverse=not yon.startswith(\"asa\"))",
     "            bulunan.sort(key=lambda r: r[\"degisim_pct\"],\n"
     "                         reverse=yon.startswith(\"asa\"))", TARA),

    ("D) o gun bari olmayan SESSIZCE atlaniyor (yanlis 'yok' beyani)",
     TL, "                    bar_yok.append(sembol)\n                    continue\n"
         "                a, b = onceki.get(\"close\"), son.get(\"close\")",
     "                    continue\n"
     "                a, b = onceki.get(\"close\"), son.get(\"close\")", TARA),

    ("E) tek barli kagit 'serisi yok' sayiliyor (kova karisir)",
     TL, "                if len(seri) < 2:\n                    bar_yok_erken.append(h[\"symbol\"])",
     "                if len(seri) < 2:\n                    seri_yok.append(h[\"symbol\"])", TARA),

    ("F) onceki bar TAKVIMDEN aliniyor, seriden degil",
     TL, "                son, onceki = seri[-1], seri[-2]",
     "                son = seri[-1]\n"
     "                import datetime as _d\n"
     "                _g = (_d.date.fromisoformat(str(son['ts'])[:10])\n"
     "                      - _d.timedelta(days=1)).isoformat()\n"
     "                onceki = (seri[-2] if str(seri[-2]['ts'])[:10] == _g\n"
     "                          else {'ts': _g, 'close': None})", ONCEKI),

    ("G) bilinmeyen endekste sessizce bos donuluyor",
     TL, "                return _hata(f\"endeks uyesi bulunamadi: {endeksler}\",\n"
         "                             f\"tanimli endeksler: {', '.join(bilinen)}\")",
     "                return _ok({\"bulunanlar\": [], \"evren\": 0})", TARA),

    ("H) arac rehberden dusuruluyor (kullaniciya GORUNMEZ olur)",
     "src/finagent/bot/yetenekler.py",
     '    "endeks_hareketlileri": "bir endeksin TUM uyelerini tek cagride tarar',
     '    "_kaldirildi_endeks_hareketlileri": "bir endeksin TUM uyelerini tek cagride tarar',
     REHBER),

    ("I) sermaye islemi kapisi kalkiyor (bolunme '%91 dustu' diye cikar)",
     TL, "                if limit and abs(pct) > limit * 100:",
     "                if False:", BOLUNME),

    ("J) `_seri` yerine `fiyat_serisi` DOGRUDAN cagriliyor (limit kaybolur)",
     TL, "                    seri, limit, _s = self._seri(h, 2, bitis=tarih,\n"
         "                                                 tercih_ccy=[\"USD\"])",
     "                    seri = self.db.fiyat_serisi(h[\"id\"], 2, bitis=tarih,\n"
     "                                                tercih_ccy=[\"USD\"])\n"
     "                    limit = None", BOLUNME),
]


def _kos(test: str) -> int:
    return subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_smoke as T; T.{test}()"],
        cwd=KOK, capture_output=True, text=True, timeout=900).returncode


yakalanan = 0
for ad, yol, eski, yeni, test in M:
    if _kos(test) != 0:
        print(f"  ! TEST ZATEN KIRMIZI, mutasyon anlamsiz: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}")
        continue
    p.write_text(t2, encoding="utf-8")
    try:
        tamam = _kos(test) == 0
        print(f"  {'✗ YAKALANMADI' if tamam else '✓ yakalandi'}: {ad}")
        yakalanan += 0 if tamam else 1
    finally:
        p.write_text(yedek, encoding="utf-8")
        for dizin in ("src", "tests"):
            for kok in KOK.joinpath(dizin).rglob("__pycache__"):
                shutil.rmtree(kok, ignore_errors=True)

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
