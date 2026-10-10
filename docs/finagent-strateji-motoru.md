# finagent — Strateji Motoru (IBKR / ABD evreni)

**Durum:** **Adım 1-4 ve 6 bitti; Adım 5 canlı emir bekliyor** (2026-08-27); §8 sınavı bekliyor
**Tarih:** 2026-08-27

> **Adım 1 ölçüm turu tamamlandı.** §0'daki **9 [?] kaleminin 9'u da ölçüldü**
> ve bu belgeye yazıldı. Hiçbiri tahmin edilmedi.
>
> Ölçüm turu **dört kusur** ortaya çıkardı:
> bayat ayna "güncel" göründü · geçici çağrı hatası "sembol yok" diye
> raporlandı · kesik `shortName` ad kapısını yanlış kapattı — üçü de
> düzeltildi ve mutasyonla kanıtlandı. Dördüncüsü (`oturum.py`'de geri
> çekilmesiz init döngüsü) **açık bırakıldı**, Adım 1'in kapsamı dışında.
>
> Bir de ölçümün *yanlış çıkardığı* bir beklenti var: sızıntının ABD'de değil
> **BIST'te** olduğu görüldü (§8.4).
**Yerine geçtiği belge:** `docs/finagent-ibkr-strateji.md` (o belgenin Adım 0–3'ü bu depoyla
tutarsız çıktı; gerekçeler §9'da kalem kalem)

---

## 0. Bu belge nasıl okunur

Bu belge, bu konuşmayı görmemiş bir ajan tarafından uygulanmak üzere yazıldı.
Kendi kendine yeter: her sayı ya bir dosya/satıra, bir SQL sorgusuna, bir commit'e ya
da çalıştırılmış bir ölçüme dayanıyor.

Üç işaret kullanılıyor ve **karıştırılmamalı**:

| İşaret | Anlamı |
|---|---|
| **[Ö]** | **Ölçüldü.** Kaynağı yanında. Uygulayan ajan bunu yeniden ölçmek zorunda değil ama isterse doğrulayabilir. |
| **[K]** | **Kodda yazılı.** `dosya:satır` verildi. Değişmişse belge yanlıştır, kod doğrudur. |
| **[?]** | **Ölçülmedi.** Uygulama sırasında ölçülecek. **Tahmin edilmeyecek.** Ölçüm sonucu bu belgeye yazılacak. |

Bu depoda tekrar eden bir kusur sınıfı var: *"veri varken yok demek"* ve
*"sessizce kırpıp kırpıldığını söylememek"*. Aynı disiplin bu belgeye de uygulanıyor —
bilinmeyen bir şey **[?]** ile işaretlenir, doldurulmuş gibi yapılmaz.

---

## 1. Amaç

IBKR'de işlem gören ABD hisselerinde, **deterministik** bir trend kuralının ürettiği
al/sat önerilerini üreten, deftere yazan, ölçen ve zamanla yetkisini kanıtla genişleten
bir motor.

**Kapsam içi:** S&P 500 + Nasdaq 100 evreni, Donchian 20/10 + 2N kuralı, günlük kırılım
tablosu, tek dokunuşla emir, ajan bazlı karne.

**Kapsam dışı:** BIST (ayrı görev), kripto, yeni veri kaynağı, yeni kütüphane,
TradingView, saatlik katman, pekiştirmeli öğrenme (gerekçe §9.4).

---

## 2. Ölçülmüş gerçekler

Bu bölümdeki hiçbir sayı tahmin değildir. Uygulama kararları bunlara dayanıyor.

### 2.1 Evren

**[Ö]** `index_members` tablosu dolu — endeks üyelikleri zaten katalogda:

```sql
SELECT m.index_name, COUNT(*) uye,
  SUM(CASE WHEN EXISTS(SELECT 1 FROM prices p WHERE p.instrument_id=i.id) THEN 1 ELSE 0 END) fiyat_var,
  SUM(CASE WHEN COALESCE(d.conid,'')<>'' THEN 1 ELSE 0 END) conid_var
FROM index_members m JOIN instruments i ON i.id=m.instrument_id
LEFT JOIN identities d ON d.instrument_id=i.id
WHERE m.index_name IN ('S&P 500','Nasdaq 100') GROUP BY 1;
```

| Endeks | Üye | Fiyat serisi var | conid var |
|---|---|---|---|
| S&P 500 | 503 | 26 | 25 |
| Nasdaq 100 | 102 | 15 | 14 |
| **Tekil toplam** | **518** | — | — |

Yani 518 ABD şirketi **enstrüman olarak zaten kayıtlı**; eksik olan iki şey var:
fiyat serisi ve conid.

**[K]** Bu üyelikleri `src/finagent/collectors/indices.py` topluyor ve sürdürüyor.
Aynı dosyadaki "KAPSAM KARARI" notu şunu diyor: bu ~660 şirket *katalog*a girer,
*araştırma hedefi* olmaz — çünkü hepsi için günlük EDGAR + basın taraması pahalı.
**Bu karar bu belgeyi bağlamaz:** Donchian kuralı yalnızca OHLCV istiyor; haber,
bilanço, EDGAR ve LLM çağrısı gerektirmiyor. Maliyet gerekçesi burada yok.

### 2.2 Kuralın ürettiği hacim

**[Ö]** Mevcut 38 sembollük (BUX venue, izleme listesi ∪ IBKR pozisyonları) alt evrende,
`analysis.trend_takip.islemler()` gerçek fiyat serileriyle koşuldu. Son 12 ay
(2025-08-27 → 2026-08-27):

| Likidite eşiği (devir) | İşlem/12 ay | Ayda | Sembol | Ort. tutma | İsabet |
|---|---|---|---|---|---|
| yok | 225 | 18,8 | 38 | 15 bar | %36 |
| 1.000.000 | 179 | **14,9** | 32 | 16 bar | %39 |
| 5.000.000 | 172 | 14,3 | 30 | 15 bar | %38 |

Buradan türeyen oran — planın en çok kullanılan sayısı:

> **~0,5 giriş / sembol / ay**

**[Ö]** Eşzamanlı açık pozisyon sayısı (aynı koşumdan, günlük sayım, 365 gün):

| Evren büyüklüğü | Ortalama eşzamanlı | Medyan | Azami |
|---|---|---|---|
| 38 sembol | 11,6 | 11 | 24 |
| 25 sembol (conid var) | 9,3 | 9 | 20 |
| 17 sembol (conid + USD) | 6,5 | 6 | 14 |

Türeyen oran: **~0,38 eşzamanlı pozisyon / sembol**.

**Bu iki orandan 518 sembol için beklenen:** ~250 sinyal/ay (~12 işlem günü başına),
~190 eşzamanlı pozisyon.

**[Ö] ÖLÇÜLDÜ (2026-08-27, Adım 1 sonrası).** Ekstrapolasyon yerine gerçek evren:
aynı fonksiyon (`analysis.trend_takip.islemler`), aynı pencere (2025-08-27 →
2026-08-27), üretim kapıları açık (USD + devir ≥ 1.000.000 + `asgari_bar` 1500).
Taranan: **485 sembol** (511 serinin 25'i 1.500 bardan sığ, 1'i EUR kote).

| | Ekstrapolasyon | **Ölçüm** | Fark |
|---|---|---|---|
| Sinyal / ay | ~250 | **247,2** | −%1 |
| Eşzamanlı pozisyon (ort.) | ~190 | **174,6** | −%8 |
| Giriş / sembol / ay | 0,50 | **0,510** | +%2 |
| Eşzamanlı / sembol | 0,38 | **0,360** | −%5 |

Yani 38 sembolden türetilen iki oran 518 sembolde **tuttu**; plan bu sayıların
üstüne kurulabilir. Ek ölçümler (aynı koşum):

- Eşzamanlı pozisyon **medyan 182, azami 308** — `gunluk_emir_tavani: 2` bu
  hacmin yanında çok küçük ve bu bilinçli (§6, Adım 4: kural TAM genişlikte
  ölçülür, hesap onay bant genişliği kadarını işler).
- **Ortalama tutma 14 bar** (38 sembolde 15-16 idi). Adım 2'nin `ufuk_gun`
  değeri buradan okunmalı: **14**, 15 değil.
- **İsabet %32** (38 sembolde %39 idi). ABD büyük-cap'te kuyruk daha baskın;
  §11'deki beklenti ayarı bu sayıyla güncellendi.
- Likidite kapısı ABD büyük-cap'te **neredeyse hiç bağlamıyor**: kapısız koşum
  3.083 işlem, kapılı 2.966 (fark %3,8). `asgari_devir.USD` bir emniyet
  supabı; evrenin şeklini o belirlemiyor.
- Aylık girişler 92 ile 399 arasında salınıyor (2026-04: 399, 2026-03: 110).
  "Günde ~12 sinyal" bir ortalamadır; kırılımlar **kümelenir**.

### 2.3 Maliyet

**[K]** `src/finagent/ibkr/emir.py:303-304` — IBKR'nin kendi `/whatif` ucundan,
gerçek bir emir gönderilmeden alınmış rakam:

> *"Ölçüldü (2026-08-26) — 0,05 lot KO için 4,25 USD tutar, 0,04 USD komisyon."*

→ **%0,94 tek yön, ~%1,9 gidiş-dönüş** (4,25 USD'lik kesirli ABD hissesi emrinde).

**[K]** `src/finagent/ibkr/mutabakat.py:373-374` — gerçek dolum kaydı alınabiliyor:
fiyat, komisyon, net tutar. Aynı satırda ölçülmüş bir vaka: tahmin 91,00, gerçek 90,99.

**[Ö] AVRUPA KOMİSYONU ÖLÇÜLDÜ (2026-08-27, `emir.onizle()` = `/whatif`,
hiçbir emir gönderilmeden).** Canlı hesap U28075748, EUR tabanlı:

| Önizlenen emir | Tutar | Komisyon | Tek yön |
|---|---|---|---|
| `ABN.AS` 1 adet @ 36,77 EUR | 36,77 EUR | **3,00 EUR** | **%8,16** |
| `AD.AS` 1 adet @ 27,50 EUR | 27,50 EUR | **3,00 EUR** | **%10,91** |
| `IWDA.AS` 1 adet @ 114,43 EUR | — | ölçülemedi | nakit yetmedi (komisyon değil) |

**Sonuç: Avrupa'da SABİT 3 EUR taban komisyon var.** ABD'de aynı hesapta
4,25 USD'lik emrin komisyonu 0,04 USD idi (%0,94) — yani Avrupa'da mutlak
komisyon **75 kat**, ve bu boyutlarda **gidiş-dönüş %16-22**.

İki yapısal fark bunu daha da sertleştiriyor:
- **Avrupa'da kesirli hisse yok.** ABD ölçümü 0,05 lot üzerindeydi; Avrupa'da
  asgari bilet 1 tam hisse, yani zaten daha büyük.
- 3 EUR sabit taban, komisyonun **%1'e inmesi için ~300 EUR'luk bilet** demek.

→ Bu ölçüm §1'deki **"kapsam: yalnızca ABD"** kararını doğruluyor ve artık
gerekçe tahmin değil. Evren Avrupa'ya genişletilecekse önce bu tablo
yenilenmeli; bugünkü hâliyle küçük bilette ekonomi çalışmaz.

**Sonuç ve uyarı:** %1,9 gidiş-dönüş maliyet, 4-5 USD'lik emirlerde ABD büyük-cap
işlem başına beklentisinin büyük kısmını yer. **Bu yüzden karne BRÜT tutulacak**
(§6.4). Küçük boyut *tesisatı* test eder, *ekonomiyi* değil.

### 2.4 Emir yolu çalışıyor

**[Ö]** `emirler` tablosunda üç kayıt var, üçüncüsü **gerçekleşti**:

```
id 3 | ali | U28075748 | conid 8894 (KO) | BUY LMT 0.05 @ 91.00 | DAY
     | durum: gerceklesti | ibkr_durum: filled | 2026-08-26T06:32:32Z
```

Yani emir rayı kurgusal değil, sahada dolmuş. Kesirli hisse çalışıyor.

### 2.5 Veri maliyeti

**[K]** `src/finagent/collectors/prices.py:388` — ölçülmüş: *"51 sembol (ABD +
Amsterdam + LSE + BIST) 1,9 saniyede, 51/51 başarılı"* (`yfinance`, toplu).
→ 518 sembol için çekim süresi saniyeler mertebesinde.

**[Ö]** `prices` tablosu bugün 988.638 satır; veritabanı 162 MB.
518 sembol × ~2.500 bar (10 yıl) ≈ +1,3M satır ≈ **+200 MB**. Veritabanı yaklaşık
iki katına çıkar.

**[Ö] ÖLÇÜLDÜ (2026-08-27, çekim sonrası):**

| | Önce | Sonra | Fark |
|---|---|---|---|
| `prices` satır | 988.638 | **2.208.605** | +1.219.967 |
| Veritabanı | 169,4 MB | **324,1 MB** | +154,7 MB |
| Yedek (VACUUM'lu) | ~120 MB | **301,3 MB** | — |

Tahmin +200 MB idi, gerçek **+155 MB** — VACUUM'suz canlı dosyada bile tahminin
altında. Veritabanı ikiye katlanmadı, **1,9 katına** çıktı.

**[Ö] Yedek koşusu ölçüldü** (`storage/yedek.py`, gerçek iCloud dizinine):
**VACUUM INTO 3,15 sn**, doğrulama + ayna dahil duvar saati **6,64 sn**.
Önceki ölçüm 128,5 MB'da 1,07 sn idi → boyutla **doğrusal**, engel yok.
Budanan yedek: yok (en eskisi 2026-08-21, `yedek.gun: 7` sınırının içinde).

> **Bu ölçüm bir kusur buldu ve düzeltildi.** Aynı gün ikinci kez yedek alındığında
> `ayna_guncelle()` "ayna guncel" dedi — ama ayna 07:30'daki eski dosyaydı:
> arşiv `prices` 2.182.843 / `predictions` 983, ayna `prices` 988.570 /
> `predictions` 888. Sebep: `dogrula(hedef)` **kaynak sayıları olmadan**
> çağrılıyordu, yani yalnızca `quick_check` — "sağlam mı", "güncel mi" değil.
> `dogrula`'nın "kaynaktan AZ satır" kolu **zaten vardı**, eksik olan tek şey
> oraya bağlanmasıydı. Ayna tam da *ağdan bağımsız geri yükleme* için var;
> bayat olduğunu ancak geri yüklerken öğrenmek bu deponun en kötü hata sınıfı.
> Düzeltildi, mutasyonla kanıtlandı (`test_yedek_AYNASI_SAGLAM_AMA_ESKI_olmayi_gecemez`)
> ve sahada doğrulandı: ayna 301,3 MB / 2.182.843 satıra tazelendi.

### 2.6 Bugünkü piyasa durumu (referans)

**[Ö]** 26 Ağustos 2026 kapanışı itibarıyla, conid'i çözülmüş 17 USD sembolün
**hiçbiri** 20 günlük yükseğinin üstünde değil. En yakını BIIB (−%0,2), ardından
EIMI.L (−%0,7), PFE (−%0,9), VRTX (−%1,0). En uzağı ALNY (−%16,6).

Bu, sistemin ilk günü için beklenti ayarıdır: **kural konuşmadığı gün susar.**
Sıfır kırılımlı bir gün arıza değildir.

### 2.7 Dış pencere (model sınavı için)

**[Ö]** 1 Haziran → 27 Ağustos 2026 endeks getirileri (veritabanından):

| QQQ | SPX | AEX | XU100 |
|---|---|---|---|
| **−4,2%** | +1,0% | +6,9% | +6,6% |

ABD düşmüş, Avrupa yükselmiş — **karışık rejim**. Uzun-yönlü bir trend kuralının
piyasa sürüklemesine binemeyeceği bir pencere. §8'deki sınav bu pencerede koşacak.

### 2.8 Kuralın kendi geçmişi (BIST'te ölçülmüş)

**[K]** commit `42ab2fd`, 327 BIST enstrümanı, 2016-09 → 2026-06:

```
ESKI (kilit yok, filtre yok) : 12.436 islem · beklenti %6,410
+A5 taban kilidi             : 12.430 islem · beklenti %5,901
+A7 likidite 50M             :  6.211 islem · beklenti %6,541
URETIM (A5+A7)               :  6.208 islem · beklenti %5,846
Rastgele giris kontrolu      :                        %2,970
→ KURALA KALAN                                        %2,876
```

Aynı commit'ten iki kritik olgu:
- **Kârın %55,9'u en iyi %5'lik işlem kuyruğundan geliyor.** Trend takibi genişlik
  ister; dar bir evren kuyruğu keser.
- Gözlem birimi **AY**: 118 ay, ay ortalaması %3,13, pozitif ay %55,1, `t_ay` 3,77.
  *"6.208 işlem BAĞIMSIZ GÖZLEM DEĞİL; aynı ayın yüzlerce işlemi TEK hareketi
  konuşuyor."*

**Bu belge için sonucu:** evreni genişletmek işlem sayısını artırır ama *"kuralın
kenarı var mı"* sorusunu **hızlandırmaz** — o soru ay sayısıyla sınırlı. Genişlik
şunları hızlandırır: kuyruk yakalama, sektör yanlılığının kalkması, eşleştirilmiş
kıyas (§8).

---

## 3. Mimari — clean architecture

### 3.1 Katmanlar

Bu depo zaten katmanlı; yeni modül **var olan katmanlara oturacak**, yeni bir dikey
hat açmayacak.

```
┌─ TESLİMAT (adapters, dışa bakan) ────────────────────────────────┐
│  run.py (CLI)   bot/listener.py (Telegram)   notify/telegram.py  │
└──────────────────────────┬───────────────────────────────────────┘
                           │ yalnızca aşağıyı çağırır
┌─ ORKESTRASYON (use case) ▼───────────────────────────────────────┐
│  pulse/runner.py (Nabiz)   pulse/gunici.py   pipeline.py         │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌─ ALAN MANTIĞI (domain) ──▼───────────────────────────────────────┐
│  pulse/strateji.py  ←── YENİ, tek yeni modül                     │
│  pulse/seviye.py    pulse/boyutlama.py   pulse/journal.py        │
│  pulse/screener.py  analysis/{indicators,trend_takip}            │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌─ KALICILIK (gateway) ────▼───────────────────────────────────────┐
│  storage/db.py                                                   │
└──────────────────────────────────────────────────────────────────┘
┌─ DIŞ DÜNYA (adapters) ───────────────────────────────────────────┐
│  collectors/*   ibkr/*   llm.py                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 Bağımlılık kuralları — ihlali test ile engellenecek

1. **Bağımlılık içe doğrudur.** `pulse/strateji.py`:
   - **import EDER:** `storage.db`, `pulse.seviye`, `pulse.boyutlama`, `config`
   - **import ETMEZ:** `notify.*`, `ibkr.*`, `llm`, `bot.*`
   - Gerekçe: mesaj biçimi, aracı kurum ve model sağlayıcısı değişince alan mantığı
     değişmemeli.
   - **Kopyalanacak hazır kalıp:** `tests/test_ibkr.py:1658`
     `test_MODEL_EMIR_GONDEREMEZ_yalnizca_onaya_sunar`. Bu test **AST ile** kaynağı
     ayrıştırıp `ast.ImportFrom` düğümlerindeki **isimleri** topluyor ve yasaklıları
     arıyor. Docstring'i ayrıca bir ders taşıyor ve yeni test onu tekrarlamamalı:
     testin önceki hali `tools.py` içinde `"ibkr.emir"` **metnini** arıyordu ve meşru
     okuma araçları eklenince yanlış yere kırmızı oldu — *"kaba metin araması yanlış
     soruyu soruyordu; doğru soru 'hangi İSİMLER içe aktarıldı'"*.
     `strateji.py` testi de metin değil **AST** kullanacak.

2. **Karar çekirdeği saf fonksiyondur.** `karar(seviyeler: dict, ayar: dict) -> dict | None`
   I/O yapmaz, `db` almaz, saat okumaz, rastgele sayı üretmez. Tüm girdi parametrede.
   Gerekçe: bu fonksiyon 300 satırlık sentetik seriyle, veritabanı olmadan test
   edilebilmeli.

3. **Tek gerçek kaynağı — ikinci kopya yasak.** Bu deponun en pahalı öğrendiği ders:
   - Göstergeler **yalnızca** `analysis/indicators.py`'den. `seviye.py:24-30` bunu
     yazıyor: *"tarayıcının kendi RSI'ını hesaplaması MSFT'de 84,8 vs 70,9 farkı
     üretmişti, ikinci bir hesap yolu açılmayacak."*
   - Seviyeler **yalnızca** `pulse/seviye.py:seviyeler()`'den.
   - Boyut **yalnızca** `pulse/boyutlama.py:boyut()`'tan. **`0.10/sigma` gibi ikinci
     bir boyutlama formülü YAZILMAYACAK** — mevcut formül gerçek 2N stop mesafesine
     dayanıyor ve daha doğru.
   - Donchian pencereleri **yalnızca** `analysis/trend_takip.py`'deki sabitlerden
     (`GIRIS_PENCERE=20`, `CIKIS_PENCERE=10`, `ATR_PENCERE=20`, `STOP_N=2.0`).
     `seviye.py:44-46` bu hizalamayı zaten yazıyor.

4. **Kod içinde liste ve eşik sabiti yok.** Evren, likidite eşiği, günlük emir tavanı,
   rastgele tohum — hepsi `config/settings.yaml`. Doğrulama `config.py`'de
   (`raise ValueError` kalıbı, örn. satır 221-336).

5. **Fiyat serisine tek kapı.** `db.fiyat_serisi()`. Doğrudan `SELECT ... FROM prices`
   yazılmayacak — para birimi karıştırır (`db.py:1374-1401`, canlı veride ölçülmüş:
   TSLA serisinde 4,07 EUR ile 489,88 USD yan yanaydı).

### 3.3 Yeni modülün sınırları

`pulse/strateji.py` — tek yeni dosya. Yaklaşık 150-200 satır.

```python
# Saf çekirdek — I/O yok, test edilebilir
def karar(seviyeler: dict, ayar: dict) -> dict | None: ...
def kirilim_mi(seviyeler: dict) -> bool: ...

# Kabuk — db okur, saf çekirdeği çağırır
def tara(db, settings, evren: list[dict]) -> list[dict]: ...
def secim(adaylar: list[dict], tavan: int, tohum: int) -> list[dict]: ...
```

`secim()` de saftır: aynı adaylar + aynı tohum → aynı seçim. `random.Random(tohum)`
kullanır, `random` modülünün global durumuna dokunmaz — `trend_takip.rastgele_kontrol`
ile aynı disiplin (`trend_takip.py:295`).

---

## 4. Yeniden kullanılacak mevcut yüzey

Uygulayan ajan bunları **yeniden yazmayacak**. İmzalar doğrulandı (2026-08-27).

| Ne | Nerede | İmza / dönüş |
|---|---|---|
| Seviyeler | `pulse/seviye.py:64` | `seviyeler(db, instrument_id) -> dict \| None`<br>Anahtarlar: `sembol`, `venue`, `son_kapanis`, `para_birimi`, `bar_ts`, `donchian_giris`, `donchian_cikis`, `n`, `stop_2n`, `sma20/50/200`, (varsa) `sermaye_islemi`, `segment_baslangici` |
| Toplu seviye | `pulse/seviye.py:206` | `dosya(db, semboller: list[str]) -> dict` |
| Boyut | `pulse/boyutlama.py:58` | `boyut(giris, stop, risk_payi=1.0) -> dict \| None`<br>Dönüş: `stop_mesafesi_pct`, `risk_payi_pct`, `pozisyon_payi_pct`, `hesaplanan_pay_pct`, `kesildi`, `not` |
| Boyut satırı | `pulse/boyutlama.py:100` | `satir(giris, stop, para_birimi, risk_payi) -> str \| None` (Telegram için, Türkçe sayı biçimli) |
| Deftere yazım | `pulse/journal.py:82` | `Defter(db).kaydet(gorusler: list[dict], sahip: str) -> dict` |
| Puanlama | `pulse/journal.py:278` | `Defter(db).puanla(sahip=None) -> dict` |
| Karne | `pulse/journal.py:418` | `Defter(db).karne(sahip, gun=180, ajan="hakem", ...)` |
| Ajan karnesi | `pulse/journal.py:797` | `Defter(db).ajan_karnesi(sahip, gun=180) -> list[dict]` |
| Fiyat serisi | `storage/db.py:1349` | `fiyat_serisi(instrument_id, limit=300, bitis=None) -> list` |
| ATR | `analysis/trend_takip.py:56` | `_atr(seri, i, pencere=20)` — `i` DAHİL DEĞİL |
| Kural sabitleri | `analysis/trend_takip.py:46-49` | `GIRIS_PENCERE=20`, `CIKIS_PENCERE=10`, `ATR_PENCERE=20`, `STOP_N=2.0` |
| Emir isteği | `ibkr/emir.py` | `EmirIstegi`, `OnayFisi`, `gonder()`, `onizle()` (=`/whatif`) |
| Dolum kaydı | `ibkr/mutabakat.py:367` | `dolum_kaydi(gecmis, emir_no) -> dict \| None` — fiyat, komisyon, net tutar |

### 4.1 `journal.kaydet()` görüş sözleşmesi

`strateji.py`'nin üreteceği her karar bu şekle uymalı (`journal.py:82-175`'ten
doğrulandı):

```python
{
  "ajan": "strateji",              # zorunlu, [:20] kırpılır, lower()
  "sembol": "BIIB",                # zorunlu, instruments'ta bulunmalı
  "yon": "yukari",                 # zorunlu: yukari | asagi | notr
  "ufuk_gun": 15,                  # yoksa VARSAYILAN_UFUK=5 (journal.py:35)
  "guven": 0.5,                    # 0-1
  "gerekce": "...",                # [:400], başına "[strateji] " eklenir
  "tez": "...",
  "gecersizlesme_kosulu": "close < 208.92",   # gramer §4.2
  "izlenecek_esik": None,
  "tur": "alim",                   # TURLER = alim|koruma|satis|bekle (seviye.py:118)
  "giris": 221.56,
  "stop": 208.92,
  "giris_kaynak": "donchian_giris",
  "stop_kaynak": "stop_2n",
}
```

**Benzersizlik kısıtı:** `predictions` üzerinde
`UNIQUE (olusma_ts, instrument_id, ufuk_gun, ajan, sahip)`. Aynı gün aynı sembole
iki `strateji` görüşü yazılamaz — `kaydet()` bunu `atilan_cakisma` olarak **sayar**,
sessizce yutmaz.

### 4.2 `gecersizlesme_kosulu` grameri — dikkat

**[K]** `pulse/tez.py:39-52`:

```
<alan> <op> <sayi>        örnek: close < 1520 · rsi14 > 75
  alan : close | rsi14 | sma20 | sma50 | sma200 | hacim_kat | car_t
  op   : < veya >
  sayi : ondalık sayı, PARA BİRİMİ YAZMA
  Alan-alan karşılaştırması (close < sma50) KABUL EDİLMEZ.
```

**Sonucu — uygulayan ajanın bilmesi gereken tuzak:** `"close < donchian_cikis"`
**geçersizdir** ve `_gecerli_kosul()` (journal.py:43) onu reddedip sayar.

**Doğru çözüm:** koşula **2N stop'un sayısal değeri** yazılır (`"close < 208.92"`).
Bu hem gramere uyar hem de semantik olarak doğrudur: 2N stop **girişte sabitlenir** ve
değişmez. Donchian 10 günlük çıkışı ise **yuvarlanan** bir seviyedir; o, günlük
kırılım tablosunda ayrıca gösterilir (§6.3), koşul alanına yazılmaz.

---

## 5. Ayarlar

`config/settings.yaml` içine, `ibkr:` bloğunun altına eklenecek. **Kod içinde
karşılığı olan sabit bırakılmayacak.**

```yaml
ibkr:
  # ... mevcut anahtarlar (acik, sahip, taban_url, yaris) ...

  strateji:
    # ANA ANAHTAR. false iken hiçbir sinyal üretilmez, hiçbir mesaj gitmez.
    enabled: true

    # EVREN — endeks adları, sembol listesi DEĞİL. Liste `index_members`
    # tablosundan gelir ve `collectors/indices.py` onu güncel tutar.
    # Buraya sembol yazmak, endeks değişince sessizce ayrışan ikinci bir
    # liste yaratmak olurdu.
    endeksler: ["S&P 500", "Nasdaq 100"]

    # Yalnızca bu para biriminde kote enstrümanlar. ABD kesirli hissede
    # komisyon ÖLÇÜLDÜ (%0,94 tek yön); Avrupa kotasyonları ölçülmedi.
    para_birimleri: ["USD"]

    # LİKİDİTE KAPISI — enstrümanın PARA BİRİMİ cinsinden günlük devir
    # medyanı (kapanış × hacim, 20 günlük, giriş anından).
    #
    # `sources.isyatirim.min_hacim_tl` (50.000.000) BURAYA MİRAS ALINMAZ:
    # o eşik TL cinsinden ve USD devire uygulanırsa yanlış ölçekte olur.
    asgari_devir:
      USD: 1000000

    # VERİ DERİNLİĞİ eşiği — TARAMA KAPISI DEĞİL. 1.500 bar ≈ 6 yıl.
    # Nerede okunuyor: `prices._tazeleme_plani` (bu derinliğe ulaşmış
    # seri kademeli tazelenir, ulaşmamış olan derin çekilir).
    # Nerede okunMUYOR: `pulse/strateji.py`. Tarama için gereken derinlik
    # göstergelerin kendi ihtiyacıdır (Donchian 20 + ATR ≈ 40 bar).
    asgari_bar: 1500

    # GÜNLÜK EMİR TAVANI — elle onay bant genişliği.
    # Kural ~250 sinyal/ay üretebilir (~12/gün); insan bu kadar onaylayamaz.
    # Tavanı aşan sinyaller DEFTERE YAZILIR ama emre dönüşmez.
    gunluk_emir_tavani: 2

    # SEÇİM TOHUMU. Tavanı aşan günlerde adaylar arasından TOHUMLU RASTGELE
    # seçim yapılır — "en iyi 2" değil.
    #
    # NEDEN RASTGELE: "en iyi" demek, test edilmemiş İKİNCİ bir kural
    # eklemek demektir. Tohumlu rastgele seçim, kuralın çıktısının YANSIZ
    # bir alt-örneklemidir: ortalama tahmini bozulmaz.
    # TOHUM SABİT: aynı adaylar aynı seçimi verir, koşum tekrarlanabilir.
    # (`analysis/trend_takip.py:295` ile aynı disiplin.)
    secim_tohumu: 20260828

    # Bir işlemde göze alınan portföy yüzdesi. `pulse/boyutlama.py`ye
    # geçirilir; oradaki AZAMI_PAY=%25 tavanı ayrıca uygulanır.
    risk_payi_pct: 1.0

    # LLM YORUM KATMANI. `false` iken sinyal yalnız gider.
    # `true` iken yorum İLİŞTİRİLİR ama sinyali BASTIRAMAZ (§7).
    llm_yorumu: false

    # Sinyal saati. `ritim.kipler` içindeki kip adı.
    # 'nabiz' (22:15 Amsterdam) seçildi: ABD kapanışı sonrası.
    # 'kapanis' (17:45) SEÇİLMEDİ — o saatte ABD piyasası AÇIK
    # (settings.yaml:169-170 bunu zaten yazıyor) ve günlük bar yarım.
    kip: nabiz
```

**Doğrulama** `src/finagent/config.py` içine, mevcut `raise ValueError` kalıbıyla:
- `enabled: true` ise `endeksler` boş olamaz
- `endeksler`in her elemanı `index_members.index_name` içinde **bulunmalı**
  (yoksa sessizce boş evren oluşur — bu deponun "yanlış yok beyanı" kusur sınıfı)
- `gunluk_emir_tavani` ≥ 0 tamsayı
- `asgari_devir` sözlüğünün anahtarları `para_birimleri` ile örtüşmeli
- `kip`, `ritim.kipler` içinde tanımlı olmalı

---

## 6. Adımlar

Her adım **tek başına biter ve test edilir**; bitmeden sonrakine geçilmez.
Her adımın **kabul ölçütü** var ve ölçüt sayısaldır.

---

### Adım 1 — Evren ve veri (yeni kod yok, veri işi)

**Ne yapılacak**

1. `sources.prices.range` **sembol başına** olacak şekilde genişletilecek.
   Bugün tek genel değer (`prices.py:94`: `aralik = self.s.get("sources.prices.range", "2y")`)
   ve tüm hedeflere aynı uygulanıyor. Strateji evreni `"10y"`, geri kalan `"2y"` kalmalı.

   Dokunulacak yerler (hepsi `collectors/prices.py`):
   `collect()` (satır 88), `_ad_dogrulayarak()` (162), `_borsa_kotasyonlari()` (192),
   `_kotasyon_yaz()` (252), `_cek()` (370).

   **Öneri:** `aralik` parametresini hedef bazlı çözen tek bir yardımcı
   (`_aralik(hedef) -> str`) yaz, beş yere ayrı mantık dağıtma.

2. 518 sembol için fiyat serisi çekilecek.
   **[Ö]** yfinance ölçümü: 51 sembol 1,9 sn (`prices.py:388`) → süre engel değil.

   **[Ö] BU ÇIKARIM YANLIŞTI — ÖLÇÜLDÜ (2026-08-27).** 1,9 sn'lik rakam sığ
   (`2y`) ve **ad doğrulaması olmayan** bir çekimden geliyor. Strateji evreni
   `10y` **ve** `ad_gerek=True` istiyor; ikincisi sembol başına ayrı bir
   `get_info()` çağrısı demek. Gerçek: **sembol başına ~2 sn**, tüm `prices`
   koşusu **1.355 sn (22,6 dk)**, 1.224.051 satır. "Saniyeler mertebesi"
   değil ama engel de değil — `nabiz` kabuk bütçesi 3000 sn.

   **Bunun operasyonel sonucu var ve Ali'nin kararı:** `prices` günde ÜÇ kipte
   koşuyor (`sabah`, `kapanis`, `nabiz`). Her koşuda 518 sembolün 10 yılı
   yeniden çekiliyor. Ucuz alternatif: derinliği OLAN sembolde tazeleme
   aralığını `2y`'ye düşürmek (`_aralik` zaten tek karar noktası, değişiklik
   üç satır). **Ölçülmeden yapılmadı** ve varsayılan değiştirilmedi.

3. `conid_coz()` (`ibkr/kimlik.py:213`) yığın koşumu — 518 sembol.
   **[K]** Yığın çalışıyor (*"conid yığını başarısız"* log satırı, `kimlik.py:239`).
   **[K]** Genel hız sınırı 10 istek/sn, `GENEL_ARALIK_SN = 0.12` (`istemci.py:59`).
   `/iserver/secdef/*` `UC_ARALIKLARI` tablosunda yok → genel sınıra tabi.
   **Ön koşul:** CPGW oturumu açık olmalı (`ibkr/oturum.py`).

4. `settings.yaml → ibkr.strateji` bloğu yazılacak (§5).

**Kabul ölçütü**

| Ölçüt | Hedef | **Ölçülen (2026-08-27)** | Durum |
|---|---|---|---|
| 518 sembolün fiyat serisi | ≥%90 | **511 / 518 = %98,6** | ✅ |
| Serisi olanların ≥1.500 barı | ≥%80 | **486 / 511 = %95,1** | ✅ |
| conid çözülme oranı | ölçülecek | **483 / 518 = %93,2** | ✅ |
| seri **ve** conid birlikte (işlem yapılabilir evren) | — | **482 / 518 = %93,1** | ✅ |
| `docs/ibkr-evren.md` üretildi | var | **511 satır + 7 serisiz, sebebiyle** | ✅ |

**Sessiz kırpma yasak:** çözülemeyen semboller tabloda **sebebiyle** listelenecek
(`prices.py:149-158` aynı disiplini uyguluyor: *"kırpmak makul, kırpıldığını GİZLEMEK
bu projenin tekrar eden kusur sınıfı"*).

**[Ö] conid ÖLÇÜLDÜ (2026-08-27 21:0x): 483/518 = %93,2**, koşu süresi **11 sn**.
`collectors/ibkrkimlik.py` bu adımda genişletildi: önceden yalnızca portföy ∪
izleme listesine bakıyordu (`conidsiz_hedefler()`), yani strateji evreninin 518
sembolü conid'siz kalırdı. Artık `BaseCollector.strateji_evreni()` üzerinden
onları da hedefliyor — evren sorgusu **tek yerde** (`db.endeks_uyeleri`), çünkü
aynı soruyu iki collector soruyor (`prices` ve `ibkrkimlik`) ve iki kopya
ayrışırdı.

**Çözülemeyen 29 sembol** (serisi olduğu hâlde) — hepsi **ad kapısı**, ve kapı
doğru çalışıyor: ARE, BF.B, BR, BRK.B, CCEP, CDNS, COO, DHI, EIX, EL, EQR, EXPD,
FIS, FRT, GE, GEHC, GWW, HII, IFF, JBHT, MAA, PCG, SJM, SPCX, TRV, TTWO, USB,
WMB, WRB. Yahoo tarafındaki tuzağın aynısı: katalogda kısa/marka adı, IBKR'de
hukuki ad. Örnek — `SPCX`: katalog *'Space Exploration Technologies Corp.'*,
IBKR *'SPACE EXPLORATION TECHN-CL A'* **ve** *'SPACE EXPLORATION TECH-CDR'*;
ikisi arasında seçim yapılamadığı için **boş bırakıldı**. Bu istenen davranış:
*"Belirsizi boş bırakmak, yanlış bağlamaktan iyidir: eksik conid emir
göndermeyi ENGELLER, yanlış conid YANLIŞ HİSSEYİ aldırır"* (`kimlik.py`).

> **Bu ölçüm bir kusur daha buldu — DÜZELTİLDİ.**
> Oturum 12:44'te normal şekilde bitti (`ssoExpires` sıfıra indi ve bu kez
> yenilenmedi). Sonrasında `ibkr/oturum.py:_tik()` kimlik yokken **her tik'te**
> `kur()` → `ssodh/init` denedi: geri çekilme yok, üst sınır yok. Bot 7,5 saat
> boyunca dakikada bir denedi; gateway logunda o gün **4.528 istek**, 1.256'sı
> `auth/status`, ve gateway `retry 7 ... giving up` durumuna düştü. Bu sürede
> TAZE girişler de reddedildi (`sso/validate?gw=1` → 401 Access Denied) —
> yani başarısız kurtarma denemesi, kurtarmayı **imkânsız kıldı**.
> `istemci.py` bu riski zaten yazıyor: *"Violator IP addresses may be put in a
> PENALTY BOX FOR 10 MINUTES. Repeat violator IP addresses may be PERMANENTLY
> BLOCKED."* Hız sınırı tek kapıda toplanmıştı ama **başarısız init döngüsü o
> kapıdan geçmiyordu**: her istek tek başına sınırın altındaydı, sorun istek
> hızı değil **ısrardı**.
>
> **Düzeltme** (`oturum.py`, `INIT_TABAN_SN` 60 sn → `INIT_AZAMI_SN` 30 dk
> tavan): aynı 6 saatte ~360 deneme yerine **14**. Sert duruş seçilmedi —
> kimlik geçerliyken brokerage oturumu geçici bir sebeple düştüyse ileriki bir
> deneme tutar; hard stop kendini iyileştiren yolu kapatırdı. Kimlik geri
> gelince sayaç sıfırlanıyor (yoksa bir sonraki düşüşte 30 dk boşuna beklerdi),
> ve rakip oturum geri çekilmeyi tetiklemiyor — `kur()` artık üç değerli:
> `None` = **hiç denenmedi**, `False` = denendi olmadı. Telefondan çıkınca bot
> hemen toparlıyor.
>
> **Beş mutasyonun beşi de yakalandı — ama biri ilk turda KAÇTI.** Test
> "kimlik gelince sıfırlanıyor mu"yu `_init_sifirla()`'yı elle çağırarak
> sınıyordu; `_tik()` içindeki sıfırlama tamamen silinince test yeşil kalıyordu.
> Test doğru YOLU geçecek şekilde yeniden yazıldı. `[[fixi-nasil-kanitlarim]]`
> bir kez daha: yeşil test tek başına kanıt değil.

**[Ö] Ölçüldü ve yazıldı:**

| Ölçüm | Önce | Sonra |
|---|---|---|
| `prices` satır | 988.638 | **2.208.605** |
| Veritabanı | 169,4 MB | **324,1 MB** |
| Yedek (VACUUM 3,15 sn / duvar 6,64 sn) | ~120 MB / 1,07 sn | **301,3 MB** |
| Tarayıcı evreni | 603 enstrüman | **1.090** |
| `screener.tara()` | 21,1 sn | **30,6 sn** |

**Tarayıcı bütçesi aşılmadı, yedek plan gerekmedi.** `screener.evren()` gerçekten
kendiliğinden büyüdü (603 → 1.090) ama maliyet **+9,5 sn**; `nabiz` kabuk bütçesi
3000 sn ve toplama 1374 sn. Kırılım taramasını ayrı koşuya almaya gerek yok.

**Bu adım iki kusur daha buldu — ikisi de düzeltildi ve mutasyonla kanıtlandı:**

1. **Geçici çağrı hatası "sembol yok" diye raporlanıyordu.** Koşumda GPC, GPN ve
   GRMN "(sembol yok)" olarak listelendi; tek tek denendiğinde **üçü de 2513 bar**
   yazdı. Sebep: `_ad_dogrulayarak` istisnayı `log.debug`'a yazıyordu ve üretim
   INFO ile koşuyor. Kullanıcıya giden cümle kalıcı bir yokluk iddiasıydı —
   *veri varken yok demek*. Artık üç sebep ayrı: `cagri hatasi:<tür>` (geçici),
   `Yahoo'da seri yok` (kalıcı), `ad eslesmedi (...)` (kapı çalışıyor demek).
2. **Ad kapısı, Yahoo'nun 30 karakterde kestiği `shortName` yüzünden yanlış yere
   kapanıyordu.** Ölçüldü, serisi çekilemeyen 14 üyede: `shortName` ile eşleşen
   **0/14**, `longName` ile eşleşen **7/14**
   (`'International Flavors & Fragran'` vs `'...Fragrances Inc.'`).
   Kural **gevşemedi** — aynı `ayni_sirket`, aynı altküme şartı, aynı yanıt;
   yalnızca aynı çağrının daha eksiksiz alanı da soruluyor. Kapsam 504 → **511**.

**Kalan 7 sembol ve kapının doğru davranışı:** BEN, BNY, DECK, IBM, SLB, SMCI, WAB.
Hepsinde katalog adı (Wikipedia) kısa/marka adı, Yahoo'nunki hukuki ad
(`'IBM'` vs `'International Business Machines Corporation'`, `'Schlumberger'` vs
`'SLB N.V.'`). Altküme şartı bunları **haklı olarak** reddediyor: `{IBM}` tek
başına ayırt edici değil. Doğru çözüm ad kuralını gevşetmek **değil**, kimliği
SEC'te doğrulamak — o zaman `_yahoo_sembolu` `sec_ticker`'ı döndürür ve ad kapısına
hiç uğranmaz. Bu `edgar`/`identity` katmanının işi, `prices`in değil.

---

### Adım 2 — `pulse/strateji.py` (saf çekirdek)

**Ne yapılacak**

```python
"""
STRATEJI — Donchian 20/10 + 2N kuralinin GUNLUK KARARI. LLM YOK.

Bu modul HESAP YAPMAZ, KARAR VERIR: seviyeleri `pulse.seviye`den alir,
kurali uygular, karari dondurur. Gosterge hesabi tek motordadir
(`analysis.indicators`) ve ikinci bir hesap yolu acilmayacak.
"""
```

Fonksiyonlar:

```python
def kirilim_mi(sv: dict) -> bool:
    """son_kapanis > donchian_giris. Eksik alanda False."""

def karar(sv: dict, ayar: dict) -> dict | None:
    """
    Kirilim varsa `journal.kaydet` sozlesmesine uygun gorus dondurur,
    yoksa None.

    SAF: db yok, saat yok, rastgele yok. Tum girdi parametrede.
    """

def tara(db, settings, evren: list[dict]) -> list[dict]:
    """Evrendeki her enstrumana `seviyeler()` + `karar()`. Kabuk."""

def secim(adaylar: list[dict], tavan: int, tohum: int) -> list[dict]:
    """
    Tavani asan gunlerde TOHUMLU RASTGELE alt-orneklem.
    SAF: ayni girdi + ayni tohum -> ayni cikti.
    """
```

**Kural (tam tanım):**

- **Giriş koşulu:** `son_kapanis > donchian_giris`
  (`donchian_giris` = önceki 20 kapanışın en yükseği, bugünün barını **dışlar** —
  `seviye.py:95-97`)
- **Stop:** `stop_2n` = `son_kapanis - 2 × ATR20` (`seviye.py:102-103`)
- **Çıkış (bilgi olarak taşınır, koşul alanına yazılmaz):** `donchian_cikis`
  = önceki 10 kapanışın en düşüğü
- **Yön:** yalnızca `yukari`. Açığa satış **yok**
  (`trend_takip.py:23-25` gerekçesi: kitabın kazancının yarısını oluşturan düşen
  trend tarafı bu sistemde yok ve bu açıkça söylenmeli)
- **`ufuk_gun`:** ölçülen ortalama tutma süresinden, **kaynağı gerekçeye yazılarak**.
  **[Ö]** Bugünkü ölçüm 15-16 bar → `15`.
  **Dürüstlük notu (zorunlu, gerekçeye yazılacak):** kuralın çıkışı ufka bağlı
  değildir (10 gün dip / 2N stop); `ufuk_gun` yalnızca defterin puanlama penceresidir.
  Bu ayrım yazılmazsa `[[tahmin-defteri-ve-getiri-gercekligi]]`'nde geçen
  *"koşullu talimat koşulsuz puanlanamaz"* hatası tekrarlanır.
- **Reddetme koşulları (hepsi sayılacak, sessizce atlanmayacak):**
  - `seviyeler()` None döndü (yetersiz bar)
  - `stop_2n` yok veya `>= son_kapanis`
  - devir medyanı `asgari_devir` altında
  - `sermaye_islemi` bayrağı var (seviyeler kısaltılmış segmentten geliyor)

**Testler** (`tests/test_smoke.py` içine, mevcut kalıba uygun):

| # | Test | Beklenen |
|---|---|---|
| 1 | 300 barlık sentetik seri, son bar önceki 20'nin üstünde | `kirilim_mi` True, `karar` görüş döndürür |
| 2 | Son bar önceki 20'nin **altında** | `karar` None |
| 3 | Son bar önceki 20 yükseğe **eşit** | `karar` None (sıkı `>`) |
| 4 | 30 barlık seri | `seviyeler` None → `karar` None, sebep sayılır |
| 5 | `karar()` iki kez aynı girdiyle | özdeş çıktı (saflık) |
| 6 | `secim(10 aday, tavan=2, tohum=X)` iki kez | özdeş seçim |
| 7 | Aynı adaylar, **farklı** tohum | seçim farklı (tohum gerçekten kullanılıyor) |
| 8 | `secim(1 aday, tavan=2, ...)` | 1 aday döner, hata yok |
| 9 | Üretilen `gecersizlesme_kosulu` | `tez.kosul_ayristir()` **kabul eder** |
| 10 | `strateji.py` import listesi | `notify`, `ibkr`, `llm`, `bot` **yok** |

**Mutasyon testi (zorunlu — `[[fixi-nasil-kanitlarim]]`):** yeşil test tek başına
kanıt değil. Aşağıdaki altı kasıtlı bozmanın **altısı da** yakalanmalı:

1. `>` yerine `>=` → test 3 kırmalı
2. `donchian_giris` yerine bugünün barını içeren pencere → test 1 kırmalı
3. `STOP_N` 2.0 → 1.0 → stop değeri testi kırmalı
4. `secim` tohumsuz (`random.choice`) → test 6 kırmalı
5. `gecersizlesme_kosulu`'na `"close < donchian_cikis"` yaz → test 9 kırmalı
6. Reddedilen sembolü sessizce atla (sayacı artırma) → sayaç testi kırmalı

**Kabul ölçütü:** 10 test yeşil + 6 mutasyonun 6'sı yakalandı.

---

**[Ö] ADIM 2 UYGULANDI (2026-08-27).** `src/finagent/pulse/strateji.py`,
14 test yeşil (belgenin istediği 10 + 4 ek), **6 mutasyonun 6'sı yakalandı**
(`scripts/mutasyon_strateji.py` ile tekrarlanabilir).

**Belgeden iki bilinçli sapma — ikisi de belgenin kendi ölçütünden doğdu:**

1. **`ufuk_gun` 15 değil `14`, ve ayardan geliyor.** Belge "[Ö] Bugünkü ölçüm
   15-16 bar → 15" diyordu; o rakam 38 sembollük BUX alt evreninden. Adım 1'de
   485 sembollük gerçek ABD evreninde **14 bar** ölçüldü ve belgenin kendi
   kuralı ("ölçülen ortalama tutma süresinden") 14'ü gösteriyor. Kod içinde
   sabit bırakılmadı (§3.2 kural 4): `ibkr.strateji.ufuk_gun`, ölçüm kaynağı
   yorumda.

2. **`tara()` liste değil SÖZLÜK dönüyor:** `{"gorusler", "sayaclar", "taranan"}`.
   Sebep Adım 3'ün kendi kabul ölçütü: mesajda *"Taranamayan: 8 sembol
   (yetersiz bar: 5, seri yok: 3)"* satırı var. Düz bir liste o sayıları
   taşıyamaz; sayıları çağıran tarafta yeniden türetmek kuralı **ikinci kez
   yazmak** olurdu. Sessizce düşen sayaç bu deponun tekrar eden kusur sınıfı.
   Red sebepleri ayrı bir saf fonksiyonda (`red_sebebi`) — `karar` onu kapı,
   `tara` sayaç olarak kullanıyor; tek tanım, iki kullanım.

**Mutasyon turu prosedürün kendisinde bir kusur buldu — ve o kusur testi
yalancı yapıyordu:**

- **3 numaralı mutasyon (`STOP_N` 2.0 → 1.0) ilk turda KAÇTI.** Test
  `g["stop"] == sv["stop_2n"]` diyordu; `STOP_N` değişince **ikisi de birlikte**
  değişiyor ve karşılaştırma yine tutuyor. Kendine referans veren bir iddia,
  iddia değildir. Çarpan artık bağımsız sabitleniyor:
  `(giris − stop) == 2,0 × N`.
- **Bayat `.pyc` sonraki koşumları yalancı yaptı.** Mutasyon aynı boyutta ve
  aynı saniye içinde geri alınınca Python önbelleği `(mtime, boyut)` çiftine
  bakıp `.pyc`'yi geçerli saydı: kaynak `STOP_N = 2.0` derken **çalışan modül
  1.0 kaldı** ve düzeltilmiş kod bozukmuş gibi göründü. Kanıt yöntemi kanıtın
  kendisini bozuyordu. Betik artık restore sonrası `__pycache__`'i siliyor.
  `[[fixi-nasil-kanitlarim]]`'e üçüncü katman: yeşil test kanıt değil,
  **mutasyon turu da tek başına kanıt değil — prosedürün kendisi doğrulanmalı.**

---

### Adım 3 — Günlük kırılım tablosu (teslimat)

**Ne yapılacak**

`pulse/runner.py` içindeki `Nabiz.calistir()` akışına, `ibkr.strateji.kip` ile
eşleşen kipte çalışan bir bölüm. Mesaj biçimi `runner.py`'nin mevcut yardımcılarını
kullanır (`_tr`, `_fiyat_tr`, `_yuzde_tr`, `_esc`) — **ikinci bir sayı biçimlendirme
yolu açılmayacak** (`boyutlama.py:104-106` bu tuzağı zaten not ediyor).

Mesaj:

```
📊 STRATEJI — 28 Agustos kirilimlari
518 sembol tarandi · 12 kirilim · 2 secildi (tohum 20260828)

  SEMBOL   KAPANIS    20G YUK    STOP(2N)   10G DIP    DEVIR
  BIIB      221,07     221,56      208,92    214,30     206M
  ...

  ▸ Secilenler:
    BIIB · giris 221,07 · stop 208,92
    Girisle stop arasi %5,49 — %1 risk icin portfoyun %18,2'si
    /emir BIIB AL <adet> LMT 221.07

  Taranamayan: 8 sembol (yetersiz bar: 5, seri yok: 3)
```

**Kurallar:**
- **Kırpma varsa söylenecek.** Tablo uzunsa kırp, ama kaç satır kırpıldığını yaz.
  (`prices.py:149-158`, canlı vakada kullanıcı 8 satır gördü, gerçekte 12 vardı.)
- **Taranamayanlar sebebiyle** yazılacak.
- Sıfır kırılım varsa mesaj yine gider: *"518 sembol tarandi · 0 kirilim"*.
  Sessizlik ile "bakılmadı" ayırt edilemez olurdu.
- **Emir butonu YOK.** `/emir` komut satırı metin olarak verilir; kullanıcı kopyalar.
  Buton Adım 6'da.

**Kabul ölçütü:** İlk gerçek koşuda mesaj gitti; tablodaki `20G YUK` ve `STOP(2N)`
değerleri, aynı sembol için `seviye.seviyeler()` çıktısıyla **birebir** aynı.

---

**[Ö] ADIM 3 UYGULANDI (2026-08-27).** `runner.strateji_mesaji()` (saf) +
`Nabiz._strateji_taramasi()` (ortak fazda, kişiden bağımsız). 9 test yeşil,
**7 mutasyonun 7'si yakalandı** (`scripts/mutasyon_strateji3.py`).
Gerçek veriyle üretilen mesaj: 518 sembol, 48 kırılım, 2 seçildi, 2.076 karakter
(Telegram sınırı 4.096).

**Belgedeki `/emir` örneği YANLIŞTI ve kopyalanmadı.** Belge
`/emir BIIB AL <adet> LMT 221.07` diyor; `emirakis.komut_coz` dördüncü parçayı
FİYAT sanıp `float("LMT")` deneyip *"Fiyat 'LMT' sayi degil"* derdi. Gerçek
söz dizimi `SEMBOL AL|SAT ADET [FIYAT]` — fiyat verilince tür zaten LMT oluyor.
Yanlış bir komut satırı `[[yanlis-ipucu]]` dersinin ta kendisi: kullanıcıyı
doğru araca değil **yanlış kapıya** yollar. Test üretilen komutu **gerçek
ayrıştırıcıya** veriyor, dizgi karşılaştırmıyor.

**conid'i olmayan sembolde komut verilmiyor** — `emirakis._conid` onu zaten
reddediyor; çalışmayacak bir komutu vermek kullanıcıyı hataya yollamak olurdu.
Onun yerine sebep ve çözüm yazılıyor.

**Tablo sütunu için ayrı fiyat biçimi (`_tablo_fiyat`), ikinci sayı yolu değil.**
`_fiyat_tr` bilerek değişken hane kullanıyor ("ROSE 0,0055 ile ASML 1.512 aynı
kalıbı paylaşamaz") ve düzyazıda doğru olan o. Tabloda ise hizalama **bilgi
taşıyor**: `189,63` ile `174,117` alt alta gelince göz basamakları
karşılaştıramaz. Aynı `_tr`, yalnızca hane sayısı açıkça veriliyor; 1'in
altındaki değerde `_fiyat_tr`'ye düşülüyor.

**Mutasyon turu yine kendi testimdeki bir sahte-yeşili buldu.** Kabul ölçütü
testi ("tablo değerleri `seviyeler()` ile birebir") değeri **satırın herhangi
bir yerinde** arıyordu. `20G YUK` sütununa kapanışı yazan bozma testi
**geçiyordu**: düz sentetik seride `10G DIP` de aynı sayı ve aranan dizgi başka
bir sütunda bulunuyordu. Bir değerin satırda **bulunması**, doğru **sütunda**
olması demek değil. Test artık sütun sütun karşılaştırıyor ve başlık
hizalamasını da doğruluyor.

**Teslimat panelden ÖNCE.** Tarama deterministik ve ucuz (4,1 sn); panel LLM'e
bağlı, pahalı ve bütçe dolunca **atlanıyor**. Sonra gönderilseydi, panelin
atlandığı bir koşuda kırılım tablosu da kaybolurdu — oysa o tablonun modelle
hiçbir ilgisi yok. Test bu sırayı bağlıyor.

**[Ö] İLK GERÇEK KOŞU (2026-08-27 22:59, nabız).** Mesaj gitti.
518 sembol · **36 kırılım** · 2 seçildi (NWS, PAYX). Kabul ölçütü **canlı çıktı
üzerinde** doğrulandı: tablodaki 25 sembolün 25'inde dört sütun da
`seviye.seviyeler()` ile birebir — 0 uyuşmazlık.

Kırılım sayısı akşam ölçtüğüm 48'den 36'ya düştü çünkü koşu önce `prices`
collector'ını çalıştırıp serileri tazeledi. Aynı kural, güncellenmiş veri.

> **İLK KOŞU BİR KUSUR ORTAYA ÇIKARDI — DÜZELTİLDİ.**
> Tablo, kipin **tüm alıcılarına** gitti (`ali` **ve** `yuksel`). İçinde
> `/emir PAYX AL <adet> 126.48` gibi kopyalanabilir komutlar var ve `/emir`
> **tek IBKR hesabını** kullanıyor (`emirakis._hesap`): kim yazarsa yazsın emir
> **Ali'nin hesabına** gider. Yani başka birine, başkasının hesabında işlem
> yapan bir komut satırı gönderilmiş oldu.
>
> Belge §7 zaten *"Ali'ye TEK mesaj"* diyor — çoğul değil. Ben kipin alıcı
> listesine genelledim ve yanlış yaptım. Artık mesaj yalnızca `ibkr.sahip`e
> gidiyor; defter satırları da (Adım 4) aynı kişi adına yazılıyor, yani
> **karne, emir ve mesaj aynı kişiyi gösteriyor.** Mutasyonla kanıtlandı.
>
> Not: `/emir`in her yazana tek hesabı kullanması **önceden de böyleydi**; bu
> değişiklik onu düzeltmiyor, yalnızca komutu ilan etmeyi bırakıyor. Yetki
> ayrımı gerekiyorsa ayrı bir iş.

---

### Adım 4 — Deftere yazım ve puanlama

**Ne yapılacak**

1. Her kırılım — **seçilsin veya seçilmesin** — `Defter.kaydet()` ile
   `ajan='strateji'` olarak yazılır.
   **Bu, tasarımın merkezi:** kural TAM GENİŞLİKTE ölçülür (~250 sinyal/ay),
   hesap ise onay bant genişliği kadarını (~40/ay) işler. İkincisi birincinin
   yansız örneği olduğu için ikisi karşılaştırılabilir.

2. Seçilen sinyaller ayrıca `ajan='strateji_secilen'` olarak **ikinci bir satırla**
   yazılır. `UNIQUE` kısıtına `ajan` dahil olduğu için çakışma olmaz.
   Böylece iki karne ayrı ayrı okunur.

3. `Defter.puanla()` zaten `taktik_tetiklendi` mantığını uyguluyor
   (`journal.py:245`, `journal.py:499`). `strateji` satırları `taktik_giris` dolu
   geldiği için aynı yoldan geçer — **yeni puanlama kodu yazılmayacak**.

**Kabul ölçütü**
- İlk koşum sonrası `SELECT ajan, COUNT(*) FROM predictions WHERE ajan LIKE 'strateji%' GROUP BY 1`
  iki satır döner ve `strateji` ≥ `strateji_secilen`.
- `kaydet()` raporunda `atilan_sembol_yok`, `atilan_seri_yok`, `atilan_cakisma`,
  `kosul_reddi` **hepsi sıfır**. Sıfır değilse sebep bulunup düzeltilecek —
  kabul edilip geçilmeyecek.

---

**[Ö] ADIM 4 UYGULANDI (2026-08-27).** `Nabiz._strateji_deftere_yaz()`.
7 test yeşil, **6 mutasyonun 6'sı yakalandı** (`scripts/mutasyon_strateji4.py`).
Sentetik koşum: `strateji` 3 satır, `strateji_secilen` 2 satır, **dört sayaç da
sıfır**, seçilenler tam listenin alt kümesi.

**`sahip` = `ibkr.sahip`, varsayılan YOK.** Belge bu alanı yazmıyordu ama
`kaydet()` onu zorunlu tutuyor. Bu satırların karnesi *emrin gideceği hesabın*
karnesi; başka birinin defterine yazmak `insert_positions`ın uyardığı tehlikenin
ta kendisi. Tanımsızsa **yazılmıyor ve söyleniyor** — sessizce "ali"ye düşmüyor.

**Tarama ile yazım ayrıldı.** `_strateji_taramasi` saf okuma, `_strateji_deftere_yaz`
yan etkili. Ayrılmasaydı her elle koşu ve her test canlı deftere satır atardı ve
karne, hiç gönderilmemiş sinyallerle kirlenirdi.

**Mutasyon turu bir yazım-yolu kopukluğu yakaladı.** Testlerin hepsi
`_strateji_deftere_yaz`'ı **doğrudan** çağırıyordu; `_ortak_faz` içindeki bağı
hiçbiri sınamıyordu. Çağrıyı devre dışı bırakan bozma **bütün testleri yeşil
bıraktı** — yani yazım yolu sessizce koparılabilir ve hiçbir şey söylemezdi.
Bu deponun bilinen kusur sınıfı (`[[iptal-defterine-gitmiyordu]]`: *"kaynak
zaten var, yazım yolu yok"*). Yeni test kaynak okumuyor, `_ortak_faz`'ı
**gerçekten koşturup** veritabanına bakıyor.

**Bir test daha kendi yanlış pozitifini üretti:** "runner kendi puanlamasını
yazmıyor" testi düz metin arıyordu ve **kendi docstring'ime** takıldı — açıklama
*"`puanla()` zaten `_tetiklendi` yolundan geçiriyor"* diyor, yani doğru şeyi
anlatan bir cümle testi kırmızı yapıyordu. AST'ye çevrildi, docstring eleniyor.
`test_MODEL_EMIR_GONDEREMEZ` dersinin aynısı: doğru soru "kod ne yapıyor",
"açıklama ne diyor" değil.

---

### Adım 5 — Emir ve dolum ölçümü

**Ne yapılacak**

1. **Şema göçü 24.** `SEMA_SURUMU` şu an **23** (`db.py:461`). `emirler` tablosuna:

   ```python
   "emirler": [("mesaj_id", "TEXT"),
               ("dolum_fiyat", "REAL"),        # YENİ
               ("dolum_komisyon", "REAL"),     # YENİ
               ("dolum_ts", "TEXT")]           # YENİ
   ```

   Mevcut kolon-ekleme kalıbı `db.py:300-340`. `ALTER TABLE ADD COLUMN` yeterli,
   tablo yeniden kurulmayacak.
   **Uyarı — `[[goc-kaliplari-ve-tuzaklari]]`:** DDL geri sarılamaz; göç testi
   gerçek eski şemayı kurarak yazılacak (`docs/finagent-goc-inceleme.md` bu kalıbı
   onaylıyor).

2. `ibkr/mutabakat.py:367 dolum_kaydi()` çıktısı bu kolonlara bağlanacak.
   **Kaynak zaten var, yazım yolu yok** — eksik olan tek şey bağlantı.

3. Emir akışı **değişmiyor**: `/emir` komutu → `onkontrol` → [ONAYLA] → `onkontrol`
   yeniden → `gonder()`. `bot/emirakis.py`'nin onay yapısına **dokunulmayacak**.

4. Her emir öncesi `emir.onizle()` (`/whatif`) çağrılacak ve dönen komisyon
   `emirler.not_` alanına yazılacak. Tahmin edilmeyecek, **sorulacak**.

**Kabul ölçütü**
- İlk gerçek emirden sonra `dolum_fiyat`, `dolum_komisyon`, `dolum_ts` dolu.
- `dolum_fiyat` ile emrin `fiyat`ı arasındaki fark bir tabloya yazıldı (dolum sapması).
- Göç testi yeşil; eski şemadan yeni şemaya geçişte kayıt sayısı korunuyor.

---

**[Ö] ADIM 5 UYGULANDI (2026-08-27).** Şema 24, `mutabakat._dolum_alanlari()`,
`db.dolum_sapmalari()`, `emirakis.dolum_sapmasi_metni()`. 5 test yeşil,
**7 mutasyonun 7'si yakalandı** (`scripts/mutasyon_strateji5.py`).

**Göç canlıda koştu ve temiz:** 23:10'da `gunici` zamanlanmış koşusu yeni kodu
aldı, `PRAGMA user_version` 23 → 24, `emirler`in **3 satırının 3'ü korundu**,
içerik bozulmadı, yeni kolonlar NULL geldi (uydurulmadı). Göç testi ayrıca
**gerçek şema 23 tablosunu kurup** göç ettiriyor — "yeni şemayı kurup üzerine
yazmak" olsaydı göcün kendisi hiç sınanmamış olurdu.

**Asıl kusur neydi:** `dolum_kaydi()` IBKR'nin beyanını (fiyat, komisyon,
zaman) **zaten çıkarıyordu** ama sonuç yalnızca `not_` içine düz metin olarak
yazılıyordu (`"dolum 90.99 kom 0.045"`). Yani "istenen fiyatla gerçekleşen
arasındaki fark ne" sorusu SQL ile cevaplanamıyordu. Kaynak var, yazım yolu
yok — Adım 4'te yakaladığım sınıfın aynısı.

> **BEDELİ ÖLÇÜLDÜ VE GERİ ALINAMADI.** Defterdeki gerçekleşmiş KO emri
> (`2141314594`, 26 Ağustos) `durum='gerceklesti'` ama `dolum_fiyat` **boş**;
> notu yalnızca *"mutabakat: dolum tespit edildi"* diyor. Veri o gün vardı —
> `emir.py:303` ve `mutabakat.py:373` onu *yorum satırı olarak* kaydetmiş
> (tahmin 91,00 → gerçek 90,99, komisyon 0,045). 2026-08-27 23:2x'te IBKR'ye
> soruldu: `/iserver/account/trades` **0 kayıt** döndürüyor, yani o dolum artık
> pencerede yok ve **geriye dönük doldurulamıyor**.
>
> Yazım yolu bir gün eksik kaldı ve ölçüm kalıcı olarak kayboldu. Kabul
> ölçütünün ilk maddesi bu yüzden **ancak yeni bir emirle** kapanabilir.

**Açık kalan:** *"İlk gerçek emirden sonra `dolum_fiyat`/`dolum_komisyon`/
`dolum_ts` dolu"* — canlı emir bekliyor. Kod, göç ve sapma tablosu hazır;
emir Ali'nin kararı.

---

### Adım 6 — Karne ve fren

**Ne yapılacak**

1. `Defter.ajan_karnesi()` (`journal.py:797`) çıktısına `strateji` ve
   `strateji_secilen` eklenecek — muhtemelen kod değişikliği gerekmez, ajan adı
   parametrik.

2. **Kontrol grubu.** Aynı pencerede `trend_takip.rastgele_kontrol()` koşulacak ve
   karnenin yanına yazılacak. **Bu satır olmadan karne yayınlanmayacak** —
   `42ab2fd`'nin dersi: kenarın yarısı piyasa sürüklemesiydi.

3. **Fren.** `taktikci`'nin mevcut freniyle aynı kalıp: karne eşiğin altındaysa
   `gunluk_emir_tavani` otomatik 1'e iner. Ölçüm yoksa **"ÖLÇÜLMEMİŞ" diye beyan
   edilir** — iyimser varsayılmaz.

4. **Emir butonu** (Adım 3'te ertelenmişti) burada açılır: tablodan tek dokunuşla
   `/emir` hazırlığı. Onay yapısı yine değişmez.

**Kabul ölçütü**
- Karne mesajında dört sayı var: `strateji` isabeti, `strateji_secilen` isabeti,
  rastgele kontrol, ve aradaki fark.
- Fren testi: karne yapay olarak eşiğin altına indirildiğinde tavan 1'e iniyor.

---

**[Ö] ADIM 6 — KARNE VE FREN UYGULANDI (2026-08-27).** `strateji.karne()`,
`strateji.tavan()`, `runner._karne_satirlari()`. 7 test yeşil, **7 mutasyonun
7'si yakalandı** (`scripts/mutasyon_strateji6.py`).

**Belgenin gözden kaçırdığı bir uyumsuzluk vardı ve düzeltildi.** Defterdeki
`isabet` **piyasaya göre düzeltilmiş** (`anormal_pct > 0`, yani beta × piyasa
getirisi çıkarılmış — `journal.puanla`); `trend_takip.rastgele_kontrol` ise
**ham getiri** ölçüyor. İkisinin farkını almak elmayla armut karşılaştırmaktı ve
kullanıcıya *"kural rastgeleyi şu kadar geçiyor"* diye okunacaktı.

Çözüm: karne **iki tabanı da** raporluyor (`isabet_%` düzeltilmiş,
`ham_isabet_%` ham) ve **fark yalnızca ham tabandan** alınıyor.
`rastgele_kontrol`'e `isabet_%` eklendi — tekil işlemler üzerinden, tur
ortalamasından **türetilmeden**: bir turun ortalaması pozitif olup içeriğinin
çoğu negatif olabilir (tek büyük kazanç taşır) ve trend takibinde tam beklenen
şey bu.

**Fren ölçütü isabet değil, RASTGELEYE GÖRE FARK.** Ham isabet eşiği bu katmanda
yanlış olurdu: kural zaten düşük isabetle çalışıyor (%32 ölçüldü) ve doğru soru
*"isabet yüksek mi"* değil *"rastgele girmekten iyi mi"*. `42ab2fd`'nin dersi:
BIST'te ölçülen kenarın yarısı piyasa sürüklemesiydi. Eşik `0.0` — rastgeleyi
geçmiyorsa fren; pozitif bir eşik, ~24 ay gerektiren bir iddia olurdu.

**Kontrolsüz karne yayınlanmıyor ve kontrolsüzken fren de çekilmiyor** — ikisi
de uydurma olurdu. Ölçüm eşiğin altındaysa "OLCULMEMIS" diye beyan ediliyor,
iyimser varsayılmıyor.

**Mutasyon turu iki zayıf testimi buldu:**
1. Fren `tavan()` içinde çalışıyordu ama **tavana bağlı mıydı** — testler
   `tavan()`'ı ayrı ayrı sınıyordu. Adım 4'teki kopukluğun aynısı. Yeni test
   `_strateji_taramasi`'yı koşturup seçilen sayısına bakıyor.
2. "Rastgele kontrol isabeti" testi `0 ≤ isabet ≤ 100` diyordu; sayacı hiç
   artırmayan bir bozma `0.0` üretiyor ve o da aralığa giriyordu. **Bir aralığı
   doğrulamak, sayıyı doğrulamak değildir.** Artık sürekli yükselen seride
   %100, sürekli düşende %0 bekleniyor.

**Canlı durum:** karne şu an *"OLCULMEMIS (0/20 ölçüm)"* diyor — doğru; bu
geceki 36 tahmin 14 gün sonra puanlanacak.

**[Ö] EMİR BUTONU AÇILDI (madde 4).** Ali'nin kararı: adet `boyutlama.boyut()`
ile hesaplansın. Üç girdi de gerekli — hesabın net likidite değeri (IBKR),
pozisyon payı (2N stop mesafesinden; **ikinci bir formül yazılmadı**) ve kur.
Biri eksikse adet **yazılmıyor**, `<adet>` yer tutucusu ve **sebep** kalıyor.

Canlı ölçüm: hesap **105,88 EUR**; PAYX için %1 risk → portföyün %18,8'i →
**23,20 USD → 0,1834 hisse** (IBKR kesirli ABD hissesi destekliyor; ölçülmüş
vaka 0,05 lot KO). §2.3'ün uyarısı burada somut: *"Küçük boyut tesisatı test
eder, ekonomiyi değil."*

**Buton ikinci bir kapı açmıyor:** doğrudan `_emir_komutu` çağrılıyor, yani
`/emir` yazmakla birebir aynı yol — hazırla → önkontrol → [ONAYLA] → önkontrol
**yeniden** → gönder. Mutasyonla kanıtlandı: onay yolunu atlayan bozma testi
kırıyor.

**Kur yoksa adet yazılmıyor.** Kuru 1 varsaymak, EUR hesapta USD emri için
~%15 yanlış boyut demekti — `[[para-birimi-ve-sembol-tuzagi]]`'nın ta kendisi
(17 pozisyonun 14'ünde ~%15,7 sapma, sebebi para biriminin varsayılmasıydı).

**Mutasyon turu iki ölü koruma daha buldu:** `callback_data`'nın 64 baytlık
Telegram sınırı gerçek ticker'larla hiç tetiklenmiyor ve testim onu
sınamıyordu — sınırı kaldıran bozma testi **geçiyordu**. Var sanılan ölü bir
koruma, korumasızlıkla aynı şey.

---

## 7. LLM nerede duruyor

**İlk fazda LLM sinyali BASTIRAMAZ.**

```
518 sembol → kirilim taramasi (LLM YOK, deterministik)
                     ↓
             gunun kirilimlari
                     ↓
         ┌───────────┴───────────┐
         ↓                       ↓
    KURAL karari            LLM yorumu (ibkr.strateji.llm_yorumu: true ise)
    ajan='strateji'         ajan='strateji_llm'
         ↓                       ↓
         └───────────┬───────────┘
                     ↓
         Ali'ye TEK mesaj, ikisi YAN YANA
         Emir KURALIN dedigine gore kurulur
```

**Gerekçe:** LLM araya süzgeç olarak girerse ölçülen şey artık kural değil,
kural+LLM bileşimi olur ve ikisi bir daha ayrılamaz. Paralel yazıldığında ise
**eşleştirilmiş kıyas** doğar: aynı sinyal, iki karar. Dört hafta sonra
*"LLM'in 'bekle' dediği N sinyalin kaçı gerçekten kötüydü"* sorusu **sayıyla**
cevaplanır.

LLM'in veto hakkı, karnesi kuralı geçtiğinde verilir — `taktikci` freninin aynası.

---

**[Ö] §7 PARALEL KOLU UYGULANDI (2026-08-28).** `pulse/strateji_llm.py`.
6 test yeşil, **8 mutasyonun 8'i yakalandı** (`scripts/mutasyon_strateji7.py`).

**Ayrı modül, çünkü `strateji.py` LLM'i içe aktaramaz** (§3.2 kural 1, AST
testiyle bağlı). §3.3 "tek yeni dosya" diyordu; §7'nin istediği kol o kurala
sığmıyor — kuralı delmek yerine ikinci dosya açıldı ve bağımlılık yönü korundu.

**Sıra bağlandı:** `secim()` LLM çağrısından **önce**. Test bunu AST ile
doğruluyor. Önce çağrılsaydı ileride biri *"modelin beğendiklerini seç"* diye
tek satır ekleyebilirdi ve ölçülen şey artık kural olmazdı.

**Seviyeler kuraldan, modelden değil.** Model kendi `giris`/`stop`'unu yazsa
bile yok sayılıyor; iki kol aynı sayılarla ölçülmezse eşleştirilmiş kıyas
anlamsızlaşır — fark **karardan** gelmeli. Modele ham seri de verilmiyor,
yalnızca ölçülmüş seviyeler (`seviye.py`'nin 335-pencere vakası).

**Tek çağrı, sembol başına değil:** 36 kırılım = 36 çağrı olurdu. Günün
tamamı tek istemde gidiyor, çıktı sembol başına ayrı satır.

**Araç yok (`allowed_tools=[]`), ve bu şimdiden §8 için.** Bu depoda araç
yüzeyi tarihe çitlenemiyor (`haberler` en yeniyi döndürüyor). Kolu şimdiden
araçsız kurmak, sınav kolunu **ayrı kurmak zorunda kalmamak** demek — ölçülen
şey ile sınanan şey aynı kalıyor. Prompt tek kaynaktan (`sistem_metni()`), ki
§8'de dondurulan metin ile koşan metin ayrışmasın.

**Canlı bir çağrıyla doğrulandı** (bir kez, PAYX): model kurallara uydu —
uydurma sayı yok, hepsi verilen seviyelerden:
> `[al] Kapanis 20G yuksegin %0,37 uzerinde (ince kirilim), 2N stop mesafesi
> %5,31, stop 119,76 10G dipin (118,54) hafif ustunde; devir medyani 295M USD
> ile likidite bol.`

**Mutasyon turu iki testimi daha çürüttü:**
1. "LLM hatası sinyali düşürmez" testi `kullanilabilir()` **False dönecek diye
   varsayıyordu**; abonelik açık olduğu için test **gerçek bir model çağrısı
   yaptı**. Testler ne ağa çıkmalı ne de ortamın o anki hâline bağlı olmalı
   (`[[test-canli-kanala-yazdi]]`). Artık hata **enjekte** ediliyor.
2. `allowed_tools=[]` kontrolü düz metindi ve **kendi yorum satırıma** takılıp
   yeşil kalıyordu — aracı açan bozma testten geçiyordu. AST'ye çevrildi.
   Bu, aynı sınıfın bugün **üçüncü** tekrarı.

**Varsayılan hâlâ `llm_yorumu: false`.** Kol yazıldı ama açılmadı; açmak
Ali'nin kararı.

**Model hesap yapmaz.** `seviye.py:5-11` bunu ölçülmüş bir vakayla yazıyor: model bir
fiyat barı görmeden 335 pencerelik istatistik tablosu üretti ve sayılar kalibreliydi.
LLM'e verilecek şey **ölçülmüş seviyeler**, hesaplanacak ham veri değil.

**Mevcut katmanlarla ilişki** — üçü ayrı soru cevaplıyor, mesajda ayrı başlık altında
görünecek, karneleri ayrı (`ix_pred_ajan` zaten `(sahip, ajan, olusma_ts)` üzerinde):

| Ajan | Tetik | LLM | Sıklık |
|---|---|---|---|
| `taktik` | Olağandışı hareket (günlük oynaklık eşiği, `gunici_tarayici`) | Var | 30 dk |
| `strateji` | 20 günlük yüksek kırılımı | **Yok** | Günde 1 |
| Panel (`teknik`/`temel`/`olay`/`risk`/`hakem`) | Nabız koşusu | Var | Günde 1 |

---

## 8. Tek atışlık dış sınav (Haziran–Ağustos 2026)

Adım 2 bittikten sonra, **canlı riske dokunmadan** koşulacak.

**Neden bu pencere:** Projede kullanılan modeller **[K]** `settings.yaml:783-785`:
`strategist_model: "claude-opus-5-5"`, `tactical_model: "claude-fable-5-1"`. Bu modellerin
bilgi kesme tarihi Mayıs 2026'dır; Haziran–Ağustos 2026 eğitim verilerinde **yok**. **[Ö]** Ve pencere adil: QQQ −%4,2, SPX +%1,0, AEX +%6,9 — karışık rejim,
uzun-yönlü bir kural sürüklemeye binemiyor (§2.7).

**Zorunlu koşullar — biri eksikse sınav geçersizdir:**

1. **Tarih kesmesi tek yerden:** `db.fiyat_serisi(..., bitis=)`. Kodda sabit tarih yok.
2. **Her karar günü ayrı çağrı.** 3 aylık seriyi tek çağrıda vermek, modelin cevabı
   kendi girdisinde görmesi demektir — bilgi kesme tarihiyle ilgisi yoktur.
3. **Araç yüzeyi tarihe çitlenir.** Web araması kapatmak **yetmez**.
   **[K]** `bot/tools.py:1006-1009` — `haberler` aracı `ORDER BY published_at DESC`
   ile en yeni haberi döndürüyor, **tarih kesmesi yok**. Aynı durum `fundamentals`,
   `takvim`, `hatirlanan` ve sohbet arşivi için de geçerli. Bu depoda tarihe çitlenmiş
   tek kapı `fiyat_serisi(bitis=)`.
   **İlk turda en basit çözüm:** modeli **araçsız** koştur, her karar günü için
   önceden hazırlanmış tarihe çitlenmiş tek bağlam paketi ver.
4. **Bilinen sızıntı kapatılacak.** **[K]** `db.py:1364-1369` açıkça yazıyor:
   `fiyat_kaynagi()` kaynağı `MAX(ts)`'e göre seçiyor — yani `bitis`'in ötesine
   bakarak. Gerekçe BIST'e özel (*"BIST'te hep yahoo_bist"*). ABD evreninde bu
   varsayım **doğrulanmadı**; QQQ'da hem `yahoo` hem `alphavantage` kaynağı var.

   **[Ö] ÖLÇÜLDÜ (2026-08-27) — ABD evreninde sızıntı YOK, ama BIST'te VAR.**
   Yöntem: her enstrüman için üretimdeki `fiyat_kaynagi()` seçimi ile, aynı
   kuralın `ts <= bitis` ile kırpılmış hâli karşılaştırıldı.

   | Küme | Çok kaynaklı | `bitis` seçimi değiştiriyor mu |
   |---|---|---|
   | 511 ABD strateji üyesi | 3 | **0 sapma** |
   | QQQ (belgenin adıyla andığı vaka) | 2 kaynak | **0 sapma** (3 kesme tarihinde) |
   | XU100 (BIST) | 2 kaynak | **SAPIYOR** |

   Yani beklenti **ters çıktı**: varsayımın kırıldığı yer ABD değil, gerekçenin
   dayandığı BIST. XU100'de üretim `isyatirim/TRY` (292 bar) seçiyor, kırpılmış
   pencerede `yahoo_bist/TRY` (2.499 bar) kazanıyor — çünkü sıralama önce
   TAZELİĞE bakıyor ve `isyatirim` bir gün daha taze. **§8 sınavı ABD evreninde
   koşacağı için bu bir engel değil**; ama BIST backtest'i bu satırı bilmeli.

   Sapan görünen 4 ABD sembolü (AVB, EQR, HONA, SPCX) sızıntı değil **yokluk**:
   2026-05-31'de henüz serileri yok (yeni kotasyon/veri boşluğu). Kaynak seçimi
   değişmiyor, seçilecek kaynak hiç yok.
5. **Pencere bir kez kullanılır.** Sonucu görüp algoritmayı ayarlarsan pencere yanar.
   **Koşumdan önce yazılı olarak dondurulacaklar:**
   - Kural parametreleri (20/10/2N)
   - Evren tanımı ve likidite eşiği
   - Prompt'un tam metni (LLM kolu için)
   - Kabul ölçütü

**Kabul ölçütü (koşumdan ÖNCE yazıldı):**
- Kural, aynı pencerede rastgele girişi **geçiyor** (`rastgele_kontrol`, tohum sabit)
- Eşleştirilmiş kıyasta LLM kolunun kurala göre farkı, işaret testiyle raporlanıyor
- Look-ahead testi geçti: `--bitis 2026-05-31` ile hesaplanan SMA200, tam seride
  31 Mayıs satırındaki SMA200'e **eşit**

---

## 8.A DONDURULMUŞ PARAMETRELER (yazıldı: 2026-08-28, koşumdan ÖNCE)

> Bu blok §8 koşumundan **önce** yazıldı ve koşumdan sonra
> **değiştirilmeyecek**. Sonucu görüp buraya dokunmak, pencereyi yakar.
> Değişiklik gerekirse yeni bir pencere gerekir.
>
> **TAZELENDİ 2026-08-28 — sınav HÂLÂ KOŞULMADI, pencere yanmadı.**
> İlk dondurmadan sonra sinyal tanımını değiştiren **iki düzeltme** yapıldı
> (aşağıda 8.A.1). Dondurulmuş metnin koşacak koddan ayrışması, belgenin
> baştan sona kaçındığı hatanın ta kendisi olurdu — o yüzden blok yeniden
> yazıldı. Bu **meşru**, çünkü sınav sonucu henüz görülmedi; sonucu görüp
> tazelemek meşru **olmayacaktı**.

### 8.A.1 İlk dondurmadan sonra ne değişti

**a) Pozisyon kapısı** (`red_sebebi` → `"zaten pozisyonda"`). Tarama artık
kuralın zaten tuttuğu sembolde tekrar sinyal vermiyor. Ölçülen etki:

| | Kırılım | Çifte dönüşen |
|---|---|---|
| Önce (6 gün) | 318 | 49 (**%15**) |
| Sonra (8 gün) | 69 | 68 (**%99**) |

Yani düzeltmeden önce raporlanan "kırılım"ların %85'i kuralın girmeyeceği
tekrarlardı. Günde ~8,5 çift; **15 karar günü ≈ 130 çift** (belge ~45 istiyor).

**b) Kotasyon tercihi** (`fiyat_serisi(tercih_ccy=)`). Strateji artık
`para_birimleri`'ndeki kotasyonu tercih ediyor. ASML'in 2513 barlık USD serisi
varken pozisyon EUR olduğu için sembol sessizce eleniyordu.

**Ne DEĞİŞMEDİ:** kural parametreleri (20/10/2N), evren tanımı, likidite eşiği,
tarih kesmesi mekanizması ve **LLM prompt'u** (`04d64ac3e9d7` — aynı).

**Kural parametreleri** — `analysis/trend_takip.py`, tek kaynak:
`GIRIS_PENCERE=20`, `CIKIS_PENCERE=10`, `ATR_PENCERE=20`, `STOP_N=2.0`.
Kitaptan geliyor, bu belgeden önce donmuştu, sınav için **değiştirilmedi**.

**Evren:** `ibkr.strateji.endeksler` = S&P 500 + Nasdaq 100 (`index_members`'tan,
518 tekil). Kapılar: `para_birimleri: ["USD"]`, `asgari_devir.USD: 1.000.000`.

> **DÜZELTME (2026-08-29).** Burada önceden `asgari_bar: 1500` de bir evren
> kapısı olarak sayılıyordu. **Yanlıştı:** `pulse/strateji.py` bu ayarı hiç
> okumuyor. Tarama için gereken derinlik göstergelerin kendi ihtiyacı
> (Donchian 20 + ATR ≈ 40 bar); "yetersiz bar" sayacı onu ölçüyor. Ölçüldü:
> 1.500 barın altındaki 24 sembol (PLTR 1485, RKLB 1446, DASH 1436, ABNB
> 1435, COIN 1351…) taranıyor ve seçilebiliyor.
>
> **§8 sınavı geçersiz değil:** sınav da üretimle **aynı** `tara()` yolunu
> kullandı, yani ölçülen ile koşan aynı evrendi. Yanlış olan ölçüm değil,
> **beyandı**. `asgari_bar` gerçekte veri **derinliğini** yönetiyor
> (`prices._tazeleme_plani`: bu derinliğe ulaşmış seri kademeli tazelenir,
> ulaşmamış olan derin çekilir) — bir tarama kapısı değil.

**Tarih kesmesi:** yalnızca `db.fiyat_serisi(bitis=)`. `seviyeler(bitis=)`,
`strateji.tara(bitis=)` ve `_devir(bitis=)` kesmeyi **devrediyor**, kendi tarih
süzgeçlerini yazmıyor. İki test bağlıyor
(`test_strateji8_LOOK_AHEAD_*`, `test_strateji8_TARIH_KESMESI_TEK_KAPIDAN_*`).

**LLM prompt'u:** `pulse/strateji_llm.sistem_metni()` — tek kaynak, elle
kopyalanmıyor.
- **sha256 (ilk 12): `04d64ac3e9d7`** · 1.388 karakter
- Araçsız (`allowed_tools=[]`), `max_turns=1`, tek çağrı/gün
- Modele **ölçülmüş seviyeler ve ölçülmüş oranlar** gidiyor; ham seri gitmiyor
- `giris`/`stop` **kuraldan** alınıyor, modelin yazdığından değil

**Prompt neden bu hâliyle donduruldu — dört günde sınandı (deftere yazmadan):**

| Gün | Kırılım | al | bekle | Oran yankısı |
|---|---|---|---|---|
| 2026-03-16 | 24 | 13 | 11 | 24/24 birebir |
| 2026-04-15 | 97 | 50 | 47 | 97/97 birebir |
| 2026-05-15 | 28 | 18 | 10 | 28/28 birebir |
| 2026-08-27 | 36 | 25 | 11 | 8/8 birebir |

**149 alıntılanan oranın 149'u** verilen değerle birebir — model hesaplamıyor,
yankılıyor. Her gün 100% kapsama (atlanan sembol yok). "al" oranı %52–%64
arasında salınıyor, yani sabit bir onay makinesi değil. Mart–Mayıs günleri
**bilerek** seçildi: modelin bilgi kesme tarihinin (Mayıs 2026) içinde
kalıyorlar, yani sınav penceresine dokunmadan **yalnızca prompt mekaniği**
ölçüldü.

**Kabul ölçütü (koşumdan ÖNCE, §8'in kendi listesi + iki ekleme):**
1. Kural, aynı pencerede rastgele girişi **geçiyor** (`rastgele_kontrol`, tohum sabit)
2. Eşleştirilmiş kıyasta LLM kolunun kurala göre farkı **işaret testiyle** raporlanıyor
3. Look-ahead testi geçti (yukarıdaki iki test)
4. **[EK] Sonuç TEK ÖRNEK olarak raporlanacak.** LLM kolu deterministik değil —
   aynı gün iki koşumda ACN "al" ve "bekle" oldu (ölçüldü 2026-08-28). Kuralın
   `secim`'i tohumlu ve `karar`'ı saf; model kolu değil. Sınav **karar günü
   başına tek çağrı** yapacak ve sonuç *"bir dağılımdan tek örnek"* diye
   yazılacak. Çoklu koşum yapılmayacak: maliyeti üç katına çıkarır ve dağılımı
   ölçmek bu sınavın sorusu değil.
5. **[EK] Örnekleme tohumlu ve yansız — GÜN seçilir, sinyal değil.**
   `scripts/dis_sinav.py` pencereden `secim_tohumu` ile karar günleri seçer ve
   o günün **bütün** kırılımlarını alır. Sinyal seçseydik aynı gün için birden
   çok LLM çağrısı gerekirdi (kol gün başına tek çağrı yapıyor).
   **Bedeli yazılıyor:** gün bazlı seçim **kümelenme** üretir — aynı günün
   kırılımları bağımsız gözlem değildir (§2.8: *"6.208 işlem BAĞIMSIZ GÖZLEM
   DEĞİL; aynı ayın yüzlerce işlemi TEK hareketi konuşuyor"*). Bu yüzden gün
   sayısı yüksek tutulacak: **15 gün ≈ 130 çift**, ama istatistiksel olarak
   ~15 bağımsız gözlem. Sonuç bu payla okunacak.
6. **[EK] Sınav DUMAN TESTİ olarak raporlanacak, kanıt olarak değil.**
   Boru hattının görülmemiş veride çalıştığını ve LLM farkının **işaretini**
   verir. "Model kuralı geçiyor" cümlesi kurulmayacak — ne örneklem ne
   gözlem birimi buna yeter (§2.8, §11).

---

> **[Ö] §8 HAZIRLIĞI BİR KUSUR ORTAYA ÇIKARDI — SINAV KOŞULMADAN ÖNCE.**
>
> Sınavın koşum betiği "318 kırılım → 49 çift" verdi ve bu farkı açıklamak
> zorunda kaldım. Sebep: **`strateji.tara()` pozisyon durumunu bilmiyordu.**
> "Kapanış > 20 günlük yüksek" diyor; bir hisse trende girip o seviyenin
> üstünde kaldıkça **her gün yeniden sinyal veriyordu**.
>
> | Ölçüm | Kırılım | Kuralın TAZE girişi |
> |---|---|---|
> | 6 doğrulama günü | 318 | **49 (%15)** |
> | 2026-04-10 | 85 | **4** |
> | 2026-08-27 (canlı) | 36 | **6** (düzeltmeden sonra) |
>
> Seçilen iki sembol de tekrardı: kural **PAYX**'e 1 Temmuz'da, **NWS**'ye
> 29 Temmuz'da girmişti.
>
> **Bedeli:** §2.2'nin "~247 sinyal/ay" rakamı `islemler()`'ten geliyor —
> pozisyon farkında. Motor ise ayda ~750–2500 satır yazıyordu ve çoğu **aynı
> açık pozisyonun tekrarı**. Yani defter kuralı değil, kuralın
> **tekrarlarını** ölçüyordu; karne onları bağımsız gözlem sayacaktı.
> Beyan ile gerçeğin ayrışması.
>
> **Düzeltildi:** `red_sebebi`'ye `"zaten pozisyonda"` kapısı. Durum
> `trend_takip.acik_pozisyon()`'dan — `islemler()` ile **aynı döngüden**
> (`_yurut`), ikinci bir giriş/çıkış mantığı yazılmadan. `islemler()`
> kapanmamış işlemi bilerek düşürüyordu, o yüzden "şu an pozisyonda mıyız"
> sorusu cevapsızdı.
>
> **Giriş barının kendisi pozisyon sayılmıyor** — sayılsaydı kural hiçbir gün
> sinyal vermezdi. Ayrı test bağlıyor. 4 mutasyonun 4'ü yakalandı.
>
> `tara()` 4,1 → 10,1 sn. Bağımsız çapraz kontrol: 2026-04-10'da düzeltilmiş
> tarama **4** diyor, elle sayım da **4** vermişti.
>
> **Bu kusurla yazılmış 38 satır (`strateji` 36 + `strateji_secilen` 2)
> silindi** — Ali'nin kararı. Puanlanmamışlardı, diğer ajanlar etkilenmedi
> (1031 → 993).

**Ne kanıtlar:** boru hattı görülmemiş veride uçtan uca çalışıyor; kararlar saçma
değil; LLM'in kurala katkısı pozitif mi negatif mi (~45+ çift).

**Ne kanıtlamaz:** kuralın kenarı olduğunu. 3 aylık gözlem birimiyle `t` hesaplanamaz
(§2.8). Bu, 4 ay sonra da böyle olacak — ve bunu şimdiden bilmek, sonucu yanlış
okumamayı sağlar.

---

## 8.B SINAV KOŞULDU — SONUÇ (2026-08-28 09:13→09:27)

Pencere 2026-06-01 → 2026-08-27 · **15 karar günü** (tohum 20260828) ·
162 kırılım · **112 çift** (kapanmış işlemi olanlar) · 15/15 günde LLM kolu
%100 kapsama.

| | n | ortalama | medyan | isabet |
|---|---|---|---|---|
| **Kural (tümü)** | 112 | **−%3,64** | −%4,52 | **%21** |
| LLM "al" | 70 | −%3,30 | −%4,64 | %21 |
| LLM "bekle" | 42 | −%4,21 | −%4,38 | %19 |
| **Rastgele kontrol** | 105 sembol | **+%0,44** | — | **%52** |
| **Kurala kalan** | | **−%4,08** | | |

**Medyan testi (Fisher kesin, iki taraflı):** "al" %48,6 ortak medyanın
üstünde, "bekle" %52,4 · **p = 0,845**.

### Üç sonuç, üçü de açık

**1. Boru hattı çalıştı — §8'in birincil amacı buydu ve geçti.**
15/15 gün, tarih kesmesi tek kapıdan, LLM kolu her gün %100 kapsama, hiçbir
çağrı düşmedi, 112 sinyalin sonucu kuralın kendi çıkış kurallarıyla ölçüldü.

**2. Kural bu pencerede kaybetti — ve rastgeleden KÖTÜ.**
Rastgele giriş +%0,44 / %52 isabet yaparken kural −%3,64 / %21 yaptı.
"Kenar yok" değil, **negatif**. §2.7 pencereyi zaten karışık rejim diye
işaretlemişti (QQQ −%4,2) ve uzun-yönlü bir trend kuralının burada
sürüklemeye binemeyeceği yazılıydı — ama sonucun bu kadar sert olacağı
yazılı değildi.

Yapısal bir açıklama var ve bahane değil, ölçülebilir bir hipotez: rastgele
kontrol **sabit süre tutuyor**, kural ise **stop'la çıkıyor**. Testere
piyasada stop'lar tetiklenip zararı kilitler, sabit tutma ise geri
toparlanmayı yakalar. Bu, kuralın kendi tanımının bedeli.

**3. LLM ölçülebilir bir katkı yapmadı.**
"al" dediklerinin ortalaması "bekle" dediklerinden 0,9 puan iyi ama medyanı
daha kötü; Fisher p = 0,845. **İşaret yok.** Model 42 sinyalde "bekle" dedi
ve o 42'nin sonucu diğerlerinden ayırt edilemez.

### Örneklem gürültüsü ÖLÇÜLDÜ

15 günlük örneklem, tüm pencereden **daha kötü** çıktı:

| | n | ortalama | isabet |
|---|---|---|---|
| 15 günlük örneklem | 112 | −%3,64 | %21 |
| **Tüm pencere** | 678 | **−%2,08** | **%26** |

Yön aynı, büyüklük farklı. §8.A'da yazdığım kümelenme bedeli teorik değil:
gün bazlı seçim 1,6 puanlık bir sapma üretti. **Bu yüzden sonuç TEK ÖRNEK
olarak okunuyor** ve "kural rastgeleden %4 kötü" cümlesi kurulmuyor.

### Kendi istatistiğim bozuktu — bulundu ve düzeltildi

İlk `isaret_testi()` tek kümede binom kuyruğu hesaplıyordu ve `k = n/2`
olduğunda alt yarının **tamamını** topluyordu: p **her zaman ~1,0** çıkıyordu.
Yani ilk raporda görünen *"p=1.0, fark yok"* bir test sonucu değil,
**bir kodlama hatasıydı**. Doğru hesap (2×2 Fisher) aynı sonuca vardı
(p=0,845) — ama bu tesadüf; yöntem yanlıştı ve başka bir veride yanlış
"anlamlı" da verebilirdi.

Kanıt yöntemi bu oturumda dördüncü kez kendini kandırdı. Diğer üçü:
`STOP_N` mutasyonunun kendine referans veren testi, bayat `.pyc`, ve zaten
kırmızı bir teste karşı koşan mutasyon turu.

### Pencere KULLANILDI

Bu sonuca bakarak parametre **değiştirilmeyecek**. Değiştirilirse ölçüm
geriye dönük uydurma olur. Kuralın kenarı sorusu §8'in kapsamında değildi ve
hâlâ değil — cevabı canlı defterden, aylar içinde gelecek (§11).

---

## 9. Neden önceki belgeden ayrıldık

`docs/finagent-ibkr-strateji.md`'nin emir katmanı hakkında yazdıkları doğru; strateji
ve veri katmanı hakkındakiler bu depoyla uyuşmuyor. Kalem kalem:

**9.1 Evren.** O belgenin sabit ETF listesi (QQQ, SPY, VUSA/VUAA, CNDX, IWDA, EXS1,
"AEX ETF") veritabanında büyük ölçüde yok: SPY, VUAA, EXS1 hiç yok; QQQ ve AEX yalnızca
`venue='INDEX'` (piyasa vekili) ve `screener.evren()` INDEX'i dışlıyor; IWDA iki kez
kayıtlı (id 223 sıfır bar, id 1780 `IWDA.AS` 513 bar). Ayrıca çıplak ticker yazılmış,
oysa `prices.py:113-128` çıplak sembolü **bilerek reddediyor** (*"sonek eklemek bir
TAHMİNDİR"*).
→ Bu belge evreni `index_members`'tan alıyor; elle liste yok.

**9.2 Likidite eşiği.** O belge `trend_takip.kosu()`'nun varsayılanını devralıyor:
`sources.isyatirim.min_hacim_tl` = 50.000.000 (`trend_takip.py:429-431`). Bu **TL**
eşiği USD/EUR devire uygulanınca ~45 kat fazla sıkı olur. **[Ö]** Mevcut BUX evreninde
ölçüldü: IWDA.AS (12M EUR/gün), EIMI.L, 4GLD.DE ve tüm SPDR sektör ETF'leri elenir.
→ Bu belgede eşik para birimi başına (§5).

**9.3 `--boyut vol` ve "maksimum düşüş".** `ozet()` (`trend_takip.py:383`) ve
`aylik_kumelenme()` (satır 348) işlem başına **ağırlıksız** ortalama alıyor; ağırlık
kavramı bu matematiğe girmiyor. `run.py:253` bunu zaten yazıyor:
*"PORTFOY DUZEYI GETIRI OLCULMUYOR… equity curve hesaplanmiyor."*
→ Bu belgede boyutlama mevcut `boyutlama.boyut()` ile, ikinci formül yok.

**9.4 Pekiştirmeli öğrenme.** `[[geri-bildirim-dongusu-yapilmadi]]`: bu fikir
24 Ağustos 2026'da tasarlandı, hacmi ölçüldü ve **yapılmamasına karar verildi**.
Gerekçe 2 birebir: *"RL bu mimaride imkânsız (abonelik, ağırlık güncellemesi yok) ve
olsa bile veri ÜÇ MERTEBE eksik."* Al-sat tarafında durum daha zor: **[Ö]** ~15
sinyal/ay (~180/yıl), ödül 15 bar gecikmeli, isabet %38 ve getiri kuyruk-baskın.
→ Bu belgede ödül-ceza döngüsü **ağırlık değil izin** güncelliyor (Adım 6): karne
kötüyse tavan düşer. `taktikci` freniyle aynı mimari.

**9.5 `risk.allow_order_execution`.** Bu bayrağı **hiçbir kod okumuyor** — tüm
repoda geçtiği yerler README:290 ve dört yorum satırı (`emir.py:7`, `koruma.py:40`,
`tools.py:2787`, `chat.py:24`). Hiçbir `settings.get()` çağrısı yok. Ayrıca
`settings.yaml:956-960` şunu taahhüt ediyor: *"IBKR emir katmanı (6. adım) bu bayrağı
DEĞİŞTİRMEYECEK."*
→ Bu belge bayrağa dokunmuyor. Emir yolu zaten ayrı ve insan onayına bağlı.

**9.6 Sinyal saati.** O belge 17:45 diyor. **[K]** `settings.yaml:169-170` o saati
şöyle tanımlıyor: *"BIST kapandı (17:00), **ABD açık**"*. Makine saati
Europe/Amsterdam (`/etc/localtime` doğrulandı). **[Ö]** Ve `yfinance` seans içinde
kapanmamış günlük bar döndürüyor — 27 Ağustos 14:18 CEST'te ASML.AS için 27 Ağustos
barı 1514,20 ile geldi. Yazım `ON CONFLICT ... DO UPDATE` olduğu için (`db.py:1269`)
kalıcı bozulma yok, ama 17:45'te üretilen bir ABD sinyali yarım günün fiyatını
"kapanış" sanar.
→ Bu belgede kip `nabiz` (22:15, ABD kapanışı sonrası).

**9.7 Kağıt hesap.** **[K]** `collectors/ibkrportfoy.py:51` → `HESAP = "ibkr"` sabit;
`h.kagit_mi` yalnızca `CollectorResult.data`'da raporlanıyor (satır 183),
ayrıştırmada kullanılmıyor. Gateway kağıt kullanıcıyla açılırsa kağıt pozisyonlar
canlı defterin üstüne yazar. Ayrıca CPGW tek oturum verir ve `ibkr.yaris: false`.
→ Bu belgede kağıt hesap fazı **yok**; ilk faz canlı, en küçük boyutta, tek dokunuşla.

---

## 10. Bilinen tuzaklar

Bu depoda **sahada ölçülmüş** kusurlar. Uygulama sırasında tekrarlanmayacak.

| Tuzak | Kaynak | Korunma |
|---|---|---|
| Para birimi karışması | `db.py:1374-1401` — TSLA serisinde 4,07 EUR ile 489,88 USD yan yana | Yalnızca `db.fiyat_serisi()` |
| Aynı kural iki kopya | `seviye.py:24-30` — RSI iki yerde, MSFT'de 84,8 vs 70,9 | Gösterge/seviye/boyut tek motordan |
| Sessiz kırpma | `prices.py:149-158` — kullanıcı 8 satır gördü, gerçekte 12 vardı | Kırpıldığı **yazılacak** |
| Yanlış "yok" beyanı | `[[yanlis-yok-beyani]]` | Boş sonuç, yokluk kanıtı değil; sebep ayrılacak |
| Sıfır getiri üreten çift bar | `indicators.py:32-49` — iki kaynak aynı günü yazınca 10 sembolde "%0,00" | `fiyat_serisi` tek kaynak seçer |
| Gramere uymayan koşul | `journal.py:43-55` | `tez.kosul_ayristir()` ile önden doğrula |
| Ufuk/koşul karışması | `[[tahmin-defteri-ve-getiri-gercekligi]]` | `ufuk_gun`'ün rolü gerekçeye yazılacak |
| Yeşil test = kanıt sanmak | `[[fixi-nasil-kanitlarim]]` | Mutasyon testi zorunlu (Adım 2) |
| Göç geri sarılmaz | `[[goc-kaliplari-ve-tuzaklari]]` | Göç testi eski şemayı gerçekten kurar |
| Test canlı kanala yazdı | `[[test-canli-kanala-yazdi]]` | Testler Telegram'a **çıkmayacak**; izolasyon ağı da kapsayacak |
| Bayat ayna "güncel" göründü | Adım 1'de ölçüldü: arşiv 2.182.843 satır, ayna 988.570 — ve `ayna_guncelle` "ayna guncel" dedi | `dogrula()` **kaynak sayılarıyla** çağrılıyor (düzeltildi) |
| Geçici hata kalıcı yokluk gibi raporlandı | Adım 1: GPC/GPN/GRMN "(sembol yok)" dendi, üçü de 2513 barlık | Sebep sınıflandırılıyor: `cagri hatasi` / `seri yok` / `ad eslesmedi` |
| Kesik `shortName` ad kapısını yanlış kapattı | Adım 1: 14 üyede `shortName` 0/14, `longName` 7/14 eşleşti | İki ad da soruluyor; **kural değişmedi** |
| **Para birimi kapısı ile `fiyat_kaynagi` çatışması** | Adım 1: **ASML**'in 2513 barlık USD serisi var ama `fiyat_kaynagi` EUR'yu seçiyor (Ali BUX'ta EUR tutuyor) → `para_birimleri: ["USD"]` onu evrenden **atıyor** | Adım 2'de karar verilecek: strateji evreni için kotasyon seçimi pozisyondan bağımsız mı olmalı? **Bugün 1 sembolü etkiliyor** |

**Son satır Adım 2'yi bağlar ve şimdiden yazılıyor.** `fiyat_kaynagi()` kaynağı
*pozisyonun* para birimine göre seçiyor — bu portföy raporu için doğru, strateji
için tartışmalı: aynı şirketin ABD kotasyonu emrin gideceği yer (IBKR'de ücretsiz
gerçek zamanlı, komisyonu ölçülmüş), Amsterdam kotasyonu değil. Bugün yalnızca
ASML'i etkiliyor; MSFT/TSLA/AMZN gibi diğer 10 EUR pozisyonunda `_fiyat_makul`
kapısı sertifika serilerini zaten reddettiği için USD serisi seçiliyor (ölçüldü:
`MSFT.AS` %98,0 sapma, `TSLA.AS` %97,6 → yazılmadı). Yani sorun **bugün küçük**
ama sessiz: ASML hiçbir hata vermeden evrenin dışında kalıyor.

---

## 11. Beklenti ayarı

Uygulayan ajanın ve kullanıcının şunu baştan bilmesi gerekiyor, çünkü ilk haftalarda
sistem "bozuk" görünecek:

- **[Ö]** İsabet oranı **%32** — 38 sembollük BUX alt evreninde %38-39 ölçülmüştü,
  gerçek 485 sembollük ABD evreninde **%32** çıktı (2026-08-27, §2.2). Yani on
  işlemin **yedisi** zarar edecek, altısı değil. Bu, trend takibinin normal
  profili: **[Ö]** kârın %55,9'u en iyi %5'lik kuyruktan geliyor. İsabetin daha
  da düşmesi kuyruğun daha baskın olduğu anlamına gelir; kötüye işaret değildir,
  ama **ilk haftalarda sistem daha da "bozuk" görünecek**.
- **[Ö]** 26 Ağustos itibarıyla 17 sembolde **sıfır** kırılım vardı. Sinyalsiz günler
  olacak ve bu arıza değil.
- Karne **brüt** tutulacak; 4-5 USD'lik emirlerde %1,9 gidiş-dönüş komisyon net
  getiriyi yer ve *"kural çalışmıyor"* diye okunur — oysa çalışmayan şey emir boyutudur.
  Hedef boyuttaki maliyet **ayrı sütun** olarak yazılacak.
- **Kuralın kenarı olup olmadığı sorusu bu belgenin kapsamında cevaplanmıyor.**
  Gözlem birimi ay; anlamlı bir `t` için ~24 ay gerekir. Bu belge şunu kuruyor:
  ölçüm hattı, defter, karne ve fren. Cevap zamanla gelecek.

---

## 12. Bitiş ölçütü

- [x] Adım 1: `docs/ibkr-evren.md` var; 511 sembolde bar sayısı, ilk/son tarih,
      medyan devir, kaynak, conid durumu; serisiz 7 sembol **sebebiyle** listeli.
      Üreten: `scripts/evren_belgesi.py` (elle yazılmıyor)
- [x] Adım 1: **[?]** işaretli üç ölçüm yapıldı ve bu belgeye yazıldı —
      veritabanı 169,4 → **324,1 MB**, yedek **3,15 sn** (duvar 6,64),
      tarayıcı 21,1 → **30,6 sn** (bütçe 3000 sn, yedek plan gerekmedi)
- [x] Adım 1: `sources.prices.range` hedef bazlı (`_aralik`), `ibkr.strateji`
      bloğu + `config.strateji_ayari()` doğrulaması, 518 sembol çekildi (%98,6)
- [x] Adım 1: conid çözümü — **483/518 = %93,2** (11 sn). Seri **ve** conid
      birlikte: 482. Çözülemeyen 29'un hepsi ad kapısı ve kapı doğru davranıyor
      (belirsiz olan boş bırakılıyor).
- [x] **Adım 1 dışı, Adım 1'de bulundu — DÜZELTİLDİ:** `ibkr/oturum.py:_tik()`
      başarısız `ssodh/init` denemesini geri çekilmesiz tekrarlıyordu (7,5 saatte
      ~450 boş deneme; taze girişleri de engelledi). Artık üstel geri çekilme
      (60 sn → 30 dk tavan): aynı 6 saatte ~360 deneme yerine **14**. Rakip
      oturum geri çekilmeyi tetiklemiyor (`kur()` artık `None` = hiç denenmedi).
      **5 mutasyonun 5'i yakalandı** — biri ilk turda kaçtı ve test düzeltildi.
- [x] Adım 2: `pulse/strateji.py` var; **14 test yeşil** (istenen 10 + 4 ek);
      **6 mutasyonun 6'sı yakalandı** (`scripts/mutasyon_strateji.py`);
      bağımlılık testi AST ile yeşil (`notify`/`ibkr`/`llm`/`bot` yok, ve kural
      sabitleri yeniden tanımlanmıyor). İki bilinçli sapma: `ufuk_gun` 14
      (ölçümden, ayardan) ve `tara()` sayaçlarla birlikte sözlük dönüyor.
- [x] Adım 3: **İlk gerçek koşuda mesaj GİTTİ** (2026-08-27 22:59, nabız).
      518 sembol · 36 kırılım · 2 seçildi (NWS, PAYX). Kabul ölçütü **canlı
      çıktı üzerinde** doğrulandı: tablodaki 25 sembolün 25'inde `KAPANIS`,
      `20G YUK`, `STOP(2N)`, `10G DIP` değerleri `seviye.seviyeler()` ile
      **birebir** (0 uyuşmazlık). 10 test yeşil, 7 mutasyonun 7'si yakalandı.
- [x] Adım 4: **Canlı koşuda yazıldı** (2026-08-27 22:59):
      `predictions`'ta `strateji` **36** satır, `strateji_secilen` **2** satır,
      `sahip='ali'`, `ufuk_gun=14`, `taktik_giris`/`taktik_stop` dolu,
      `gecersizlesme_kosulu` = `close < 33.435` biçiminde. 7 test yeşil,
      6 mutasyonun 6'sı yakalandı.
- [~] Adım 5: **Şema 24 göçü yeşil ve canlıda koştu** (23:10, `gunici`;
      3/3 satır korundu). Dolum sapması tablosu + önizleme komisyonunun
      kaydı hazır; 5 test yeşil, 7 mutasyonun 7'si yakalandı.
      **Açık:** `dolum_fiyat`/`dolum_komisyon`/`dolum_ts` canlı bir emir
      bekliyor. Mevcut KO emrinin dolumu geriye dönük alınamadı — IBKR'nin
      işlem penceresi 0 kayıt döndürüyor (ölçüldü)
- [x] Adım 6: Karnede **dört sayı** var (strateji, seçilen, rastgele, fark);
      **fren testi yeşil** ve fren tavana gerçekten bağlı (ayrı test).
      **Emir butonu açıldı**: adet `boyutlama.boyut()` × hesap net likidite ×
      kur ile hesaplanıyor; üçünden biri eksikse adet YAZILMIYOR ve sebebi
      söyleniyor. Buton `/emir` yolunu kullanıyor, onay yapısı değişmedi.
      11 test yeşil, **12 mutasyonun 12'si yakalandı**.
- [x] §8: **Dış sınav koşuldu** (2026-08-28, §8.B). Dondurulan parametreler
      koşumdan **önce** yazılmıştı (§8.A, sınav görülmeden tazelendi);
      look-ahead testi geçti. Boru hattı 15/15 günde çalıştı; kural bu
      pencerede rastgeleden kötü (−%3,64 vs +%0,44); LLM'in katkısı
      ölçülemedi (Fisher p=0,845). Sonuç **tek örnek** olarak yazıldı.
- [x] **Uygulama sonrası (28 Ağustos):** evren genişlemesinin bedeli
      **komşu katmanda** ödendi — `prices` 80 → 861 sn çıktı ve sabah
      panel bütçesini 900 → 61 sn'ye düşürüp iki sahibin de panelini
      düşürdü. İki düzeltme: (a) derin geçmiş artık her koşuda
      indirilmiyor, `asgari_bar`'a ulaşmış seriye boşluk kademeli
      tazeleme; (b) evren `strateji_fiyat` adıyla ayrı collector'a
      alındı ve **yalnızca `nabiz`** kaynak listesinde. Sonuç: `prices`
      73 sn ve `ok`, panel payı 450 sn'ye döndü. Ayrıca `_kotasyon_yaz`
      Adım 1-2'de açılmış bir yan etkiden temizlendi.
      **Açık kalan:** hakem 450 sn'de de kesiliyordu — panel bağımsız
      bir kapasite sorunu taşıyor, evren ayrımı onu çözmez.
- [x] **ÇIKIŞ SİNYALİ (29 Ağustos).** Motor yalnızca `AL` üretiyordu.
      2N stop tahmin defterinin koşuluyla (`close < stop`) tez alarmına
      düşüyordu, ama Donchian'ın **asıl çıkışı — 10 günlük dip** hiçbir
      yerde izlenmiyordu: giriş günündeki tabloda bir kez gösterilip
      unutuluyordu. Trend takibinde kenar büyük ölçüde çıkıştadır.
      `trend_takip.cikis_karari()` + `strateji.cikislar()` eklendi;
      çıkış testi `_yurut` ile **tek kopya** (`_cikis_sebebi`).
      **Çıkış YALNIZCA sahip olunan kâğıt için konuşulur** — tarama
      tarafındaki simüle defter (167 sembol) kullanılsaydı, kullanıcının
      hiç almadığı kâğıtlar için "SAT" denirdi. Stop ancak pozisyon
      maliyeti kayıtlı girişe yakınsa ödünç alınır; değilse
      "bilinmiyor" denir. Çıkış **frenden etkilenmez**.
      6 test yeşil, **10/10 mutasyon**; refactor'ün davranışı koruduğu
      395 seri / 23.496 işlemde **0 fark** ile kanıtlandı.
- [x] Mevcut test sayısı korundu ve arttı: `test_smoke.py` 680 → **764**,
      `test_ibkr.py` 143 → **145**. Adım 1'de eklenen 4 test ve Adım 2'nin
      6 mutasyonu ayrıca mutasyonla kanıtlandı (ayna bayatlığı, geçici hata
      sebebi, `longName` kapısı, init geri çekilmesi, Donchian kuralı)
