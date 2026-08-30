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

## Ölçüm sonucu (2026-08-30, koşum sonrası)

Yukarıdaki hiçbir parametre sonuca bakılarak değiştirilmedi.

### Veri — çekildi ve ölçüldü

```
9 sektör ETF (XLK XLF XLE XLV XLI XLP XLU XLB XLY)  6.963 bar  1998-12-22 →
XLRE                                                2.738 bar  2015-10-08 →
XLC                                                 2.060 bar  2018-06-19 →
SPX (^GSPC)                                        24.782 bar  1927-12-30 →
```

Fiyatlar `auto_adjust=False`, yani **temettüsüz**. Her iki kol da aynı
şekilde temettüsüz olduğu için kıyaslar geçerli; mutlak getiriler
düşük. Nakitte geçen süre için de **faiz sayılmadı**. İki eksik kısmen
birbirini götürür ama **ölçülmedi**.

### Sınav A — sektör rotasyonu: **BAŞARISIZ**

N=2, 106 çeyrek, 2000-03 → 2026-07:

| | bileşik | yıllık | en derin düşüş |
|---|---|---|---|
| KURAL | +221,9% | 4,5% | **−49,0%** |
| AL-TUT SPX | +427,3% | 6,5% | −47,6% |

Rastgele ETF seçimini geçiyor (+%1,145/çeyrek, t=1,84) **ama al-tut'tan
az kazanıp daha derin düşüyor.** "Az kazandırır ama korur" savunması
yok. Nakit kapısı 106 çeyreğin 4'ünde çalıştı, o da çöküşlerden SONRA
(2003-03, 2008-12, 2009-04, 2009-07) — dipte satıp toparlanmayı kaçırdı.

### Sınav B — endeks zamanlaması: **KISMEN BAŞARILI**

| dönem | yıl | KURAL | düşüş | AL-TUT | düşüş | KAYDIRILMIŞ (medyan) | işlem/yıl |
|---|---|---|---|---|---|---|---|
| 1929+ | 97,3 | 5,1% | **−42,8%** | 6,1% | −85,5% | 3,4% *(%94)* | 1,00 |
| 1950+ | 75,5 | 6,0% | **−34,4%** | 8,1% | −56,6% | 5,3% *(%72)* | 0,91 |
| 1990+ | 35,6 | 8,1% | **−30,8%** | 9,3% | −54,5% | 6,4% *(%78)* | 0,76 |
| 2000+ | 25,6 | 7,1% | **−25,6%** | 7,2% | −52,0% | 4,9% *(%83)* | 0,59 |

*(%N) = kaydırılmış 200 turun yüzde kaçı kuralın altında kaldı.*

**Üç bulgu, üçü de DÖRT dönemde birden tutuyor:**
1. Kural, adil kontrolü (aynı takvim rastgele tarihe kaydırılmış) her
   dönemde geçiyor: +%0,6 ile +%2,2/yıl, yüzdelik %72-94.
2. Kural, al-tut'tan **az** kazanıyor: −%0,1 ile −%2,1/yıl.
3. Kural, azami düşüşü **yarıya indiriyor** — en dayanıklı bulgu.

Getiri ÷ azami düşüş: kural her dönemde 1,2-2,0 kat daha iyi.

### İKİ KONTROL HATASI BULUNDU VE DÜZELTİLDİ

**1. Kontrol grubu haksız cezalandırılıyordu.** İlk kontrolüm her ay
bağımsız zar atıyordu → ayda %42 durum değişimi (2p(1−p), p=0,70) →
**yılda ~5 işlem**, kural ise 1. Kontrole yılda ~%3 fazladan komisyon
yüklüyordum. Düzeltme: kuralın KENDİ durum dizisini rastgele bir
noktadan dairesel kaydır — işlem sayısı, piyasada kalma oranı ve blok
uzunlukları AYNI, yalnızca zamanlama rastgele.

**2. Çarpık dağılımda ortalama raporlanıyordu.** Kaydırılmış turların
%94'ü kuralın altındayken *ortalama* kuralın üstünde çıkıyordu — birkaç
şanslı kaydırma ortalamayı çekiyor. Medyan ve yüzdelik raporlanıyor.

### Beyan edilen sınırlar

- Temettü ve nakit faizi **sayılmadı** (yukarıda).
- Düşüş üstünlüğü **4-6 bağımsız ayı piyasasına** dayanıyor; gözlem
  sayısı sanıldığı kadar büyük değil.
- Tek piyasa, tek varlık. Başka endekslerde tekrarlanmadı.
- Yüzdelik %72-94 **düşündürücü, kesin değil**; yalnızca tam dönem
  geleneksel eşiğe yaklaşıyor.
