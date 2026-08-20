"""
Gomme (embedding) katmani — YEREL Ollama uzerinden, veri makineden cikmaz.

Neden bu model: `embeddinggemma` (768 boyut, 621 MB). Bir onceki turda
`model2vec` (statik gomme) denendi ve GERCEK arsivde basarisiz oldu —
"altin hesabimla ilgili ne konusmustuk" sorgusu AVTX ve coin turlarini
dondurdu, altin konusmasi ilk uce bile girmedi. Kisa cumlelerdeki iyi
gorunum yanilticiydi.

Neden Ollama: bu makinede `torch` ve `onnxruntime` icin Python 3.14 /
x86_64 wheel'i YOK, yani `sentence-transformers` kurulur ama calismaz.
Ollama ayri bir ikili; venv'in Python surumunden bagimsiz.

OLCULEN UC GERCEK (2026-08-20, ollama 0.32.14)
----------------------------------------------
1. Vektorler L2-NORMALIZE geliyor (dort ayri metinde norm tam 1.0).
   Yani kosinus benzerligi = nokta carpimi; ayrica normalize etmeye
   gerek yok ve etmek yuvarlama hatasi disinda bir sey eklemez.

2. SABLONU OLLAMA UYGULAMIYOR. `ollama show --modelfile` ciktisi
   `TEMPLATE {{ .Prompt }}` — duz gecis. Onekleri BURADA ekliyoruz.
   (Gorev belgesi "ayni metni ham ve sablonlu gonder, vektorler
   aynysa uygulanmiyordur" diyordu; o test iki durumu AYIRT EDEMEZ —
   Ollama sablonu uygulasaydi ikinci girdi CIFT sablonlanir ve
   vektorler yine farkli cikardi. Modelfile dogrudan cevap veriyor.)

3. ~4616 KARAKTERDE SESSIZ KIRPMA (num_ctx 2048). Olcum: en uzun
   arsiv satirinin (8983 karakter) ilk 4616 karakterinin vektoru,
   tam metnin vektoruyle OZDES cikti — yani %49'u atilmis ve hicbir
   uyari verilmemis. Arsivde 156 satirin 8'i (%5,1) siniri asiyor,
   toplam metnin %3,4'u kirpiliyor. Kirpma KABUL EDILIYOR ama
   BEYAN EDILIYOR: `kirpildi()` cagrilabilir ve indeksleme kac
   satirin kirpildigini raporluyor. Sessiz kayip bu projede en
   yuksek siddetli hata sinifi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

__all__ = ["Gomme", "GommeHatasi", "SORGU_ONEKI", "BELGE_ONEKI"]

log = logging.getLogger(__name__)

# EmbeddingGemma ASIMETRIK calisir: sorgu ve belge ayri sablonlardan
# gecer. Ollama uygulamadigi icin (yukari bak) burada ekleniyor.
SORGU_ONEKI = "task: search result | query: "
BELGE_ONEKI = "title: none | text: "

# Olculen sessiz kirpma sinirinin biraz altinda. Amac kirpmayi
# ENGELLEMEK degil — model zaten kirpiyor — GORMEK.
AZAMI_KARAKTER = 4600


class GommeHatasi(RuntimeError):
    """
    Gomme uretilemedi: Ollama kapali, model yok, ya da boyut beklenenden
    farkli.

    BOS LISTE DONMEK YERINE ISTISNA. Arama katmani "sonuc bulunamadi"
    ile "arama CALISMADI" arasindaki farki kaybederse, kullaniciya
    yanlis bir "yok" beyani gider — bu projede olculmus en yuksek
    siddetli hata sinifi (bkz. README). Ollama'nin kapali olmasi bir
    arama sonucu degildir.
    """


@dataclass(frozen=True)
class Gomme:
    url: str
    model: str
    boyut: int
    timeout_sn: float
    batch: int

    # ------------------------------------------------------------------
    def sorgu(self, metin: str) -> list[float]:
        """Tek bir arama sorgusunu gommeye cevirir (sorgu oneki ile)."""
        return self._cagir([SORGU_ONEKI + self._kirp(metin)])[0]

    def belgeler(self, metinler: list[str]) -> list[list[float]]:
        """
        Arsiv metinlerini gommeye cevirir (belge oneki ile), toplu.

        Bos liste icin AG CAGRISI YAPMAZ — dongu bos donerek bunu zaten
        sagliyor, ayri bir koruma EKLENMEDI. (Eklenmisti; kasitli bozma
        onu KALDIRINCA test yine gecti, cunku olu koddu.) Sozlesme yine
        de testle sabit: "indekslenecek bir sey yok" ile "Ollama kapali"
        ayri seyler ve yalnizca ikincisi hata vermeli.
        """
        cikti: list[list[float]] = []
        for bas in range(0, len(metinler), self.batch):
            oebek = metinler[bas:bas + self.batch]
            cikti.extend(self._cagir([BELGE_ONEKI + self._kirp(m) for m in oebek]))
        return cikti

    @staticmethod
    def kirpildi(metin: str) -> bool:
        """Bu metin modelin baglamina SIGMIYOR — cagiran beyan etsin."""
        return len(metin or "") > AZAMI_KARAKTER

    # ------------------------------------------------------------------
    @staticmethod
    def _kirp(metin: str) -> str:
        # Model zaten kirpiyor; burada kirpmak yalnizca aga bosuna
        # bayt tasimayi onluyor ve sinirin NEREDE oldugunu kodda
        # gorunur kiliyor.
        return (metin or "")[:AZAMI_KARAKTER]

    def _cagir(self, girdiler: list[str]) -> list[list[float]]:
        try:
            r = httpx.post(f"{self.url.rstrip('/')}/api/embed",
                           json={"model": self.model, "input": girdiler},
                           timeout=self.timeout_sn)
            r.raise_for_status()
            veri = r.json()
        except httpx.HTTPError as e:
            raise GommeHatasi(
                f"Ollama'ya ulasilamadi ({self.url}): {e}. "
                f"`ollama serve` calisiyor mu?") from e

        vektorler = veri.get("embeddings")
        if not vektorler or len(vektorler) != len(girdiler):
            raise GommeHatasi(
                f"Ollama {len(girdiler)} metin icin "
                f"{len(vektorler or [])} vektor dondurdu: "
                f"{str(veri)[:200]}")
        for v in vektorler:
            if len(v) != self.boyut:
                # Model kartina guvenilmez, olculur. Boyut degistiyse
                # eski BLOB'lar gecersizdir ve sessizce yanlis
                # benzerlik uretirler.
                raise GommeHatasi(
                    f"beklenen boyut {self.boyut}, gelen {len(v)} "
                    f"(model {self.model!r} degismis olabilir)")
        return vektorler
