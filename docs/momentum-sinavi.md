# Momentum sınavı — DONDURULMUŞ PARAMETRELER

**Yazıldı: 2026-08-30, koşumdan ÖNCE.** Sonucu görüp bu blok
değiştirilmeyecek. Değişiklik gerekirse yeni bir pencere gerekir.

---

## Neden bu sınav

Önceki iki ölçüm:

| koşum | sonuç | güvenilir mi |
|---|---|---|
| Donchian 20/10, ABD, 10 yıl, 31.096 işlem | rastgeleye göre −%0,353 | **evet** |
| Kesitsel momentum, ABD hisseleri, 10 yıl | rastgeleye göre +%15,5/çeyrek | **hayır** |

İkincisi **hayatta kalma yanlılığıyla** bozuldu ve ölçüldü:

```
evren (474 sembol) eşit ağırlıklı 10 yıllık al-tut   +%372
medyan sembol                                        +%166
10 yılda para kaybeden                                55/474  (%12)
10 kat ve üzeri artan                                 38/474
kıyas SPX                                            +%255
```

Gerçek bir evrende 10 yılda hisselerin %30-40'ı kaybeder. Burada %12.
Kaybedenler endeksten çıkarıldığı için **veride yoklar**. Kuralın seçtiği
semboller (NVDA, TSLA, CVNA, MSTR, APP) tam da "10 kat artan 38" kovasından.

**Çözüm evrende, kuralda değil.** Şirket batar, sektör batmaz.

## Sınav A — Sektör ETF rotasyonu

```
EVREN      11 SPDR sektör ETF'i:
           XLK XLF XLE XLV XLI XLP XLU XLB XLY XLRE XLC
           Hiçbiri delist olmadı. Üyelik sabit, seçim yok.
           XLRE (2015) ve XLC (2018) sonradan KURULDU — bu hayatta
           kalma yanlılığı DEĞİL (başarısızlıktan çıkarılma yok), ama
           evren zaman içinde büyüyor ve bu beyan ediliyor.

SINYAL     12-1 aylık getiri: close[t-21] / close[t-252] - 1
           (`analysis/momentum.py`, GERIYE=252 ATLA=21 — DEĞİŞMEDİ)
KAPI       sinyal > 0 (düşen piyasada pozisyon açma, nakitte kal)
YENILEME   63 bar (~çeyreklik)
N          2 — Ali'nin kurulumu. 1, 3, 4 AYRICA raporlanacak.
MALIYET    %1,23 gidiş-dönüş, YALNIZCA devir oranına
           (Tiered + 2 pozisyon; IBKR /whatif ile ÖLÇÜLDÜ 2026-08-30)
           Fixed (%2,00) duyarlılığı da raporlanacak.
KONTROL    aynı dönemlerde, aynı sayıda RASTGELE ETF, tohum sabit
PENCERE    verinin izin verdiği en uzun — çekildikten SONRA ölçülecek
KIYAS      SPX al-tut, aynı pencerede
```

## Sınav B — Endeks zamanlaması (zaman serisi momentumu)

```
EVREN      SPX tek başına (^GSPC). Hayatta kalma yanlılığı SIFIR.
SINYAL     12 aylık getiri > 0 -> tut, değilse nakit
YENILEME   21 bar (~aylık)
MALIYET    %1,23 gidiş-dönüş, yalnızca giriş/çıkış olduğunda
KONTROL    aynı sayıda rastgele "tut/nakit" kararı, tohum sabit
PENCERE    ^GSPC'nin izin verdiği en uzun — ölçülecek
```

## Kabul ölçütü (koşumdan ÖNCE yazıldı)

- Kural, **rastgele kontrolü geçiyor** mu (fark > 0)
- Dönem bazlı `t` raporlanacak; işlem sayısıyla hesaplanan bir `t`
  YAZILMAYACAK (aynı dönemin pozisyonları bağımsız gözlem değil)
- Al-tut endeksten iyi mi
- **Beyan edilecek sınırlar:** ETF'ler 1998'de kuruldu, yani sınav
  penceresi tek bir uzun ABD boğa piyasasını kapsıyor olabilir; bu
  ölçülüp yazılacak.

## Ölçüm sonucu

> Koşum sonrası buraya yazılacak. Bu satırın üstündeki hiçbir şey
> sonuca bakılarak değiştirilmeyecek.
