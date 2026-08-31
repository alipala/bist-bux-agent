"""
Dolum kurtarma mutasyon turu (2026-08-31).

OLCULEN KUSUR: VRT emri 1473988529 IBKR'de DOLDU (0,38 @ 257,80,
komisyon 0,35). Mutabakat satiri "gerceklesti" diye kapatti ama DOLUM
VERISINI ALMADAN — cunku `gecmis` cekimi kararla AYRI bir kosula
bagliydi. Satir kapaninca `kapanmamis_emirler` onu disladi ve dolum bir
daha ASLA gorunmedi.

Ayni tuzak eski KO emrinde de duruyordu (26 Agu'dan beri).

ZINCIR — her halkasi ayri kirilabilir:
  1. karar "dolmus" -> islem kaydi ARANMALI       (mutasyon A)
  2. kanit eksik satir GIRIS KAPISINDAN gecmeli   (mutasyon B)
  3. kurtarma YENIDEN KARAR VERMEMELI             (mutasyon C)
  4. tamamlanmis satir TEKRAR ISLENMEMELI         (mutasyon D)
  5. kayip kayit SESSIZ GECILMEMELI               (mutasyon E)

Yesil test kanit degil; `[[fixi-nasil-kanitlarim]]`.
"""
import pathlib
import shutil
import subprocess

KOK = pathlib.Path("/Users/alipala/github/bist-bux-agent")
MU = "src/finagent/ibkr/mutabakat.py"
DB = "src/finagent/storage/db.py"

ARANIR = "test_DOLMUS_ilan_edilen_emirde_islem_kaydi_HER_DURUMDA_araniyor"
KURTAR = "test_KILITLI_satir_kurtariliyor_ve_YENIDEN_KARAR_VERILMIYOR"
TAM = "test_DOLUMU_TAM_olan_kapanmis_satir_TEKRAR_islenmiyor"
SESSIZ = "test_ISLEM_KAYDI_YOKSA_kurtarma_SESSIZ_GECMEZ"
KAPI = "test_kapanmamis_emirler_DOLUMU_EKSIK_kapanmis_satiri_DONDURUR"

M = [
    ("A) 'gerceklesti' dali islem gecmisini CEKMIYOR — asil kusur",
     MU, "            if not gecmis_cekildi:\n"
         "                gecmis, gecmis_cekildi = islemler(istemci), True\n"
         '            kayit = dolum_kaydi(gecmis, str(s.get("emir_id") or ""))',
     '            kayit = dolum_kaydi(gecmis, str(s.get("emir_id") or ""))',
     ARANIR),

    ("B) GIRIS KAPISI kilitli satiri disliyor — kurtarma imkansiz",
     DB, '        kosul = ("(e.durum NOT IN (\'gerceklesti\',\'dustu\',\'iptal_edildi\',"\n'
         '                 "\'reddedildi\',\'engellendi\',\'suresi_doldu\')"\n'
         '                 " OR (e.durum = \'gerceklesti\' AND e.dolum_fiyat IS NULL))")',
     '        kosul = ("e.durum NOT IN (\'gerceklesti\',\'dustu\',\'iptal_edildi\',"\n'
     '                 "\'reddedildi\',\'engellendi\',\'suresi_doldu\')")',
     KAPI),

    ("C) KURTARMA yeniden DURUM yaziyor — dogru durumu yeniden turetir",
     MU, '                int(s.get("id") or 0), "S1b_dolum_kurtarildi",',
     '                int(s.get("id") or 0), "S1b_dolum_kurtarildi",\n'
     '                yeni_durum="gerceklesti",', KURTAR),

    ("C2) KURTARMA bir EYLEM oneriyor (iptal/teyit butonu cikar)",
     MU, "                alanlar=_dolum_alanlari(kayit), emir_no=str(s[\"emir_id\"])))",
     "                alanlar=_dolum_alanlari(kayit), eylem=\"iptal\","
     " emir_no=str(s[\"emir_id\"])))", KURTAR),

    ("D) TAMAMLANMIS satir da yeniden isleniyor — her kosuda tekrar",
     MU, '            if not (s.get("durum") == "gerceklesti"\n'
         '                    and s.get("dolum_fiyat") is None\n'
         '                    and s.get("emir_id")):',
     '            if not (s.get("durum") == "gerceklesti"\n'
     '                    and s.get("emir_id")):', TAM),

    ("E) KAYIP islem kaydi SESSIZ geciliyor — olculemez dolum fark edilmez",
     MU, '                log.warning("[ibkr] emir %s dolmus ama islem kaydi "',
     '                log.debug("[ibkr] emir %s dolmus ama islem yok "', SESSIZ),
]


def _pycache_temizle() -> None:
    """
    Bayat `.pyc` MUTASYONU GORUNMEZ KILAR.

    OLCULDU 2026-08-31: tek karakterlik bir mutasyon (`"?"` -> `"0"`)
    dosya BOYUTUNU degistirmiyor ve Python'un import kontrolu
    (mtime, size) bayat bayt kodu yeniden kullanabiliyor. Ayni mutasyon
    bir kosumda KACTI, sonrakinde yakalandi — yani harness'in kendisi
    guvenilmezdi ve "N/N yakalandi" raporu bunu gizliyordu.

    Temizlik artik testten ONCE de yapiliyor.
    """
    for dizin in ("src", "tests"):
        for k in KOK.joinpath(dizin).rglob("__pycache__"):
            shutil.rmtree(k, ignore_errors=True)


def _kos(test: str) -> int:
    return subprocess.run(
        [str(KOK / ".venv/bin/python"), "-c",
         f"import sys; sys.path.insert(0,'tests');"
         f"import test_ibkr as T; T.{test}()"],
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
    _pycache_temizle()
    try:
        tamam = _kos(test) == 0
        print(f"  {'✗ YAKALANMADI' if tamam else '✓ yakalandi'}: {ad}")
        yakalanan += 0 if tamam else 1
    finally:
        p.write_text(yedek, encoding="utf-8")
        _pycache_temizle()

print(f"\n{yakalanan}/{len(M)} mutasyon yakalandi · kaynaklar geri alindi")
