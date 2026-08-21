# finagent — Yol haritası: inceleme maddeleri + proaktif taktik katmanı

Tarih: 2026-08-21. Kaynak iki şey:

1. **Beş uzmanlı panel incelemesi** (kantitatif strateji, veri altyapısı,
   LLM/danışman mimarisi, operasyon/test, hafıza denetimi). Her incelemeci
   iddialarını kodda doğruladı: test paketi koşuldu (469/469, 41 sn), git
   geçmişi sır desenleriyle tarandı (temiz), hafıza kayıtları commit ve
   dosya düzeyinde örneklemle teyit edildi. Özet hüküm
   `dis-inceleme-dongusu` hafızasında (beşinci tur).
2. **Ürün kararı:** sistem pasif "koşul bekçisi"nden, gün içinde fırsat
   analiz edip iki kullanıcıya **alım / koruma / satış taktiği** sunan
   proaktif bir danışmana evrilecek. Verilen kararlar:
   - Gün içi kapsam: **BIST + ABD** (Avrupa kotasyonları günlük ritimde kalır)
   - Tempo: **günde en çok 2-3 taktik**; koruma sinyali her zaman anında
   - Boyutlama: **seviye + risk yüzdesi** (tutar telaffuz edilmez)

> **ARA OLAY — 21 Ağustos 08:00 sabah koşusu (KAPATILDI, `990d0b5`).**
> Bu belge yazıldıktan sonra sabah koşusu süre sınırında öldürüldü ve
> hiçbir çıktı üretemedi; üstelik ROSE'un tez alarmı tespit edilip
> kalıcı olarak kayboldu. Kök neden: **panelin duvar saati yoktu** ve
> `panel_butce_sn` yalnızca sahipler arasında bakıyordu. Dört katmanlı
> düzeltme yapıldı (panel kendi saati + sahip başına adil pay +
> kabuğun öldürme anının `KOSU_BITIS_TS` ile içeriye geçirilmesi +
> tespit→teslimat→damga sırası) ve altı kasıtlı bozmayla sınandı.
> Ayrıntı `zamanlanmis-kosu-gozetimi` hafızasında. Aşağıdaki A/B
> maddeleri bu olaydan etkilenmedi; sıra aynı.
>
> Olayın yan ürünü olarak **A2 (yedekleme) için bir ölçüm çıktı**:
> canlı veritabanı WAL modunda ve düz `cp` son işlemleri KAÇIRIYOR —
> yedek `VACUUM INTO` ile alınmalı, `cp` ile değil.

Maddeler **birbirinden bağımsız** olacak şekilde ayrıştırıldı: işaretsiz
her madde tek başına eklendiğinde değer üretir. Gerçek bağımlılıklar
açıkça yazıldı (yalnızca B5→B4 ve B6→B4+B5).

Bu belge yazılırken kod tabanında hiçbir değişiklik yapılmadı; 21 Ağustos
akşamı başlanan Faz-1 düzenlemeleri (şema 13 taslağı) geri alındı.
Çalışma ağacı temiz.

---

## A. Teknik incelemeden çıkan maddeler

Her biri bağımsız; sıralama değer/maliyet oranına göre.

### A1. Vision oturumuna `can_use_tool` kapısı — küçük  ✅ **BITTI** (`fb12025`)

`src/finagent/vision/screenshot.py:260-272` `permission_mode=
"bypassPermissions"` ile ve **kapısız** koşuyor. Chat katmanında ölçülen
ders (`bot/chat.py:761-770` + `tests/test_smoke.py:1128`): bypass altında
`allowed_tools` filtre değil ipucu. Ekran görüntüsü dış veridir; görsel
prompt injection'a karşı tek savunma şu an prompt kuralı. Chat'teki
`_izin` kapısının aynısı buraya + `Read` için medya dizini yol kilidi.

### A2. Günlük SQLite yedeği — küçük, en yüksek değer/maliyet  ✅ **BITTI** (`09c0587`)

