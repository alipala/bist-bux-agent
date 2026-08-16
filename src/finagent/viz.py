"""
Grafik uretimi — KENDI VERIMIZDEN, Telegram'a gonderilmek uzere.

NEDEN ONEMLI
------------
Bir sayiyi okumak ile serinin seklini gormek ayni sey degil. "SMA200'un
%44 altinda" cumlesi dogru ama grafikte bunun ne demek oldugu bir bakista
gorunuyor.

Ikinci ve daha onemli fayda: CAPRAZ KONTROL. Grafik bizim veritabanimizin
ne dusundugunu gosterir; `kaynak_goruntusu` araci ise kaynagin ne
gosterdigini. Ikisi ayrilirsa veri hatasi vardir — nitekim projedeki en
buyuk hata (17 pozisyonun 14'unde yanlis para birimi) tam olarak boyle
bir karsilastirmayla yakalandi.

BASLIKSIZ ORTAM
---------------
launchd altinda GUI yok; matplotlib "Agg" backend'i ile calisiyor. Bu
import SIRASI onemli: pyplot'tan ONCE ayarlanmali.

PARA BIRIMI HER GRAFIKTE YAZAR
------------------------------
Ayni sembolun EUR ve USD serisi olabiliyor (`db.fiyat_kaynagi` tekini
secer). Grafigin uzerinde hangisi oldugu yazmazsa, okuyan kisi yanlis
para biriminde bir seviye okur — projedeki hatanin gorsel versiyonu.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                       # pyplot'tan ONCE
import matplotlib.pyplot as plt             # noqa: E402
import matplotlib.dates as mdates           # noqa: E402

log = logging.getLogger(__name__)

RENK = {"fiyat": "#1f77b4", "sma20": "#ff7f0e", "sma50": "#2ca02c",
        "sma200": "#d62728", "hacim": "#8c8c8c", "vekil": "#9467bd"}


def _basamak(deger: float) -> int:
    """Kripto kurus altinda; sabit 2 hane seriyi duzlestirir."""
    a = abs(deger or 0)
    return 2 if a >= 100 else 4 if a >= 1 else 6 if a >= 0.01 else 8


def _kaydet(fig, hedef: Path) -> Path:
    hedef.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(hedef, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return hedef


def fiyat_grafigi(db, instrument_id: int, sembol: str, gun: int = 180,
                  hedef_dizin: Path | None = None) -> dict | None:
    """
    Fiyat + hareketli ortalamalar + hacim. Doner: {"yol","ozet"} veya None.

    Seri TEK kaynaktan alinir (`db.fiyat_serisi`) — `prices`'i dogrudan
    sorgulamak ayni sembolde EUR ve USD satirlarini karistirir.
    """
    # ORTALAMALAR TAM SERIDEN, gosterim kirpilmis seriden.
    # Once kirpip sonra hesaplamak SMA200'u yok ediyordu: 180 gunluk
    # grafikte 200 barlik ortalama hesaplanamaz ve grafigin en onemli
    # uzun vade cizgisi sessizce kayboluyordu.
    tam = db.fiyat_serisi(instrument_id, max(gun, 400))
    if len(tam) < 20:
        return None

    def _sma(kaynak_kapanis, n):
        if len(kaynak_kapanis) < n:
            return None
        return [None] * (n - 1) + [sum(kaynak_kapanis[i - n + 1:i + 1]) / n
                                   for i in range(n - 1, len(kaynak_kapanis))]

    tam_kapanis = [r["close"] for r in tam]
    tam_sma = {n: _sma(tam_kapanis, n) for n in (20, 50, 200)}

    kes = max(0, len(tam) - gun)
    seri = tam[kes:]
    tarih = [datetime.strptime(r["ts"][:10], "%Y-%m-%d") for r in seri]
    kapanis = [r["close"] for r in seri]
    hacim = [r["volume"] or 0 for r in seri]
    ccy = seri[-1]["currency"] or "?"
    kaynak = seri[-1]["source"]
    nd = _basamak(kapanis[-1])

    def sma(n):
        s = tam_sma.get(n)
        return s[kes:] if s else None

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(10, 6), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})

    ax.plot(tarih, kapanis, color=RENK["fiyat"], lw=1.6, label=f"{sembol}")
    for n, anahtar in ((20, "sma20"), (50, "sma50"), (200, "sma200")):
        s = sma(n)
        if s:
            ax.plot(tarih, s, color=RENK[anahtar], lw=1.0, alpha=0.85,
                    label=f"SMA{n}")
    ax.set_title(f"{sembol} · {ccy} · son {len(seri)} bar "
                 f"({seri[0]['ts'][:10]} → {seri[-1]['ts'][:10]})",
                 fontsize=11, loc="left")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    ax.grid(alpha=0.25)
    # PARA BIRIMI EKSENDE YAZAR — hangi birimde okundugu belirsiz kalmasin.
    ax.set_ylabel(ccy)
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"{v:,.{nd}f}".rstrip("0").rstrip(".")))

    ax2.bar(tarih, hacim, color=RENK["hacim"], alpha=0.55, width=1.0)
    ax2.set_ylabel("hacim", fontsize=9)
    ax2.grid(alpha=0.2)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))
    fig.autofmt_xdate(rotation=0, ha="center")

    # Kaynak ve olcum ani grafigin uzerinde: ekran goruntusu paylasilinca
    # verinin nereden ve ne zaman geldigi kaybolmasin.
    fig.text(0.99, 0.01, f"kaynak: {kaynak} · {datetime.now(timezone.utc):%d.%m.%Y %H:%M} UTC",
             ha="right", fontsize=7, color="#666")

    dizin = hedef_dizin or Path("data/bot/gorseller")
    yol = _kaydet(fig, dizin / f"grafik_{sembol}_{gun}g.png")
    return {"yol": str(yol), "para_birimi": ccy, "kaynak": kaynak,
            "bar": len(seri), "son_kapanis": kapanis[-1],
            "ilk_tarih": seri[0]["ts"][:10], "son_tarih": seri[-1]["ts"][:10]}


def karsilastirma_grafigi(db, hedefler: list[tuple], gun: int = 180,
                          hedef_dizin: Path | None = None) -> dict | None:
    """
    Birden fazla enstrumani NORMALIZE ederek karsilastirir (baslangic=100).

    Ham fiyat karsilastirmasi anlamsiz olurdu: ASML 1.579 EUR ile ROSE
    0.0055 USDT ayni eksende gorunemez. Normalize edince soru "hangisi
    daha pahali" degil "hangisi daha cok kazandirdi" olur — sorulmasi
    gereken de bu.
    """
    fig, ax = plt.subplots(figsize=(10, 5.5))
    cizilen = []
    for iid, sembol in hedefler[:6]:
        seri = db.fiyat_serisi(iid, gun)
        if len(seri) < 20:
            continue
        kapanis = [r["close"] for r in seri]
        taban = kapanis[0]
        if not taban:
            continue
        tarih = [datetime.strptime(r["ts"][:10], "%Y-%m-%d") for r in seri]
        ax.plot(tarih, [k / taban * 100 for k in kapanis], lw=1.5, label=sembol)
        cizilen.append({"sembol": sembol,
                        "getiri_%": round((kapanis[-1] / taban - 1) * 100, 2),
                        "para_birimi": seri[-1]["currency"]})
    if not cizilen:
        plt.close(fig)
        return None

    ax.axhline(100, color="#999", lw=0.8, ls="--")
    ax.set_title(f"Normalize karsilastirma (baslangic = 100) · son {gun} gun",
                 fontsize=11, loc="left")
    ax.set_ylabel("endeks (100 = baslangic)")
    ax.legend(fontsize=9, framealpha=0.9)
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))
    fig.autofmt_xdate(rotation=0, ha="center")
    # Farkli para birimleri ayni grafikte: getiri karsilastirmasi gecerli
    # ama SEVIYE karsilastirmasi degil. Uyari grafigin uzerinde dursun.
    birimler = {c["para_birimi"] for c in cizilen}
    if len(birimler) > 1:
        fig.text(0.01, 0.01,
                 f"UYARI: farkli para birimleri ({', '.join(sorted(map(str, birimler)))}) — "
                 "getiri karsilastirilabilir, seviye karsilastirilamaz.",
                 fontsize=7.5, color="#b00")

    dizin = hedef_dizin or Path("data/bot/gorseller")
    ad = "_".join(c["sembol"] for c in cizilen)[:40]
    yol = _kaydet(fig, dizin / f"karsilastirma_{ad}.png")
    return {"yol": str(yol), "seriler": cizilen}


def portfoy_grafigi(db, hesap: str, sahip: str,
                    hedef_dizin: Path | None = None) -> dict | None:
    """Pozisyon agirliklari — yogunlasma bir bakista gorunsun."""
    poz = [p for p in db.latest_positions(hesap, sahip)
           if (p["market_value"] or 0) > 0]
    if not poz:
        return None
    poz.sort(key=lambda p: -(p["market_value"] or 0))
    toplam = sum(p["market_value"] for p in poz)
    ccy = poz[0]["currency"] or ""

    deger = [p["market_value"] for p in poz]
    agirlik = [d / toplam * 100 for d in deger]

    # Dilim UZERINE yalnizca okunabilecek kadar buyuk olanlar yazilir.
    # 18 pozisyonun hepsini yazmak, kucuk paylarin etiketlerini ust uste
    # bindirip okunmaz bir harf yigini yapiyordu (olculdu 2026-08-16:
    # "PAOTRWANLO"). Kucuk paylar KAYBOLMUYOR — legend'da tam agirligi ve
    # tutariyla duruyorlar; sayiyi grafikten degil legend'dan okuyorsun.
    ETIKET_ESIGI = 3.0
    etiket = [f"{p['symbol']}\n%{w:.1f}" if w >= ETIKET_ESIGI else ""
              for p, w in zip(poz, agirlik)]

    fig, ax = plt.subplots(figsize=(10.5, 6))
    # Yogunlasma en buyuk risk: en buyuk dilim vurgulanir.
    patlat = [0.06 if i == 0 else 0 for i in range(len(deger))]
    dilimler, *_ = ax.pie(deger, labels=etiket, explode=patlat, startangle=90,
                          textprops={"fontsize": 9},
                          wedgeprops={"linewidth": 0.6, "edgecolor": "white"})
    ax.legend(dilimler,
              [f"{p['symbol']}  %{w:.1f}   {p['market_value']:,.0f} {ccy}"
               for p, w in zip(poz, agirlik)],
              loc="center left", bbox_to_anchor=(0.98, 0.5), frameon=False,
              fontsize=8, ncol=2 if len(poz) > 10 else 1,
              handlelength=1.0, handletextpad=0.5, columnspacing=1.2)
    ax.set_title(f"{hesap.upper()} · toplam {toplam:,.2f} {ccy} · "
                 f"{len(poz)} pozisyon\nen buyuk: {poz[0]['symbol']} "
                 f"%{deger[0]/toplam*100:.1f}", fontsize=11)
    dizin = hedef_dizin or Path("data/bot/gorseller")
    # Dosya adinda SAHIP var: iki kisinin grafigi birbirini ezmemeli.
    yol = _kaydet(fig, dizin / f"portfoy_{sahip}_{hesap}.png")
    return {"yol": str(yol), "hesap": hesap, "toplam": round(toplam, 2),
            "para_birimi": ccy, "pozisyon": len(poz),
            "en_buyuk_agirlik_%": round(deger[0] / toplam * 100, 1)}
