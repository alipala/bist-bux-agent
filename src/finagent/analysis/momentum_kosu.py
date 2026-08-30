"""
Momentum koşumunun KABUGU — db okur, saf cekirdegi cagirir.

Ayrim `momentum.py` docstring'indeki kuralla ayni: karar mantigi orada
ve I/O YAPMIYOR; burasi veriyi ortak takvime hizalayip cagiriyor.
"""
from __future__ import annotations

from . import momentum as M

# Kural ile kontrolun AYNI kapidan gecmesi icin tek yer.
VARSAYILAN_MALIYET = 0.0123      # Tiered, 2 pozisyon — OLCULDU 2026-08-30


def _hizala(db, evren: list, takvim: list[str],
            tercih_ccy=("USD",)) -> dict[str, list[float | None]]:
    """
    Her sembolun kapanisini ORTAK TAKVIME oturtur.

    NEDEN ORTAK TAKVIM: kesitsel bir kural "ayni gunde hangisi daha
    guclu" diye soruyor. Semboller kendi bar dizinlerinde tutulursa
    i. bar bir kagitta 3 Mart, digerinde 5 Mart olur ve kiyas
    FARKLI TARIHLERI karsilastirir.

    Eksik gun ONCEKI kapanisla tasiniyor (ileri doldurma). Ileriye
    dogru doldurmak GELECEGE BAKMAK olurdu; geriye dogru tasima o
    gun bilinen son fiyati kullanir.

    `db.fiyat_serisi` TEK KAPI — dogrudan `prices` sorgulanmiyor
    (para birimi ve sermaye islemi suzgeci orada).
    """
    yer = {t: i for i, t in enumerate(takvim)}
    out: dict[str, list[float | None]] = {}
    for e in evren:
        seri = db.fiyat_serisi(e["id"], limit=100000,
                               tercih_ccy=list(tercih_ccy))
        if len(seri) < M.ASGARI_BAR:
            continue
        dizi: list[float | None] = [None] * len(takvim)
        for r in seri:
            i = yer.get(r["ts"])
            if i is not None and r["close"]:
                dizi[i] = float(r["close"])
        son = None
        for i in range(len(dizi)):          # ileri doldurma (gecmisten)
            if dizi[i] is None:
                dizi[i] = son
            else:
                son = dizi[i]
        if sum(1 for x in dizi if x) >= M.ASGARI_BAR:
            out[e["symbol"]] = dizi
    return out


def _takvim(db, kiyas_kod: str, baslangic: str, bitis: str) -> list[str]:
    """Kiyas endeksinin islem gunleri — ortak takvim."""
    from .backtest import _endeks_serisi
    endeks = _endeks_serisi(db, kiyas_kod)
    return sorted(t for t in endeks if baslangic <= t <= bitis)


def kosu(db, baslangic: str, bitis: str, n: int,
         venue: str = "BUX", endeksler=("S&P 500", "Nasdaq 100"),
         kiyas_kod: str = "SPX", maliyet: float = VARSAYILAN_MALIYET,
         asgari_bar: int = 400) -> dict:
    """
    Kural + KONTROL GRUBU birlikte. Kontrolsuz sonuc DONMUYOR — bu
    deponun en pahali dersi (`42ab2fd`): BIST'te olculen kenarin yarisi
    piyasa surukletmesiydi.
    """
    from .backtest import _evren

    takvim = _takvim(db, kiyas_kod, baslangic, bitis)
    if len(takvim) < M.ASGARI_BAR + M.YENILEME:
        return {"hata": f"takvim kisa: {len(takvim)} bar "
                        f"({kiyas_kod} serisi eksik olabilir)"}

    evren = _evren(db, venue, asgari_bar=asgari_bar, endeksler=endeksler)
    kapanislar = _hizala(db, evren, takvim)
    if not kapanislar:
        return {"hata": "hizalanmis seri yok"}

    kural = M.yurut(kapanislar, n, maliyet=maliyet)
    kontrol = M.yurut(kapanislar, n, maliyet=maliyet,
                      secici=M.rastgele_secici(sorted(kapanislar)))

    o_k, o_r = M.ozet(kural), M.ozet(kontrol)
    fark = (round(o_k["ort_net_%"] - o_r["ort_net_%"], 3)
            if o_k.get("donem") and o_r.get("donem") else None)

    # AL-TUT: ayni pencerede endeks. "Hicbir sey yapmamak"tan iyi mi?
    from .backtest import _endeks_serisi
    seri = _endeks_serisi(db, kiyas_kod)
    al_tut = None
    if len(takvim) > 1 and seri.get(takvim[0]) and seri.get(takvim[-1]):
        al_tut = round((seri[takvim[-1]] / seri[takvim[0]] - 1) * 100, 1)

    return {
        "n": n, "maliyet_%": round(maliyet * 100, 3),
        "kapsam": {"sembol": len(kapanislar), "takvim_bar": len(takvim),
                   "baslangic": takvim[0], "bitis": takvim[-1]},
        "kural": o_k, "rastgele": o_r, "fark_%": fark,
        "al_tut_endeks_%": al_tut,
        "_donemler": kural,
    }