`data/finagent.db` (~122 MB, WAL) geri üretilemez veri içeriyor:
portföy ekran görüntüsü geçmişi, tahmin defteri, sohbet arşivi. Repoda
hiçbir yedekleme mekanizması yok. `VACUUM INTO` ile günlük kopya —
kapanış koşusuna tek satır ya da ayrı küçük bir launchd işi; istenirse
bulut senkronu. Tek disk arızası bugün projeyi sıfırlar.

### A3. Sohbet araçlarına sermaye işlemi filtresi — orta, hafızada "EN ACİL"  ✅ **BITTI** (`35f07f7`)

`BORSA_LIMITI` yalnız tarayıcı/backtest yolunda (`pulse/screener.py`,
`analysis/backtest.py`, `analysis/trend_takip.py`); `teknik`,
`karsilastir`, `fiyat_serisi` araçları o kapıdan geçmiyor. Ölçülmüş
vaka: ADEL, 800 günlük pencerede -%82,2 "uydurma" getiri (11:1 bölünme).
"Son 3 yılda ne yaptı?" sınıfı soru yanlış sayı döndürüyor.

### A4. `Read`/`WebFetch` parametre denetimi — küçük-orta

`bot/chat.py:774-783` `_izin` kapısı yalnız **araç adına** bakıyor,
`tool_input`'a bakmıyor. Görsel penceresi açıkken zehirli sayfa →
"`Read` ile `.env`" → `WebFetch` URL'siyle sızdırma zinciri teorik
olarak açık. `Read` için yol kilidi, `WebFetch` için alan adı politikası.

### A5. Backtest'e taban (limit-down) çıkışı — orta

`analysis/trend_takip.py:114-115` stop dolumunu **tam stop fiyatından**
sayıyor. BIST'te ardışık taban serileri (her biri %12 filtresinin
altında) gerçekte çıkışa izin vermez; gap-down dolumu stop'un altındadır.
Tavan girişi modellenmişken tabanın atlanması beklentiyi tek yönlü
şişirir. Donchian sonucuna güvenmeden önce kapanmalı.

### A6. Kontrol analizlerinin koda alınması — orta

Kenarı +%6,4'ten ~+%3'e indiren rastgele-giriş kontrolü ve aylık
kümelenmiş GA analizi repoda yok — tek kayıt commit mesajı (75e331a).
En önemli sonuç tekrarlanamaz durumda. `trend_takip`'e iki fonksiyon +
`run.py trend` çıktısına bölüm.

### A7. Backtest evrenine likidite filtresi — küçük

Üretim tarayıcısı 50M TL hacim eşiği uyguluyor (`screener.py:131`),
backtest `_evren`'i uygulamıyor (`backtest.py:73-85`). Kârın %55,9'unu
taşıyan üst %5'lik kuyruk büyük olasılıkla taranmayan illikit
kâğıtlarda; oralarda %0,4 maliyet varsayımı da düşük.

### A8. `fiyat_serisi()`'ne currency süzgeci — küçük  ✅ **BITTI** (`35f07f7`)

`prices.py:434-445`: `fast_info` düşerse yeni barlar `currency=NULL`
yazılabilir ve tek kaynak içinde karışık etiketli seri oluşur.
`fiyat_serisi` yalnız `source` ile süzüyor. Ek olarak `_seriden_kur`
(`db.py:1334-1353`) sembol-tekilliğine yaslanıyor — venue kapısı yok.

### A9. Koşu başına healthchecks ping'i — küçük

healthchecks.io bugün yalnız **bot sürecinin** kalp atışını izliyor
(`watchdog.py:575-599`): bot canlıyken zamanlanmış koşular bozuksa dış
izleyici yeşil kalır. `run_kosu.sh` sonunda başarıda ping, hatada
`/fail` — dört arıza senaryosuna Telegram'dan bağımsız ikinci göz.

### A10. Bekçiye plist-drift ölçütü — küçük

Bekçi beklenen saatleri **repodaki** plistlerden türetiyor
(`watchdog.py:297-317`); launchd **kurulu kopyayı** koşturuyor
(`launchd_install.sh` cp ile kopyalıyor). Repo plisti değişip kurulum
atlanırsa "çalışıyor ama beyan edilen saatte değil" boşluğu açılır ve
kimse ölçmüyor. Kurulu kopya ↔ repo hash karşılaştırması altıncı ölçüt.

