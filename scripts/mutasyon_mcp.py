"""
IBKR bulut baglayicisi kanali — Faz 0 mutasyon turu (2026-09-25).

Her satir bir korumayi TEK BASINA geri alir, ilgili testi kosar ve
KIRMIZIYA dondugunu gosterir (`[[fixi-nasil-kanitlarim]]`).

EN KRITIK MUTASYON
------------------
A) Arac `allowed_tools`a konuyor. OLCULDU (2026-09-25): SDK o araci
   otomatik onayliyor ve `can_use_tool` HIC cagrilmiyor — arguman kapisi
   sessizce OLU. Modul bu tuzak olculdukten sonra yazildi; bu mutasyon
   tuzagin geri gelmesini yakalamali.

Betik KENDI KONUMUNDAN kok turetir ve CANLI AGACTA KOSTURULMAZ: bot her
mesaj icin yeni bir isci surec aciyor ve bozuk kodu o saniyelerde
yukleyebilir. Deponun bir kopyasinda kostur.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile

KOK = pathlib.Path(__file__).resolve().parents[1]
PY = pathlib.Path("/Users/alipala/github/bist-bux-agent/.venv/bin/python")
if KOK == pathlib.Path("/Users/alipala/github/bist-bux-agent"):
    raise SystemExit("CANLI AGAC — bu betigi deponun bir kopyasinda kostur")

KN = "src/finagent/ibkr/mcp_kanal.py"
GZ = "src/finagent/ibkr/mcp_gozlem.py"
CF = "src/finagent/config.py"
SH = "scripts/run_kosu.sh"

SECIM = "test_mcp_SECILEN_12_arac_ve_JOKER_YOK"
KAPI = "test_mcp_kapi_yalnizca_BEKLENEN_arac_ve_arguman"
HAM = "test_mcp_cagri_veriyi_HAM_SONUCTAN_alir_modelin_METNINDEN_DEGIL"
ARG = "test_mcp_model_ARGUMANI_DEGISTIRIRSE_arac_CALISMAZ"
BICIM = "test_mcp_ham_ayristirici_OLCULEN_IKI_BICIM_ve_bos_YOK_DEMEZ"
HATA = "test_mcp_hata_TURLERI_korunur"
VAR = "test_mcp_arac_varligi_EKSIK_araci_ve_BICIM_bozuklugunu_soyler"
GOZ = "test_mcp_gece_gozlemi_ESLESMEYI_olcer_ve_ASLA_patlamaz"
KABLO = "test_mcp_gozlemi_nabizdan_SONRA_ZARARSIZ_ve_kip_karari_AYARDA"
MESAJ = "test_mcp_BAGLAYICI_YOK_mesaji_iki_sebebi_AYIRIR_ve_dogru_cozumu_soyler"
ONB = "test_mcp_auth_onbellegi_YALNIZCA_OKUNUR_ve_okunamamak_KAYIT_YOK_sayilmaz"
SEBEP = "test_mcp_BAGLAYICI_YOK_kanaldan_SEBEBIYLE_cikar_ve_gozleme_GIRER"
YD = "src/finagent/ibkr/yedek.py"
PF = "src/finagent/ibkr/portfoy.py"
TP = "src/finagent/collectors/ibkrportfoy.py"
CH = "src/finagent/bot/chat.py"
AG = "src/finagent/pulse/agents.py"
ESD = "test_faz1_IKI_KANAL_ayni_pozisyondan_BIREBIR_ayni_satiri_uretir"
GRC = "test_faz1_gercek_baglayici_yanitlari_ESLENIYOR"
YOK = "test_faz1_CPGW_yoklamasi_DOGRU_KATMANA_bakar_bos_listeyi_OKUNUR_saymaz"
ONBK = "test_faz1_yoklama_ONBELLEGI_hiz_sinirini_korur_ve_BOZUKTA_yeniden_yoklar"
TOPK = "test_faz1_toplayici_yedek_KAPALIYKEN_atlar_DUSERSE_iki_sebebi_de_soyler"
SHK = "test_faz1_sohbet_karari_YALNIZCA_CPGW_okunamazken_acar"
SHKB = "test_faz1_sohbet_KABLOSU_karar_arac_listesine_ve_gizlemeye_bagli"
GIZ = "test_07_HER_model_oturumu_claudeai_baglayicilarini_GIZLER"
GT = "src/finagent/ibkr/getiri.py"
RN = "src/finagent/pulse/runner.py"
GTP = "src/finagent/collectors/ibkrgetiri.py"
ZIN = "test_faz3_gunluk_zincir_PA_kumulatifini_YENIDEN_URETIR"
REV = "test_faz3_gecmis_gun_REVIZYONU_sayilir_ve_son_deger_kalir"
PEN = "test_faz3_KIYAS_PENCERESI_hesabin_omruyle_sinirli"
KNT = "test_faz3_KANIT_DEGIL_uyarisi_ve_TURKCE_mesaj"
HFT = "test_faz3_haftalik_mesaj_GUN_KIP_SAHIP_ayardan_ve_NABZI_DUSURMEZ"
GTK = "test_faz3_toplayici_hatayi_TURUYLE_soyler_ve_KABLOSU_bagli"

M = [
    ("A) arac allowed_tools'ta — SDK otomatik onaylar, kapi OLU",
     KN, "        allowed_tools=[],\n        can_use_tool=_kapi,",
     "        allowed_tools=[ad],\n        can_use_tool=_kapi,", HAM),
    ("B) kapi argumani karsilastirmiyor — model argumani degistirebilir",
     KN, "    if _normal(tool_input) != _normal(beklenen_arg):",
     "    if False:", ARG),
    ("C) kapi baska araca da izin veriyor",
     KN, "    if tool_name != beklenen_ad:", "    if False:", KAPI),
    ("D) veri kancadan alinmiyor — model metnine kalir",
     KN, '        if inp.get("tool_name") == ad:\n            yakalanan["yanit"]',
     '        if False:\n            yakalanan["yanit"]', HAM),
    ("E) icerik blogu listesi ayristirilmiyor — get_price_snapshot bicim hatasi",
     KN, '    if isinstance(tool_response, list):\n        metin = "".join(',
     '    if False:\n        metin = "".join(', BICIM),
    ("F) bos yanit {} donuyor — yanlis 'veri yok'",
     KN, '        raise McpYanitBicimi(f"bos ya da metin olmayan yanit',
     '        return {}  # (f"bos ya da metin olmayan yanit', BICIM),
    ("G) yazmada zaman asimi 'yeniden denenebilir' sayiliyor",
     KN, "        sinif = DurumBilinmiyorHatasi if yazma else UlasilamadiHatasi",
     "        sinif = UlasilamadiHatasi", HATA),
    ("H) baglayici yokken 'model cagirmadi' deniyor",
     KN, '        if arac_bulundu["deger"] is False:', "        if False:", HATA),
    ("I) yetki dususu taninmiyor",
     KN, "    if any(d in m for d in _YETKI_DESENLERI):", "    if False:", HATA),
    ("J) secim listesine alinmayan bir arac eklenmis",
     KN, '    "get_price_snapshot": (4, False),\n}',
     '    "get_price_snapshot": (4, False),\n    "delete_watchlist": (0, True),\n}', SECIM),
    ("K) varlik kontrolu eksik araci soylemiyor",
     KN, '            "eksik": sorted(set(adlar) - bulunan),', '            "eksik": [],', VAR),
    ("L) 'bilinmiyor' 'eslesmiyor' diye yaziliyor",
     GZ, "        kayit[\"eslesme\"] = (mcp == cpgw) if mcp is not None else None",
     "        kayit[\"eslesme\"] = (mcp == cpgw)", GOZ),
    ("M) gozlem istisnayi disari sizdiriyor",
     GZ, "        kayit[\"okuma\"] = {\"sure_sn\"",
     "        raise RuntimeError('x')\n        kayit[\"okuma\"] = {\"sure_sn\"", GOZ),
    ("N) gozlem nabizi etkileyebilir (|| true yok)",
     SH, '.venv/bin/python run.py mcp-gozlem --kip "$KIP" >> data/pulse.log 2>&1 || true',
     '.venv/bin/python run.py mcp-gozlem --kip "$KIP" >> data/pulse.log 2>&1', KABLO),
    ("O) ritim_kip bayragi tasimiyor — ayar dogru, komut goremez",
     CF, '            "mcp_gozlem": bool(ayar.get("mcp_gozlem", False)),', "", KABLO),
    ("Q) onbellek kaydi yok sayiliyor — ise yaramayan 'yeniden baglayin' onerilir",
     KN, "    if kayit is not None:\n        yas = \"\"", "    if False:\n        yas = \"\"", MESAJ),
    ("R) okunamayan onbellek 'kayit yok' sayiliyor",
     KN, '        return None, f"okunamadi: {type(e).__name__}"', "        return None, None", ONB),
    ("S) kanal onbellegi hic okumuyor",
     KN, "            kayit, notu = auth_onbellek_kaydi(_onbellek_yolu)",
     "            kayit, notu = None, None", SEBEP),
    ("T) gece gozlemi sebebi kaydetmiyor",
     GZ, '        d["auth_onbellek_kaydi"] = getattr(e, "onbellek_kaydi")', "        pass", SEBEP),
    ("U) okuyucu Claude Code'un ic dosyasina YAZIYOR",
     KN, "    kayit = d.get(AUTH_ANAHTARI)\n",
     "    kayit = d.get(AUTH_ANAHTARI)\n    p.write_text(json.dumps(d))\n", ONB),
    ("F1) baglayici getirisi FARKLI formulle — ayni hisse iki getiri",
     PF, '            pnl_pct=pnl_yuzde(pnl, maliyet, adet),\n            para_birimi=r.get("currency"),\n            varlik_sinifi=r.get("asset_class"),',
     '            pnl_pct=(pnl / (maliyet or 1) * 100.0 if pnl is not None else None),\n            para_birimi=r.get("currency"),\n            varlik_sinifi=r.get("asset_class"),', ESD),
    ("F2) BASE para birimi sayiliyor",
     PF, "        if not pb or pb == TOPLAM_ANAHTARI:\n            continue\n        cikti[pb] = Nakit(\n            para_birimi=pb,\n            nakit=_sayi(v.get(\"cash_balance\")),",
     "        if not pb:\n            continue\n        cikti[pb] = Nakit(\n            para_birimi=pb,\n            nakit=_sayi(v.get(\"cash_balance\")),", GRC),
    ("F3) bos hesap listesi 'okunur' sayiliyor",
     YD, '        if not hesaplar:\n            return False, "CPGW hesap listesi bos dondu"', "        pass", YOK),
    ("F4) yoklama onbellegi yok sayiliyor — hiz siniri, ceza kutusu riski",
     YD, "        if simdi - float(d[\"ts\"]) < sure_sn:", "        if False:", ONBK),
    ("F5) toplayici yedek KAPALIYKEN de buluta gidiyor",
     TP, "        if not acik:\n            return CollectorResult(self.name, \"skipped\", 0, cpgw_sebebi)",
     "        pass", TOPK),
    ("F6) yedek duserse CPGW sebebi yutuluyor",
     TP, '                f"{cpgw_sebebi} · bulut baglayicisi da okunamadi "',
     '                f"bulut baglayicisi okunamadi "', TOPK),
    ("F7) sohbet CPGW saglamken de buluta aciliyor",
     CH, "    if okunur:\n        return False, \"\"", "    pass", SHK),
    ("F8) sohbette gizleme karara bagli degil — yedek modda IBKR de gizli",
     CH, "            **sdk_ortami(claudeai_baglayicilari=ibkr_bulut),",
     "            **sdk_ortami(),", SHKB),
    ("F9) bir model oturumu gizlemeyi unutuyor (hakem)",
     AG, "        opts = ClaudeAgentOptions(**sdk_ortami(), system_prompt=hakem_prompt(),",
     "        opts = ClaudeAgentOptions(system_prompt=hakem_prompt(),", GIZ),
    ("G1) gunluk getiri FARK olarak turetiliyor (oran degil) — zincir PA'yi uretmez",
     GT, "        r = (1.0 + float(c)) / (1.0 + onceki) - 1.0", "        r = float(c) - onceki", ZIN),
    ("G2) gecmis gun revizyonu fark edilmiyor",
     GT, "                if abs(eski[tarih] - r) > REVIZYON_TOLERANSI:", "                if False:", REV),
    ("G3) kiyas penceresi hesabin omruyle kirpilmiyor (sahada bulunan hata)",
     GT, "        if bas < ilk:\n            if ad != \"baslangic\":", "        if False:\n            if ad != \"baslangic\":", PEN),
    ("G4) 'kanit degil' uyarisi dusuyor",
     GT, "    if o.get(\"kanit_degil\"):", "    if False:", KNT),
    ("G5) haftalik mesaj hesap sahibi olmayana da gidiyor",
     RN, "            if not hedef or hedef not in sahipler:\n                log.info(\"[%s] getiri karnesi atlandi",
     "            if not hedef:\n                log.info(\"[%s] getiri karnesi atlandi", HFT),
    ("G6) getiri karnesi hatasi nabza SIZIYOR",
     RN, "        except Exception as e:                            # noqa: BLE001\n            log.warning(\"[%s] getiri karnesi gonderilemedi",
     "        except ValueError as e:\n            log.warning(\"[%s] getiri karnesi gonderilemedi", HFT),
    ("G7) toplayici baglayici hatasini 'ok' diye yutuyor",
     GTP, '            return CollectorResult(self.name, "error", 0,\n                                   f"{type(e).__name__}',
     '            return CollectorResult(self.name, "ok", 0,\n                                   f"{type(e).__name__}', GTK),
    ("P) bool olmayan bayrak kabul ediliyor",
     CF, '        if "mcp_gozlem" in ayar and not isinstance(ayar["mcp_gozlem"], bool):',
     "        if False:", KABLO),
]


def _pycache_temizle() -> None:
    for dizin in ("src", "tests"):
        for k in KOK.joinpath(dizin).rglob("__pycache__"):
            shutil.rmtree(k, ignore_errors=True)


def _kos(test: str) -> int:
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "DB_PATH": str(pathlib.Path(d) / "t.db"),
               "TELEGRAM_BOT_TOKEN": ""}
        return subprocess.run(
            [str(PY), "-c", f"import sys; sys.path.insert(0,'tests');"
                            f"import test_ibkr as T; T.{test}()"],
            cwd=KOK, capture_output=True, text=True, timeout=900,
            env=env).returncode


yakalanan, uygulanan = 0, 0
_pycache_temizle()
for ad, yol, eski, yeni, test in M:
    if _kos(test) != 0:
        print(f"  ! TEST ZATEN KIRMIZI: {ad} [{test}]")
        continue
    p = KOK / yol
    yedek = p.read_text(encoding="utf-8")
    if yedek.count(eski) != 1:
        print(f"  ! UYGULANAMADI ({yedek.count(eski)} eslesme): {ad}")
        continue
    p.write_text(yedek.replace(eski, yeni), encoding="utf-8")
    _pycache_temizle()
    try:
        kod = _kos(test)
    finally:
        p.write_text(yedek, encoding="utf-8")
        _pycache_temizle()
    uygulanan += 1
    if kod != 0:
        yakalanan += 1
        print(f"  ✓ YAKALANDI (cikis {kod}): {ad}")
    else:
        print(f"  ✗ KACTI: {ad}  [{test}]")

for _, yol, eski, _, _ in M:
    assert eski in (KOK / yol).read_text(encoding="utf-8"), f"GERI ALINAMADI: {yol}"
print(f"\n{yakalanan}/{uygulanan} mutasyon yakalandi ({len(M)} tanimli)")
raise SystemExit(0 if yakalanan == uygulanan == len(M) else 1)
