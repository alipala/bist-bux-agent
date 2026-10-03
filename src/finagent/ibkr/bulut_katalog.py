"""
IBKR BULUT BAGLAYICISININ TAM KATALOGU — bot hangisini kullaniyor, hangisini
kullanmiyor, kullanilmayani Ali nerede/nasil kullanir.

NEDEN VAR
---------
Ali 2026-10-03'te sordu: "IBKR cloud kullanarak local agent disinda hangi
MCP ozelliklerini kullanabilirim?" Bot bu soruyu CEVAPLAYAMAZDI: elinde
baglayicinin arac listesi yoktu, yani ya "bilmiyorum" der ya da UYDURURDU
(bkz. hafiza: uydurma-sayi-ve-bilesik-arac). Cozum prompt degil, VERI.

KAYNAK — OLCULDU, TAHMIN DEGIL
------------------------------
* Arac adlari: baglayicinin 34 araci (25 Eyl Faz 0 olcumu + oturum kayitlari).
* `ne` alanlari: baglayicinin KENDI aciklamalari, 2026-10-03'te `ToolSearch
  select:` ile yuklenip okundu (hicbir IBKR araci CAGRILMADAN), Turkceye
  sadik cevrildi. Ilk 15 arac zaten koddaki kullanim yerinden biliniyor.
* Karar gerekceleri: "IBKR MCP entegrasyon plani" (claude.ai Docs, 25 Eyl)
  ve sonraki fazlar (`mcp_kanal.SECILEN` yorumlari).

BOTUN KULLANDIGI KUME BURADA TEKRAR YAZILMAZ
--------------------------------------------
"Bot kullaniyor mu" `mcp_kanal.SECILEN`den TURETILIR. Iki elle yazilmis
liste ayrisir (hafiza: ayni-kural-iki-kopya); bir arac SECILEN'e eklendigi
an burada "kullaniliyor"a gecer ve `nerede` alani eksikse test kirilir.

BOT BU ARACLARI SOHBETTE CAGIRAMAZ
----------------------------------
Bu katalog BILGI'dir, yetki degil. Bot oturumunda claude.ai baglayicilari
izin kapisindan gecemez (hafiza: ibkr-mcp-baglayici, kapali-kapiyi-calan-
ajan). Kullanilmayan araclari Ali claude.ai sohbetinde (web/mobil/masaustu,
IBKR baglayicisi bagliyken) DOGRUDAN kullanir.
"""
from __future__ import annotations

from .mcp_kanal import SECILEN, SOHBET_OKUMA

OLCUM_TARIHI = "2026-10-03"