### A11. Güven kalibrasyonu — orta

`guven` alanı toplanıyor ama hiç ölçülmüyor: Brier skoru ya da
güven-isabet güvenilirlik kırılımı yok. 0,8 güvenli tahminlerin
0,5'liklerden isabetli olup olmadığı bilinmiyor. Karneye eklenmeli.

### A12. Test koşucusu iyileştirmesi — küçük

`tests/test_smoke.py:14081-14086` naif döngü: ilk assert'te tüm koşu
durur, kalan ~460 testin durumu görünmez. Hata toplayan + isim filtreli
mini koşucu. (Dosyanın 1. satırındaki "python -m pytest" ibaresi de
yanlış — pytest kurulu değil.)

### A13. E2E B katmanına DB izolasyonu — küçük

`scripts/e2e_senaryo.py:311-326` yan etki kapısı yalnız Telegram
token'larını düşürüyor; `DB_PATH`/`BOT_STATE_DIR` izolasyonu B
katmanında yok. Model `veri_topla`/`izlemeye_al` çağırırsa canlı yazım
olur. Duman testlerinde kapatılan sızıntı sınıfı burada açık.

### A14. Hafıza bakımı — küçük, kod değil

Durum-anlık-görüntüleri çok dosyada bayatlamış: MEMORY.md "28 araç"
diyor (gerçek 36+), `veri-katmanlari` tablosu 15 Ağustos'ta donuk,
`midas-kayit-bekliyor` bitmiş bir durumu anlatıyor. Durum metriklerini
yalnız `siradaki-is`'te tutup diğerlerinden çıkarmak.

### A0 (doğrulama, kod değil): İlk puanlama gerçekten koştu mu?  ✅ **CEVAPLANDI 2026-08-21**

**Puanlama bozuk DEĞİL — henüz vadesi dolmamış.** Ölçüldü 2026-08-21:
353 tahminin 3'ü puanlanmış, hakem çağrılarının 0'ı. Sebep mekanizma
değil TAKVİM: 15 Ağustos'taki 5 günlük ufuklar 4 bar görmüş, 5
gerekiyor (hafta sonu + bugünün kapanmamış barı). Puanlanan 3 tahmin
KRİPTO — haftada 7 bar ürettiği için önce olgunlaşıyor.

Yani ilk gerçek karne bu akşamki koşuyla başlıyor; `yeterli_mi`
eşiği (n≥20) için ~2 hafta daha gerekiyor. O güne kadar çıkan her
isabet oranı gürültü ve karne bunu zaten beyan ediyor.

---

## B. Proaktif taktik katmanı — bağımsız değer analizi

Kullanılan ölçüt: *bu parça tek başına eklendiğinde iki kullanıcı ertesi
gün farklı ne görür?* Bu ölçüt ilk taslağın sırasını ters çevirdi:
ilk plan veri katmanından başlıyordu, oysa en yüksek değerli parçalar
mevcut veriyle bugün çalışıyor. Kilit gözlem: **koruma bacağının
saatlik veriye ve gün içi koşuya ihtiyacı yok.**

### B1. Koruma bacağı — günlük veriyle, mevcut ritimde · BAŞLANGIÇ NOKTASI  ✅ **BITTI** (`6300cf5`)

