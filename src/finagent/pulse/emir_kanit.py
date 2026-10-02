"""
Emir kaniti ve beyani — "bu emir neden verildi" sorusunun KAYITTAKI yuzu.

IKI KATMAN, BILEREK AYRI (Ali, 2026-10-02):

* KANIT (`emir_kanit`) — emir hazirlanirken kayitta GORULEBILEN seyler:
  kullaniciya giden tahminler, video/reel analizleri, sohbet danismalari;
  her biri emirden KAC SAAT ONCE oldugu ile. Kod yazar, hukum vermez.
* BEYAN (`emirler.beyan`) — kararin asil kaynagi. Ali yazar, tek dokunus,
  zorunlu degil. Verilmezse NULL kalir; kanittan TURETILMEZ.

Neden turetilmez: 2 Eki ETN emrinden once UC kaynak birden vardi (21 Eyl
strateji "al", 3 saat once YouTube analizi, 9 dk once "VRT mi ETN mi"
danismasi). Hangisinin kararı verdirdigi kayitta yok; tahmin etmek,
bugun karnede duzelttigimiz sinifta bir sayi uretmek olurdu.

ESIK YOK, PENCERE VAR. `PENCERE_SAAT` yalnizca ne kadar geriye BAKILACAGI
— bir kanitin "etkili" sayilma esigi degil. 16 emirde video->emir araligi
iki olcumdu (2 ve 3 saat); esik bu veriden turetilemez. `saat_once`
saklanir, esik okuma aninda secilir.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Kullaniciya GIDEN tahminler. `teslim = 0` (golge: uretildi ama BILEREK
# gonderilmedi, sema 38) DISARIDA — kullanicinin gormedigi bir satir
# "botun onerisi" kaniti olamaz. Panel ajanlari (teknik/temel/olay/risk)
# disarida: kullanici onlari ayri okumuyor ve neredeyse her kagit icin
# her gun satirlari var. 2026-10-02 olcumu: 13 emrin 13'unde 0,5-1,6 gun
# once bir panel satiri vardi — "bot bu kagittan bahsetmisti" ayirt edici
# degil, "bot bu YONDE bir sey ONERMISTI" ayirt edici.
ONERI_AJANLARI = ("hakem", "taktik", "strateji", "strateji_secilen")
VIDEO_ARACLARI = ("video_transkript", "instagram_reel")
PENCERE_SAAT = {"oneri": 30 * 24, "video": 14 * 24, "danisma": 7 * 24}
DANISMA_AZAMI = 20

# Emrin hazirlandigi kapi. Kod yazar.
KANALLAR = ("komut", "komut_stop", "strateji_butonu", "sohbet", "sohbet_stop")

# Beyan secenekleri: kod -> kullaniciya gorunen etiket. Tek yerde — buton,
# onay metni ve arac ciktisi buradan okur.
BEYANLAR = {"bot": "Botun önerisi", "kendi": "Kendi kararım",
            "video": "Video/reels", "karisik": "Karışık"}

_UYUM = {"BUY": "yukari", "SELL": "asagi"}


def topla(db, emir_id: int, yaz: bool = True) -> list[dict]:
    """
    Emrin `olusma_ts`inden geriye bakip kanitlari toplar.

    `yaz=False` hicbir sey yazmaz, yalnizca listeyi doner — geriye donuk
    doldurma once GOSTERILIR, sonra yazilir. Tekrar cagrilirsa ayni
    kanit ikinci kez yazilmaz (`UNIQUE (emir_id, ref_tablo, ref_id)`).
    """
    e = db.query("SELECT id, sahip, instrument_id, yon, olusma_ts "
                 "FROM emirler WHERE id = ?", (emir_id,))
    if not e or e[0]["instrument_id"] is None:
        return []
    e = e[0]
    an, iid, sahip = e["olusma_ts"], e["instrument_id"], e["sahip"]
    kanit: list[dict] = []

    # ONERI — ajan basina EN SON satir. `yayim_ts` UTC dakika damgasi;
    # yoksa (sema 29 oncesi) `olusma_ts` tarihi gun basi sayilir —
    # `saat_once` o satirlarda en fazla bir gun fazla cikar.
    gorulen = set()
    for r in db.query(
            f"""SELECT id, ajan, yon, COALESCE(yayim_ts, olusma_ts) ts,
                       (julianday(?) - julianday(COALESCE(yayim_ts, olusma_ts)))
                       * 24 saat
                FROM predictions
                WHERE sahip = ? AND instrument_id = ?
                  AND ajan IN ({','.join('?' * len(ONERI_AJANLARI))})
                  AND (teslim IS NULL OR teslim <> 0)
                  AND julianday(COALESCE(yayim_ts, olusma_ts)) <= julianday(?)
                  AND (julianday(?) - julianday(COALESCE(yayim_ts, olusma_ts)))
                      * 24 <= ?
                ORDER BY julianday(COALESCE(yayim_ts, olusma_ts)) DESC, id DESC""",
            (an, sahip, iid, *ONERI_AJANLARI, an, an, PENCERE_SAAT["oneri"])):
        if r["ajan"] in gorulen:
            continue
        gorulen.add(r["ajan"])
        kanit.append({"tur": "oneri", "ref_tablo": "predictions",
                      "ref_id": r["id"], "ts": r["ts"],
                      "saat_once": round(r["saat"], 2), "ajan": r["ajan"],
                      "yon": r["yon"],
                      "uyumlu": int(_UYUM.get(e["yon"]) == r["yon"])})

    # VIDEO ve DANISMA — sohbet arsivinin o kagida baglanan ASISTAN turlari
    # (`araclar` asistan satirinda). Emri hazirlayan tur DISARIDA kalir:
    # arsive tur BITINCE yazilir, yani damgasi emirden sonradir.
    video_kosul = " OR ".join("k.araclar LIKE ?" for _ in VIDEO_ARACLARI)
    video_arg = [f"%{a}%" for a in VIDEO_ARACLARI]
    for r in db.query(
            f"""SELECT k.id, k.ts,
                       (julianday(?) - julianday(k.ts)) * 24 saat,
                       ({video_kosul}) video
                FROM sohbet_kaydi k
                JOIN sohbet_sembol s ON s.kayit_id = k.id
                WHERE s.instrument_id = ? AND k.sahip = ?
                  AND k.kaynak = 'sohbet' AND k.rol = 'assistant'
                  AND julianday(k.ts) <= julianday(?)
                  AND (julianday(?) - julianday(k.ts)) * 24 <= ?
                ORDER BY julianday(k.ts) DESC, k.id DESC""",
            (an, *video_arg, iid, sahip, an, an,
             max(PENCERE_SAAT["video"], PENCERE_SAAT["danisma"]))):
        tur = "video" if r["video"] else "danisma"
        if r["saat"] > PENCERE_SAAT[tur]:
            continue
        if tur == "danisma" and sum(
                k["tur"] == "danisma" for k in kanit) >= DANISMA_AZAMI:
            continue
        kanit.append({"tur": tur, "ref_tablo": "sohbet_kaydi",
                      "ref_id": r["id"], "ts": r["ts"],
                      "saat_once": round(r["saat"], 2), "ajan": None,
                      "yon": None, "uyumlu": None})

    if yaz and kanit:
        with db.tx() as c:
            c.executemany(
                """INSERT OR IGNORE INTO emir_kanit
                   (emir_id, tur, ref_tablo, ref_id, ts, saat_once, ajan,
                    yon, uyumlu)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [(emir_id, k["tur"], k["ref_tablo"], k["ref_id"], k["ts"],
                  k["saat_once"], k["ajan"], k["yon"], k["uyumlu"])
                 for k in kanit])
    return kanit