# Arac -> sozluk. Zorunlu: `ne` (ne yapar), `yazma` (IBKR'de bir sey
# degistirir mi). Bot KULLANIYORSA `nerede`; KULLANMIYORSA `neden`,
# `oneri` (ONERI_DUZEYLERI'nden) ve `not_`.
KATALOG: dict[str, dict] = {
    # --- BOTUN KULLANDIKLARI (SECILEN) ---------------------------------
    "get_account_positions": {
        "ne": "hesaptaki pozisyonlar", "yazma": False,
        "nerede": "yerel gecit (CPGW) kapaliyken pozisyon okumasinin yedegi "
                  "(sohbet + zamanli toplayici)"},
    "get_account_balances": {
        "ne": "para birimi bazinda nakit", "yazma": False,
        "nerede": "CPGW kapaliyken nakit okumasinin yedegi"},
    "get_account_summary": {
        "ne": "net varlik, alim gucu gibi hesap ozeti", "yazma": False,
        "nerede": "CPGW kapaliyken `ibkr_durum` hesap ozeti"},
    "get_account_orders": {
        "ne": "acik emirler", "yazma": False,
        "nerede": "CPGW kapaliyken 'emrim duruyor mu' sorusu"},
    "get_account_trades": {
        "ne": "secilen donemdeki islemler: sembol, yon, adet, fiyat, "
              "komisyon, islem zamani", "yazma": False,
        "nerede": "CPGW kapaliyken dolum mutabakatinin yedegi"},
    "create_alert": {
        "ne": "IBKR sunucusunda fiyat/zarar alarmi kurar", "yazma": True,
        "nerede": "/alarm — stop seviyesi ve gunluk zarar alarmi (onayla)"},
    "get_alerts": {
        "ne": "tum alarmlarin listesi ve durumu", "yazma": False,
        "nerede": "/alarm mutabakati: bizim kayit ile IBKR'deki alarmlar "
                  "+ sohbette alarm listesi (`ibkr_bulut_oku`)"},
    "update_alert": {
        "ne": "alarmin seviyesini degistirir", "yazma": True,
        "nerede": "/alarm — stop seviyesi degisince alarmi tasir (onayla)"},
    "delete_alert": {
        "ne": "alarmi siler", "yazma": True,
        "nerede": "/alarm — yalnizca BOTUN kurdugu alarmi siler (onayla)"},
    "get_pa_performance_all_periods": {
        "ne": "hesabin gercek getirisi (zaman agirlikli, TWR), donem donem",
        "yazma": False,
        "nerede": "gece nabzi `ibkrgetiri` + cuma getiri mesaji"},
    "get_option_parameters": {
        "ne": "bir hissenin opsiyon vadeleri ve kullanim fiyatlari",
        "yazma": False,
        "nerede": "bilanco beklentisi: bilancoyu kapsayan ilk vade"},
    "get_option_data": {
        "ne": "opsiyon zinciri fiyatlari", "yazma": False,
        "nerede": "bilanco beklentisi: piyasanin bekledigi hareket (straddle)"},
    "get_price_snapshot": {
        "ne": "anlik fiyat", "yazma": False,
        "nerede": "bilanco beklentisi + CPGW kapaliyken `ibkr_fiyat` yedegi"},
    "get_company_themes": {
        "ne": "bir sirketin IBKR tema siniflandirmasi", "yazma": False,
        "nerede": "`tema_yogunlugu`: portfoy hangi temalarda yogunlasmis"},
    "search_contracts": {
        "ne": "sembol/sirket adindan IBKR kontratlari: borsa, ulke, "
              "kontrat kimligi (conid)",
        "yazma": False,
        "nerede": "tema verisi icin kimligi olmayan portfoy sembolu + "
                  "sohbette sirketin ANA listelemesini bulmak "
                  "(`ibkr_bulut_oku`)"},
    # Faz 7 (3 Eki, Ali: "4 arti 3 okuma araci"): sohbette `ibkr_bulut_oku`.
    "get_company_connections": {
        "ne": "bir sirketin rakipleri, urunleri, faaliyet gosterdigi "
              "ulke/bolgeler; sektor/trend baglari kanitiyla",
        "yazma": False,
        "nerede": "sohbette arastirma (`ibkr_bulut_oku`); ANA listeleme "
                  "conid'i ister (ASML: AEB dolu, NASDAQ bos — olculdu)"},
    "search_investment_topics": {
        "ne": "sektor/trend/konuya gore sirket arar ('gunes enerjisinde "
              "kimler var', 'yapay zeka hisseleri')",
        "yazma": False,
        "nerede": "sohbette arastirma (`ibkr_bulut_oku`); sonuc bir liste, "
                  "ALIM ONERISI degil"},
    "get_theme_details": {
        "ne": "bir konunun tam profili: sirketler (ONEM sirasiyla — piyasa "
              "degerine gore DEGIL) ve istenirse o konuyu kapsayan ETF/fonlar",
        "yazma": False,
        "nerede": "sohbette arastirma (`ibkr_bulut_oku`)"},
    "get_price_history": {
        "ne": "bir enstrumanin gecmis OHLCV fiyat cubuklari",
        "yazma": False,
        "nerede": "sohbette, botun fiyat serisini IBKR'nin kendi serisiyle "
                  "capraz kontrol (`ibkr_bulut_oku`)"},
    "get_pa_allocation": {
        "ne": "hesabin net varliginin bir boyutta (varlik sinifi, sektor, "
              "bolge...) dagilimi",
        "yazma": False,
        "nerede": "sohbette (`ibkr_bulut_oku`); az hissede az bilgi tasir "
                  "(sayi: `ibkr_hisse_pozisyonu`, nakit haric)"},
    "get_alert": {
        "ne": "tek bir alarmin tam detayi: e-posta, suresi, kosulu",
        "yazma": False,
        "nerede": "sohbette alarm detayi (`ibkr_bulut_oku`; kimlik "
                  "`get_alerts`ten)"},
    # --- BOTUN KULLANMADIKLARI -----------------------------------------
    "set_alert_status": {
        "ne": "alarmlari SILMEDEN duraklatir ya da yeniden baslatir",
        "yazma": True, "oneri": "dikkat",
        "neden": "botun alarm yasam dongusunde duraklatma YOK: yalnizca "
                 "kurma, seviye guncelleme ve pozisyon kapaninca silme",
        "not_": "Duraklatirsan bot bunu BILMEZ: alarm mutabakati yalnizca "
                "alarmin IBKR'de VAR olup olmadigina bakiyor, durumuna "
                "bakmiyor — duraklatilmis alarmi aktif sayar. Duraklatilmis "
                "alarm seni korumaz"},
    "create_order_instruction": {
        "ne": "emir TALIMATI (taslak) olusturur — CANLI EMIR DEGIL; "
              "gondermek icin IBKR uygulamasindan sen onaylarsin",
        "yazma": True, "oneri": "karar_sende",
        "neden": "botun emir yolu yerel gecit (CPGW) uzerinden ve Telegram "
                 "onayli; bulutta canli emir yok",
        "not_": "CPGW kapaliyken emir hazirlamanin TEK bulut yolu. Bota "
                "eklenip eklenmeyecegi acik bir soru, karar senin"},
    "get_order_instructions": {
        "ne": "kayitli emir talimatlarini listeler", "yazma": False,
        "oneri": "karar_sende",
        "neden": "`create_order_instruction` ile ayni", "not_": ""},
    "delete_order_instruction": {
        "ne": "bir emir talimatini siler", "yazma": True,
        "oneri": "karar_sende",
        "neden": "`create_order_instruction` ile ayni", "not_": ""},
    "get_watchlists": {
        "ne": "IBKR uygulamasindaki izleme listelerin", "yazma": False,
        "oneri": "gerekirse",
        "neden": "botun izleme listesi kendi veritabaninda; iki liste "
                 "SENKRON DEGIL",
        "not_": "IBKR uygulamasindaki listeyi claude.ai'dan yonetmek icin"},
    "get_watchlist": {
        "ne": "bir izleme listesinin icerigi", "yazma": False,
        "oneri": "gerekirse", "neden": "`get_watchlists` ile ayni",
        "not_": ""},
    "create_watchlist": {
        "ne": "yeni izleme listesi olusturur", "yazma": True,
        "oneri": "gerekirse", "neden": "`get_watchlists` ile ayni",
        "not_": ""},
    "edit_watchlist": {
        "ne": "izleme listesini TAMAMEN yeniden yazar", "yazma": True,
        "oneri": "dikkat", "neden": "`get_watchlists` ile ayni",
        "not_": "tam degistirme: gonderilen listede OLMAYAN kagit listeden "
                "SILINIR"},
    "delete_watchlist": {
        "ne": "izleme listesini kalici olarak siler", "yazma": True,
        "oneri": "dikkat", "neden": "`get_watchlists` ile ayni",
        "not_": "GERI ALINAMAZ"},
    "search_futures": {
        "ne": "bir dayanagin vadeli islem kontratlari (vade merdiveni)",
        "yazma": False, "oneri": "gerek_yok",
        "neden": "hesap nakit hesabi (STKCASH); vadeli islem yok",
        "not_": ""},
    "get_combo_identifier": {
        "ne": "opsiyon bacaklarindan kombine kontrat kimligi uretir",
        "yazma": False, "oneri": "gerek_yok",
        "neden": "opsiyon stratejisi islemi yok; opsiyon yalnizca olcum "
                 "icin okunuyor", "not_": ""},
    "provide_customer_feedback": {
        "ne": "IBKR'ye ozellik istegi / geri bildirim METNI gonderir",
        "yazma": True, "oneri": "gerek_yok",
        "neden": "IBKR'ye senin adina metin gonderir; botun isi degil",
        "not_": "yalnizca sen IBKR'den bir ozellik istemek istersen"},
    "whats_new": {
        "ne": "baglayiciya eklenen yeni araclar ve degisiklikler",
        "yazma": False,
        "nerede": "sohbette (`ibkr_bulut_oku`): 'IBKR'de ne yeni' sorusu"},
}

