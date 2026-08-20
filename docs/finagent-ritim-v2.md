# Uygulama belgesi — Ritim v2: dört koşu, her koşuda panel

Kaynak: `finagent-uygulama.md` İŞ 2 (üç koşu, ikisi LLM'siz) canlıya
alındı ve ölçüldü. Kullanıcı talebi değişti: **her koşunun sonunda ajan
takımının yorumu** istiyor ve günde **dört** bildirim. Bu belge İŞ 2'yi
iptal etmiyor, üstüne çıkıyor. Mevcut mekanizmalar (kip parametresi,
`run_hafif.sh`, tek-örnek kilidi, bekçi, `bildirim_durumu`, `tez_bozuldu_ts`,
sahip döngüsü) **aynen kalır**; değişen şey hangi kipin ne zaman koştuğu,
panel çalıştırıp çalıştırmadığı ve kime gönderdiği — üçü de koddan
**ayara** taşınıyor.

Kullanıcı profili (Europe/Amsterdam): 08:00 kalkar, 08:00–17:00 mesai,
00:00 yatar. İki sahip: `ali`, `yuksel`.

> **REVİZYON 2026-08-19.** Belge yazıldıktan sonra mevcut kodun ve canlı
> verinin karşısında okundu. İki maddesi ölçümle çürüdü ve düzeltildi:
> **§2.1(b)** bütçe (dayandığı ölçüm 5,6 kat sapmış; 19 Ağustos gecesi
> nabız koşusu tam o bütçede öldü) ve **§2.2** defter teşhisi (`olusma_ts`
> tarih olduğu için dört koşu dört satır yazmıyor, aynı satırı EZİYOR).
> Buna bağlı olarak §3.0 eklendi, §4 ve §5 genişledi, §6 sırası
> yeniden kuruldu. İncelemenin işlenmemiş üç bulgusu §6 sonunda listeli.

---

## 0. Saat dilimi — ÖLÇÜLDÜ 2026-08-19, kapandı

`finagent-uygulama.md` İŞ 2 tablosu "Europe/Istanbul" diyor. launchd
`StartCalendarInterval` ise **makinenin yerel saatini** kullanır ve
makine Amsterdam'da. İkisi aynı anda doğru olamaz. Ölçüm:

```
$ date "+%Y-%m-%d %H:%M:%S %Z (%z)"
2026-08-19 23:40:29 CEST (+0200)

$ launchctl print gui/$(id -u)/com.alipala.finagent.pulse | grep -A3 calendar
  "Minute" => 15   "Hour" => 22   "Weekday" => 1..5

$ grep -n "^22:15" data/pulse.log | tail
1696:22:15:00 INFO  Sema hazir: .../data/finagent.db (surum 11)
```

**Sonuç: birinci şık. Kod doğru çalışıyor, belge yanlıştı.** Makine
CEST (+0200), plist saatleri yerel, yani 22:15 = 22:15 CEST = ABD
kapanışından (22:00 CEST) 15 dk sonra. `finagent-uygulama.md`'deki
"İstanbul" etiketi düzeltilecek; başka bir şey yapılmayacak.

İki ek tespit, ikisi de bu ölçümün yan ürünü:

- **Bugünkü saatler bu belgenin sandığı gibi değil.** Kurulu plist'ler
  `sabah 09:30`, `ogle 18:00`, `nabiz(pulse) 22:15`. Yani §1 tablosu
  dört saatin **üçünü** kaydırıyor, ikisini değil.
- **`config/settings.yaml` içindeki `timezone: "Europe/Istanbul"` bir
  çelişki DEĞİL ve dokunulmayacak.** Yalnızca iki yerde kullanılıyor:
  `collectors/kap.py` (KAP yayın saatini ayrıştırır — KAP gerçekten
  İstanbul saatiyle yayınlar) ve `browser/session.py` (`timezone_id`).
  Zamanlamayla ilgisi yok. Bunu "tutarsızlık" sanıp Amsterdam'a çekmek
  KAP damgalarını üç saat kaydırırdı.

Bundan sonra bu belgedeki tüm saatler **Europe/Amsterdam**. Plist'e yazılan
saat = Amsterdam saati. İstanbul'a çevirme yok; bekçi (`watchdog.py`)
plist'i okuyor, plist tek doğruluk kaynağı kalıyor.

---

## 1. Ne — dört koşu, dördünde panel

| Amsterdam | kip | Piyasa anı | Toplama | Panel | Alıcı |
|---|---|---|---|---|---|
| 08:00 | `sabah` | ABD/Asya gecesi kapandı, Avrupa açılmadı, kripto 7/24 | `prices makro binance` | ✅ | ali, yuksel |
| 12:30 | `ogle` | Avrupa + BIST seans ortası, ABD pre-market | `kap makro binance` + K1/K2 haber | ✅ (hafif bütçe) | ali, yuksel |
| 17:45 | `kapanis` | Euronext 17:30 ve BIST 17:00'de kapandı, ABD açık | `isyatirim midas prices makro takvim kap` | ✅ | ali, yuksel |
| 22:15 | `nabiz` | ABD kapandı | mevcut tam zincir | ✅ (mevcut) | ali, yuksel |

Neden bu dördü ve 15:15 (ABD açılış öncesi) değil: kullanıcının iki
portföyü de Avrupa/BIST ağırlıklı (BUX EUR, Midas TRY); Avrupa kapanışı
ABD açılışından daha çok bilgi taşıyor. ABD pre-market bilançoları ve
14:30'daki ABD makro verileri 17:45 koşusuna zaten girer. 15:15 istenirse
beşinci kip olarak **ayara eklenir, koda değil** — §3'ün amacı bu.

Neden 22:15 kalıyor ve sabaha kaydırılmıyor: kullanıcı 00:00'a kadar
uyanık ve ABD kapanışı günün en yoğun bilgi anı. 08:00 koşusu gecenin
Asya ve kripto kısmını + 22:15'ten sonra gelen K1 dosyalamalarını alır;
22:15'in tekrarı değildir.

---

## 2. Neden her koşuda panel — ve bunun bedeli

Kullanıcı talebi açık. Ama üç sonucu var ve üçü de tasarımda karşılanmalı:

**2.1 Bütçe — İKİ AYRI EKSEN, biri kabul edildi biri BLOKE EDİYOR.**

*(a) Panel bütçesi: doğrulandı.* Panel iki sahiple ~8,5 dk ve abonelik
kullanımı. 2026-08-18 canlı koşusunda yeniden ölçüldü ve tuttu:

```
panel_runs: ali    ajanlar 20:41:35 → hakem 20:43:29   ≈ 4,0 dk
            yuksel ajanlar 20:46:05 → hakem 20:48:11   ≈ 4,7 dk
```

Dört koşu = günde ~34 dk panel, kullanım ×4. Kabul edildi. Tek koruma:
her kipin **kendi panel bütçesi** var (`panel_butce_sn`), bütçe dolunca
kalan sahip atlanır ve **ona bildirilir** — mevcut iki kademeli koruma,
kip başına parametreleşiyor.

*(b) Toplama bütçesi: bu belgenin dayandığı ölçüm ÖLÜ.* §3.1'in
`nabiz: kabuk_butce_sn: 2700` değeri "toplama zinciri 7,8 dk"
ölçümünden (2026-08-16) türetildi. 2026-08-19 gecesi tekrar ölçüldü:

```
22:15:00  pulse basladi
22:38–22:53  [tuik] zaman asimi ×4 (timeout_sn: 300)
22:59:24  nabiz basladi (223 piyasa sinyali)
22:59:27  panel ajanlari acildi
23:00:00  [run_pulse] 2700 sn asildi, olduruluyor   ← SIGTERM

  tuik          1225,9 sn   ← 20,4 dk, tek basina butcenin %45'i
  isyatirim      790,8 sn   ← kendi ic butcesi 780
  stocknews      163,7 sn
  cgfiyat        146,6 sn
  midasbilanco   135,3 sn
  digerleri      ~170  sn
  ────────────────────────
  TOPLAMA       2632  sn = 43,9 dk   (kabuk butcesi 2700 sn = 45 dk)
```

Panel 33 saniye yaşadı, bildirim gitmedi, koşu izi yazılmadı. **Toplama
tek başına bütçenin %98'ini yiyor; 5,6 kat sapma.**

`tuik.tazelik_saat: 24` yüzünden bu her gece değil, ~iki gecede bir
tekrarlar (18 Ağu'da tuik 3,4 sn'ydi — atlamıştı; 19 Ağu'da 1226 sn).
Yani ariza ARALIKLI, ve aralıklı arıza en geç fark edilendir.

**Bu düzeltilmeden dördüncü koşu eklenmez.** Gerekçe aritmetik değil
mantıksal: bugün kaybedilen bir koşu var; ritim v2 onu kurtarmıyor,
kaybı dörde katlıyor. Somut iş (ayrıntı §3.0):

- `tuik`'i nabız zincirinden çıkar — AYLIK yayın yapan bir kaynak,
  gecelik zincirde işi yok. Kendi plist'i ya da haftalık kip.
- Ya da `tuik.timeout_sn`'i düşür ve deneme sayısını 1'e indir;
  20 dakikanın neredeyse tamamı **bekleme**, veri değil — log'da dört
  ayrı `zaman asimi (deneme 1/2)` ve aralarında ~5'er dakika
  (22:38:25 → 22:43:39 → 22:48:43 → 22:53:45).
- `kabuk_butce_sn` her kip için ÖLÇÜMDEN sonra yazılır (§4).

**2.2 Tekrar.** Dört panel aynı günde aynı kağıt hakkında aynı cümleyi dört
kez kurabilir. Bunu üç yerde kesmek gerekiyor (üçüncüsü, ilk sürümde
yanlış teşhis edilmiş olan defter ayağı):

- **Hakem girdisi:** hakem promptuna, aynı sahibin **bugünkü önceki
  koşularının SADE katmanı** eklenir (en fazla üç; `panel_runs` WHERE
  `ajan='hakem' AND sahip=? AND date(run_ts)=today`). Talimat:
  *"Bunlar bugün daha önce söylendi. Aynı şeyi tekrarlama; ne DEĞİŞTİ onu
  söyle. Değişen bir şey yoksa bunu bir cümlede söyle."* Bu, P0-4'ün
  (geçmiş araçları) ucuz bir öncüsü ve "Düne Göre Ne Değişti"nin gün içi
  sürümü. İkinci bir LLM çağrısı yok, yalnızca prompt bağlamı.
- **Defter — İLK TEŞHİS TERSTİ, düzeltildi (2026-08-19).** Bu belge
  "dört koşu = aynı enstrüman-güne dört hakem tahmini" diyordu ve
  kümelenmeden korkuyordu. Şema öyle çalışmıyor:

  ```
  journal.py:103   ts = _bugun()        # "2026-08-19" — TARİH, damga DEĞİL
  UNIQUE (olusma_ts, instrument_id, ufuk_gun, ajan, sahip)
  ON CONFLICT DO UPDATE SET yon=…, guven=…, gerekce=…, signal_id=…,
                            tez=…, gecersizlesme_kosulu=…, izlenecek_esik=…
  ```

  Dört koşu dört satır **yazmaz — aynı satırı dört kez EZER.** Gerçek
  risk kümelenme değil, **sessiz üzerine yazma**: 22:15 paneli 08:00'in
  tezini, gerekçesini ve başlangıç fiyatını iz bırakmadan siler.
  "Sabah ne demiştin" sorusunun cevabı kalmaz.

  Ve bir yan etki ölçülebilir bir arızaya dönüyor:

  > `ON CONFLICT` `gecersizlesme_kosulu`'nu yeniliyor ama
  > `tez_bozuldu_ts`'i **temizlemiyor** (journal.py:152-158).
  > `tez_kontrol` ise `tez_bozuldu_ts IS NULL` süzüyor (journal.py:377).
  > Zincir: 08:00 tezi yazar → 12:30'da bozulur, alarm gider, damga
  > yazılır → 17:45 **aynı satıra yeni bir koşul** yazar → o koşul
  > günün geri kalanında **hiç kontrol edilmez.** Bugün tek panel
  > koşusu olduğu için bu senaryo İMKÂNSIZ; dört koşuda KAÇINILMAZ.

  Doğru çözüm ikisinden biri, ritim açılmadan seçilir:
  **(a)** `olusma_ts`'i tam damgaya çevir (koşu başına ayrı satır,
  geçmiş korunur, `bagimsiz_kume` gerçekten iş görmeye başlar — ama
  `puanla()` bar sayımı ve `karne()` tarih karşılaştırmaları da göç
  ister, DDL geri sarılmaz: `goc-kaliplari` altı maddesi geçerli); ya da
  **(b)** aynı gün ikinci kez yazmayı BİLEREK engelle ve `ON CONFLICT`'i
  `DO NOTHING`'e çevir — günün ilk paneli kazanır, sonrakiler yalnızca
  `panel_runs`'a yazar. Ucuz, geri alınabilir, ve "en erken tahmin en
  dürüst tahmindir" ilkesiyle tutarlı. **Tercih (b).**

  Hangisi seçilirse seçilsin `tez_bozuldu_ts` davranışı açıkça yazılır
  ve test edilir; sessiz bırakılmaz.

- **Karne kümelenme testi — ZATEN GEÇMİYOR, sebebi dört koşu değil.**
  `journal.py:326` "hakem enstrüman-gün başına TEK çağrı verdiği için
  `olcum` ile `bagimsiz_kume` eşit olmalı" diyor. Canlı veri:

  ```
  16 Ağu, ali:  iid 222 → ufuk (5, 20)   n=2
                iid 225 → ufuk (5, 20)   n=2
                iid 231 → ufuk (5, 60)   n=2
  ```

  Sebep hakemin **değişken `ufuk_gun`** vermesi; `ufuk_gun` benzersizliğin
  parçası olduğu için aynı gün aynı enstrümana iki satır girebiliyor.
  Yani bu bir ritim kusuru değil, bugün açık duran bir kusur. Belgenin
  istediği test doğru testtir ama **ritimden bağımsız olarak** yazılır ve
  kırmızı başlar. Ayrıca ölçüm şu an sentetik olmak zorunda:
  `karne(ali)` ve `karne(yuksel)` bugün `olcum=0` döndürüyor — ilk
  puanlama henüz gelmedi.

**2.3 Sessizlik kuralı değişiyor — bilerek.** İŞ 2 §2.7 "bir şey olmadı
mesajı gönderme" diyordu. Kullanıcı günde dört mesaj istiyor; bu bir
**özet (digest)** sınıfıdır, alarm sınıfı değil. İki sınıf ayrılır:

- **Özet** (kip başına bir mesaj, sahip başına): her zaman gider. İskeleti
  deterministik, içi panelden.
- **Alarm** (tez bozuldu, risk eşiği): `tez_bozuldu_ts` ve
  `bildirim_durumu` bastırması **aynen** — koşu sayısı dörde çıkınca bu
  iki tablo tam da bu yüzden daha kritik.

Özet mesajının iskeleti (her koşuda aynı sıra, boş bölüm atlanır):

```
⏱ 17:45 · Kapanış                        ← kip etiketi + Amsterdam saati
📊 Portföy: +%0,4 (EUR) · en çok: ASML +1,8 · en az: ROSE −2,1   ← deterministik, 08:00'den beri
🌍 Makro: gram altın 4.512 ₺ +0,3 · Brent 68,4 $ −1,1 · USDTRY 47,9 +0,1   ← deterministik, MAKRO serilerinden
🔔 Tez/Risk: (varsa, mevcut metinler)
🧠 Panel: (hakem SADE; "bugün önceki koşudan değişen yok" da geçerli çıktı)
🔍 Teknik detay  [buton, mevcut det:<id>]
```

"Panel: değişen yok" bir **cümledir**, sessizlik değildir — dört mesaj
sözü tutulur, gürültü üretilmez. Alarm bölümü boşsa satır yok.

---

## 3. Nasıl — ayara taşıma, koda değil

### 3.0 ÖN KOŞUL: gecelik zinciri bütçeye sığdır

§2.1(b) ölçümünün karşılığı. Bu madde bitmeden §3.1'e geçilmez.

**TUIK.** `sources.tuik` AYLIK yayın yapan bir kaynak (`tazelik_saat: 24`
zaten bunu biliyor) ama gecelik zincirin içinde duruyor ve zaman aşımına
girdiğinde 20 dakika yiyor. Bunun 25 dakikası **bekleme**, veri değil:
`timeout_sn: 300` × 5 deneme. İki seçenek:

- **(a) tercih:** `tuik`'i `run_pulse.sh` zincirinden çıkar, haftalık
  kendi plist'ine al (`com.alipala.finagent.tuik`, Pazar). Aylık veri
  için haftalık tazeleme fazlasıyla sık. Nabız zinciri 20 dk kısalır.
- (b) `timeout_sn`'i 90'a, denemeyi 1'e indir. Kazanç var ama üst sınır
  hâlâ 3×90 = 4,5 dk ve TUIK yavaşladığında geri gelir.

**İş Yatırım.** 790,8 sn ölçüldü ve `azami_sure_sn: 780`'e dayandı — yani
iç bütçe DOĞRU çalışıyor, kesip `partial` dönüyor. Dokunma. Ama v2'de
`isyatirim` artık `kapanis` kipinde; `kapanis` kabuk bütçesi bu 780'i
**ve** panelin 8,5 dakikasını birlikte kaldırmalı (§3.1 kuralı).

**Ölçüm zorunlu.** Zincir kısaldıktan sonra her kip bir kez elle
koşturulur (`--no-notify`), duvar saati ölçülür, `kabuk_butce_sn` o
ölçümün en az 1,5 katı yazılır ve **ölçüm README'ye geçer** (§4). Bugünkü
2700 gibi tahmine dayalı bir sayı bir daha yazılmaz — bu belgenin ilk
sürümünün düştüğü hata tam olarak oydu.

**Gözetim aynı adımda açılır.** 19 Ağustos gecesi koşu öldü, bildirim
gitmedi, iz yazılmadı — ve bekçi *hiçbir şey söylemedi*:

```
kacirilan_nabiz   -> None     # collector_runs dolu, "kostu" saniyor
kacirilan_kosular -> []       # IZ_KIPLERI = {"sabah", "ogle"} — nabiz YOK
```

`kacirilan_nabiz` "koşu **başladı** mı"yı ölçüyor, "koşu **bitti** mi"yi
değil. Tek kesin kanıt koşunun kendi izi ve nabız o kapının dışında.
§3.5'teki `pulse` → `nabiz` yeniden etiketlemesi + `IZ_KIPLERI`'nin
ayardan üretilmesi bunu kapatıyor; **o madde buraya, en öne alındı**
(bkz. §6). Dördüncü koşuyu eklemeden önce üçüncüsünün sessizce
öldüğünü görebiliyor olmak gerekir.

### 3.1 `settings.yaml` — yeni bölüm

```yaml
ritim:
  # Kip adı plist etiketinin son parçasıyla AYNI olmalı
  # (com.alipala.finagent.<kip>). Bekçi plist'ten okur; burada saat YOK.
  #
  # DİKKAT — aşağıdaki `kabuk_butce_sn` değerleri TAHMİNDİR ve şu hâliyle
  # YAZILMAZ. §3.0 bitip her kip bir kez ölçüldükten sonra, ölçümün 1,5
  # katı olarak doldurulur. Bu belgenin ilk sürümü 2700'ü ölçmeden yazdı
  # ve 19 Ağustos gecesi koşu tam o sayıda öldü.
    sabah:
      kaynaklar: [prices, makro, binance]
      panel: true
      panel_butce_sn: 900
      kabuk_butce_sn: 1500
      alicilar: [ali, yuksel]
    ogle:
      kaynaklar: [kap, makro, binance]
      panel: true
      panel_butce_sn: 600
      kabuk_butce_sn: 1200
      alicilar: [ali, yuksel]
    kapanis:
      kaynaklar: [isyatirim, midas, prices, makro, takvim, kap]
      panel: true
      panel_butce_sn: 900
      kabuk_butce_sn: 2100
      alicilar: [ali, yuksel]
    nabiz:
      # `tam_zincir` = mevcut run_pulse.sh zinciri EKSİ tuik (§3.0).
      # 19 Ağu ölçümü: tuik tek başına 1225,9 sn; çıkarılınca zincir
      # ~24 dk'ya iner. Yine de ÖLÇÜLECEK, bu satır tahmindir.
      kaynaklar: [tam_zincir]
      panel: true
      panel_butce_sn: 1800
      kabuk_butce_sn: 2700          # ÖLÇÜLMEDİ — §3.0 sonrası doldurulacak
      alicilar: [ali, yuksel]
```

Kurallar (projenin mevcut ilkeleri, tekrar):

- **Varsayılan yok.** Bilinmeyen kip → `ValueError("tanımsız kip: …")`,
  sessizce "nabiz" kabul edilmez. Eksik alan → hata, `DEFAULT` yok.
- `alicilar` içinde `telegram.sahipler`'de olmayan bir ad → hata. Boş
  liste → hata (koşup kimseye göndermemek, hiç koşmamaktan kötü — mevcut
  `_sahibe_bildir` gerekçesi).
- `kabuk_butce_sn` > `isyatirim.azami_sure_sn` + 240 **ve** >
  `panel_butce_sn` + ölçülen toplama süresi. Mevcut test
  (`HAFIF_TIMEOUT`) bu ayara bağlanır; plist'teki `HAFIF_TIMEOUT`
  **kalkar**, tek kaynak settings.

### 3.2 `run.py nabiz` — panel kararı ayardan

Şu an:
```python
hafif = args.kip in ("sabah", "ogle")        # koda gömülü
panel = not (args.no_panel or hafif)
```
Olacak:
```python
kip_ayar = settings.ritim_kip(args.kip)      # bilinmeyen → raise
panel = kip_ayar["panel"] and not args.no_panel
```
`--no-panel` bayrağı **kalır** (elle LLM'siz koşu hâlâ gerekir).

### 3.3 `Nabiz.calistir(kip=…)` — üç ek, hiçbiri yapısal değil

1. Sahip döngüsü `kip_ayar["alicilar"]` üzerinden döner (şu an tüm
   `sahip_listesi`). Alıcı olmayan sahip için **hiçbir şey** koşmaz —
   tez kontrolü de dahil; o sahibin tez kontrolü bir sonraki kipinde.
2. Panel bütçesi `kip_ayar["panel_butce_sn"]`; mevcut "bütçe dolunca
   kalan sahibi atla ve bildir" davranışı aynen.
3. Hakem promptuna günün önceki SADE katmanları (§2.2). Üretim yeri:
   `Panel.calistir()` hakem aşamasında, sahip bazlı sorgu. Metin 3×600
   karakterle kırpılır; kırpma yapıldıysa prompta yazılır ("…kısaltıldı").

Özet mesajı: `_gonder()` iskeleti §2'deki sıraya getirilir. Portföy
değişimi için `db.portfoy(sahip)` son iki snapshot farkı — **yeni hesap
yok**, var olan `portfoy` aracının döndürdüğü sayılar. Snapshot yoksa
satır atlanır, uydurulmaz.

### 3.4 Kabuk — tek betik, kip adıyla

`run_hafif.sh` adı artık yanlış (hafif değil). İki seçenek:

- **(a) tercih:** `git mv scripts/run_hafif.sh scripts/run_kosu.sh`;
  `KAYNAKLAR` ve `AZAMI_SN` değerleri `.venv/bin/python -c
  "from finagent.config import load_settings as L; k=L().ritim_kip('$KIP');
  print(' '.join(k['kaynaklar']), k['kabuk_butce_sn'])"` ile ayardan
  okunur. `run_pulse.sh` **dokunulmaz** (tam zincir, kendi kilidi,
  kendi bekçisi — çalışıyor, ölçülmüş). Plist'ler `run_kosu.sh <kip>`'e
  işaret eder. Testlerdeki `run_hafif.sh` yolu güncellenir.
- (b) adı bırak, başlığa "ARTIK PANEL DE KOŞABİLİR, ad tarihsel" yaz.
  Yapma — beyan/gerçek ayrışması bu projenin tekrar eden kusur sınıfı.

Kilit dosyası kip başına (`data/kosu_<kip>.lock`) — mevcut kalıp.

### 3.5 launchd

Dört plist, hafta içi (Weekday 1-5):

```
com.alipala.finagent.sabah    08:00
com.alipala.finagent.ogle     12:30
com.alipala.finagent.kapanis  17:45   ← yeni
com.alipala.finagent.nabiz    22:15   ← mevcut "pulse" yeniden etiketlenir
```

`pulse` → `nabiz` yeniden etiketleme bekçi için gerekli: `IZ_KIPLERI`
sözlüğü label'ın son parçasını kip sanıyor. Etiketi değiştirmek istemezsen
`IZ_KIPLERI["pulse"] = "nabiz"` eşlemesi ekle — ama o zaman iki ad tek şeyi
gösterir; yeniden etiketle. `launchd_install.sh`/`_uninstall.sh` etiket
listesine `kapanis` eklenir, eski `pulse` unload edilir (kurulum
betiğinde **açık** adım, yoksa iki iş 22:15'te çakışır ve kilit birini
düşürür — sessiz kayıp).

`IZ_KIPLERI` elle yazılmaz: `settings.ritim.kipler.keys()`'ten üretilir.
Bu **§3.0'a çekildi ve öne alındı** (§6 Faz A3): bugün `IZ_KIPLERI`
yalnızca `{"sabah", "ogle"}` ve nabız iz-tabanlı gözcünün dışında —
19 Ağustos gecesi koşu öldüğünde bekçi hiçbir şey söylemedi.

Bekçi gecikme payı 90 dk aynen. `kapanis` için payın yeterli olup
olmadığı **ölçüldükten sonra** söylenecek; ilk sürümdeki "35 dk olduğundan
pay yeterli" cümlesi ölçülmemiş bir sayıya dayanıyordu (§2.1b).

### 3.6 Makro satırı ve alınabilir vekil

**Veri zaten var**, yeni collector yok: `makro` collector'ı ALTIN_VADELI
(GC=F), PAXG'den türetilen ALTIN_GRAM, BRENT, WTI, GUMUS, BAKIR, TTF,
USDTRY/EURUSD/EURTRY, DXY, US10Y, VIX serilerini `venue='MAKRO'` altında
tutuyor ve dört kipin dördünde `makro` kaynak listesinde. İki ek:

**(a) Özet mesajına makro satırı — LLM'siz.** Ayarda hangi kodların
görüneceği:

```yaml
ritim:
  ozet_makro: [ALTIN_GRAM, BRENT, USDTRY]    # boş liste = satır yok
```

Değişim, kipin **önceki koşusundan** değil **önceki günün kapanışından**
(`date(ts) < today` son bar) — aksi halde 12:30 satırı 08:00'e göre %0,0
gösterir ve bilgi taşımaz. Seri yoksa ya da son bar 2 günden eskiyse
satır "ALTIN_GRAM: veri bayat (16 Ağu)" der, sayı göstermez. Para birimi
enstrümandan (`instruments.currency`), elle yazılmaz. `_MAKRO_UYARI`
burada da geçerli: bu satır **yorum değil**, üç sayıdır; "altın
yükselişte" gibi bir sıfat yazılmaz.

**(b) `MAKRO → alınabilir vekil` eşlemesi.** Brent'e ya da spot altına
doğrudan pozisyon alınamaz; BUX'ta ETC, Midas'ta ABD ETF'i ya da TEFAS
fonu alınır. Panel "Brent düştü" dediğinde yanında kullanıcının
borsasında karşılığı olan enstrüman dursun ki "etkileşime girmek"
mümkün olsun. Yeni tablo, `instruments`'a kolon **değil** (bir makro
kodun birden çok vekili var):

```sql
CREATE TABLE IF NOT EXISTS makro_vekil (
    makro_kod     TEXT NOT NULL,               -- BRENT, ALTIN_ONS ...
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    venue         TEXT NOT NULL,               -- BUX | MIDAS | BINANCE
    not_          TEXT,                        -- "vadeli yapılı, contango kayması"
    PRIMARY KEY (makro_kod, instrument_id)
);
```

Doldurma **elle ve doğrulanarak** (ISIN'den, `IdentityResolver` ile);
otomatik isim eşlemesi yok — "Gold" geçen her ETF altın ETC'si değil.
İlk sürüm: BUX katalogundaki altın/gümüş/petrol ETC'leri + Midas'ta
alınabilen GLD/SLV/USO/BNO benzeri ETF'ler + PAXG (Binance, zaten
katalogda). Panel ajanlarına araç olarak `vekil(makro_kod)` verilir;
`alinabilir: false` mantığı aynen: vekil yoksa "bu borsada karşılığı yok"
denir, uydurulmaz. Vekil için al/sat önerisi **yine yok** — mevcut kural;
vekil yalnızca "nereye bakacaksın" sorusunun cevabı.

### 3.7 Sohbet yolu — dokunulmuyor, kanıtlanıyor

Bu belge `bot/listener.py`, `chat.py`, `run.py bot` ve onların
promptlarına **hiç** dokunmuyor. `/rapor`, `/ozet`, `/evren`, `/aday`,
ekran görüntüsü akışı, `maruziyet`/`iliski`/`karsilastir` araçları aynen.
Paylaşılan iki kaynak (SQLite, abonelik) bugün de paylaşılıyor; değişen
tek şey panelin günde ~8 dk yerine ~34 dk açık olması. Kabul kriterine
giriyor (§4): sohbet duman testleri dokunulmadan geçiyor; hakem promptuna
eklenen "günün önceki koşuları" bağlamı `chat.py` SYSTEM_PROMPT'unda
**yok** (grep ile test). Panel ile sohbet aynı anda LLM çağırdığında
abonelik sınırı yenirse panel atlanır ve bildirilir (mevcut bütçe yolu);
sohbet önceliklidir çünkü arkasında bekleyen bir insan var.

Hafta sonu: kripto 7/24 ama dört plist hafta içi. Cumartesi/Pazar için
yalnızca `sabah` kipinin `binance` + kripto tez kontrolü koşması
isteniyorsa **ayrı karar**, bu belgede yok. Şimdilik hafta sonu sessiz;
bekçi hafta sonu "kaçırıldı" demez (plist'te gün yok).

---

## 4. Kabul kriteri

- `run.py nabiz --kip <bilinmeyen>` açık hatayla düşüyor; `--kip ogle`
  panel koşturuyor (log'da `claude_agent_sdk` izi **var**); `--no-panel`
  ile koşturmuyor.
- `alicilar: [ali]` ile `yuksel` için hiçbir satır yazılmıyor
  (`panel_runs`, `predictions`, `bildirim_durumu`) ve mesaj gitmiyor.
- **Defter (§2.2).** Dört kip art arda koşulduğunda (`--no-notify`) aynı
  sahip için `panel_runs`'ta **dört** hakem satırı var, `predictions`'ta
  aynı (enstrüman, gün, ufuk, ajan) için **tek** satır var ve o satır
  **günün İLK koşusunun** değerlerini taşıyor (`DO NOTHING` seçildiyse).
  İkinci koşu satırı ezmiş olamaz — `tez`, `gerekce`, `baslangic_fiyat`
  karşılaştırılarak test edilir.
- **`tez_bozuldu_ts` sızıntısı (§2.2).** Senaryo testi: 1. koşu tez yazar
  → koşul tetiklenir, `tez_bozuldu_ts` dolar → 2. koşu aynı enstrümana
  yeni koşul üretir → **yeni koşul kontrol edilebilir durumda olmalı**
  (ya yeni satır, ya damga temizlenmiş, ya da yazma hiç kabul edilmemiş).
  Üç davranıştan hangisi seçildiyse test onu kanıtlar; "sessizce hiç
  kontrol edilmiyor" hâli düşürür.
- **Kümelenme (§2.2, ritimden BAĞIMSIZ).** `olcum == bagimsiz_kume`
  testi yazılır ve **bugün kırmızı başlar** (16 Ağu: iid 222/225/231 aynı
  gün iki ufukla). Yeşile dönmesi, hakemin enstrüman-gün başına tek ufuk
  vermesi (ya da `karne`'nin ufku kümeye katması) ile olur. Bu madde
  ritim işini BLOKE ETMEZ ama ritim işi de bunu ÇÖZDÜ sayılmaz.
- Hakem promptunda günün önceki SADE metinleri görünüyor (sahte
  `panel_runs` satırıyla test; sahipler çapraz sızmıyor — `sahip=?`).
- Özet mesajı her koşuda gidiyor; "Panel: değişen yok" durumu bir
  satır, boş mesaj değil. Alarm bölümü `bildirim_durumu` ve
  `tez_bozuldu_ts` ile bastırılıyor (mevcut testler geçmeye devam).
- `settings.ritim.kipler` anahtarları ↔ `launchd/*.plist` etiketleri
  birebir eşleşiyor — test (bekçinin plist okuyucusu kullanılarak; biri
  fazla/eksikse düş).
- **Bütçe (§2.1b, §3.0).** Her kipin duvar saati **ölçülmüş** ve
  `kabuk_butce_sn` o ölçümün en az 1,5 katı; ölçüm README'ye yazılmış
  (tahmin değil). Ayrıca: `tuik` gecelik zincirde **değil** — grep testi,
  çünkü tek bir yavaş collector bütçenin yarısını yiyebiliyor ve bu bir
  kez ölçülüp unutulacak bir şey değil.
- **Bütçe aşımı SESSİZ OLMASIN (yeni).** 19 Ağu gecesi `run_pulse.sh`
  süreci SIGTERM'ledi, log'a bir satır yazdı ve **kimseye haber
  vermedi** — `bildir()` yalnızca çıkış kodu sıfırdan farklıysa
  çağrılıyor, oysa süreç grubu öldürüldüğünde o yola hiç gelinmiyor.
  Kabul: bütçe aşımı Telegram'a düşüyor, testi var.
- **Nabız gözetimi (yeni, §3.0).** `IZ_KIPLERI` ayardan üretiliyor ve
  `nabiz` içeriyor; nabız izi yazılmadığında `kacirilan_kosular()` onu
  **döndürüyor** — 19 Ağu durumu sahte iz dizini ile kurulup test edilir
  (bugün `[]` dönüyor, yani test kırmızı başlar).
- Dört plist yüklü, `launchctl print` ile saatler Amsterdam'da
  doğrulanmış; `pulse` etiketi kalmamış.
- Makro satırı: `ozet_makro` boşsa satır yok; seri bayatsa sayı yerine
  "veri bayat (tarih)"; değişim önceki **gün** kapanışına göre — test
  (sahte MAKRO serisiyle, 12:30 ve 08:00 koşusu aynı değişimi gösteriyor).
- `makro_vekil`: bilinmeyen `makro_kod` → hata; vekilsiz kod için araç
  "karşılığı yok" döndürüyor, boş liste değil; `alinabilir: false`
  enstrüman vekil olarak yazılamıyor — test.
- Sohbet: mevcut sohbet duman testleri dokunulmadan geçiyor;
  `chat.py`'de "onceki kosu"/"bugun daha once" dizisi **yok** (grep testi);
  `/rapor` çıktısı ritim öncesiyle aynı şablonda.

---

## 5. Tuzaklar

- **Bütçeyi ölçmeden yazma.** Bu belgenin ilk sürümü tam olarak bunu
  yaptı: 2700 sn'yi üç gün önceki bir ölçümden devraldı, ve 19 Ağustos
  gecesi koşu o sayıda öldü. Ölçülmemiş her `_butce_sn` değeri, ileride
  sessizce kaybedilecek bir koşudur.
- **Tek yavaş collector bütün koşuyu düşürür.** `tuik` 20 dakikayı
  *beklemeyle* geçirdi, veri çekmeyle değil. Zincire kaynak eklerken
  soru "faydalı mı" değil, "en kötü hâlinde ne kadar sürer".
- **Aynı gün ikinci panelin deftere yazması masum değil.** `olusma_ts`
  TARİH; ikinci yazma birinciyi EZER (§2.2). "Nasılsa üstüne yazar,
  sonuncusu en günceli" diye düşünme — sabahki tez kaybolur ve
  `tez_bozuldu_ts` yüzünden yenisi hiç kontrol edilmez.
- **`kip` adını kodda listeleme.** `("sabah","ogle")` gibi bir demet
  kaldıysa bu belge uygulanmamış demektir.
- **Hakeme önceki koşuyu verirken ajanlara verme.** Ajanlar birbirini
  görmediği gibi geçmişi de görmesin; bağımsızlık ajan katmanında, sentez
  hakemde.
- **Özet mesajına "sessizlik" dönmesin.** Kullanıcı dört mesaj bekliyor;
  gitmeyen mesaj bekçiye "koşmadı" gibi görünür ve kullanıcıya sistem
  bozuk gibi görünür.
- **Alarmı özete eritme.** Tez bozulması özetin içinde bir satır
  olabilir ama bastırma tabloları aynen yazılmalı; aksi halde 17:45'te
  bildirilen tez 22:15'te yine bildirilir.
- **17:45 bayat Avrupa kapanışı.** Fiyat sağlayıcısı Euronext kapanışını
  17:45'te vermiyorsa koşu önceki günü analiz eder ve bunu söylemez.
  İlk hafta `prices` collector'ının son bar tarihini logla; gecikme
  varsa plist'i 18:15'e al — saat ayardadır, kodda değil.
- **DST.** ABD ve Avrupa yaz saati geçişleri iki-üç hafta ayrışır;
  o haftalarda ABD kapanışı 21:00 CET olur, 22:15 yine sonra. 17:45
  Euronext'e bağlı, ABD'ye değil — etkilenmez. Bir şey yapma, bil.
- **Yüksel'in ritmi farklıysa** ikinci bir plist seti değil, `alicilar`
  listesi. Aynı koşu, farklı alıcı kümesi.

---

## 6. Sıra — REVİZE (2026-08-19)

İlk sürümün sırası "ayarı kur, koşuları çoğalt, sonra ölç" diyordu.
Ölçüm önce yapıldı ve sırayı değiştirdi: **bugün üçüncü koşu kaybediliyor
ve kimse görmüyor.** Dördüncüyü eklemeden önce bu ikisi kapanır.

### Faz A — dördüncü koşudan ÖNCE (bloke edici)

```
A1  tuik'i gecelik zincirden cikar (§3.0)              ~30 dk
    → haftalik kendi plist'i; nabiz zinciri 20 dk kisalir
    → grep testi: run_pulse.sh icinde "tuik" YOK

A2  butce asimi Telegram'a dussun (§4)                 ~45 dk
    → run_pulse.sh bekcisi oldururken bildir() cagirsin
    → bugun sessiz: 19 Agu SIGTERM'i loga yazdi, kimseye gitmedi

A3  pulse → nabiz etiketi + IZ_KIPLERI ayardan (§3.5)  ~1 saat
    → kacirilan_kosular() nabzi da yargilasin
    → test bugun KIRMIZI baslar ([] donuyor)

A4  her kipin duvar saatini olc, README'ye yaz         1 gece
    → A1 sonrasi tek elle kosu, --no-notify
```

Faz A tek başına değerli: v2 hiç uygulanmasa bile bugün kaybedilen
koşuyu kurtarır ve bir daha kaybolursa **haber verir**.

### Faz B — defter, ritimden bağımsız (§2.2)

```
B1  predictions ayni-gun ikinci yazma karari           ~1 saat
    → tercih (b): ON CONFLICT DO NOTHING, gunun ILK paneli kazanir
    → tez_bozuldu_ts davranisi ACIKCA yazilir ve test edilir
B2  olcum == bagimsiz_kume testi (kirmizi baslar)      ~30 dk
    → hakem ufuk_gun tutarliligi ya da karne'nin ufku kumeye katmasi
```

B ritmi beklemez, ama **ritim B'siz açılmaz**: dört panel koşusu B1
olmadan sabahki tezi her akşam siler.

### Faz C — ritmin kendisi (ilk sürümün 1-8'i, sırası korundu)

```
C1  settings.ritim + ritim_kip() + hata yollari + test  bagimsiz
C2  run.py panel karari + Nabiz alici dongusu + butce   C1'e bagli
C3  hakem prompt: gunun onceki SADE'leri + sizinti testi bagimsiz
C4  ozet iskeleti (_gonder) + alarm/ozet ayrimi testi    C2'ye bagli
C5  run_kosu.sh + 4 plist + README saat/HAFIF_TIMEOUT    C1+A3'e bagli
C6  ozet makro satiri (ozet_makro) + bayatlik testi      C4'e bagli
C7  sohbet koruma testleri (grep + duman)                bagimsiz, 20 dk
C8  makro_vekil tablosu + vekil araci + elle doldurma    ISTEGE BAGLI
C9  bir hafta canli: sure olcumu, kabuk_butce_sn guncelle
```

C3 panel promptuna dokunuyor; İŞ 3'ün (sade/teknik) dosyasıyla aynı yer —
art arda yapılırsa çakışma yok. C8 ertelenebilir.

### Bu belgeye HENÜZ işlenmemiş bulgular

2026-08-19 incelemesi beş nokta buldu; ① ve ② yukarıya işlendi. Kalan üçü
**bilerek işlenmedi** ve karar bekliyor — belge bunlar hakkında hâlâ
gerçekte olmayan bir şey varsayıyor:

- **③ §2 ve §3.3'teki portföy satırı bugün dürüstçe hesaplanamaz.**
  `positions` tam anlık görüntü değil, PARÇALI: son dört snapshot
  sırasıyla 18 / 2 / 1 / 4 satır (ekran görüntüsü kanalından geliyor,
  her kare yalnızca görüneni taşıyor). "Son iki snapshot farkı" 4
  pozisyonu 1 pozisyonla karşılaştırır. Ayrıca `yuksel`'in **hiç
  pozisyonu yok** (0 satır) ama dört kipin dördünde de alıcı.
- **④ §1'in "K1/K2 haber" sözünün arkasında collector yok.** `news`
  (RSS) hiçbir zamanlanmış koşuda geçmiyor; son koşusu 2026-08-18 19:59
  ve elle tetiklenmiş. Türkiye makro akışı şu an bayat: AA-Ekonomi
  18 Ağu 16:43, Ekonomim 18 Ağu 19:29, BloombergHT **6 Ağu**.
  `gundem` aracı `haber_gun: 3` penceresiyle okuyor.
- **⑤ Panel yolunda bildirim disiplini yok.** Tazelik süzgeci
  (`_taze_sinyaller`), bastırma (`_yeni_sinyaller` → `bildirim_durumu`),
  seans satırı (`durum_satiri`) ve gruplama YALNIZCA `_hafif` dalında
  (runner.py:421-433, 704, 714). `panel: true` yapılan anda sabah ve
  öğle koşuları bu düzeltmelerin dışına çıkar. Yani C4 yeni bir özellik
  değil, **`_hafif_bildir`'in disiplinini `_gonder`'e taşımak** —
  ve §2.2'nin "hakeme kendini tekrarlama de" reçetesinden daha ucuz bir
  yol var: panele giden gündemi `_yeni_sinyaller`'den geçirmek
  (prompt değil, ölçülen bastırma).

Bittiğinde kapanış belgesindeki hat aynen devam eder:
İş Yatırım ölçümü → backtest güç analizi → P0-3 → P0-4. Faz A bir gün,
Faz B yarım gün, Faz C bir-iki gün.
