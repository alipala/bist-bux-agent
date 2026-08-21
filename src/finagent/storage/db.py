"""SQLite depolama katmani. Tum yazmalar idempotent (UPSERT)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..search.normalize import leksik

log = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# BIST bilanco kavramlari (kaynak: midasbilanco). XBRL kavramlariyla AYNI
# etiket uzayina cevriliyor ki `finansal_ozet` her iki borsayi da ayni
# bicimde dondursun. `NetKarTTM` KASITLI OLARAK YOK — o bir takvim donemi
# degil, son 12 ay; seriye karisirsa "en son donem" gibi gorunur.
BIST_KAVRAMLARI = {
    "Hasilat": "gelir",
    "BrutKar": "brut_kar",
    "FaaliyetKari": "faaliyet_kari",
    "NetKar": "net_kar",
    "AnaOrtaklikPayi": "ana_ortaklik_payi",
    "Ozkaynak": "ozkaynak",
    "DonenVarlik": "donen_varlik",
    "DuranVarlik": "duran_varlik",
    "Nakit": "nakit",
}

_SEMBOL_BICIMI = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,19}$")

# ----------------------------------------------------------------------
# ARACI KURUM ILE PIYASA AYNI SEY DEGIL.
#
# `positions.account` NEREDE TUTTUGUNU, `instruments.venue` NEREDE ISLEM
# GORDUGUNU soyler. Bunlar bir sure ayni sanildi cunku ilk iki hesap
# (bux, binance) kendi KATALOGLARI olan yerlerdi ve `venue=account.upper()`
# tesadufen dogru cikti.
#
# MIDAS'TA BOZULDU (olculdu 2026-08-18 07:41): Midas bir BIST araci
# kurumu, "MIDAS piyasasi" diye bir sey yok. Ekran goruntusu akisi
# `venue='MIDAS'` diye YENI bir TRALT acti (0 fiyat bari), oysa
# `TRALT/BIST` zaten vardi (285 bar, 49,10 TRY). 10 adetlik pozisyon
# BOS olana baglandi: degerlenemedi, teknik sinyal uretmedi, o gun
# `sma50_kirilimi` sinyali `ortak` kalip portfoye hic girmedi.
# Ayni sinifin bir onceki vakasi 17 Agustos'ta `venue='BUX'` hayalet
# kayitlariydi; O ZAMAN KAYITLAR TEMIZLENDI AMA BURASI DUZELTILMEDI ve
# ertesi sabah tekrarladi. Bu yuzden duzeltme kayitta degil, KAPIDA.
HESAP_VENUE = {"bux": "BUX", "binance": "BINANCE", "midas": "BIST"}

# Ayni sembol hem hisse hem coin olabilir (or. GRAM). Eslestirme SINIFI
# ASMAZ: bir Binance ekranindaki sembol BIST hissesine baglanamaz.
KRIPTO_VENUE = frozenset({"BINANCE", "CRYPTO"})

# Bir pozisyon ASLA bunlara baglanmaz. `makro` collector'i endeks/emtia/
# kur/faizi de enstruman olarak tutuyor (18 satir) ve "XU100" gibi bir
# sembol ekrandan okunursa pozisyon bir ENDEKSE baglanirdi.
POZISYONSUZ_VENUE = frozenset({"MAKRO", "INDEX"})


def sembol_gecersiz(symbol: str | None) -> str | None:
    """
    Enstruman sembolu kabul edilebilir mi? Degilse SEBEBI doner.

    NEDEN KAPIDA DURUYOR (olculdu 2026-08-17): CoinGecko'dan `币安人生`
    (BinanceLife) adli bir sembol katalogda kaydedildi. Sonrasinda her
    `kripto` toplamasi bu sembolu URL'ye kodlayip gonderdi ve API
    isteği 400 ile REDDETTI — yani TEK bir bozuk satir, kripto kimlik
    zincirinin TAMAMINI kalici olarak durdurdu. Toplama hattinda bu
    satirin ayiklanmasi yetmez: kaynak degistikce ayni sey baska bicimde
    girer. Kapi SEMANIN ONUNDE olmali.
    """
    if not symbol or not symbol.strip():
        return "bos"
    s = symbol.strip().upper()
    if not _SEMBOL_BICIMI.fullmatch(s):
        # Hangi karakterin batirdigini SOYLE; sessiz ret hata ayiklanamaz.
        kotu = [c for c in s if not re.fullmatch(r"[A-Z0-9._-]", c)]
        if kotu:
            return ("ASCII disi/gecersiz karakter: "
                    + " ".join(f"{c!r}(U+{ord(c):04X})" for c in kotu[:5]))
        return f"bicim disi (uzunluk {len(s)})"
    return None


def _gun_once(gun: int) -> str:
    """
    N gun oncesinin damgasi, `utcnow()` ILE AYNI BICIMDE.

    SQLite'in `datetime('now', ...)` ciktisi bosluk ayracli ve ofissiz
    ("2026-08-16 19:45:23"); arsiv damgasi ise ISO-8601 ("...T19:45:23
    +00:00"). Ikisini metin olarak karsilastirmak SINIR GUNUNDE yanlis
    sonuc verir ('T' > ' '), yani "son 30 gun" penceresi 30. gunun
    turlarini sessizce disarida birakirdi. Ayni uretecten uret.
    """
    return (datetime.now(timezone.utc).replace(microsecond=0)
            - timedelta(days=int(gun))).isoformat()


def _like_kacir(s: str) -> str:
    """
    LIKE jokerlerini notrlestirir. Kacirmadan `%` iceren bir arama
    ("%20 dustu") TUM satirlari dondururdu — bos sonuc kadar yaniltici,
    cunku alakasiz satirlar "bulundu" diye modele gider.
    """
    return (s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


# FTS5 trigram uc harflik pencerelerle calisir; daha kisa bir terimi HIC
# eslestiremez. Olculdu: MATCH '"altin"' -> 1 satir, '"tl"' / '"al"' ->
# bos. Yani kisa terimi sorguya koymak sonucu daraltmaz, SIFIRLAR —
# ortuk AND'de tek bir "tl" tum sorguyu oldururdu.
FTS_ASGARI_TERIM = 3


def fts_ifadesi(sorgu: str | None, birlestir: str = " OR ") -> str | None:
    """
    Serbest metni guvenli bir FTS5 MATCH ifadesine cevirir.

    HAM SORGU DOGRUDAN VERILEMEZ. Olculdu — kullanicinin yazabilecegi
    siradan dizeler sozdizimi hatasi firlatiyor:

        MATCH '-13,40'   ->  no such column: 13
        MATCH 'a"b'      ->  unterminated string
        MATCH '(altin'   ->  fts5: syntax error

    Bunlar arama sonucu degil, ISTISNA uretirdi: "gecmiste ne
    konusmustuk" sorusu bir hata mesajina donerdi.

    NEDEN OR: ortuk AND (FTS5 varsayilani) her terimi ZORUNLU kilar;
    dogal bir cumlede tek bir eslesmeyen kelime sonucu sifirlar — LIKE'in
    hatasinin daha yumusagi. OR + bm25 eksik terimi cezalandirir ama
    satiri ELEMEZ. Ikisi de altin kumeye karsi olculdu (2026-08-20):

                        tam_kelime  parafraz  kelime_yok   MRR
        OR                  100%       80%        20%     0.576
        AND (varsayilan)    100%        0%         0%     0.333

    AND, parafraz ve kelime_yok'ta SIFIR satir donduruyor — donen satir
    ortalamasi 0,0. Yani secim bir ince ayar degil, o iki kategorinin
    var olup olmamasi.
    """
    if not sorgu or not sorgu.strip():
        return None
    terimler = [t for t in re.findall(r"\w+", leksik(sorgu))
                if len(t) >= FTS_ASGARI_TERIM]
    if not terimler:
        return None
    # Tirnak icinde FTS5 yalnizca `"` karakterini ozel sayar ve ciftlemek
    # onu kacirir. `\w+` zaten tirnak uretmez; yine de kacisi BURADA
    # yapiyoruz, cunku "token'da tirnak olamaz" varsayimi ilerideki bir
    # tokenlestirme degisikliginde sessizce cokerdi.
    return birlestir.join('"' + t.replace('"', '""') + '"' for t in terimler)


# Yalnizca HUKUKI/KURUMSAL ekler atilir. Ayirt edici kelimeler KALIR:
# "Siemens", "Siemens Energy" ve "Siemens Healthineers" UC AYRI sirkettir.
# Ilk kelimeye bakan bir anahtar bunlari birlestirir ve ikisinin verisi
# kaybolur — olculdu, bu yuzden tum anlamli kelimeler kullaniliyor.
_AD_EKLERI = {"nv", "sa", "ag", "plc", "inc", "corp", "corporation", "ltd",
              "limited", "se", "holding", "holdings", "group", "groep",
              "the", "company", "co", "asa", "ab", "oyj", "spa", "class"}


def _ad_anahtari(ad: str | None) -> str:
    """
    Ayni sirketin farkli yazimlarini esitler, farkli sirketleri AYIRIR.

        'ASML' ~ 'ASML Holding'          -> 'asml'
        'ING'  ~ 'ING Group'             -> 'ing'
        'Siemens' vs 'Siemens Energy'    -> 'siemens' / 'siemensenergy'  (AYRI)
    """
    if not ad:
        return ""
    parcalar = [p for p in "".join(
        ch if ch.isalnum() else " " for ch in str(ad).casefold()).split() if p]
    # Tek harfli parcalar hukuki bicim kisaltmalarindan geliyor:
    # "Adyen N.V." -> [adyen, n, v]. Atilmazsa "Adyen" ile eslesmez.
    anlamli = [p for p in parcalar
               if p not in _AD_EKLERI and not (len(p) == 1 and p.isalpha())]
    return "".join(anlamli) or "".join(parcalar)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # `sohbet_fts` tetikleyicileri bunu cagiriyor (bkz. schema.sql).
        # Normalizasyonun TEK uygulamasi burada: indeks de arama sorgusu
        # da ayni Python fonksiyonundan geciyor, yani ikisi AYRISAMAZ.
        # Kayit baglanti basina; kod tabaninda tek `sqlite3.connect`
        # yukaridaki satir. UDF'siz bir baglanti `sohbet_kaydi`'ya
        # yazmaya kalkarsa "no such function: leksik" ile SESLI patlar —
        # indeksin sessizce eskimesinden her zaman iyidir.
        self._conn.create_function(
            "leksik", 1, lambda s: leksik(s or ""), deterministic=True)
        # Collector'lar AYRI SURECTE calisabiliyor (bot sohbetten
        # `veri_topla` cagirdiginda `run.py collect` alt surec olarak
        # baslatiliyor). WAL eszamanli okumaya izin verir ama yazma
        # kilidi tektir; beklemeden hata vermek yerine BEKLE.
        #
        # 15 -> 30 sn: sohbet isleri de artik ayri sureclerde kosuyor
        # (bkz. bot/kuyruk.py), yani ayni anda yazabilecek surec sayisi
        # 2 iken 4'e cikti (iki worker + collector + nabiz). Sure
        # UZATILDI cunku burada beklemek, "database is locked" ile
        # kullaniciya donmekten HER ZAMAN daha iyidir.
        self._conn.execute("PRAGMA busy_timeout = 30000")
        # WAL hizli ve eszamanli okumaya izin verir; bazi ag/FUSE dosya
        # sistemlerinde desteklenmez, o durumda sessizce DELETE moduna doneriz.
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("SELECT 1").fetchone()
        except sqlite3.OperationalError:
            log.warning("WAL modu desteklenmiyor -> journal_mode=DELETE")
            self._conn.execute("PRAGMA journal_mode = DELETE")

    # ------------------------------------------------------------------
    def init_schema(self) -> None:
        sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        # Temizlik SEMADAN ONCE: schema.sql artik BENZERSIZ indeks kuruyor ve
        # kopyalar dururken indeks OLUSTURULAMAZ (IntegrityError). Yani goc
        # adimi semadan sonra calisamaz — once temizle, sonra kur.
        self._on_goc()
        self._conn.executescript(sql)
        self._migrate()
        # AYRI ADIM, semanin parcasi degil. `executescript` DDL'i kendi
        # basina commit'ler; sema kurulumu ile VERI yazimini ayni islem
        # saymak bu projede daha once yarim goce yol acti (bkz. goc
        # tuzagi 1). Burasi veri yazimi ve donusu acikca denetleniyor.
        self._sohbet_fts_esitle()
        onceki = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if onceki != self.SEMA_SURUMU:
            self._conn.execute(f"PRAGMA user_version = {self.SEMA_SURUMU}")
            if onceki:
                log.info("Sema surumu %s -> %s", onceki, self.SEMA_SURUMU)
        self._conn.commit()
        log.info("Sema hazir: %s (surum %s)", self.path, self.SEMA_SURUMU)

    def _migrate(self) -> None:
        """
        CREATE TABLE IF NOT EXISTS mevcut tabloya YENI KOLON eklemez.
        Sema buyudukce eski veritabanlari sessizce eksik kalir; burada
        kolonlari tek tek kontrol edip ekliyoruz (idempotent).
        """
        eklemeler = {
            "disclosures": [("source", "TEXT NOT NULL DEFAULT 'kap'")],
            "news": [("publisher", "TEXT"), ("tier", "INTEGER NOT NULL DEFAULT 0")],
            # Kripto kimligi: hangi Binance cifti, hangi CoinGecko coin'i.
            # SEC alanlari kriptoda anlamsiz, bu ikisi onlarin karsiligi.
            "identities": [("pair", "TEXT"), ("coingecko_id", "TEXT")],
            # Fiyat serisinin PARA BIRIMI. Yoklugu sahada su hataya yol
            # acti: Yahoo'dan gelen USD seri, EUR portfoy degerleriyle yan
            # yana kullanildi ve 17 pozisyonun 14'unde ~%15,7 (EUR/USD
            # kuru kadar) sapma olustu. Model "SMA50 = 206.52" derken bunun
            # hangi para biriminde oldugu BILINMIYORDU.
            "prices": [("currency", "TEXT")],
            # Tez bozulma damgasi. Bu bir KOLON EKLEME, kisit degisikligi
            # degil — `ALTER TABLE ADD COLUMN` yetiyor, tablo yeniden
            # kurmaya gerek yok.
            # TAKTIK SOZLESMESI (sema 15). Bes kolon da NULL kalabilir:
            # hakem taktik teklif etmeyebilir ya da teklifi dogrulamada
            # REDDEDILEBILIR — ikisinde de gorus kaydi yasar, yalnizca
            # taktik alanlari bos kalir.
            "predictions": [("tez_bozuldu_ts", "TEXT"),
                            ("taktik_tur", "TEXT"),
                            ("taktik_giris", "REAL"),
                            ("taktik_stop", "REAL"),
                            ("taktik_giris_kaynak", "TEXT"),
                            ("taktik_stop_kaynak", "TEXT")],
            # Anlam vektoru ve URETEN MODEL. Uc kolon da NULL kalabilir:
            # gomme katmani kapaliyken ya da Ollama yokken arsiv yazmaya
            # devam etmeli — indeks eksikligi bir veri kaybi degil.
            "sohbet_kaydi": [("gomme", "BLOB"), ("gomme_model", "TEXT"),
                             ("gomme_ts", "TEXT")],
        }
        for tablo, kolonlar in eklemeler.items():
            mevcut = {r["name"] for r in self.query(f"PRAGMA table_info({tablo})")}
            for ad, tanim in kolonlar:
                if ad not in mevcut:
                    self._conn.execute(f"ALTER TABLE {tablo} ADD COLUMN {ad} {tanim}")
                    log.info("Sema guncellendi: %s.%s eklendi", tablo, ad)
        self._haber_kopyalarini_birlestir()

    def _sohbet_fts_esitle(self) -> None:
        """
        Arsivi metin indeksine al — IDEMPOTENT.

        Tetikleyiciler yalnizca BUNDAN SONRAKI yazmalari yakalar; indeks
        kurulmadan once yazilmis 156 satir onlar icin gorunmez. Burasi o
        gecmisi kapatiyor.

        IKI KEZ CALISTIRILINCA CIFT KAYIT OLMAZ: eklenen kume "id'si
        indekste OLMAYANLAR" diye tanimli, bos kume de gecerli bir
        cevaptir. Ters yon de kapali — kaynagi silinmis yetim satirlar
        temizleniyor; onlar arama sonucuna girip JOIN'de dusseydi
        "sonuc var ama gosterilemiyor" gibi sessiz bir eksilme olurdu.
        """
        c = self._conn
        yetim = c.execute("DELETE FROM sohbet_fts "
                          "WHERE rowid NOT IN (SELECT id FROM sohbet_kaydi)").rowcount
        # `cursor.rowcount`, `total_changes` DEGIL. Olculdu: 156 satirlik
        # bir dolduruma `total_changes` 468 dedi — FTS5'in GOLGE
        # tablolarina (`*_data`, `*_idx`, `*_content`, `*_docsize`)
        # yazilanlari da sayiyor. Ilk surumde tam bu yuzden loga
        # "+739 satir" dusmustu; veri dogruydu, SAYI yanlisti.
        eklenen = c.execute("""INSERT INTO sohbet_fts(rowid, metin)
                               SELECT k.id, leksik(k.metin)
                               FROM sohbet_kaydi k
                               WHERE k.id NOT IN (SELECT rowid FROM sohbet_fts)
                            """).rowcount
        if eklenen or yetim > 0:
            log.info("sohbet_fts esitlendi: +%s satir, %s yetim silindi",
                     eklenen, max(yetim, 0))

    # Sema surumu. Goc durumu bugune kadar KOLON VARLIGINDAN cikarsaniyordu
    # ("`ajan` var mi") ve bu her goc icin ayri bir tespit yontemi icat
    # etmek demek. Sirada en az iki goc daha var (`signal_stats`, makro);
    # ucuncusu ve dorduncusu kendi yontemini uydurmadan once tek satirlik
    # bir sayac koymanin maliyeti sifir. Kolon kontrolleri KALIYOR —
    # surum yalnizca "bu veritabani hangi asamada" sorusunu ucuza
    # cevapliyor, tespitin yerine gecmiyor.
    SEMA_SURUMU = 15

    # Goc sirasinda yeniden kurulan tablolar. Yetim `*_eski` artiklari
    # bu listeden taraniyor.
    GOC_TABLOLARI = ("positions", "predictions", "signals",
                     "panel_runs", "analysis_runs", "bildirim_durumu")

    def _on_goc(self) -> None:
        """Sema kurulmadan ONCE calismasi gereken temizlikler."""
        var = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fundamentals'")
        if var:
            self._anlik_finansal_kopyalarini_temizle()

        # YETIM TABLO KURTARMASI EN BASTA VE KOSULSUZ.
        #
        # Onceden kurtarma yalnizca "goc gerekiyor" denen tablolar icin
        # kosuyordu. Bes tablo da gocmusse o liste BOS kalir, kurtarma hic
        # calismaz ve diskte kalmis bir `*_eski` sonsuza dek durur. Bir
        # sonraki goc ayni ada RENAME yapinca "already exists" ile patlar
        # — yani TEK BIR COKME sonraki gocu KALICI OLARAK bloke eder.
        # `predictions_eski` icin bu senaryo bir kez yasandi.
        for tablo in self.GOC_TABLOLARI:
            self._yarim_tabloyu_kurtar(tablo)

        self._predictions_ajan_gocu()
        self._sahip_gocu()
        self._sahip_varsayilani_gocu()
        self._bildirim_durumu_sahip_gocu()
        self._news_konu_gocu()
        self._saatlik_currency_gocu()

    def _news_konu_gocu(self) -> None:
        """
        `news.konu` kolonu (sema 9).

        ALTI MADDELIK KALIP KULLANILMIYOR, bilerek. O kalip var olan bir
        tablonun SEKLINI degistiren (kisit, benzersizlik, DEFAULT) gocler
        icin: tablo yeniden kurulmasi gerektiginde. Buradaki islem
        NULL kabul eden tek bir kolon eklemek — `ALTER TABLE ADD COLUMN`
        tabloyu yeniden kurmaz, kisit degistirmez, veri tasimaz.
        Gereksiz karmasiklik uretmemek de bir kural (bkz. sema 7 ve 8).

        Indeks schema.sql'de ve YENI bir ad tasiyor; `CREATE INDEX IF NOT
        EXISTS` mevcut bir indeksi YENIDEN TANIMLAMAZ, o yuzden ad
        catismasi olmamasi onemli.
        """
        kolonlar = self._kolonlar("news")
        if not kolonlar or "konu" in kolonlar:
            return
        with self.tx() as c:
            c.execute("ALTER TABLE news ADD COLUMN konu TEXT")
        log.info("news.konu kolonu eklendi (sema 9)")

    def _saatlik_currency_gocu(self) -> None:
        """
        `prices_hourly.currency` kolonu (sema 14).

        Tablo kripto-yalnizken para birimi ORTUK USDT idi ve kolon
        gereksizdi. Gun ici katman BIST (TRY) ve ABD (USD) saatlik
        barlarini da bu tabloya yaziyor; etiketsiz seri, gunluk `prices`
        tablosunda 17 pozisyonun 14'unu bozan kusur sinifinin aynisini
        burada acardi.

        Eski kripto satirlari NULL kalir — okuyan taraf NULL'u
        "etiketsiz" bilir, USDT VARSAYMAZ. `_news_konu_gocu` ile ayni
        kalip: NULL kabul eden tek kolon, `ALTER TABLE ADD COLUMN` yeter.
        """
        kolonlar = self._kolonlar("prices_hourly")
        if not kolonlar:
            return
        if "currency" not in kolonlar:
            with self.tx() as c:
                c.execute("ALTER TABLE prices_hourly ADD COLUMN currency TEXT")
            log.info("prices_hourly.currency kolonu eklendi (sema 14)")
        self._saatlik_currency_doldur()

    def _saatlik_currency_doldur(self) -> None:
        """
        Etiketsiz saatlik barlari GUNLUK SERIDEN doldurur — IDEMPOTENT.

        Kolonu eklemek etiketsiz satirlari doldurmuyor ve o satirlar
        "para birimi bilinmiyor" olarak kaliyordu: `saatlik` araci
        "tum seviyeler None cinsinden" diyordu (olculdu 2026-08-21,
        41.597 kripto bari).

        DOLUM UYDURMA DEGIL TUREME: yalnizca ayni enstrumanin ayni
        kaynaktaki GUNLUK serisi TEK bir para birimi tasiyorsa o deger
        yaziliyor. Birden fazla para birimi varsa satir ETIKETSIZ kalir
        — belirsizken tahmin etmek, bu projenin tam olarak kacindigi sey.

        Kolon eklendikten SONRA da her acilista kosuyor (normalde 0
        satir): eski bir yedekten donen ya da baska bir makinede
        olusmus bir veritabani da ayni bakimi gormeli.
        """
        var = self.query(
            "SELECT 1 FROM prices_hourly WHERE currency IS NULL LIMIT 1")
        if not var:
            return
        with self.tx() as c:
            # `tx()` CONNECTION veriyor, cursor degil: satir sayisi
            # `execute`in DONDURDUGU cursor'da. `c.rowcount` yazmak
            # AttributeError uretiyor ve bu, gocu tumden dusuruyordu
            # (olculdu 2026-08-21, `init-db` patladi).
            imlec = c.execute("""
                UPDATE prices_hourly AS h
                   SET currency = (
                       SELECT MIN(p.currency) FROM prices p
                       WHERE p.instrument_id = h.instrument_id
                         AND p.source = h.source
                         AND p.currency IS NOT NULL)
                 WHERE h.currency IS NULL
                   AND (SELECT COUNT(DISTINCT p.currency) FROM prices p
                        WHERE p.instrument_id = h.instrument_id
                          AND p.source = h.source
                          AND p.currency IS NOT NULL) = 1""")
            n = imlec.rowcount
        if n:
            log.info("prices_hourly: %d etiketsiz bar gunluk seriden "
                     "para birimi aldi (sema 14)", n)


    # Ilk sahip. Cok kullanicili katmandan ONCEKI her kayit ona ait.
    ILK_SAHIP = "ali"

    def _sahip_gocu(self) -> None:
        """
        Cok kullanicili katman: `sahip` kolonu + benzersizlige dahil.

        NEDEN ACIL: `positions` sorgulari "her account'in en son
        snapshot'i" kalibini kullaniyordu. Ikinci kisi bir BUX ekran
        goruntusu onaylasaydi onun snapshot'i en yenisi olur ve birinci
        kisinin portfoyu HER sorgudan kaybolurdu — nabiz, yogunlasma
        riski ve panel gundemi birlesik hayalet bir portfoye gore
        calisirdi. Ikinci portfoy sisteme girmeden bu kapanmali.

        UC TABLO YENIDEN KURULUYOR (positions, predictions, signals)
        cunku benzersizlik kisiti degisiyor ve SQLite kisit
        degistiremez. Ikisi (panel_runs, analysis_runs) yalnizca kolon
        aliyor.

        HEPSI TEK ISLEMDE: uc tablonun ikisi tasinip ucuncusu patlarsa
        veritabani yarim kalir ve hangi tablonun hangi asamada oldugu
        bilinemez. `_goc_islemi` DDL'i de kapsiyor.

        SIGNALS OZEL: piyasa sinyalleri (fiyattan turer) 'ortak',
        portfoy sinyalleri (yogunlasma, acik_zarar) ilk sahibe gecer.
        """
        # KAPI TABLO BASINA. Once yalnizca `positions`e bakiliyordu ve
        # bu YANLISTI: bos bir veritabaninda `positions` hic yokken
        # `predictions` eski sekilde durabiliyor, goc atlaniyor ve
        # schema.sql `sahip` kolonuna basvuran indeksi kurmaya calisip
        # patliyordu. Duman testi yakaladi — hata yalnizca kismi
        # sekilli veritabanlarinda, yani gocun hedef kitlesinde cikiyordu.
        gerekli = [tablo for tablo in ("positions", "predictions", "signals")
                   if self._kolonlar(tablo) and "sahip" not in self._kolonlar(tablo)]
        if not gerekli:
            return

        oncesi = {t: self.query(f"SELECT COUNT(*) n FROM {t}")[0]["n"]
                  for t in gerekli}

        # LEGACY_ALTER_TABLE ACIK — REFERANS YENIDEN YAZIMINI ENGELLER.
        #
        # Modern SQLite'ta `ALTER TABLE x RENAME TO y`, DIGER tablolarin
        # x'e bakan yabanci anahtarlarini da y'ye cevirir. Bu goc
        # tablolari sirayla yeniden kurdugu icin sonuc su oluyordu:
        # `signals` -> `signals_eski` yeniden adlandirilinca
        # `predictions.signal_id` de `signals_eski`'ye baglaniyor, sonra
        # o kopya dusuruldugunde referans ASKIDA kaliyordu ve her
        # `INSERT INTO predictions` "no such table: main.signals_eski"
        # ile patliyordu. Olculdu: gercek kosuda panel bu yuzden
        # tamamen calismadi.
        #
        # Pragma islem DISINDA verilmeli.
        self._conn.execute("PRAGMA legacy_alter_table = ON")
        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self._goc_islemi() as c:
                self._sahip_tablolarini_tasi(c, gerekli)
                for tablo, eski in oncesi.items():
                    yeni = c.execute(f"SELECT COUNT(*) FROM {tablo}").fetchone()[0]
                    if yeni != eski:
                        raise RuntimeError(
                            f"sahip gocunde kayit kaybi: {tablo} "
                            f"{eski} -> {yeni}; islem geri sarildi")
                for tablo in gerekli:
                    c.execute(f"DROP TABLE {tablo}_eski")
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA legacy_alter_table = OFF")

        log.info("sahip gocu: %s", oncesi)

    def _kolonlar(self, tablo: str) -> set:
        return {r["name"] for r in self.query(f"PRAGMA table_info({tablo})")}


    # ------------------------------------------------------------------
    # INDEKS TUZAGI — sonraki gocler icin
    #
    # `CREATE INDEX IF NOT EXISTS` MEVCUT bir indeksi YENIDEN TANIMLAMAZ.
    # Bir indeksin kolonlari degisiyorsa schema.sql'i guncellemek YETMEZ:
    # tablo duruyorsa eski indeks de durur ve veritabani, semanin
    # soyledigi seyden farkli bir sey icerir — hicbir uyari olmadan.
    # Ya acik `DROP INDEX` gerekir ya da tablo yeniden kurulmali
    # (rename indeksi tasir, DROP TABLE onu dusurur, sema yenisini kurar).
    # Siradaki gocler (signal_stats, makro) bu tuzaga girebilir.
    # ------------------------------------------------------------------

    def _bildirim_durumu_sahip_gocu(self) -> None:
        """
        `bildirim_durumu` anahtarina `sahip` ekler.

        NEDEN: iki kisi ayni enstrumani tutuyorsa yogunlasma oranlari
        FARKLIDIR ve ikisi de kendi alarmini almali. Sahipsiz anahtarda
        A'nin bastirma satiri B'nin degerini ezer; B ya kendi riskini
        HIC gormez ya da A ertesi gun gereksiz alarm alir. Tablo bildirim
        yorgunlugunu cozmek icin kurulmustu ve sahipsiz hali, cozmeye
        calistigi seyi baska bicimde uretiyordu.

        Kalip yerlesik gocle AYNI: sema tek kaynaktan okunur, acik islem,
        sayim iceride, `legacy_alter_table` ile FK yeniden yazimi
        engellenir (bu tablonun `instruments`'a yabanci anahtari var).
        """
        kolonlar = {r["name"] for r in
                    self.query("PRAGMA table_info(bildirim_durumu)")}
        if not kolonlar or "sahip" in kolonlar:
            return

        oncesi = self.query("SELECT COUNT(*) n FROM bildirim_durumu")[0]["n"]
        self._conn.execute("PRAGMA legacy_alter_table = ON")
        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self._goc_islemi() as c:
                c.execute("ALTER TABLE bildirim_durumu "
                          "RENAME TO bildirim_durumu_eski")
                c.execute("CREATE TABLE bildirim_durumu ("
                          + self._sema_govdesi("bildirim_durumu") + "\n)")
                c.execute(f"""
                    INSERT INTO bildirim_durumu
                        (sahip, instrument_id, tur, son_deger, son_bildirim_ts)
                    SELECT '{self.ILK_SAHIP}', instrument_id, tur, son_deger,
                           son_bildirim_ts FROM bildirim_durumu_eski""")
                yeni = c.execute(
                    "SELECT COUNT(*) FROM bildirim_durumu").fetchone()[0]
                if yeni != oncesi:
                    raise RuntimeError(
                        f"bildirim_durumu gocunde kayit kaybi: "
                        f"{oncesi} -> {yeni}; islem geri sarildi")
                c.execute("DROP TABLE bildirim_durumu_eski")
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA legacy_alter_table = OFF")
        log.info("bildirim_durumu sahip gocu: %s kayit", oncesi)

    def _sahip_varsayilani_gocu(self) -> None:
        """
        `sahip` kolonundaki DEFAULT'u kaldirir — tablo yeniden kurarak.

        NEDEN: `DEFAULT 'ali'` tek sahipliyken zararsizdi ama Faz B'de
        panel kisi basina kosarken bir INSERT yolunda sahip parametresi
        unutulursa sorgu PATLAMAZ, sessizce ilk sahibe yazardi. Ikinci
        kisinin tahminleri birincinin defterine duser ve hicbir sey hata
        vermez — bu katmanin engellemek icin var oldugu hatanin kendisi.

        NEDEN TABLO YENIDEN KURULUYOR: SQLite bir kolonun DEFAULT'unu
        DUSUREMEZ; `ALTER TABLE` yalnizca kolon ekler.

        SEMA schema.sql'DEN OKUNUYOR, satir ici yazilmiyor. Onceki goc
        kendi CREATE'ini tasiyordu ve ayni gercegin iki yerde beyan
        edilmesi bu projenin tekrar eden kusur sinifi — gocteki kopya
        sessizce geride kalabilirdi. Tek kaynak schema.sql.
        """
        hedefler = [tablo for tablo in
                    ("positions", "predictions", "signals",
                     "panel_runs", "analysis_runs")
                    if self._sahip_varsayilani_var(tablo)]
        if not hedefler:
            return

        # Kurtarma `_on_goc` basinda KOSULSUZ yapildi; burada tekrarlanmaz.
        oncesi = {tablo: self.query(f"SELECT COUNT(*) n FROM {tablo}")[0]["n"]
                  for tablo in hedefler}

        # LEGACY_ALTER_TABLE ACIK — REFERANS YENIDEN YAZIMINI ENGELLER.
        #
        # Modern SQLite'ta `ALTER TABLE x RENAME TO y`, DIGER tablolarin
        # x'e bakan yabanci anahtarlarini da y'ye cevirir. Bu goc
        # tablolari sirayla yeniden kurdugu icin sonuc su oluyordu:
        # `signals` -> `signals_eski` yeniden adlandirilinca
        # `predictions.signal_id` de `signals_eski`'ye baglaniyor, sonra
        # o kopya dusuruldugunde referans ASKIDA kaliyordu ve her
        # `INSERT INTO predictions` "no such table: main.signals_eski"
        # ile patliyordu. Olculdu: gercek kosuda panel bu yuzden
        # tamamen calismadi.
        #
        # Pragma islem DISINDA verilmeli.
        self._conn.execute("PRAGMA legacy_alter_table = ON")
        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self._goc_islemi() as c:
                for tablo in hedefler:
                    self._semadan_yeniden_kur(c, tablo)
                for tablo, eski in oncesi.items():
                    yeni = c.execute(f"SELECT COUNT(*) FROM {tablo}").fetchone()[0]
                    if yeni != eski:
                        raise RuntimeError(
                            f"varsayilan gocunde kayit kaybi: {tablo} "
                            f"{eski} -> {yeni}; islem geri sarildi")
                for tablo in hedefler:
                    c.execute(f"DROP TABLE {tablo}_eski")
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA legacy_alter_table = OFF")
        log.info("sahip varsayilani kaldirildi: %s", oncesi)

    def _sahip_varsayilani_var(self, tablo: str) -> bool:
        for r in self.query(f"PRAGMA table_info({tablo})"):
            if r["name"] == "sahip":
                return r["dflt_value"] is not None
        return False

    def _sema_govdesi(self, tablo: str) -> str:
        """schema.sql'deki CREATE govdesi — TEK KAYNAK."""
        import re
        sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {tablo} \((.*?)\n\);",
                      sql, re.S)
        if not m:
            raise RuntimeError(f"schema.sql'de {tablo} tanimi bulunamadi")
        return m.group(1)

    def _semadan_yeniden_kur(self, c, tablo: str) -> None:
        """
        Tabloyu schema.sql'deki tanimla yeniden kurar, veriyi tasir.

        ORTAK KOLONLAR kesistirilir: eski tabloda olmayan yeni bir kolon
        varsa NULL/varsayilan alir, kaldirilan kolon sessizce dusurulur.
        Sabit kolon listesi yazmak, iki gocun sirasina gizli bagimlilik
        kurar ve kismi sekilli veritabanlarinda patlar (olculdu).
        """
        eski_kolonlar = [r[1] for r in c.execute(f"PRAGMA table_info({tablo})")]
        c.execute(f"ALTER TABLE {tablo} RENAME TO {tablo}_eski")
        c.execute(f"CREATE TABLE {tablo} ({self._sema_govdesi(tablo)}\n)")
        yeni_kolonlar = [r[1] for r in c.execute(f"PRAGMA table_info({tablo})")]
        ortak = [k for k in yeni_kolonlar if k in eski_kolonlar]
        alan = ", ".join(ortak)
        c.execute(f"INSERT INTO {tablo} ({alan}) "
                  f"SELECT {alan} FROM {tablo}_eski")

    def _yarim_tabloyu_kurtar(self, tablo: str) -> None:
        """
        Yarim kalmis bir gocten kalan `<tablo>_eski` artigini cozer.

        SILMEK YETMEZ, once HANGISININ GERCEK VERIYI TUTTUGUNA bakilir:
        sqlite3 eski kipte DDL'i otomatik commit ettigi icin cokmus bir
        goc "canli tablo BOS, veri kopyada" halinde birakabiliyor. Korene
        bakmadan silmek, kurtarilabilir bir yarim gocu KALICI VERI
        KAYBINA cevirir.
        """
        if not self.query("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name=?", (f"{tablo}_eski",)):
            return
        eski_n = self.query(f"SELECT COUNT(*) n FROM {tablo}_eski")[0]["n"]
        yeni_n = (self.query(f"SELECT COUNT(*) n FROM {tablo}")[0]["n"]
                  if self.query("SELECT name FROM sqlite_master "
                                "WHERE type='table' AND name=?", (tablo,)) else -1)
        if eski_n > yeni_n:
            log.warning("yarim goc: %s %s kayit, %s_eski %s kayit -> GERI ALINIYOR",
                        tablo, yeni_n, tablo, eski_n)
            if yeni_n >= 0:
                self._conn.execute(f"DROP TABLE {tablo}")
            self._conn.execute(f"ALTER TABLE {tablo}_eski RENAME TO {tablo}")
        else:
            self._conn.execute(f"DROP TABLE {tablo}_eski")
        self._conn.commit()

    def _sahip_tablolarini_tasi(self, c, gerekli: list[str]) -> None:
        s = self.ILK_SAHIP

        # --- positions ---------------------------------------------------
        if "positions" in gerekli:
          c.execute("ALTER TABLE positions RENAME TO positions_eski")
          c.execute("""
              CREATE TABLE positions (
                  sahip         TEXT    NOT NULL,
                  snapshot_ts   TEXT    NOT NULL,
                  account       TEXT    NOT NULL,
                  instrument_id INTEGER NOT NULL
                                REFERENCES instruments(id) ON DELETE CASCADE,
                  quantity REAL, avg_cost REAL, last_price REAL,
                  market_value REAL, pnl_abs REAL, pnl_pct REAL,
                  currency      TEXT,
                  PRIMARY KEY (sahip, snapshot_ts, account, instrument_id)
              )""")
          c.execute(f"""
              INSERT INTO positions (sahip, snapshot_ts, account, instrument_id,
                  quantity, avg_cost, last_price, market_value, pnl_abs,
                  pnl_pct, currency)
              SELECT '{s}', snapshot_ts, account, instrument_id, quantity,
                     avg_cost, last_price, market_value, pnl_abs, pnl_pct,
                     currency FROM positions_eski""")

        # --- predictions ---------------------------------------------------
        if "predictions" in gerekli:
          c.execute("ALTER TABLE predictions RENAME TO predictions_eski")
          c.execute("""
              CREATE TABLE predictions (
                  id            INTEGER PRIMARY KEY,
                  olusma_ts     TEXT NOT NULL,
                  instrument_id INTEGER NOT NULL
                                REFERENCES instruments(id) ON DELETE CASCADE,
                  ajan          TEXT NOT NULL DEFAULT 'bilinmiyor',
                  signal_id     INTEGER REFERENCES signals(id) ON DELETE SET NULL,
                  yon           TEXT NOT NULL,
                  ufuk_gun      INTEGER NOT NULL,
                  guven         REAL,
                  gerekce       TEXT,
                  tez                  TEXT,
                  gecersizlesme_kosulu TEXT,
                  izlenecek_esik       TEXT,
                  tez_bozuldu_ts       TEXT,
                  baslangic_fiyat REAL NOT NULL,
                  para_birimi   TEXT,
                  olcum_ts      TEXT,
                  bitis_fiyat   REAL,
                  getiri_pct    REAL,
                  piyasa_getiri_pct REAL,
                  anormal_pct   REAL,
                  isabet        INTEGER,
                  sahip         TEXT NOT NULL,
                  UNIQUE (olusma_ts, instrument_id, ufuk_gun, ajan, sahip)
              )""")
          # VAR OLAN KOLONLARLA KESISTIR. `_predictions_ajan_gocu` tabloyu
          # `tez_bozuldu_ts` eklenmeden ONCE kurmus olabilir (o kolon
          # `_migrate` asamasinda geliyor ve o asama semadan SONRA).
          # Sabit kolon listesi yazmak, iki gocun sirasina gizli bir
          # bagimlilik kurar ve kismi sekilli veritabanlarinda patlar.
          mumkun = ("id", "olusma_ts", "instrument_id", "ajan", "signal_id",
                    "yon", "ufuk_gun", "guven", "gerekce", "tez",
                    "gecersizlesme_kosulu", "izlenecek_esik", "tez_bozuldu_ts",
                    "baslangic_fiyat", "para_birimi", "olcum_ts",
                    "bitis_fiyat", "getiri_pct", "piyasa_getiri_pct",
                    "anormal_pct", "isabet")
          var = {r[1] for r in c.execute("PRAGMA table_info(predictions_eski)")}
          alan = ", ".join(k for k in mumkun if k in var)
          c.execute(f"""
              INSERT INTO predictions ({alan}, sahip)
              SELECT {alan}, '{s}' FROM predictions_eski""")

        # --- signals -------------------------------------------------------
        if "signals" in gerekli:
          c.execute("ALTER TABLE signals RENAME TO signals_eski")
          c.execute("""
              CREATE TABLE signals (
                  id            INTEGER PRIMARY KEY,
                  olusma_ts     TEXT NOT NULL,
                  instrument_id INTEGER NOT NULL
                                REFERENCES instruments(id) ON DELETE CASCADE,
                  tur           TEXT NOT NULL,
                  yon           TEXT,
                  guc           REAL,
                  kanit         TEXT,
                  fiyat         REAL,
                  para_birimi   TEXT,
                  sahip         TEXT NOT NULL,
                  UNIQUE (olusma_ts, instrument_id, tur, sahip)
              )""")
          # PORTFOY sinyali kisiye, PIYASA sinyali 'ortak'.
          c.execute(f"""
              INSERT INTO signals (id, olusma_ts, instrument_id, tur, yon, guc,
                                   kanit, fiyat, para_birimi, sahip)
              SELECT id, olusma_ts, instrument_id, tur, yon, guc, kanit, fiyat,
                     para_birimi,
                     CASE WHEN tur IN ('yogunlasma','acik_zarar')
                          THEN '{s}' ELSE 'ortak' END
              FROM signals_eski""")

        # --- yalnizca kolon alanlar ---------------------------------------
        # Bu ikisi HENUZ VAR OLMAYABILIR: goc semadan once calisiyor ve
        # yeni bir veritabaninda tablolari schema.sql kuracak (zaten
        # `sahip` kolonuyla). Yoksa atla.
        for tablo in ("panel_runs", "analysis_runs"):
            mevcut = {r[1] for r in c.execute(f"PRAGMA table_info({tablo})")}
            if not mevcut:
                continue
            if "sahip" not in mevcut:
                c.execute(f"ALTER TABLE {tablo} ADD COLUMN sahip TEXT "
                          f"NOT NULL DEFAULT '{s}'")

    def _predictions_ajan_gocu(self) -> None:
        """
        `predictions` benzersizligine `ajan` ekler — TABLO YENIDEN KURARAK.

        NEDEN ALTER TABLE YETMIYOR: SQLite bir kisiti (UNIQUE) sonradan
        degistiremez, `CREATE TABLE IF NOT EXISTS` de mevcut tabloya
        dokunmaz. Yani schema.sql'deki yeni tanim ESKI veritabanina
        kendiliginden uygulanmaz; tablo kopyalanarak tasinmali.

        NEDEN SEMADAN ONCE: schema.sql yeni tabloyu ve `ix_pred_ajan`
        indeksini kurmaya calisiyor; eski tablo dururken indeks eksik
        kolona basvurur. Ayni sira gerekcesi `_anlik_finansal_kopyalarini
        _temizle` icin de gecerliydi.

        ZAMANLAMA: bu goc 26 kayit tasiyor. Ayni duzeltme bir hafta sonra
        yapilsaydi, aradaki her nabiz kosusunda atilan gorusler KALICI
        olarak kaybolmus olacakti — kaybedilen sey kayit degil, celiskinin
        kendisi.

        `ajan` degeri `gerekce` onekinden ("[teknik] ...") geri kazanilir;
        cozulemezse 'bilinmiyor' kalir ve bu ciktida BEYAN edilir.
        """
        # KURTARMA EN BASTA. Yarim kalmis bir gocte canli `predictions`
        # YENI sekillidir (ajan kolonu var) ama BOSTUR; veri
        # `predictions_eski`'de durur. Bu kontrol asagidaki "zaten goc
        # edilmis" testinden SONRA calissaydi fonksiyon erken doner,
        # veri kalici olarak eski tabloda kalir ve canli tablo bos
        # gorunurdu — sessiz ve tam olarak kacindigimiz turden.
        self._yarim_gocu_kurtar()

        satir = self.query(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='predictions'")
        if not satir or not satir[0]["sql"]:
            return                                   # tablo yok, sema kuracak
        kolonlar = {r["name"] for r in self.query("PRAGMA table_info(predictions)")}
        if "ajan" in kolonlar:
            return                                   # goc yapilmis

        # YABANCI ANAHTAR DENETIMI GECICI OLARAK KAPALI.
        #
        # Yeni tablo `signals(id)`'ye basvuruyor, ama bu goc semadan ONCE
        # calisiyor (index `ix_pred_ajan` eski tabloda olusturulamaz).
        # `signals` henuz yoksa INSERT "no such table: main.signals" ile
        # patlar. Bunu duman testi yakaladi: gercek veritabaninda tablo
        # zaten vardi, yani hata yalnizca ESKI/BOS bir veritabaninda —
        # tam da gocun hedef kitlesinde — ortaya cikiyordu.
        #
        # Pragma islem DISINDA degistirilmeli; SQLite islem icinde
        # sessizce yok sayar.
        #
        # TRY/FINALLY SART. Pragma baglanti duzeyindedir ve bu baglanti bot
        # surecinin OMRU BOYUNCA acik. Islem icinde bir sey patlarsa
        # istisna yukari gider; `finally` olmazsa baglanti FK denetimi
        # KAPALI olarak yasamaya devam eder ve hasar gocun cok otesine,
        # surecin tum yazma islemlerine yayilir.
        ortak = [k for k in (
            "olusma_ts", "instrument_id", "yon", "ufuk_gun", "guven", "gerekce",
            "baslangic_fiyat", "para_birimi", "olcum_ts", "bitis_fiyat",
            "getiri_pct", "piyasa_getiri_pct", "anormal_pct", "isabet")
            if k in kolonlar]

        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            dagilim = self._predictions_tablosunu_tasi(ortak)
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA legacy_alter_table = OFF")

        log.info("predictions gocu: %s kayit tasindi, ajan dagilimi %s",
                 sum(dagilim.values()), dagilim)

    def _yarim_gocu_kurtar(self) -> None:
        """
        Yarim kalmis bir gocten kalan `predictions_eski`'yi ele alir.

        NEDEN SILMEK YANLIS: Python'un sqlite3 modulu eski kipte
        (`isolation_level=''`) islemi yalnizca DML'den ONCE aciyor;
        `ALTER TABLE` ve `CREATE TABLE` otomatik commit oluyor. Yani goc
        ortasinda bir hata olursa DDL geri SARILMIYOR ve veritabani su
        halde kaliyor: `predictions` BOS, veri `predictions_eski`'de.
        Olculdu 2026-08-16 (ariza enjekte edilerek).

        Bu durumda artik tabloyu silmek, KURTARILABILIR bir yarim gocu
        KALICI VERI KAYBINA cevirir. Once hangi tablonun gercek veriyi
        tuttuguna bakiliyor.
        """
        if not self.query("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='predictions_eski'"):
            return
        eski_n = self.query("SELECT COUNT(*) n FROM predictions_eski")[0]["n"]
        yeni_n = (self.query("SELECT COUNT(*) n FROM predictions")[0]["n"]
                  if self.query("SELECT name FROM sqlite_master "
                                "WHERE type='table' AND name='predictions'")
                  else -1)
        if eski_n > yeni_n:
            log.warning("yarim kalmis goc: predictions %s kayit, "
                        "predictions_eski %s kayit -> ESKISI GERI ALINIYOR",
                        yeni_n, eski_n)
            if yeni_n >= 0:
                self._conn.execute("DROP TABLE predictions")
            self._conn.execute(
                "ALTER TABLE predictions_eski RENAME TO predictions")
        else:
            log.warning("onceki gocten kalan predictions_eski (%s kayit) "
                        "siliniyor; canli tabloda %s kayit var", eski_n, yeni_n)
            self._conn.execute("DROP TABLE predictions_eski")
        self._conn.commit()

    @contextmanager
    def _goc_islemi(self):
        """
        DDL'I DE KAPSAYAN acik islem.

        `tx()` yetmiyor: sqlite3 eski kipte islemi yalnizca DML icin
        aciyor, dolayisiyla ALTER/CREATE/DROP onun disinda kaliyor ve
        geri sarilamiyor. SQLite'in KENDISI islemli DDL destekler —
        eksik olan sey Python katmaninin `BEGIN`'i acmasi. Burada acikca
        aciliyor ki tasima ya TAMAMEN olsun ya HIC olmasin.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def _predictions_tablosunu_tasi(self, ortak: list[str]) -> dict:
        """
        Tasima ISLEMI — sayim denetimi ISLEMIN ICINDE ve DROP'tan ONCE.

        Onceden denetim islemden sonra yapiliyordu: `RuntimeError` atildigi
        anda eski tablo COKTAN silinmis ve islem commit edilmis oluyordu.
        Yani denetim kaybi bildiriyordu, ENGELLEMIYORDU — otopsi, koruma
        degil. Simdi `raise` islemi geri sariyor ve veri yerinde kaliyor.

        Kaybi test etmek zor; dogru cevap testi guclendirmek degil, kaybi
        YAPISAL OLARAK IMKANSIZ kilmak.
        """
        with self._goc_islemi() as c:
            c.execute("ALTER TABLE predictions RENAME TO predictions_eski")
            c.execute("""
                CREATE TABLE predictions (
                    id            INTEGER PRIMARY KEY,
                    olusma_ts     TEXT NOT NULL,
                    instrument_id INTEGER NOT NULL
                                  REFERENCES instruments(id) ON DELETE CASCADE,
                    ajan          TEXT NOT NULL DEFAULT 'bilinmiyor',
                    signal_id     INTEGER REFERENCES signals(id) ON DELETE SET NULL,
                    yon           TEXT NOT NULL,
                    ufuk_gun      INTEGER NOT NULL,
                    guven         REAL,
                    gerekce       TEXT,
                    tez                  TEXT,
                    gecersizlesme_kosulu TEXT,
                    izlenecek_esik       TEXT,
                    baslangic_fiyat REAL NOT NULL,
                    para_birimi   TEXT,
                    olcum_ts      TEXT,
                    bitis_fiyat   REAL,
                    getiri_pct    REAL,
                    piyasa_getiri_pct REAL,
                    anormal_pct   REAL,
                    isabet        INTEGER,
                    UNIQUE (olusma_ts, instrument_id, ufuk_gun, ajan)
                )""")
            # `ajan` gerekce onekinden: "[teknik] ..." -> "teknik".
            # Onek yoksa 'bilinmiyor'; uydurmuyoruz.
            alanlar = ", ".join(ortak)
            c.execute(f"""
                INSERT INTO predictions (id, ajan, {alanlar})
                SELECT id,
                       CASE WHEN gerekce LIKE '[%]%'
                            THEN substr(gerekce, 2, instr(gerekce, ']') - 2)
                            ELSE 'bilinmiyor' END,
                       {alanlar}
                FROM predictions_eski""")

            # DENETIM BURADA: DROP'tan once, islem icinde. Sayilar
            # tutmuyorsa `raise` geri sarar ve eski tablo YERINDE kalir.
            yeni = c.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            eski = c.execute("SELECT COUNT(*) FROM predictions_eski").fetchone()[0]
            if yeni != eski:
                raise RuntimeError(
                    f"predictions gocunde kayit kaybi: {eski} -> {yeni}; "
                    "islem geri sarildi, eski tablo yerinde")

            dagilim = {a: n for a, n in c.execute(
                "SELECT ajan, COUNT(*) FROM predictions GROUP BY ajan")}
            c.execute("DROP TABLE predictions_eski")
        return dagilim

    def _anlik_finansal_kopyalarini_temizle(self) -> None:
        """
        ANLIK (days IS NULL) finansal kayitlar her collector calismasinda
        yeniden ekleniyordu — PRIMARY KEY icindeki `days` NULL oldugu icin
        cakisma hic olusmuyordu (SQLite'ta NULL != NULL).

        Degerler ayni oldugu icin hicbir sayi yanlis cikmiyordu, ama
        `finansal_seri(donem="anlik")` ayni kalemi 10 kez donduruyordu ve
        model bunu "10 ayri kayit" diye okuyabilirdi. Ayrica benzersiz
        indeks kopyalar dururken OLUSTURULAMAZ.

        En son dosyalanan (filed) satir tutulur.
        """
        var = self.query(
            """SELECT COUNT(*) c FROM (
                   SELECT 1 FROM fundamentals
                   GROUP BY instrument_id, concept, period_end,
                            COALESCE(days,-1), form, unit
                   HAVING COUNT(*) > 1)""")[0]["c"]
        if not var:
            return
        with self.tx() as c:
            c.execute(
                """DELETE FROM fundamentals WHERE rowid NOT IN (
                       SELECT MAX(rowid) FROM fundamentals
                       GROUP BY instrument_id, concept, period_end,
                                COALESCE(days,-1), form, unit)""")
            silinen = c.total_changes
        log.info("Anlik finansal kopyalari temizlendi: %d grup, ~%d satir",
                 var, silinen)

    def _haber_kopyalarini_birlestir(self) -> None:
        """
        Haber kimligi URL'den ICERIGE tasindi (bkz. _haber_anahtari). Eskiden
        yazilmis kayitlarda ayni makale iki satirda durabiliyor: biri
        news.google.com yonlendirmesi, digeri cozulmus yayinci linki.
        Birlestirmezsek olay-etki analizi ayni olayi iki kez sayar.
        """
        kopya = self.query(
            """SELECT title, substr(published_at,1,10) g, COUNT(*) n
               FROM news WHERE title IS NOT NULL AND title <> ''
               GROUP BY title, g HAVING n > 1""")
        if not kopya:
            return
        silinen = 0
        with self.tx() as c:
            for k in kopya:
                satirlar = c.execute(
                    """SELECT id, url, symbols, tier FROM news
                       WHERE title = ? AND substr(published_at,1,10) = ?
                       ORDER BY (url LIKE '%news.google.com%'), id""",
                    (k["title"], k["g"])).fetchall()
                tut = satirlar[0]                     # yonlendirme olmayan kazanir
                semboller = set()
                kademe = 0
                for s in satirlar:
                    semboller.update(x for x in (s["symbols"] or "").split(",") if x)
                    kademe = max(kademe, s["tier"] or 0)
                c.execute("UPDATE news SET symbols=?, tier=? WHERE id=?",
                          (",".join(sorted(semboller)), kademe, tut["id"]))
                for s in satirlar[1:]:
                    c.execute("DELETE FROM news WHERE id=?", (s["id"],))
                    silinen += 1
        log.info("Haber kopyalari birlestirildi: %d satir silindi", silinen)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------
    def upsert_instrument(
        self, symbol: str, venue: str, name: str | None = None,
        asset_type: str | None = None, currency: str | None = None,
        isin: str | None = None,
    ) -> int:
        symbol, venue = symbol.strip().upper(), venue.strip().upper()
        gecersiz = sembol_gecersiz(symbol)
        if gecersiz:
            raise ValueError(f"gecersiz sembol {symbol!r}: {gecersiz}")
        with self.tx() as c:
            c.execute(
                """INSERT INTO instruments (symbol, venue, name, asset_type, currency, isin)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(symbol, venue) DO UPDATE SET
                     name       = COALESCE(excluded.name, instruments.name),
                     asset_type = COALESCE(excluded.asset_type, instruments.asset_type),
                     currency   = COALESCE(excluded.currency, instruments.currency),
                     isin       = COALESCE(excluded.isin, instruments.isin)""",
                (symbol, venue, name, asset_type, currency, isin),
            )
            row = c.execute(
                "SELECT id FROM instruments WHERE symbol=? AND venue=?", (symbol, venue)
            ).fetchone()
        return int(row["id"])

    def upsert_prices(self, instrument_id: int, rows: Iterable[dict], source: str,
                      currency: str | None = None) -> int:
        """
        `currency` ZORUNLU DEGIL ama VERILMELI. Yoklugu sahada su hataya
        yol acti: Yahoo'nun USD serisi EUR portfoy degerleriyle yan yana
        kullanildi ve her seviye yanlis para biriminde cikti.
        """
        payload = [
            (instrument_id, r["ts"], r.get("open"), r.get("high"), r.get("low"),
             r.get("close"), r.get("volume"), source, r.get("currency") or currency)
            for r in rows
        ]
        if not payload:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO prices
                   (instrument_id, ts, open, high, low, close, volume, source, currency)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(instrument_id, ts, source) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume,
                     currency=COALESCE(excluded.currency, prices.currency)""",
                payload,
            )
        return len(payload)

    def fiyat_kaynagi(self, instrument_id: int) -> dict | None:
        """
        Bir enstruman icin KULLANILACAK TEK fiyat kaynagini secer.

        NEDEN SART: `prices` ayni enstruman icin birden fazla kaynak
        tutabiliyor ve bunlar FARKLI PARA BIRIMINDE olabiliyor. ASML'de
        hem Yahoo (USD 1844) hem Alpha Vantage (EUR 1579.60) serisi var.
        Kaynak filtresi olmayan bir sorgu ikisini KARISTIRIR ve SMA/RSI
        birbirine karismis iki para biriminden hesaplanir — sayi uretilir,
        hepsi yanlis cikar, hicbiri hata vermez.

        Secim kurali UC ADIMLI:
          1. Pozisyonun para birimiyle ESLESEN kaynaklar (kullanicinin
             ekraninda gordugu para birimi odur).
          2. SIG SERILER ELENIR. Bir kaynak tek bir gunluk bar yazmis
             olabilir (`midas` seans icinde BIST kapanisi yaziyor);
             derinligi olan bir alternatif varken onu secmek SMA200'u,
             RSI'i ve tum olay penceresini yok eder.
          3. Kalanlar icinde EN TAZE kazanir, esitlikte en cok barli.
             Onceden yalnizca bar sayisina bakiliyordu ve bu, portfoyun
             %41'ini gorunmez yapmisti: ASML'nin EUR serisi Alpha
             Vantage'dan 100 barla geliyor ve AV'nin gunluk kotasi
             tukendigi icin 14 Agustos'ta kalmisti; Yahoo'nun Amsterdam
             kotasyonu ayni para biriminde 508 barla ve GUNCEL duruyordu.
        """
        kaynaklar = self.query(
            """SELECT source, currency, COUNT(*) bar, MAX(ts) son
               FROM prices WHERE instrument_id = ?
               GROUP BY source, currency""", (instrument_id,))
        if not kaynaklar:
            return None
        if len(kaynaklar) == 1:
            return dict(kaynaklar[0])
        # SAHIP FILTRESI YOK, BILEREK. Soru "kim tutuyor" degil, "bu
        # enstruman hangi para biriminde tutuluyor" — piyasa katmanina
        # ait bir karar. Iki kisi ayni kagidi ayni para biriminde tutar;
        # sahibe gore fiyat kaynagi secmek ayni enstruman icin iki farkli
        # seri secilmesine yol acardi.
        poz = self.query(
            """SELECT currency FROM positions WHERE instrument_id = ?
               ORDER BY snapshot_ts DESC LIMIT 1""", (instrument_id,))
        hedef = (poz[0]["currency"] if poz else None) or ""
        # SIG SERI KAPISI TUM ADAYLARA UYGULANIR, yalnizca para birimi
        # eslesenlere DEGIL.
        #
        # Eski sira "once para birimi, sonra derinlik"ti ve derinlik
        # kontrolu ESLESEN KUME ICINDE kaliyordu: eslesen tek aday sig
        # olsa bile `or aday` ile geri geliyordu. Olculdu 2026-08-21:
        # TSLA'nin pozisyonu EUR, ve EUR "kaynagi" 13 barlik TSLA.AS
        # SERTIFIKA serisiydi (4,07 EUR). 505 barlik gercek USD serisi
        # dururken 13 barlik sertifika seciliyordu — o seriyle SMA50 de
        # RSI de olay penceresi de hesaplanamaz.
        #
        # Yeni sira: para birimi eslesmesi TERCIH, derinlik SART.
        # Eslesen adaylarin hepsi sigsa TUM kaynaklara donulur; hicbiri
        # derin degilse eldekinin en iyisi alinir (eski davranis).
        # Para birimi cikti sozlesmesinde ZATEN beyan ediliyor, yani
        # farkli para biriminde derin bir seri sessiz kalmiyor.
        eslesen = [k for k in kaynaklar if (k["currency"] or "") == hedef]
        derin = [k for k in eslesen if k["bar"] >= self.ASGARI_SERI_BARI]
        if not derin:
            derin = ([k for k in kaynaklar if k["bar"] >= self.ASGARI_SERI_BARI]
                     or eslesen or kaynaklar)
        sirali = sorted(derin, key=lambda k: ((k["son"] or ""), k["bar"]),
                        reverse=True)
        return dict(sirali[0])

    # Bir kaynagin "seri" sayilmasi icin gereken en az bar. 30 secildi:
    # SMA20 ve RSI14 icin yeter, ama seans ici tek bar yazan bir kaynagi
    # (BIST'te `midas`) derin bir alternatifin onune gecirmez.
    ASGARI_SERI_BARI = 30

    def fiyat_serisi(self, instrument_id: int, limit: int = 300,
                     bitis: str | None = None) -> list:
        """
        TEK kaynaktan gunluk seri, ARTAN tarih sirali. Teknik analizin
        girdisi burasi olmali — dogrudan `prices` sorgulamak para birimi
        karistirir (bkz. fiyat_kaynagi).

        `bitis` — LOOK-AHEAD KAPISI. Verilirse o TARIHTEN SONRAKI barlar
        hic donmez. Backtest'in var olma sebebi budur: gecmisteki bir
        gunde uretilmis gibi davranan bir sinyal, o gun HENUZ OLMAMIS
        fiyatlari gorurse olcum degil kehanet uretir. Tek bir yerden
        gecirmek sart, cunku gostergeler (RSI, SMA) bu seriden turuyor;
        suzgeci cagiranin insafina birakmak, bir yolda unutulup sessizce
        gelecege bakmak demekti.

        KUCUK BIR SIZINTI BILEREK KABUL EDILDI VE BURAYA YAZILIYOR:
        `fiyat_kaynagi()` kaynak secerken MAX(ts)'e bakiyor, yani
        `bitis`ten SONRAKI veriye. Fiyat SEVIYESINI etkilemiyor (secim
        para birimi ve derinlik uzerine) ve backtest penceresi boyunca
        secim degismiyor — BIST'te hep `yahoo_bist`. Yine de bir
        varsayimdir; kaynak dagilimi degisirse yeniden dusunulmeli.
        """
        k = self.fiyat_kaynagi(instrument_id)
        if not k:
            return []
        # PARA BIRIMI DE SUZULUYOR — kaynak adi TEK BASINA YETMIYOR.
        #
        # `prices` birincil anahtari (instrument_id, ts, source) ve para
        # birimi ANAHTARDA YOK: ayni kaynak adi altinda iki para
        # biriminde bar durabiliyor. Kaynak secimi (`fiyat_kaynagi`)
        # (source, currency) CIFTINI seciyor ama sorgu yalnizca `source`
        # ile suzuyordu, yani secilen ciftin DISINDAKI barlar da doniyordu.
        #
        # OLCULDU 2026-08-21, CANLI VERIDE — teorik degil:
        #   TSLA  : 389 USD bar (205..490) + 11 EUR bar (4,07..9,13)
        #   MSFT  : 398 USD bar + 2 EUR bar (6,77 · 6,94)
        #   SHELL.AS: 389 USD + 11 EUR
        # TSLA'nin serisinde 4,07 ile 489,88 YAN YANA duruyordu; gunluk
        # getiri +%10.464 cikiyor, RSI/SMA/oynaklik/korelasyon hepsi
        # cop uretiyor ve HICBIRI hata vermiyor.
        #
        # EUR barlarin kendisi de ayri bir hikaye: tarihleri ABD borsa
        # TATILLERI (MLK, Memorial Day, Juneteenth, 4 Temmuz...) — ABD
        # kapaliyken Euronext acik ve o gun yakalanan sey TSLA.AS
        # SERTIFIKASI, hissenin kendisi degil. Sertifika kapisi
        # `prices.py`'de sonradan konuldu ve bugun calisiyor; bu satirlar
        # ondan ONCEKI donemden kalma.
        #
        # SIKI ESITLIK, `IS NULL` TOLERANSI YOK: canli veride etiketsiz
        # bar SIFIR (797.738/797.738 etiketli, olculdu). Etiketsiz bir
        # bar ileride olusursa (or. `fast_info` duserse) seri BAYAT
        # gorunur ve bayatlik bekcisi bunu soyler — sessizce yanlis para
        # biriminde bir bar eklemekten iyidir.
        ccy = k["currency"]
        if bitis:
            return self.query(
                """SELECT * FROM (
                       SELECT ts, open, high, low, close, volume, currency, source
                       FROM prices
                       WHERE instrument_id = ? AND source = ? AND ts <= ?
                         AND currency IS ?
                       ORDER BY ts DESC LIMIT ?
                   ) ORDER BY ts ASC""",
                (instrument_id, k["source"], bitis, ccy, limit))
        return self.query(
            """SELECT * FROM (
                   SELECT ts, open, high, low, close, volume, currency, source
                   FROM prices WHERE instrument_id = ? AND source = ?
                     AND currency IS ?
                   ORDER BY ts DESC LIMIT ?
               ) ORDER BY ts ASC""", (instrument_id, k["source"], ccy, limit))

    def piyasa_vekili(self, instrument_id: int) -> dict | None:
        """
        Bir enstrumanin PIYASA VEKILI serisini bulur (olay calismasindaki
        alfa/beta modeli icin).

        Eslesme PARA BIRIMINE gore: piyasa modeli enstruman getirisini
        piyasa getirisine regresyon eder. Ikisi farkli para biriminde
        olursa beta kur hareketini de icine ceker ve anormal getiri kur
        gurultusuyle kirlenir.

            EUR  -> AEX          (Amsterdam kotasyonlari)
            USD  -> QQQ          (ABD kotasyonlari; portfoy tekno agirlikli)
            USDT -> BTC          (kripto beta'si standart olarak BTC'ye olculur)
            TRY  -> XU100        (Is Yatirim cevabindaki END_DEGER alani)

        Enstrumanin KENDISI vekilse None doner — kendine regresyon
        anlamsiz olurdu (beta=1, anormal getiri her zaman 0).
        """
        k = self.fiyat_kaynagi(instrument_id)
        if not k:
            return None
        kendisi = self.query(
            "SELECT symbol, venue, asset_type FROM instruments WHERE id = ?",
            (instrument_id,))
        if kendisi and kendisi[0]["venue"] == "INDEX":
            return None
        ccy = (k["currency"] or "").upper()

        # VARLIK SINIFI PARA BIRIMINDEN ONCE GELIR.
        #
        # Olculdu 2026-08-16: Binance'te listelenmeyen 21 referans coin
        # (XMR, HYPE, OKB, KAS...) CoinGecko'dan USD olarak geliyor ve
        # yalnizca para birimine bakan esleme onlari QQQ'ya baglıyordu.
        # Yani Monero'nun anormal getirisi NASDAQ-100 regresyonuyla
        # hesaplanip "piyasa modeli" diye beyan edilecekti. Kur gurultusu
        # gerekcesi burada da gecerli ama BASKINI degil: USD ile USDT
        # arasindaki fark ~%0,1 ve trendsiz, oysa kripto ile Nasdaq
        # arasindaki beta farki yapisal.
        #
        # Kripto icin dogru vekil, kotasyon para birimi ne olursa olsun
        # BTC'dir — sektorun beta olcusu standart olarak odur.
        varlik = (kendisi[0]["asset_type"] or "").lower() if kendisi else ""
        if varlik == "crypto":
            hedef = ("BTC", "BINANCE")
        else:
            hedef = {"EUR": ("AEX", "INDEX"), "USD": ("QQQ", "INDEX"),
                     "TRY": ("XU100", "INDEX"),
                     "USDT": ("BTC", "BINANCE")}.get(ccy)
        if not hedef:
            return None
        sembol, venue = hedef
        if kendisi and kendisi[0]["symbol"] == sembol:
            return None                    # BTC'nin vekili BTC olamaz
        r = self.query(
            "SELECT id FROM instruments WHERE symbol=? AND venue=? LIMIT 1",
            (sembol, venue))
        if not r:
            return None
        return {"instrument_id": r[0]["id"], "sembol": sembol,
                "para_birimi": ccy}

    def fx_kuru(self, base: str, quote: str, ts: str | None = None) -> dict | None:
        """
        1 <base> kac <quote> eder. Tarih verilirse O TARIHTEN ONCEKI en yakin
        kur (ileriye bakmak gelecek bilgisi sizdirir), yoksa en guncel.

        Ters cift de denenir: EUR/USD yoksa USD/EUR'un tersi kullanilir.

        UCUNCU YOL — FIYAT SERISINDEN TUREV (`kaynak: "seri:..."`).
        `fx_rates` yalnizca gercek kur ciftlerini tasiyor (EUR/USD,
        USD/TRY, EUR/TRY). Ama bir POZISYON PARA BIRIMI her zaman bir kur
        cifti degildir: Binance hesabi USDT cinsinden ve `fx_rates`'te
        USDT'li SIFIR satir var. Sonuc olculdu (2026-08-20): hesabin
        %98,6'si cevrilemedigi icin gunluk degisim "kapsam %1" ile
        reddedildi ve `maruziyet` o pozisyonlari agirlik disinda birakti.

        PEG VARSAYILMIYOR. USDT'nin USD fiyati ZATEN OLCULU: `cgfiyat`
        365 barlik seri yaziyor ve son deger 0,99925 — 1,0 DEGIL. Yani
        dogru cevap "stablecoin'dir, 1 kabul et" degil, "olculen fiyati
        kullan". Kural dar tutuldu: sembolu `base`'e ESIT bir enstruman
        ve serisi tam olarak `quote` para biriminde olacak.
        """
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return {"base": base, "quote": quote, "rate": 1.0, "ts": ts, "kaynak": "ayni"}
        kosul = "AND ts <= ?" if ts else ""
        par = (base, quote) + ((ts,) if ts else ())
        r = self.query(f"""SELECT ts, rate, source FROM fx_rates
                           WHERE base=? AND quote=? {kosul}
                           ORDER BY ts DESC LIMIT 1""", par)
        if r:
            return {"base": base, "quote": quote, "rate": r[0]["rate"],
                    "ts": r[0]["ts"], "kaynak": r[0]["source"]}
        par = (quote, base) + ((ts,) if ts else ())
        r = self.query(f"""SELECT ts, rate, source FROM fx_rates
                           WHERE base=? AND quote=? {kosul}
                           ORDER BY ts DESC LIMIT 1""", par)
        if r and r[0]["rate"]:
            return {"base": base, "quote": quote, "rate": 1.0 / r[0]["rate"],
                    "ts": r[0]["ts"], "kaynak": r[0]["source"] + " (ters cevrildi)"}

        # --- 3) FIYAT SERISINDEN TUREV ---------------------------------
        d = self._seriden_kur(base, quote, ts)
        if d:
            return d
        d = self._seriden_kur(quote, base, ts)
        if d and d["rate"]:
            return {"base": base, "quote": quote, "rate": 1.0 / d["rate"],
                    "ts": d["ts"], "kaynak": d["kaynak"] + " (ters cevrildi)"}

        # --- 4) TEK ARA BIRIM UZERINDEN (ucgenleme) --------------------
        #
        # USDT -> EUR boyle cozuluyor: USDT->USD olculu seriden,
        # USD->EUR `fx_rates`ten. Spot kurlarda bu CARPIM TAM, yaklasik
        # degil.
        #
        # TEK ADIM ve YALNIZCA GERCEK KUR CIFTLERI uzerinden. Sinirsiz
        # zincir, uzun yollarda sessizce sacma kurlar uretirdi; ara
        # birim havuzu `fx_rates`in kendi para birimleriyle sinirli
        # (bugun EUR/USD/TRY).
        for ara in self._kur_birimleri():
            if ara in (base, quote):
                continue
            a = self._tek_adim(base, ara, ts)
            b = self._tek_adim(ara, quote, ts)
            if a and b:
                return {"base": base, "quote": quote,
                        "rate": a["rate"] * b["rate"],
                        "ts": min(a["ts"], b["ts"]),
                        "kaynak": f"{a['kaynak']} x {b['kaynak']} ({ara} uzerinden)"}
        return None

    def _kur_birimleri(self) -> list[str]:
        """`fx_rates`te gecen para birimleri — ara birim havuzu."""
        return sorted({r["c"] for r in self.query(
            "SELECT base c FROM fx_rates UNION SELECT quote c FROM fx_rates")})

    def _tek_adim(self, base: str, quote: str, ts: str | None) -> dict | None:
        """
        TEK adimlik kur: dogrudan / ters / seriden. UCGENLEME YAPMAZ —
        `fx_kuru`ya geri cagirmak sonsuz dongu ve zincirleme uretirdi.
        """
        if base == quote:
            return {"rate": 1.0, "ts": ts or "", "kaynak": "ayni"}
        kosul = "AND ts <= ?" if ts else ""
        for a, b, ters in ((base, quote, False), (quote, base, True)):
            par = (a, b) + ((ts,) if ts else ())
            r = self.query(f"""SELECT ts, rate, source FROM fx_rates
                               WHERE base=? AND quote=? {kosul}
                               ORDER BY ts DESC LIMIT 1""", par)
            if r and r[0]["rate"]:
                return {"rate": (1.0 / r[0]["rate"]) if ters else r[0]["rate"],
                        "ts": r[0]["ts"],
                        "kaynak": r[0]["source"] + (" (ters)" if ters else "")}
        d = self._seriden_kur(base, quote, ts)
        if d:
            return {"rate": d["rate"], "ts": d["ts"], "kaynak": d["kaynak"]}
        d = self._seriden_kur(quote, base, ts)
        if d and d["rate"]:
            return {"rate": 1.0 / d["rate"], "ts": d["ts"],
                    "kaynak": d["kaynak"] + " (ters)"}
        return None

    def _seriden_kur(self, base: str, quote: str, ts: str | None) -> dict | None:
        """
        Sembolu `base` olan bir enstrumanin `quote` cinsinden son fiyati.

        DAR KAPI: sembol TAM esit ve seri para birimi TAM esit olmali.
        Gevsetilirse (or. "yakin sembol") sessizce YANLIS kur uretirdi ve
        bu, portfoy degerini bozmanin en sinsi yoludur.
        """
        kosul = "AND p.ts <= ?" if ts else ""
        par = (base.upper(), quote.upper()) + ((ts,) if ts else ())
        r = self.query(f"""SELECT p.ts, p.close, p.source
                           FROM prices p JOIN instruments i ON i.id = p.instrument_id
                           WHERE UPPER(i.symbol) = ? AND UPPER(p.currency) = ?
                                 {kosul} AND p.close > 0
                           ORDER BY p.ts DESC LIMIT 1""", par)
        if not r:
            return None
        return {"base": base.upper(), "quote": quote.upper(),
                "rate": float(r[0]["close"]), "ts": r[0]["ts"],
                "kaynak": f"seri:{r[0]['source']}"}

    def upsert_prices_hourly(self, instrument_id: int, rows: Iterable[dict],
                             source: str, currency: str | None = None) -> int:
        """
        Saatlik barlar AYRI tabloya yazilir — `prices` ile karistirilmaz.
        Gerekcesi schema.sql'de: gunluk varsayan tum hesaplar bozulurdu.

        `currency` tum satirlara uygulanir; satirin kendi `currency`
        alani varsa o kazanir. Gunluk tablodaki dersle ayni: para birimi
        VERININ PARCASIDIR, sonradan tahmin edilmez.
        """
        payload = [
            (instrument_id, r["ts"], r.get("open"), r.get("high"), r.get("low"),
             r.get("close"), r.get("volume"), r.get("quote_volume"),
             r.get("trades"), source, r.get("currency") or currency)
            for r in rows
        ]
        if not payload:
            return 0
        # DAMGA BICIMI DOGRULANIYOR — 'YYYY-MM-DD HH:MM', SIFIR DOLGULU.
        #
        # Butun sorgular `ORDER BY ts` ile SOZLUK SIRALAMASINA guveniyor
        # ve dolgusuz bir saat onu sessizce bozar: "2026-08-20 8:00"
        # sozlukte "2026-08-20 13:30"dan BUYUKTUR, yani sabahki bar
        # ogleden sonrakinden "yeni" gorunur. `saatlik_kaynagi` en taze
        # kaynagi bu siraya gore seciyor — yanlis seri secilir ve
        # HICBIR HATA VERMEZ. Bu bir programlama hatasidir, GURULTULU
        # patlamali (olculdu: kendi testimde tam bu oldu).
        import re as _re
        kotu = [p[1] for p in payload
                if not _re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}",
                                     str(p[1]))]
        if kotu:
            raise ValueError(
                "prices_hourly.ts bicimi 'YYYY-MM-DD HH:MM' olmali "
                f"(sifir dolgulu). Gecersiz: {kotu[:3]}")
        with self.tx() as c:
            c.executemany(
                """INSERT INTO prices_hourly
                   (instrument_id, ts, open, high, low, close, volume,
                    quote_volume, trades, source, currency)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(instrument_id, ts, source) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume,
                     quote_volume=excluded.quote_volume, trades=excluded.trades,
                     currency=COALESCE(excluded.currency,
                                       prices_hourly.currency)""",
                payload,
            )
        return len(payload)

    def saatlik_kaynagi(self, instrument_id: int) -> dict | None:
        """
        Saatlik seri icin KULLANILACAK TEK (kaynak, para birimi) cifti.

        `fiyat_kaynagi` ile ayni gerekce ve ayni ders: ayni enstrumanda
        birden fazla saatlik kaynak olabilir (kriptoda `binance`,
        hissede `yahoo_saatlik`) ve para birimi ANAHTARDA YOK. Kaynak
        adiyla suzmeyen bir sorgu iki para birimini karistirir; gunluk
        tabloda tam bu, TSLA'nin serisine 4,07 EUR ile 489,88 USD'yi yan
        yana koymustu.

        En TAZE seri kazanir, esitlikte en cok barli.
        """
        kaynaklar = self.query(
            """SELECT source, currency, COUNT(*) bar, MAX(ts) son
               FROM prices_hourly WHERE instrument_id = ?
               GROUP BY source, currency""", (instrument_id,))
        if not kaynaklar:
            return None
        sirali = sorted(kaynaklar, key=lambda k: ((k["son"] or ""), k["bar"]),
                        reverse=True)
        return dict(sirali[0])

    def saatlik_seri(self, instrument_id: int, limit: int = 168) -> list[sqlite3.Row]:
        """
        Son N saatlik bar, ARTAN tarih sirali (varsayilan 7 gun).

        TEK KAYNAK + TEK PARA BIRIMI (`saatlik_kaynagi`). Gunluk
        `fiyat_serisi` ile ayni disiplin; oradaki kusur canli veride
        olculdu ve saatlik tarafta tekrarlanmasin diye kapi bastan kondu.
        """
        k = self.saatlik_kaynagi(instrument_id)
        if not k:
            return []
        return self.query(
            """SELECT * FROM (
                   SELECT ts, open, high, low, close, volume, quote_volume,
                          trades, currency, source
                   FROM prices_hourly WHERE instrument_id = ?
                     AND source = ? AND currency IS ?
                   ORDER BY ts DESC LIMIT ?
               ) ORDER BY ts ASC""",
            (instrument_id, k["source"], k["currency"], limit))

    def pozisyon_enstrumani(self, symbol: str, account: str,
                            name: str | None = None,
                            asset_type: str | None = None,
                            currency: str | None = None) -> int:
        """
        Ekrandan okunan bir sembolu KATALOGDAKI enstrumana baglar.

        ONCE VAR OLANI ARA, SONRA YARAT. Eski davranis kosulsuz
        `upsert_instrument(symbol, account.upper())` idi; katalogda
        aynisi dursa bile aracinin adiyla IKINCI bir kayit aciyordu.
        Ikinci kayit fiyat serisi olmayan bir kopya oluyor ve pozisyon
        oraya bagli kaldigi icin degerleme/teknik/tez zincirinin TAMAMI
        o pozisyon icin sessizce korlesiyor (bkz. HESAP_VENUE).

        ARAMA SINIFI ASMAZ: kripto hesabi yalnizca kripto venue'lariyla,
        hisse hesabi yalnizca hisse venue'lariyla eslesir. Yoksa bir
        Binance ekranindaki `GRAM` BIST'teki GRAM hissesine baglanirdi.
        """
        symbol = (symbol or "").strip().upper()
        tercih = HESAP_VENUE.get(account.strip().lower(), account.strip().upper())
        kripto_mu = tercih in KRIPTO_VENUE
        adaylar = [
            r for r in self.query(
                "SELECT id, venue FROM instruments WHERE UPPER(symbol) = ?",
                (symbol,))
            if r["venue"] not in POZISYONSUZ_VENUE
            and ((r["venue"] in KRIPTO_VENUE) == kripto_mu)
        ]
        # Kendi venue'su varsa o kazanir; yoksa kalanlar arasinda
        # DETERMINISTIK sec (ada gore) — "bazen su, bazen bu" bagli
        # bir portfoy, hic baglanmamaktan daha kotudur.
        adaylar.sort(key=lambda r: (r["venue"] != tercih, r["venue"]))
        hedef_venue = adaylar[0]["venue"] if adaylar else tercih
        # `upsert_instrument` ile devam ediliyor: ad/para birimi gibi
        # ekrandan gelen alanlar mevcut kaydi ZENGINLESTIRSIN (COALESCE
        # oldugu icin dolu olani ezmez).
        return self.upsert_instrument(
            symbol, hedef_venue, name, asset_type, currency)

    def insert_positions(self, account: str, snapshot_ts: str,
                         rows: Iterable[dict], sahip: str) -> int:
        """
        `sahip` ZORUNLU ve varsayilani YOK. Yanlis kisinin portfoyune
        yazmak bu isin tek gercek tehlikesi; sessiz varsayilan onu
        kaza degil TASARIM haline getirirdi.
        """
        if not sahip:
            raise ValueError("insert_positions: sahip zorunlu")
        n = 0
        with self.tx() as c:
            for r in rows:
                iid = self.pozisyon_enstrumani(
                    r["symbol"], account, r.get("name"),
                    r.get("asset_type"), r.get("currency")
                )
                c.execute(
                    """INSERT INTO positions
                       (sahip, snapshot_ts, account, instrument_id, quantity,
                        avg_cost, last_price, market_value, pnl_abs, pnl_pct,
                        currency)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(sahip, snapshot_ts, account, instrument_id)
                       DO UPDATE SET
                         quantity=excluded.quantity, avg_cost=excluded.avg_cost,
                         last_price=excluded.last_price, market_value=excluded.market_value,
                         pnl_abs=excluded.pnl_abs, pnl_pct=excluded.pnl_pct""",
                    (sahip, snapshot_ts, account.lower(), iid, r.get("quantity"), r.get("avg_cost"),
                     r.get("last_price"), r.get("market_value"), r.get("pnl_abs"),
                     r.get("pnl_pct"), r.get("currency")),
                )
                n += 1
        return n

    def upsert_disclosures(self, rows: Iterable[dict], source: str = "kap") -> int:
        payload = [
            (r.get("id") or sha1(r.get("url", "") + r.get("title", "")),
             r.get("published_at"), r.get("symbol"), r.get("company"),
             r.get("category"), r.get("title"), r.get("url"), r.get("body"), source)
            for r in rows
        ]
        if not payload:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO disclosures
                   (id, published_at, symbol, company, category, title, url, body, source)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     body=COALESCE(excluded.body, disclosures.body)""",
                payload,
            )
        return len(payload)

    # --- temel veri (XBRL) ----------------------------------------------
    def upsert_fundamentals(self, rows) -> int:
        rows = list(rows)
        if not rows:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO fundamentals
                   (instrument_id, concept, unit, period_start, period_end, days,
                    val, form, fy, fp, frame, filed, accn)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(instrument_id, concept, period_end, COALESCE(days,-1), form, unit)
                   DO UPDATE SET val=excluded.val, frame=COALESCE(excluded.frame, fundamentals.frame)""",
                [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
                  r[10], r[11], r[12]) for r in rows],
            )
        return len(rows)

    def finansal_seri(self, instrument_id: int, kavramlar: list[str],
                      donem: str = "yillik", limit: int = 6) -> list[sqlite3.Row]:
        """
        Karsilastirilabilir donem serisi dondurur.

        `donem`:
          "yillik"  -> 350-380 gunluk kayitlar (yillik)
          "yariyil" -> 175-190 gunluk kayitlar (6 aylik)
          "ceyrek"  -> 80-100 gunluk kayitlar (ceyregin KENDISI, kumulatif degil)
          "anlik"   -> bilanco kalemleri (days IS NULL)

        Gun bandi filtresi SART: ayni kavram ayni dosyalamada hem 6 aylik hem
        3 aylik geliyor; filtresiz sorgu ikisini karistirir ve gelir/kar
        karsilastirmasi tamamen yanlis cikar.

        YARIYIL KOVASI SONRADAN EKLENDI. BIST sirketlerinin cogu 6 aylik
        raporluyor (days=181) ve bu deger NE yillik NE ceyrek bandina
        giriyordu: 57 sirketin EN GUNCEL verisi (2026-06-30) hicbir
        sorguda gorunmuyordu. Bant genisletmek yanlis olurdu — 181 ile
        365'i ayni kovaya koymak tam da bu fonksiyonun onlemek icin var
        oldugu karsilastirma hatasi.
        """
        yer = ",".join("?" * len(kavramlar))
        if donem == "anlik":
            kosul = "f.days IS NULL"
        elif donem == "ceyrek":
            kosul = "f.days BETWEEN 80 AND 100"
        elif donem == "yariyil":
            kosul = "f.days BETWEEN 175 AND 190"
        else:
            kosul = "f.days BETWEEN 350 AND 380"
        return self.query(
            f"""SELECT f.concept, f.val, f.unit, f.period_start, f.period_end,
                       f.days, f.form, f.fy, f.fp, f.frame
                FROM fundamentals f
                WHERE f.instrument_id = ? AND f.concept IN ({yer}) AND {kosul}
                ORDER BY f.period_end DESC LIMIT ?""",
            (instrument_id, *kavramlar, limit),
        )

    def finansal_ozet(self, instrument_id: int) -> dict:
        """
        Bir enstrumanin en son yillik + ceyreklik + bilanco anlik goruntusu.
        Kavram adlari Turkcelestirilir; gelir icin uc alternatif etiketten
        dolu olani kullanilir.
        """
        # BIST KAVRAMLARI DA DAHIL.
        #
        # Onceden yalnizca XBRL kavramlari sorulaniyordu ve BIST tarafi
        # tamamen gorunmezdi: 97 BIST sirketinin bilancosu tabloda
        # DURURKEN arac "ASELS icin XBRL verisi yok" donduruyordu.
        # Teknik olarak dogru, pratikte YANLIS BEYAN — bu projenin en
        # kotu hata sinifi (kendi veritabanimiz hakkinda "yok" demek).
        # `NetKarTTM` BILEREK DISARIDA: takvim donemi degil, seriye
        # girmemeli (bkz. scripts/ttm_temizle.py).
        from ..collectors.xbrl import KAVRAMLAR
        kavramlar = list(KAVRAMLAR) + list(BIST_KAVRAMLARI)

        def topla(donem: str, n: int) -> list[dict]:
            gruplar: dict[str, dict] = {}
            for r in self.finansal_seri(instrument_id, kavramlar, donem, limit=400):
                anahtar = r["period_end"]
                g = gruplar.setdefault(anahtar, {
                    "donem_sonu": r["period_end"], "donem_basi": r["period_start"],
                    "gun": r["days"], "form": r["form"], "mali_yil": r["fy"],
                    "ceyrek": r["fp"], "takvim": r["frame"], "kalemler": {},
                })
                etiket = KAVRAMLAR.get(r["concept"]) or BIST_KAVRAMLARI[r["concept"]]
                # Gelir icin birden fazla kavram var; ilk doleni tut.
                g["kalemler"].setdefault(etiket, {"deger": r["val"], "birim": r["unit"]})
            return sorted(gruplar.values(), key=lambda x: x["donem_sonu"],
                          reverse=True)[:n]

        out = {
            "yillik": topla("yillik", 3),
            "yariyil": topla("yariyil", 4),
            "ceyreklik": topla("ceyrek", 4),
            "bilanco": topla("anlik", 2),
        }
        # TTM AYRI BASLIK ALTINDA. Donem serisine karistirilmaz ama
        # gizlenmez de: BIST'te en guncel kar rakami cogu zaman budur.
        ttm = self.finansal_seri(instrument_id, ["NetKarTTM"], "yillik", limit=1)
        if ttm:
            out["ttm"] = {"net_kar": ttm[0]["val"], "birim": ttm[0]["unit"],
                          "olcum_tarihi": ttm[0]["period_end"],
                          "not": ("SON 12 AY — takvim donemi DEGIL. Yukaridaki "
                                  "yillik/yariyil kalemleriyle AYNI TABLOYA "
                                  "koyma, ayri soyle.")}
        kaynaklar = {r["form"] for blok in out.values()
                     if isinstance(blok, list) for r in blok if r.get("form")}
        out["not"] = (
            "Donem uzunlugu 'gun' alaninda; FARKLI uzunluktaki donemler "
            "KARSILASTIRILMAZ (yillik ile yariyil ayni tabloya girmez). "
            + ("Kaynak: " + ", ".join(sorted(kaynaklar)) + ". "
               if kaynaklar else "")
            + "XBRL = sirketin SEC dosyalamasi; midasbilanco = BIST bilanco "
              "sayfasi.")
        return out

    def finansal_kapsam(self) -> list[sqlite3.Row]:
        return self.query(
            """SELECT i.symbol, i.name, COUNT(*) n, MAX(f.period_end) son
               FROM fundamentals f JOIN instruments i ON i.id = f.instrument_id
               GROUP BY i.symbol ORDER BY i.symbol""")

    # --- arastirma ------------------------------------------------------
    def research_targets(self, kripto: bool | None = False) -> list[sqlite3.Row]:
        """
        Arastirilacak enstrumanlar = guncel portfoy pozisyonlari ∪ izleme listesi.

        Nakit haric tutulur: arastirilacak bir sirketi yok.

        KRIPTO VARSAYILAN OLARAK HARIC (`kripto=False`). Sebep: bu fonksiyonu
        cagiran hisse collector'lari (edgar, xbrl, prices, stocknews) kripto
        sembolunu alirsa YANLIS VERI ceker — "Bitcoin" SEC'de aranir, BTC
        Yahoo'da baska bir enstrumana denk gelir. Kripto collector'lari
        `kripto=True` ile yalnizca kendi evrenini alir.
        `kripto=None` ikisini birden dondurur (portfoy ozeti gibi yerler icin).

        AYNI SIRKET IKI KEZ TARANMAZ. Katalog ve ekran goruntusu ayni sirketi
        farkli sembolle kaydedebiliyor (ASML / ASML.AS, ADYEN / ADYEN.AS);
        ikisi de hedef olsaydi ayni EDGAR ve haber sorgusu iki kez calisir,
        rapora da ayni gelisme iki farkli sembolle girerdi.
        """
        # IKI KRIPTO VENUE'SU VAR. 'BINANCE' = alinip satilabilen; 'CRYPTO' =
        # ilk 100'de olup Binance'te listelenmeyen REFERANS coin (HYPE, XMR,
        # OKB...). Ikisi de kripto sayilir: 'CRYPTO' hisse tarafina sizarsa
        # EDGAR'da "Monero" aranir, Yahoo'da XMR baska bir enstrumana denk
        # gelir — kimlik cozumunun engellemek icin var oldugu hatanin aynisi.
        filtre = {False: "AND i.venue NOT IN ('BINANCE','CRYPTO')",
                  True: "AND i.venue IN ('BINANCE','CRYPTO')",
                  None: ""}[kripto]
        rows = self._research_targets_ham(filtre)
        gorulen: dict[str, sqlite3.Row] = {}
        for r in rows:
            anahtar = _ad_anahtari(r["name"]) or r["symbol"].upper()
            onceki = gorulen.get(anahtar)
            if onceki is None:
                gorulen[anahtar] = r
                continue
            # Sonekli katalog sembolu yerine pozisyonda kullanilani tut:
            # portfoy kayitlari ve K/Z gecmisi ona bagli.
            if "." in onceki["symbol"] and "." not in r["symbol"]:
                gorulen[anahtar] = r
        return list(gorulen.values())

    def _research_targets_ham(self, kripto_filtresi: str = "") -> list[sqlite3.Row]:
        return self.query(f"""
            SELECT DISTINCT i.id, i.symbol, i.name, i.asset_type, i.venue
            FROM instruments i
            WHERE i.asset_type IS NOT 'cash' AND i.symbol <> 'CASH'
              {kripto_filtresi} AND (
                -- TOPLAMA EVRENI: HERKESIN pozisyonlari. Sahibe gore
                -- suzmek bu mimarinin amacina ters — piyasa verisi bir
                -- kez toplanip herkesin okumasi icin var. B'nin tuttugu
                -- bir kagidin fiyati cekilmezse A da onu goremez.
                i.id IN (
                    SELECT p.instrument_id FROM positions p
                    WHERE p.snapshot_ts = (SELECT MAX(snapshot_ts) FROM positions
                                           WHERE account = p.account
                                             AND sahip = p.sahip)
                )
                OR i.id IN (SELECT instrument_id FROM watchlist)
            )
            ORDER BY i.symbol
        """)

    def save_identity(self, instrument_id: int, kimlik) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO identities
                   (instrument_id, cik, sec_ticker, sec_name, exchange, ir_url,
                    status, method, note, resolved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(instrument_id) DO UPDATE SET
                     cik=excluded.cik, sec_ticker=excluded.sec_ticker,
                     sec_name=excluded.sec_name, exchange=excluded.exchange,
                     ir_url=COALESCE(identities.ir_url, excluded.ir_url),
                     status=excluded.status, method=excluded.method,
                     note=excluded.note, resolved_at=excluded.resolved_at""",
                (instrument_id, kimlik.cik, kimlik.sec_ticker, kimlik.sec_name,
                 kimlik.exchange, kimlik.ir_url, kimlik.status, kimlik.method,
                 kimlik.note),
            )

    def save_crypto_identity(self, instrument_id: int, k: dict) -> bool:
        """
        Kripto kimligini yazar. ELLE atanmis kimligi EZMEZ.

        Sebep hisse tarafinda olculdu: EDGAR her calismada kimligi yeniden
        cozup kullanicinin /kimlik ile yaptigi duzeltmeyi siliyordu. Ayni
        hata burada tekrarlanmasin.
        """
        mevcut = self.query(
            "SELECT method FROM identities WHERE instrument_id = ?", (instrument_id,))
        if mevcut and mevcut[0]["method"] == "elle":
            return False
        with self.tx() as c:
            c.execute(
                """INSERT INTO identities
                   (instrument_id, sec_ticker, sec_name, exchange, status,
                    method, note, pair, coingecko_id, resolved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(instrument_id) DO UPDATE SET
                     sec_name=excluded.sec_name, exchange=excluded.exchange,
                     status=excluded.status, method=excluded.method,
                     note=excluded.note, pair=excluded.pair,
                     coingecko_id=excluded.coingecko_id,
                     resolved_at=excluded.resolved_at""",
                (instrument_id, k.get("symbol"), k.get("name"), "BINANCE",
                 k.get("status"), "kripto", k.get("note"),
                 k.get("pair"), k.get("coingecko_id")),
            )
        return True

    def identities(self, status: str | None = None) -> list[sqlite3.Row]:
        sql = """SELECT i.symbol, i.name, i.asset_type, d.*
                 FROM identities d JOIN instruments i ON i.id = d.instrument_id"""
        if status:
            return self.query(sql + " WHERE d.status = ? ORDER BY i.symbol", (status,))
        return self.query(sql + " ORDER BY i.symbol")

    def add_index_member(self, instrument_id: int, index_name: str) -> None:
        with self.tx() as c:
            c.execute("""INSERT INTO index_members (instrument_id, index_name)
                         VALUES (?, ?) ON CONFLICT DO NOTHING""",
                      (instrument_id, index_name))

    def index_summary(self) -> list[sqlite3.Row]:
        return self.query("""SELECT index_name, COUNT(*) n FROM index_members
                             GROUP BY index_name ORDER BY n DESC""")

    def search_catalog(self, term: str = "", index_name: str | None = None,
                       limit: int = 25) -> list[sqlite3.Row]:
        """Katalog aramasi: hisse + ETF, istege bagli endeks filtresi."""
        like = f"%{term.strip()}%"
        sql = """SELECT DISTINCT i.symbol, i.name, i.asset_type, i.isin,
                        (SELECT GROUP_CONCAT(m.index_name, ', ')
                         FROM index_members m WHERE m.instrument_id = i.id) AS endeksler
                 FROM instruments i
                 LEFT JOIN index_members m2 ON m2.instrument_id = i.id
                 WHERE i.venue = 'BUX'"""
        params: list = []
        if index_name:
            sql += " AND m2.index_name = ?"
            params.append(index_name)
        if term.strip():
            sql += " AND (i.name LIKE ? OR i.symbol LIKE ?)"
            params += [like, like]
        sql += " ORDER BY i.name LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def add_watchlist(self, instrument_id: int, note: str | None = None) -> None:
        with self.tx() as c:
            c.execute("""INSERT INTO watchlist (instrument_id, kind, note)
                         VALUES (?, 'aday', ?)
                         ON CONFLICT(instrument_id) DO UPDATE SET note=excluded.note""",
                      (instrument_id, note))

    def watchlist(self) -> list[sqlite3.Row]:
        """
        Izleme listesi. `kind` ONEMLI ve disari veriliyor:
          'aday'     kullanicinin KENDI sectigi isim  -> tam analiz hak eder
          'evren'    piyasa degeri siralamasindan otomatik gelen kripto
          'referans' Binance'te alinamayan, yalnizca baglam icin tutulan
        Ucunu ayni agirlikta islemek 86 satirlik bir listeyi modele tam
        goruntuyle vermek demek; dikkat seyrelmesi token maliyetinden once
        gelir (envanteri 4293 -> 317 karaktere indirirken olculdu).
        """
        return self.query("""SELECT i.id, i.symbol, i.name, i.asset_type, i.venue,
                                    w.kind, w.added_at, w.note
                             FROM watchlist w JOIN instruments i ON i.id = w.instrument_id
                             ORDER BY i.name""")

    def budama(self, haber_gun: int = 90, bildirim_gun: int = 365,
               snapshot_sayisi: int = 30) -> dict:
        """
        Eski kayitlari siler. Analize giren pencerelerden COK daha genis
        esikler kullanir (haber 7 gun, bildirim 30 gun okunuyor) — amac disk
        buyumesini durdurmak, veri kaybetmek degil.

        Pozisyon anlik goruntuleri hesap basina korunur: portfoy gecmisi
        ilerideki getiri analizinin tek kaynagi, kolay silinmemeli.
        """
        ozet = {}
        with self.tx() as c:
            ozet["haber"] = c.execute(
                "DELETE FROM news WHERE published_at < datetime('now', ?)",
                (f"-{haber_gun} days",)).rowcount
            ozet["bildirim"] = c.execute(
                "DELETE FROM disclosures WHERE published_at < datetime('now', ?)",
                (f"-{bildirim_gun} days",)).rowcount

            silinen_snapshot = 0
            # SAHIP x HESAP: her sahibin her hesabi ayri budanir. Tek
            # hesap uzerinden budamak, iki kisinin anlik goruntulerini
            # tek listede sayar ve az goruntu gonderenin gecmisini
            # digerinin goruntuleriyle silerdi.
            sahipler = [r["sahip"] for r in self.query(
                "SELECT DISTINCT sahip FROM positions")]
            for sahip in sahipler:
              for hesap in ("bux", "midas"):
                  tutulacak = [r["snapshot_ts"] for r in self.query(
                      """SELECT DISTINCT snapshot_ts FROM positions
                         WHERE account=? AND sahip=?
                         ORDER BY snapshot_ts DESC LIMIT ?""",
                      (hesap, sahip, snapshot_sayisi))]
                  if not tutulacak:
                      continue
                  yer = ",".join("?" * len(tutulacak))
                  silinen_snapshot += c.execute(
                      f"DELETE FROM positions WHERE account=? AND sahip=? "
                      f"AND snapshot_ts NOT IN ({yer})",
                      (hesap, sahip, *tutulacak)).rowcount
            ozet["pozisyon"] = silinen_snapshot

        # VACUUM transaction icinde calismaz.
        self._conn.execute("VACUUM")
        return ozet

    def recent_disclosures_by_source(self, source: str, hours: int = 720,
                                     limit: int = 60) -> list[sqlite3.Row]:
        return self.query(
            """SELECT * FROM disclosures
               WHERE source = ? AND published_at >= datetime('now', ?)
               ORDER BY published_at DESC LIMIT ?""",
            (source, f"-{hours} hours", limit),
        )

    @staticmethod
    def _haber_anahtari(r: dict) -> str:
        """
        Haberin kimligi URL DEGIL, ICERIKTIR: baslik + yayin gunu.

        Neden: ayni makale once news.google.com yonlendirme linkiyle, sonra
        cozulmus yayinci linkiyle gelebiliyor. URL'i anahtar yapmak ayni
        makaleyi IKI KEZ kaydediyordu — olay-etki analizi ayni olayi iki
        kez sayip sahte tekrar uretiyordu.
        """
        baslik = re.sub(r"\W+", " ", (r.get("title") or "").lower()).strip()
        if not baslik:
            return sha1(r["url"])          # basliksiz haberde URL'den baskasi yok
        return sha1(f"{baslik}|{(r.get('published_at') or '')[:10]}")

    def upsert_news(self, rows: Iterable[dict]) -> int:
        # KONU BURADA ATANIR, COLLECTOR'DA DEGIL.
        #
        # Uc ayri yol haber yaziyor (`news`, `stocknews`, `alphavantage`).
        # Siniflandirmayi her birine birakmak, birinde unutulunca o
        # kaynagin tamaminin gundem bolumunden SESSIZCE dusmesi demekti —
        # `publisher`/`tier` alanlari tam bu sekilde 405 haberde bos
        # kalmisti. Tek yazma noktasindan gecirmek bunu yapisal olarak
        # imkansiz kilar.
        from ..research.konular import konu as _konu

        payload = []
        for r in rows:
            if not r.get("url"):
                continue
            semboller = ",".join(r.get("symbols", []) or [])
            payload.append(
                (self._haber_anahtari(r), r.get("published_at"), r.get("source"),
                 r.get("title"), r.get("url"), r.get("summary"), semboller,
                 r.get("publisher"), int(r.get("tier") or 0),
                 r.get("konu") or _konu(r.get("title"), r.get("summary"), semboller,
                                        r.get("publisher") or r.get("source"))))
        if not payload:
            return 0
        with self.tx() as c:
            c.executemany(
                """INSERT INTO news
                   (id, published_at, source, title, url, summary, symbols,
                    publisher, tier, konu)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     symbols = CASE
                       WHEN instr(',' || news.symbols || ',', ',' || excluded.symbols || ',') > 0
                       THEN news.symbols
                       ELSE news.symbols || ',' || excluded.symbols END,
                     publisher = COALESCE(excluded.publisher, news.publisher),
                     tier      = MAX(excluded.tier, news.tier),
                     konu      = COALESCE(excluded.konu, news.konu),
                     -- Cozulmus yayinci linki, yonlendirme linkini EZER.
                     url = CASE WHEN excluded.url LIKE '%news.google.com%'
                                THEN news.url ELSE excluded.url END""",
                payload,
            )
        return len(payload)

    def log_collector_run(self, collector: str, status: str, rows: int,
                          duration_ms: int, error: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO collector_runs
                   (run_ts, collector, status, rows_written, duration_ms, error)
                   VALUES (?,?,?,?,?,?)""",
                (utcnow(), collector, status, rows, duration_ms, error),
            )

    def log_analysis_run(self, model: str, scope: str, input_stats: dict,
                         output_md: str, status: str, error: str | None = None,
                         sahip: str = "ali") -> int:
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO analysis_runs
                   (run_ts, model, scope, input_stats, output_md, status,
                    error, sahip)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (utcnow(), model, scope, json.dumps(input_stats, ensure_ascii=False),
                 output_md, status, error, sahip),
            )
        return int(cur.lastrowid)

    # ------------------------------------------------------------------
    # Okuma yardimcilari
    def price_history(self, symbol: str, venue: str = "BIST", limit: int = 400) -> list[sqlite3.Row]:
        return self.query(
            """SELECT p.ts, p.open, p.high, p.low, p.close, p.volume
               FROM prices p JOIN instruments i ON i.id = p.instrument_id
               WHERE i.symbol=? AND i.venue=?
               ORDER BY p.ts DESC LIMIT ?""",
            (symbol.upper(), venue.upper(), limit),
        )

    def son_snapshot(self, sahip: str) -> sqlite3.Row | None:
        """
        Bir SAHIBIN en son yazilan anlik goruntusu (hesap + damga + adet).

        "Geri al" TEK islemi geri almalidir, o yuzden hesap da doner.
        SAHIP ZORUNLU: sahipsiz secim, en son yazan kim olursa olsun onun
        kaydini bulur ve baskasinin "geri al"ini yanlis hedefe yollar.
        """
        if not sahip:
            raise ValueError("son_snapshot: sahip zorunlu")
        r = self.query(
            """SELECT account, snapshot_ts, COUNT(*) n FROM positions
               WHERE sahip = ?
               GROUP BY account, snapshot_ts
               ORDER BY snapshot_ts DESC LIMIT 1""", (sahip,))
        return r[0] if r else None

    def latest_snapshot_ts(self, account: str, sahip: str) -> str | None:
        row = self.query(
            "SELECT MAX(snapshot_ts) AS ts FROM positions "
            "WHERE account = ? AND sahip = ?",
            (account.lower(), sahip),
        )
        return row[0]["ts"] if row and row[0]["ts"] else None

    def snapshot_positions(self, account: str, snapshot_ts: str,
                           sahip: str) -> dict[str, float | None]:
        """sembol -> piyasa degeri (o anlik goruntudeki)."""
        rows = self.query(
            """SELECT i.symbol AS s, p.market_value AS v
               FROM positions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.account = ? AND p.snapshot_ts = ? AND p.sahip = ?""",
            (account.lower(), snapshot_ts, sahip),
        )
        return {r["s"]: r["v"] for r in rows}

    def snapshot_satirlari(self, account: str, snapshot_ts: str,
                           sahip: str) -> list[dict]:
        """
        Bir anlik goruntunun TAM satirlari — yeniden yazilabilir bicimde.

        Diger `snapshot_*` yardimcilari tek bir alan donduruyor (deger,
        adet, ad) cunku karsilastirma icin o yetiyor. Burasi TASIMA
        icin: bir sonraki anlik goruntuye devredilecek satirlar, kaybi
        olan alan olmadan cikmali.
        """
        rows = self.query(
            """SELECT i.symbol AS symbol, i.name AS name,
                      i.asset_type AS asset_type,
                      p.quantity, p.avg_cost, p.last_price,
                      p.market_value, p.pnl_abs, p.pnl_pct, p.currency
               FROM positions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.account = ? AND p.snapshot_ts = ? AND p.sahip = ?
               ORDER BY i.symbol""",
            (account.lower(), snapshot_ts, sahip),
        )
        return [dict(r) for r in rows]

    def snapshot_quantities(self, account: str, snapshot_ts: str,
                            sahip: str) -> dict[str, float | None]:
        """
        sembol -> ADET (o anlik goruntudeki).

        Neden deger degil ADET: ekran goruntusunun TEK OTORITE oldugu
        alan kac adet tuttugundur. Piyasa degeri fiyattan turer ve fiyati
        sistem zaten kendi serisinden biliyor — degeri karsilastirmak
        "fiyat oynadi" ile "pozisyon degisti"yi ayni sey sayardi.
        """
        rows = self.query(
            """SELECT i.symbol AS s, p.quantity AS q
               FROM positions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.account = ? AND p.snapshot_ts = ? AND p.sahip = ?""",
            (account.lower(), snapshot_ts, sahip),
        )
        return {r["s"]: r["q"] for r in rows}

    def snapshot_symbol_names(self, account: str, snapshot_ts: str,
                              sahip: str) -> dict[str, str | None]:
        """sembol -> enstruman adi (sembol hizalamasi icin)."""
        rows = self.query(
            """SELECT i.symbol AS s, i.name AS n
               FROM positions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.account = ? AND p.snapshot_ts = ? AND p.sahip = ?""",
            (account.lower(), snapshot_ts, sahip),
        )
        return {r["s"]: r["n"] for r in rows}

    def snapshot_value(self, account: str, snapshot_ts: str,
                       sahip: str) -> float:
        row = self.query(
            """SELECT COALESCE(SUM(market_value), 0) AS v FROM positions
               WHERE account = ? AND snapshot_ts = ? AND sahip = ?""",
            (account.lower(), snapshot_ts, sahip),
        )
        return float(row[0]["v"]) if row else 0.0

    def delete_snapshot(self, account: str, snapshot_ts: str,
                        sahip: str) -> int:
        """`/sil` YALNIZCA kendi anlik goruntusunu siler."""
        with self.tx() as c:
            cur = c.execute(
                "DELETE FROM positions WHERE account=? AND snapshot_ts=? "
                "AND sahip=?", (account.lower(), snapshot_ts, sahip))
        return cur.rowcount

    def search_instruments(self, venue: str, term: str = "", limit: int = 25) -> list[sqlite3.Row]:
        like = f"%{term.strip()}%"
        return self.query(
            """SELECT symbol, name, asset_type, isin FROM instruments
               WHERE venue = ? AND (? = '' OR name LIKE ? OR symbol LIKE ?)
               ORDER BY name LIMIT ?""",
            (venue.upper(), term.strip(), like, like, limit),
        )

    def count_instruments(self, venue: str) -> int:
        return int(self.query("SELECT COUNT(*) c FROM instruments WHERE venue=?",
                              (venue.upper(),))[0]["c"])

    def enstrumanlar_by_id(self, ids) -> list[sqlite3.Row]:
        """Kimlik listesinden enstruman satirlari. Bos liste -> bos sonuc."""
        ids = [int(i) for i in ids]
        if not ids:
            return []
        yer = ",".join("?" * len(ids))
        return self.query(
            f"""SELECT id, symbol, name, venue, asset_type, currency
                FROM instruments WHERE id IN ({yer}) ORDER BY symbol""", tuple(ids))

    def enstrumanlar_by_symbol(self, semboller, venue: str | None = None) -> list[sqlite3.Row]:
        """
        Sembol listesinden enstruman satirlari.

        `venue` VERILMELI: sade sembol kimlik degildir. "BTC" hem BINANCE
        hem CRYPTO'da, "ADA" gundelik bir Turkce kelime, RBOT Yahoo'da
        bambaska bir sirket. Venue'suz cagri yalnizca cagiranin gercekten
        tum evrende aradigi yerlerde kullanilmali.
        """
        semboller = [s.strip().upper() for s in semboller if s and s.strip()]
        if not semboller:
            return []
        yer = ",".join("?" * len(semboller))
        kosul = "AND venue = ?" if venue else ""
        params = tuple(semboller) + ((venue.upper(),) if venue else ())
        return self.query(
            f"""SELECT id, symbol, name, venue, asset_type, currency
                FROM instruments WHERE UPPER(symbol) IN ({yer}) {kosul}
                ORDER BY symbol""", params)

    def latest_positions(self, account: str, sahip: str) -> list[sqlite3.Row]:
        """
        Bir SAHIBIN bir hesabindaki en son anlik goruntusu.

        `sahip` varsayilani YOK ve olmayacak. Onceden sorgu "her
        account'in en son snapshot'i" diyordu; ikinci kisi bir ekran
        goruntusu onayladiginda onun snapshot'i en yenisi olur ve
        birinci kisinin portfoyu HER sorgudan kaybolurdu.
        """
        if not sahip:
            raise ValueError("latest_positions: sahip zorunlu")
        # `i.currency` TAKMA ADLA aliniyor: `p.*` de bir `currency` getiriyor
        # ve sqlite3.Row ad ile erisimde ILK eslesmeyi donduruyordu — yani
        # `r["currency"]` sessizce KATALOG para birimini veriyordu, oysa
        # dogru olan POZISYONUN para birimi (kullanicinin ekraninda goren o).
        # Bugun ikisi ayni, ama katalog EUR derken pozisyon USD oldugu an
        # fark hicbir uyari vermeden yanlis hesaba donusurdu.
        return self.query(
            """SELECT i.symbol, i.name, i.asset_type,
                      i.currency AS katalog_para_birimi, p.*
               FROM positions p JOIN instruments i ON i.id = p.instrument_id
               WHERE p.account = ? AND p.sahip = ?
                 AND p.snapshot_ts = (SELECT MAX(snapshot_ts) FROM positions
                                      WHERE account = ? AND sahip = ?)
               ORDER BY p.market_value DESC""",
            (account.lower(), sahip, account.lower(), sahip),
        )

    def hesaplar(self, sahip: str) -> list[str]:
        """Bir sahibin pozisyon tuttugu hesaplar."""
        return [r["account"] for r in self.query(
            "SELECT DISTINCT account FROM positions WHERE sahip = ? "
            "ORDER BY account", (sahip,))]

    def sahip_pozisyon_idleri(self, sahip: str) -> set[int]:
        """
        Sahibin GUNCEL enstruman kimlikleri — gundem ve hafif kosu icin.

        Hesap basina en son anlik goruntu; farkli hesaplarin goruntuleri
        farkli zamanlarda gelebilir, o yuzden hesap bazinda MAX.
        """
        return {r["instrument_id"] for r in self.query(
            """SELECT DISTINCT p.instrument_id FROM positions p
               WHERE p.sahip = ?
                 AND p.snapshot_ts = (SELECT MAX(snapshot_ts) FROM positions
                                      WHERE sahip = p.sahip
                                        AND account = p.account)""", (sahip,))}

    def recent_news(self, hours: int = 36, limit: int = 60) -> list[sqlite3.Row]:
        return self.query(
            """SELECT * FROM news
               WHERE published_at >= datetime('now', ?)
               ORDER BY published_at DESC LIMIT ?""",
            (f"-{hours} hours", limit),
        )

    def recent_disclosures(self, hours: int = 48, limit: int = 60) -> list[sqlite3.Row]:
        return self.query(
            """SELECT * FROM disclosures
               WHERE published_at >= datetime('now', ?)
               ORDER BY published_at DESC LIMIT ?""",
            (f"-{hours} hours", limit),
        )

    # --- sohbet arsivi ---------------------------------------------------
    def sohbet_kaydet(self, chat_id, rol: str, metin: str,
                      sahip: str | None = None, gorsel: bool = False,
                      araclar: Sequence[str] | None = None,
                      ts: str | None = None) -> int:
        """
        Bir sohbet turunu ARSIVE yazar. Append-only; budama YOK.

        `sahip` None gelebilir ve bu KABUL EDILIR — bkz. schema.sql'deki
        gerekce. Arsivin tek gorevi kaybetmemek; sahip cozulemedi diye
        satiri dusurmek, tam da onlemek icin kuruldugu seyi yapardi.
        """
        if rol not in ("user", "assistant"):
            raise ValueError(f"sohbet_kaydet: gecersiz rol {rol!r}")
        with self.tx() as c:
            cur = c.execute(
                """INSERT INTO sohbet_kaydi
                       (ts, chat_id, sahip, rol, metin, gorsel, araclar)
                   VALUES (?,?,?,?,?,?,?)""",
                (ts or utcnow(), str(chat_id),
                 str(sahip).strip().lower() if sahip else None,
                 rol, metin, 1 if gorsel else 0,
                 ", ".join(araclar) if araclar else None))
        return int(cur.lastrowid)

    def sohbet_ara(self, sahip: str, gun: int = 30, sorgu: str | None = None,
                   limit: int = 40) -> list[sqlite3.Row]:
        """
        Arsiv okuma — DAIMA sahip suzgeciyle.

        `sorgu` verilirse metinde gecen turlar; verilmezse en yeniler.
        Sonuc ESKIDEN YENIYE siralanir: bir konusma parcasi ancak sirasi
        korunursa okunabilir, ve LIMIT en YENI turlari almali. Bu yuzden
        ic sorgu DESC alip dis sorgu ASC ceviriyor.
        """
        if not sahip:
            raise ValueError("sohbet_ara: sahip zorunlu")
        kosul = ["sahip = ?", "ts >= ?"]
        par: list[Any] = [str(sahip).strip().lower(), _gun_once(gun)]
        if sorgu and sorgu.strip():
            kosul.append("metin LIKE ? ESCAPE '\\'")
            par.append(f"%{_like_kacir(sorgu.strip())}%")
        return self.query(
            f"""SELECT * FROM (
                    SELECT id, ts, rol, metin, gorsel, araclar
                    FROM sohbet_kaydi
                    WHERE {' AND '.join(kosul)}
                    ORDER BY ts DESC, id DESC LIMIT ?
                ) ORDER BY ts ASC, id ASC""", (*par, int(limit)))

    def sohbet_ara_fts(self, sahip: str, gun: int = 30,
                       sorgu: str | None = None,
                       limit: int = 40) -> list[sqlite3.Row]:
        """
        Metin indeksinden arama — ALAKA SIRASIYLA.

        `sohbet_ara`'dan iki farki var:

        1. Sorgu TEK BIR `LIKE '%...%'` kalibi degil; terimlere ayrilip
           trigram indeksinde aranir. Olculdu (altin kume, 2026-08-20):
           LIKE ile 15 dogal sorgunun 13'u SIFIR satir donduruyordu,
           cunku "altın hesabı kaç TL" diye bir dize arsivde gecmiyor.

        2. Sonuc KRONOLOJIK degil, `bm25` sirasiyla doner. `sohbet_ara`
           eslesenlerin en yenilerini alip eskiden yeniye diziyordu —
           okunabilirlik icin dogru, SECIM icin yanlis: "ilk 3" orada
           "en alakali 3" demek degildi. Gosterim sirasi cagiranin isi;
           once dogru satirlar secilmeli.

        `sorgu` bos ise en yeni turlar doner (`sohbet_ara` ile ayni
        davranis) — indeksin isi ARAMAK, listelemek degil.
        """
        if not sahip:
            raise ValueError("sohbet_ara_fts: sahip zorunlu")
        ifade = fts_ifadesi(sorgu)
        if ifade is None:
            # Aranabilir terim yok (bos sorgu ya da hepsi 3 harften
            # kisa). Sessizce "sonuc yok" DEMEK yanlis olurdu: sorgu
            # yoksa en yeniler istenmis demektir, `sohbet_ara` ne
            # yapiyorsa o.
            return self.sohbet_ara(sahip, gun=gun, sorgu=None, limit=limit)
        return self.query(
            """SELECT k.id, k.ts, k.rol, k.metin, k.gorsel, k.araclar,
                      bm25(sohbet_fts) AS puan
               FROM sohbet_fts
               JOIN sohbet_kaydi k ON k.id = sohbet_fts.rowid
               WHERE sohbet_fts MATCH ? AND k.sahip = ? AND k.ts >= ?
               ORDER BY puan ASC, k.ts DESC, k.id DESC
               LIMIT ?""",
            (ifade, str(sahip).strip().lower(), _gun_once(gun), int(limit)))

    # --- anlam vektorleri (M4 / T5) --------------------------------------
    #
    # Vektor `float32` dizisi olarak saklaniyor: 768 x 4 = 3072 bayt.
    # `float64` iki kati yer kaplardi ve model zaten float32 uretiyor;
    # `json` ise ~6 kat sisirir ve her okumada ayristirma maliyeti bindirir.

    def sohbet_gomme_yaz(self, kayitlar: Iterable[tuple[int, Any]],
                         model: str) -> int:
        """
        Satir gommelerini yazar. `kayitlar`: (id, vektor) ciftleri.

        `gomme_model` HER SATIRDA yaziliyor — tek bir genel "su anki
        model" ayari yeterli olmazdi: indeksleme yarida kalirsa
        veritabaninda IKI modelin vektorleri yan yana durur ve hangi
        satirin hangisinden geldigini yalnizca satirin kendisi bilir.
        """
        import numpy as np

        simdi = utcnow()
        satirlar = [(np.asarray(v, dtype=np.float32).tobytes(), model, simdi, int(i))
                    for i, v in kayitlar]
        if not satirlar:
            return 0
        with self.tx() as c:
            c.executemany(
                "UPDATE sohbet_kaydi SET gomme = ?, gomme_model = ?, "
                "gomme_ts = ? WHERE id = ?", satirlar)
        return len(satirlar)

    def sohbet_gomme_eksikler(self, model: str,
                              limit: int | None = None) -> list[sqlite3.Row]:
        """
        Gommesi olmayan ya da BASKA bir modelden gelen satirlar.

        Model degisince eski satirlar da "eksik" sayilir; aksi halde
        yeniden indeksleme onlari atlar ve veritabani kalici olarak
        karisik bir vektor uzayinda kalirdi.
        """
        sql = ("SELECT id, metin FROM sohbet_kaydi "
               "WHERE gomme IS NULL OR gomme_model IS NOT ? ORDER BY id")
        if limit:
            return self.query(sql + " LIMIT ?", (model, int(limit)))
        return self.query(sql, (model,))

    def sohbet_gomme_ara(self, sahip: str, sorgu_vektoru: Any, model: str,
                         gun: int = 30, limit: int = 40) -> list[dict]:
        """
        Anlam aramasi — kosinus benzerligine gore.

        TEK MATRIS CARPIMI. Vektorler L2-normalize (olculdu: norm tam
        1.0), yani kosinus = nokta carpimi ve ayrica normalize etmeye
        gerek yok. 156 satir x 768 boyut = 480 KB; bir indeks yapisi
        (sqlite-vec, faiss) veriden buyuk olurdu.

        FARKLI MODELDEN GELEN SATIR GORULURSE HATA VERIR, ATLAMAZ.
        Sessizce atlamak arama sonucunu sessizce eksiltirdi; karistirmak
        ise daha kotusu — iki ayri vektor uzayinin nokta carpimi bir
        SAYI uretir, ve o sayi anlamsiz oldugu halde makul gorunur.
        """
        import numpy as np

        if not sahip:
            raise ValueError("sohbet_gomme_ara: sahip zorunlu")
        satirlar = self.query(
            """SELECT id, ts, rol, metin, gorsel, araclar, gomme, gomme_model
               FROM sohbet_kaydi
               WHERE sahip = ? AND ts >= ? AND gomme IS NOT NULL""",
            (str(sahip).strip().lower(), _gun_once(gun)))
        if not satirlar:
            return []

        yabanci = {r["gomme_model"] for r in satirlar} - {model}
        if yabanci:
            raise ValueError(
                f"vektor uzayi karisik: beklenen model {model!r}, "
                f"veritabaninda {sorted(map(str, yabanci))} var. "
                "Once `sohbet_gomme_eksikler` ile yeniden indeksle.")

        M = np.frombuffer(b"".join(r["gomme"] for r in satirlar),
                          dtype=np.float32).reshape(len(satirlar), -1)
        q = np.asarray(sorgu_vektoru, dtype=np.float32)
        if q.shape[0] != M.shape[1]:
            raise ValueError(
                f"sorgu vektoru {q.shape[0]} boyutlu, kayitlar {M.shape[1]}")
        puan = M @ q
        sira = np.argsort(-puan)[:int(limit)]
        # `gomme` BLOB'u disarida birakiliyor: cagirana faydasi yok ve
        # her sonuc satirini 3 KB sisirir.
        out = []
        for i in sira:
            d = {k: satirlar[i][k] for k in satirlar[i].keys() if k != "gomme"}
            d["puan"] = float(puan[i])
            out.append(d)
        return out

    # --- kalici gercekler (hatirlanan) -----------------------------------
    #
    # Sohbetten DAMITILAN katman. `sohbet_kaydi` ne konusuldugunu tutar;
    # burasi neyin GECERLI oldugunu. Ikisi ayri sorulardir ve tek tabloda
    # tutulmalari, "dedi" ile "oyle" arasindaki farki silerdi.
    HATIRLANAN_TURLERI = ("tercih", "olgu", "karar")

    def hatirla(self, sahip: str, tur: str, konu: str, icerik: str,
                kaynak_ts: str | None = None) -> dict:
        """
        Kalici bir gercek yazar. AYNI (sahip, tur, konu) varsa ESKISINI
        GECERSIZLESTIRIR — silmez.

        Doner: {"id": ..., "gecersizlesen": [id, ...]}

        SILME YOK cunku "ne zaman fikir degistirdi" cevaplanabilir
        kalmali. DELETE onu imkansiz kilardi ve bu katmanin varlik
        sebebi tam olarak gecmisi KAYBETMEMEK.

        KONU CAKISMA ANAHTARIDIR: "altin fiyati" konusuna ikinci bir
        tercih yazilirsa birincisi duser. Konu serbest metin oldugu icin
        normalize ediliyor (kucuk harf, kirpilmis) — "Altin Fiyati" ile
        "altin fiyati" iki ayri kural gibi durmasin.
        """
        sahip = (sahip or "").strip().lower()
        if not sahip:
            raise ValueError("hatirla: sahip zorunlu")
        tur = (tur or "").strip().lower()
        if tur not in self.HATIRLANAN_TURLERI:
            raise ValueError(
                f"hatirla: gecersiz tur {tur!r}; "
                f"{', '.join(self.HATIRLANAN_TURLERI)}")
        konu_norm = (konu or "").strip().lower()
        icerik = (icerik or "").strip()
        if not konu_norm or not icerik:
            raise ValueError("hatirla: konu ve icerik bos olamaz")

        simdi = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.tx() as c:
            eski = [r["id"] for r in c.execute(
                """SELECT id FROM hatirlanan
                   WHERE sahip=? AND tur=? AND LOWER(konu)=? AND gecerli=1""",
                (sahip, tur, konu_norm)).fetchall()]
            cur = c.execute(
                """INSERT INTO hatirlanan
                   (sahip, tur, konu, icerik, kaynak_ts, olusma_ts, gecerli)
                   VALUES (?,?,?,?,?,?,1)""",
                (sahip, tur, konu_norm, icerik, kaynak_ts, simdi))
            yeni_id = int(cur.lastrowid)
            if eski:
                c.executemany(
                    """UPDATE hatirlanan
                       SET gecerli=0, gecersiz_ts=?, gecersiz_sebep=?
                       WHERE id=?""",
                    [(simdi, f"yeni kayit #{yeni_id}", i) for i in eski])
        return {"id": yeni_id, "gecersizlesen": eski}

    def hatirlananlar(self, sahip: str, tur: str | None = None,
                      gecerli: bool = True) -> list[sqlite3.Row]:
        """Bir sahibin kalici gercekleri. SAHIP ZORUNLU — varsayilan yok."""
        sahip = (sahip or "").strip().lower()
        if not sahip:
            raise ValueError("hatirlananlar: sahip zorunlu")
        kosul, par = ["sahip = ?"], [sahip]
        if gecerli:
            kosul.append("gecerli = 1")
        if tur:
            kosul.append("tur = ?")
            par.append(str(tur).strip().lower())
        return self.query(
            f"""SELECT id, tur, konu, icerik, kaynak_ts, olusma_ts,
                       gecerli, gecersiz_ts, gecersiz_sebep
                FROM hatirlanan WHERE {' AND '.join(kosul)}
                ORDER BY tur, olusma_ts DESC""", tuple(par))

    def unut_hatirlanan(self, sahip: str, kayit_id: int,
                        sebep: str = "kullanici unuttu") -> bool:
        """
        Tek bir kaydi gecersizlestirir. SAHIP SUZGECI ZORUNLU: id ile
        baskasinin kaydini dusurmek mumkun olmamali.

        Doner: gercekten bir satir dustu mu.
        """
        sahip = (sahip or "").strip().lower()
        if not sahip:
            raise ValueError("unut_hatirlanan: sahip zorunlu")
        simdi = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.tx() as c:
            cur = c.execute(
                """UPDATE hatirlanan
                   SET gecerli=0, gecersiz_ts=?, gecersiz_sebep=?
                   WHERE id=? AND sahip=? AND gecerli=1""",
                (simdi, sebep, int(kayit_id), sahip))
            return cur.rowcount > 0

    # --- ogretilen ipuclari ---------------------------------------------
    def ipucu_ilk_mi(self, sahip: str, kod: str) -> bool:
        """
        Bu ipucu bu kisiye DAHA ONCE verildi mi? Ilk kezse isaretler ve
        True doner; sonraki cagrilarda False.

        Kontrol ile isaretleme AYNI cagrida: ikisi ayrilirsa model
        "verilebilir" cevabini alip ipucunu verir ama isaretlemeyi
        atlarsa ayni ipucu her turda tekrar eder.
        """
        if not sahip:
            return False
        with self.tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO ogretilen (sahip, kod, ilk_ts) "
                "VALUES (?,?,?)",
                (str(sahip).strip().lower(), kod, utcnow()))
        return cur.rowcount > 0

    def ogretilenler(self, sahip: str) -> set[str]:
        if not sahip:
            return set()
        return {r["kod"] for r in self.query(
            "SELECT kod FROM ogretilen WHERE sahip = ?",
            (str(sahip).strip().lower(),))}

    def ogretilenleri_sifirla(self, sahip: str) -> int:
        """Ipuclarini bastan alabilmek icin (`/rehber sifirla`)."""
        if not sahip:
            return 0
        with self.tx() as c:
            cur = c.execute("DELETE FROM ogretilen WHERE sahip = ?",
                            (str(sahip).strip().lower(),))
        return int(cur.rowcount)

    def sohbet_sayisi(self, sahip: str) -> int:
        """Arsivdeki tur sayisi. COUNT ile — metinleri cekmeden."""
        if not sahip:
            return 0
        r = self.query("SELECT COUNT(*) n FROM sohbet_kaydi WHERE sahip = ?",
                       (str(sahip).strip().lower(),))
        return int(r[0]["n"]) if r else 0

    def sohbet_sil(self, sahip: str, gun: int | None = None) -> int:
        """Arsiv silme — YALNIZCA acik istek uzerine (`/unut arsiv`)."""
        if not sahip:
            raise ValueError("sohbet_sil: sahip zorunlu")
        kosul, par = ["sahip = ?"], [str(sahip).strip().lower()]
        if gun is not None:
            kosul.append("ts >= ?")
            par.append(_gun_once(gun))
        with self.tx() as c:
            cur = c.execute(
                f"DELETE FROM sohbet_kaydi WHERE {' AND '.join(kosul)}", par)
        return int(cur.rowcount)