ONERI_DUZEYLERI: dict[str, str] = {
    "dene": "claude.ai'da dogrudan kullanmaya deger",
    "gerekirse": "belirli bir ihtiyac dogarsa",
    "dikkat": "kullanilabilir ama geri donusu zor ya da botu yaniltir",
    "karar_sende": "bota eklenip eklenmeyecegi senin kararin",
    "gerek_yok": "bu hesapta karsiligi yok",
}


def katalog(ibkr_hisse_pozisyonu: int | None = None) -> dict:
    """
    Botun sohbet araci icin TAM katalog. Saf; ag cagrisi yok.

    `ibkr_hisse_pozisyonu` cagiranin veritabanindan SAYDIGI hisse sayisi
    (nakit haric). Metne sayi GOMULMEZ: elle yazilan "2 pozisyon" ilk
    alimda bayatlar ve model nakit satirlarini da sayip baska bir sayi
    uyduruyordu (olculdu 3 Eki: "4 pozisyon", gercek 2).
    """
    kullanilan, kullanilmayan = [], []
    for ad, k in KATALOG.items():
        satir = {"arac": ad, "ne": k["ne"],
                 "ibkr_de_degisiklik_yapar": bool(k["yazma"]),
                 "sohbette_okunur": ad in SOHBET_OKUMA}
        if ad in SECILEN:
            kullanilan.append({**satir, "bot_nerede_kullaniyor": k["nerede"]})
        else:
            kullanilmayan.append({
                **satir,
                "oneri": k["oneri"],
                "oneri_anlami": ONERI_DUZEYLERI[k["oneri"]],
                "bot_neden_kullanmiyor": k["neden"],
                **({"not": k["not_"]} if k.get("not_") else {})})
    sira = list(ONERI_DUZEYLERI)
    kullanilmayan.sort(key=lambda s: sira.index(s["oneri"]))
    return {
        "toplam_arac": len(KATALOG),
        "olcum_tarihi": OLCUM_TARIHI,
        **({"ibkr_hisse_pozisyonu": ibkr_hisse_pozisyonu}
           if ibkr_hisse_pozisyonu is not None else {}),
        "kaynak": ("IBKR baglayicisinin kendi arac aciklamalari "
                   f"({OLCUM_TARIHI} olculdu) + botun kodu"),
        "bot_kullaniyor": kullanilan,
        "bot_kullanmiyor": kullanilmayan,
        "nasil_kullanirsin": (
            "claude.ai sohbetinde (web, mobil ya da masaustu) IBKR "
            "baglayicisi bagliyken dogal dille iste; ornek: 'IBKR'den "
            "ASML'in rakiplerini ve bolge maruziyetini getir', 'yapay zeka "
            "temasini hangi ETF'ler kapsiyor'. Yazma yapan araclarda "
            "claude.ai senden onay ister."),
        "model_notu": (
            "`sohbette_okunur: true` olanlari `ibkr_bulut_oku` ile "
            "OKUYABILIRSIN; digerlerini sohbette cagiramazsin, deneme — "
            "kullaniciya onlari claude.ai'da KENDISININ kullanacagini "
            "soyle. Onerini `oneri` alanina dayandir; "
            "'dikkat' satirlarinin notunu MUTLAKA aktar. Bir araci bota "
            "eklemek kod degisikligidir — 'ekledim' deme. Botun ne "
            "YAPABILDIGINI yalnizca `bot_nerede_kullaniyor` alanindan "
            "soyle; 'bana soyle, ben yaparim' diye bir yetenek UYDURMA."),
    }
