"""Adim 5 mutasyon turu — sema 24 ve dolum olcumu."""
import pathlib, shutil, subprocess
KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
DB, MU, EA = ("src/finagent/storage/db.py", "src/finagent/ibkr/mutabakat.py",
              "src/finagent/bot/emirakis.py")
M = [
    ("A) sema surumu bumplanmasin (24 -> 23)",
     DB, "SEMA_SURUMU = 24", "SEMA_SURUMU = 23",
     "test_strateji5_SEMA_24_gocu_KAYIT_KAYBETMEZ"),
    ("B) dolum kolonlari HIC eklenmesin",
     DB, '''            "emirler": [("mesaj_id", "TEXT"),
                        ("dolum_fiyat", "REAL"),
                        ("dolum_komisyon", "REAL"),
                        ("dolum_ts", "TEXT")],''',
     '            "emirler": [("mesaj_id", "TEXT")],',
     "test_strateji5_SEMA_24_gocu_KAYIT_KAYBETMEZ"),
    ("C) cevrilemeyen komisyonu 0.0 yaz",
     MU, "    if v is None or isinstance(v, bool):\n        return None\n    try:\n        return float(str(v).replace(\",\", \"\"))\n    except (TypeError, ValueError):\n        return None",
     "    if v is None or isinstance(v, bool):\n        return None\n    try:\n        return float(str(v).replace(\",\", \"\"))\n    except (TypeError, ValueError):\n        return 0.0",
     "test_strateji5_DOLUM_KOLONLARA_yaziliyor_METNE_degil"),
    ("D) eksik alani None ile EZ",
     MU, "        v = _sayi(kayit.get(anahtar))\n        if v is not None:\n            out[kolon] = v",
     "        out[kolon] = _sayi(kayit.get(anahtar))",
     "test_strateji5_DOLUM_KOLONLARA_yaziliyor_METNE_degil"),
    ("E) MKT emrinde sapmayi SIFIR yaz",
     DB, "CASE WHEN e.fiyat IS NULL OR e.fiyat = 0 THEN NULL\n                        ELSE (e.dolum_fiyat / e.fiyat - 1) * 100\n                   END AS sapma_pct",
     "COALESCE((e.dolum_fiyat / NULLIF(e.fiyat,0) - 1) * 100, 0) AS sapma_pct",
     "test_strateji5_DOLUM_SAPMASI_hesaplaniyor_MKT_DUSMUYOR"),
    ("F) dolmamis emirleri de tabloya kat",
     DB, 'kosul = "WHERE e.dolum_fiyat IS NOT NULL"', 'kosul = "WHERE 1=1"',
     "test_strateji5_DOLUM_SAPMASI_hesaplaniyor_MKT_DUSMUYOR"),
    ("G) onizleme komisyonunu yine kaydetme",
     EA, 'not_="; ".join(notlar) or None', 'not_="; ".join(k.uyarilar) or None',
     "test_strateji5_ONIZLEME_KOMISYONU_DEFTERE_yaziliyor"),
]
for ad, yol, eski, yeni, test in M:
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    t2 = yedek.replace(eski, yeni)
    if t2 == yedek:
        print(f"  ! UYGULANAMADI: {ad}")
        continue
    p.write_text(t2, encoding="utf-8")
    try:
        r = subprocess.run(
            [str(KOK / ".venv/bin/python"), "-c",
             f"import sys; sys.path.insert(0,'tests');"
             f"import test_smoke as T; T.{test}()"],
            cwd=KOK, capture_output=True, text=True, timeout=600)
        print(f"  {'✗ YAKALANMADI' if r.returncode == 0 else '✓ yakalandi'}: {ad}")
    finally:
        p.write_text(yedek, encoding="utf-8")
        # Bayat .pyc mutasyon testini YALANCI yapar — olculdu 2026-08-27.
        for kok in KOK.joinpath("src").rglob("__pycache__"):
            shutil.rmtree(kok, ignore_errors=True)
print("kaynaklar geri alindi")