Pozisyonlar için 2N-ATR stop seviyesi deterministik hesaplanır (kod
`trend_takip._atr`'de hazır), tez gramerine `close < <stop>` koşulu
olarak yazılır (`pulse/tez.py` grameri bunu bugün ifade edebiliyor);
`tez_kontrol` zaten günde 4 koşuda çalışıyor — kırılım alarmı mevcut
mekanizmadan gelir. Yeni veri yok, yeni koşu yok, LLM yok.

Tek başına değeri: "koruma taktiği" bacağının tamamı ilk günden çalışır.
Kenar kanıtı gerektirmez — risk disiplinidir; backtest'in kanıtlayamadığı
alım tarafının aksine bugün dürüstçe sunulabilir.

### B2. Taktik sözleşmesi — mevcut panel çıktısına

Hakem görüş şablonu taktik sözleşmesine yükseltilir: tür
(alım/koruma/satış) + giriş bölgesi + stop + geçersizleşme koşulu +
ufuk + güven. Seviyeler **koddan hesaplanıp modele verilir; model seçer,
hesaplamaz** (335-pencere dersi: çözüm prompt değil araç/veri). Deftere
`ajan='taktik'` yazılır — mevcut `Defter.kaydet` sözleşmesi
(`pulse/journal.py:63`) bu alanları zaten taşıyor, puanlama ve
`ajan_karnesi` bedavaya gelir.

Tek başına değeri: kullanıcı gün içi katman olmadan da günde 4 kez
"ne yapayım" cevabını yapılandırılmış alır.

### B3. Boyutlama satırı — seviye + risk yüzdesi

Mesajlara "portföyün %1'inden fazlasını riske atma; stop mesafesi %X →
bu kabaca pozisyonun %Y'si demek" satırı. Tutar telaffuz edilmez.
B1 ya da B2'nin mesajına eklenen küçük, bağımsız parça.

### B4. Saatlik veri katmanı (BIST + ABD)  ✅ **BITTI** (`d7dceef`)

- `prices_hourly`'ye `currency` kolonu (şema 13; `_news_konu_gocu`
  kalıbı — `ALTER TABLE ADD COLUMN`, NULL kabul eden tek kolon; eski
  kripto satırları NULL kalır, okuyan taraf USDT varsaymaz).
- Yeni `saatlik` toplayıcısı: yfinance 60 dk. Evren = pozisyon ∪ izleme;
  BIST → `SEMBOL.IS` / TRY (bistgecmis emsali: sonek tekil), ABD →
  `fiyat_kaynagi()` USD seçen BUX-venue enstrümanlar, ham sembol.
  İkili para birimli enstrümanlar (ASML, MSFT, TSLA: USD+EUR serisi
  var) tek meşru seçici `fiyat_kaynagi()` üzerinden çözülür — elle
  liste tutulmaz.
- Ölçüldü 2026-08-21: Yahoo 60m GARAN.IS/THYAO.IS 9 bar/gün (TRT),
  NVDA 7 bar/gün (ET). Veri var.
- `binance` saatlik yazımına da `currency="USDT"` eklenir.