def kanitlar(db, emir_id: int) -> list[dict]:
    """Yazilmis kanitlar, en yakindan en uzaga."""
    return [dict(r) for r in db.query(
        """SELECT tur, ajan, yon, uyumlu, ts, saat_once
           FROM emir_kanit WHERE emir_id = ? ORDER BY saat_once""",
        (emir_id,))]


def _sure(saat: float) -> str:
    if saat < 1:
        return f"{max(1, round(saat * 60))} dk"
    if saat < 48:
        return f"{saat:.0f} saat"
    return f"{saat / 24:.0f} gün"


_YON = {"yukari": "yükselir", "asagi": "düşer", "notr": "nötr"}
_AJAN = {"hakem": "hakem", "taktik": "taktik", "strateji": "strateji",
         "strateji_secilen": "strateji (seçilen)"}


def ozet_satirlari(db, emir_id: int) -> list[str]:
    """
    Kullaniciya giden kisa kanit listesi. Danismalar TEK satira iner
    (en yakini + sayisi); yoksa ETN gibi bir emirde liste mesaji bogar.
    """
    ks = kanitlar(db, emir_id)
    satir = []
    for k in ks:
        if k["tur"] == "oneri":
            satir.append(f"• {_AJAN.get(k['ajan'], k['ajan'])}: "
                         f"\"{_YON.get(k['yon'], k['yon'])}\" "
                         f"({_sure(k['saat_once'])} önce)"
                         + ("" if k["uyumlu"] else " — emrin yönüyle uyuşmuyor"))
        elif k["tur"] == "video":
            satir.append(f"• bu kağıdın geçtiği video/reels analizi "
                         f"({_sure(k['saat_once'])} önce)")
    dan = [k for k in ks if k["tur"] == "danisma"]
    if dan:
        satir.append(f"• bu kağıdın geçtiği sohbet: {len(dan)} tur, "
                     f"en yakını {_sure(dan[0]['saat_once'])} önce")
    return satir


def beyan_yaz(db, emir_id: int, sahip: str, kod: str) -> bool:
    """
    Beyani yazar. YALNIZCA emrin sahibi yazabilir; bilinmeyen kod reddedilir.
    Degistirilebilir: son basilan gecerli, `beyan_ts` ne zaman oldugunu tutar.
    """
    if kod not in BEYANLAR:
        return False
    r = db.query("SELECT sahip FROM emirler WHERE id = ?", (emir_id,))
    if not r or r[0]["sahip"] != sahip:
        return False
    with db.tx() as c:
        c.execute("UPDATE emirler SET beyan = ?, beyan_ts = ? WHERE id = ?",
                  (kod, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   emir_id))
    return True