Tek başına değeri: `saatlik` sohbet aracı hisseler için dolar ("bugün
gün içinde ne oldu?"); koşu olmadan mevcut koşularda toplanabilir.

### B5. Gün içi koşu — LLM'siz *(B4'e bağlı)*  ✅ **BITTI** (`2ba1009`)

30 dk aralıklı, piyasa-saati kapılı launchd işi (BIST 09:00-17:10,
ABD 15:30-22:05, Europe/Amsterdam; hafta içi). Saatlik kapanışla
tez/stop koşulu kontrolü + gün içi kümülatif hareket taraması
(günlük σ'ya göre, `BORSA_LIMITI` süzgeciyle). Tasarım notları:

- `ritim.kipler`'e SOKULMAZ — o sözleşme sabit saatli koşular için
  (`ritim_kip` doğrulaması, `run_kosu.sh`, bekçinin plist-saat okuması).
  Ayrı `ritim.gunici` bloğu + kendi doğrulayıcısı (`gomme_ayari`
  kalıbı), ayrı `run_gunici.sh` (flock + duvar saati `_ortak.sh`'tan),
  `StartInterval` plist'i.
- `launchd_install.sh` 2b kontrolü her plist etiketini `ritim_kip` ile
  doğruluyor — `gunici` etiketi için `gunici_ayari` yoluna ayrılmalı,
  yoksa kurulum düşer.
- Bekçiye ayrı ölçüt: pencere içinde iz (`data/bot/kosu/gunici.json`)
  2×aralık + pay'dan eskiyse uyar; pencere dışında sessiz.
- Para birimi kapısı: saatlik barın `currency`'si günlük serininkiyle
  eşleşmiyorsa o enstrüman atlanır (karşılaştırma yapılmaz).

Tek başına değeri: koruma alarmı kapanışı beklemez — yoğun kullanıcı
için asıl kritik an, stop kırıldığı an.

### B6. Gün içi alım/satış taktikçisi — mini LLM *(B4+B5'e bağlı, en sonda)*

Eşiği geçen adaylara tek çağrılık taktikçi
(`analysis.llm.tactical_model`), panel gibi 4 ajan + hakem değil.
Disiplin kuralları:

- Aday yoksa ya da günlük tavan dolmuşsa **LLM hiç çağrılmaz**.
- Günde sahip başına en çok 2-3 alım/satış taktiği (`predictions`
  üzerinden sayılır; `DO NOTHING` anahtarı gün içi tekrar teslimatı da
  engeller). Koruma tavandan muaf.
- Geçersizleşme koşulu gramerden geçmezse taktik reddedilir ve sayılır
  (`_gecerli_kosul` kalıbı). Model, koddan verilen seviyelerin dışına
  çıkarsa taktik reddedilir.
- Karne: puanlanmış `ajan='taktik'` (yön ≠ nötr) n<20 iken her mesajda
  "henüz karnesiz — ÖLÇÜLMEMİŞ" ibaresi; n≥20 ve isabet ≤%50 ise tavan
  otomatik 1'e iner (fren).

Alım tarafının kanıtı yok (24 backtest hücresinin 22'si sıfırdan ayırt
edilemez) — bu parçanın en sonda ve en frenli olmasının sebebi bu.

---

## Önerilen yürüyüş

**B1 → B2+B3 → A2/A1/A3 → B4 → B5 → B6**; kalan A maddeleri araya
bağımsız serpiştirilir. Her adımda: test (`tests/test_smoke.py`,
kasıtlı-bozma dahil), README güncellemesi, bot restart
(`launchctl kickstart -k gui/$UID/com.alipala.finagent.bot`).

Korunacak sınırlar (değişmiyor): emir iletimi yok; alıcılar yalnızca
tanımlı iki sahip (`telegram.sahipler`) — sistemi kişisel kullanım
çerçevesinde tutan çizgi; sessizlik geçerli çıktıdır — taktik tavanı
doldurulmak zorunda değildir.

---

## Yol boyunca çıkan YENİ işler (2026-08-21)

Bunlar ilk listede yoktu; uygulama sırasında ölçülerek bulundu.

### Y1. Kripto gün içi kapsam dışı — orta *(B5 devamı)*

`acik_borsalar` yalnızca BIST/ABD döndürüyor ve kripto saatlik barları
sadece zamanlanmış koşularda tazeleniyor. Sonuç: seans içinde 4+ saat
bayat kalıp atlanıyorlar (logda görünüyor: `BNB(bar 4.4 saat eski)`).
Kripto 7/24 işliyor ve portföyde BNB/XRP/ROSE var, yani **kripto
pozisyonlarının gün içi koruması yok.** `binance` için ayrı bir HAFİF
saatlik tazeleme gerekiyor (mevcut collector günlük+saatlik tam çekim
yapıyor, 55 sn — 30 dakikalık döngüye uygun değil).

### Y2. `launchd_install.sh` bootstrap yarışı — küçük

Kurulum "en az bir servis yüklenemedi" dedi ve **bot yüklenmeden
kaldı** (2026-08-21). Belgelenmiş `bootout`/`bootstrap` yarışı: bekleme
döngüsü `launchctl list | grep -q com.alipala.finagent` ile bakıyor ve
DİĞER servisler listede durduğu için erken çıkıyor. Elle `bootstrap`
ile kurtarıldı. Kurulumun bot'u ayakta bırakmaması, en kötü sessiz
arıza — Telegram tamamen ölür.

### Y3. TSLA/MSFT sertifika satırları duruyor — küçük, KARAR GEREKTİRİR

`prices` tablosunda 13 EUR bar (TSLA 11, MSFT 2) ABD borsa
tatillerinde yakalanmış **sertifika** fiyatları (4,07 EUR vs 430 USD).
A8'den sonra artık seçilmiyorlar, yani zararsızlar. Silmek geri
alınamaz olduğu için yapılmadı; temizlenirse tablo tamamen tutarlı olur.

### Y4. Yedek dizini kararı — SENİN KARARIN

`data/yedek` aynı diskte: kazaya karşı korur, disk arızasına karşı
korumaz. Tek satırlık değişiklik, ayrıntı `veri-dayanikliligi`
hafızasında.
