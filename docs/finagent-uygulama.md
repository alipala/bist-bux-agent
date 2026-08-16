# Uygulama belgesi — proaktif ritim, tez alarmı, sade dil

Kaynak: `finagent-kapanis.md` sonrası kullanıcı talebi.
Üç iş var, sırayla bağımsız uygulanabilir. Her birinde **ne**, **neden**,
**nasıl**, **kabul kriteri** ve **tuzaklar** ayrı yazılı.

Sıra değişikliği: **P2-10 (tez alarmı) öne alındı.** Kapanış belgesinde
P0-4'ten sonraydı; kullanıcının aradığı "al/sat'a yakın karar bildirimi"nin
doğru biçimi tam olarak bu ve şema zaten hazır. Yeni sıra:

```
İŞ 1  tez + bozulma alarmı        (P2-10, öne alındı)
İŞ 2  proaktif ritim               (yeni)
İŞ 3  sade dil + kademeli detay    (yeni)
─── sonra kapanış belgesindeki hat ───
İş Yatırım ölçümü → backtest → P0-3 → P0-4 → P1'ler
```

Ayrıca §0'da, `b031ab6` incelemesinden çıkan tek düzeltme var.

---

## 0. Önce: `_atilanlari_isle` kendi gerekçesini çürütüyor

`runner.py::_atilanlari_isle` docstring'i şöyle diyor:

> *"Yanlış dağıtılmış bir sayı, hiç yazılmamış bir sayıdan daha kötüdür."*

Sonra dört ajanın atılan sayaçlarını **koşunun ilk ajan satırına** yazıyor.
Yani `SELECT ajan, atilan_sembol_yok FROM panel_runs` sorgusu, dört ajanın
toplamını `olay` (ya da id'si en küçük hangi ajansa) satırında görür ve
onun sanır. Kaçınılmak istenen şeyin ta kendisi.

**Düzeltme — iki seçenek, birincisi tercih edilir:**

**(a) Kırılımı gerçekten üret.** `ajan` alanı düşürme anında sözlükte zaten
var (`Panel.calistir()` her görüşe `{**g, "ajan": ad}` ekliyor).
`Defter.kaydet()` raporunu `{ajan: {sebep: n}}` yapmak birkaç satır:

```python
rapor = {"yazilan": 0, "ajan_bazli": {}}
...
def _at(ajan, sebep):
    rapor["ajan_bazli"].setdefault(ajan, {
        "atilan_sembol_yok": 0, "atilan_seri_yok": 0, "atilan_cakisma": 0
    })[sebep] += 1
```

`atilan_sembol_yok` kontrolü `ajan` okunmadan önce yapılıyor ama sözlük
orada; `g.get("ajan") or "bilinmiyor"` yeterli.

**(b) Yapamıyorsan koşu satırına yaz.** `panel_runs`'a `ajan='_kosu'` diye
ek bir satır aç. O zaman sayı kimseye yanlış atfedilmez; "bu koşuda toplam
şu kadar görüş düştü" der ve doğru olur.

**Kabul kriteri:** hiçbir ajan satırı, kendisine ait olmayan bir atılan
sayısı taşımıyor. Test: iki farklı ajandan birer görüş düşür, `panel_runs`'ta
her ajanın kendi sayısını gör.

---

## İŞ 1 — Tez ve bozulma alarmı (P2-10)

### 1.1 Ne

Bir görüş üretildiğinde yalnızca "yön + güven + ufuk" değil, **hangi
koşulda geçersizleşeceği** de kaydedilir. Her gün deterministik olarak
kontrol edilir; koşul tetiklenirse kullanıcıya bildirim gider.

Kolonlar zaten var: `predictions.tez`, `gecersizlesme_kosulu`,
`izlenecek_esik`. Yazma ve kontrol mantığı yok.

### 1.2 Neden bu, al/sat sinyali değil

Al/sat sinyali bir **tahmindir** ve isabet oranı ölçülmeden dürüstçe
sunulamaz — şu an ölçülmüş isabet yok. Tez bozulması bir **koşul
kontrolüdür**: sistemin daha önce açıkça beyan ettiği bir eşiğin
gerçekleşip gerçekleşmediğini söyler. Tahmin içermediği için isabet oranı
bilinmeden de dürüst.

Kullanıcı açısından karara yakınlık aynı, epistemik yük çok daha düşük:

> 🔔 ASML tezi bozuldu. 14 Ağustos'ta *"SMA50 üstünde tutunuyor; 1.520 EUR
> altına inerse tez geçersiz"* denmişti. Bugünkü kapanış **1.511 EUR**.

Bu bir emir değil ama karar anını kullanıcıya getiriyor — üstelik onun
daha önce kabul ettiği bir eşikle.

### 1.3 Nasıl — A. Koşul dili

`gecersizlesme_kosulu` **makine-okunur** olmalı, serbest metin değil.
Serbest metin kabul edilirse alan dolar ama kontrol edilemez ve altı ay
sonra "bu kolonu kimse kullanmıyor" deriz.

Dar bir gramer tanımla ve **onun dışını reddet**:

```
<kosul> := <alan> <op> <sayi>
<alan>  := close | rsi14 | sma20 | sma50 | sma200 | hacim_kat | car_t
<op>    := "<" | ">"
<sayi>  := ondalık sayı (para birimi YOK, alanın kendi birimi)
```

Geçerli: `close < 1520`, `rsi14 > 75`, `hacim_kat < 0.8`
Geçersiz: `fiyat düşerse`, `close < 1520 EUR`, `close < sma50`

Alan-alan karşılaştırması (`close < sma50`) **ilk sürümde yok**. İkisi de
zamana göre değiştiği için "bozuldu" anı belirsizleşir ve yanlış alarm
üretir. Sabit eşik yeterli ve doğrulanabilir.

Ayrıştırıcı yeni dosyada: `src/finagent/pulse/tez.py`

```python
KOSUL = re.compile(r"^(close|rsi14|sma20|sma50|sma200|hacim_kat|car_t)"
                   r"\s*([<>])\s*(-?\d+(?:\.\d+)?)$")

def kosul_ayristir(metin: str) -> tuple[str, str, float] | None:
    """Gramere uymayan koşul KAYDEDILMEZ. Uydurulmuş bir koşul,
    hiç koşul olmamasından kötüdür: kontrol edilir, hep False döner,
    ve kullanıcı 'tez hâlâ geçerli' sanır."""
```

### 1.4 Nasıl — B. Prompt tarafı

`HAKEM` prompt'undaki JSON şeması `tez` ve `gecersizlesme_kosulu` alanlarını
zaten içeriyor. Üç şey eklenmeli:

1. **Gramer prompt'a yazılsın** — model hangi alanları kullanabileceğini
   bilmeli. Alan listesi `tez.py`'den **üretilsin**, elle yazılmasın
   (kapanış belgesi §4: beyan/gerçek ayrışması).
2. **Koşul zorunlu değil.** Model uygun bir eşik göremiyorsa `null`
   bıraksın. Zorunlu yapmak, uydurma eşik üretmeye iter.
3. **Ajanlar da tez üretsin mi?** Hayır — ilk sürümde yalnızca hakem.
   Gerekçe: tez kullanıcıya gönderilen çağrıya ait; ajan görüşleri iç
   girdi. Bu zaten mevcut davranış (`tez dolu 8` = hakem çağrıları).

### 1.5 Nasıl — C. Kontrol döngüsü

Yeni fonksiyon: `Defter.tez_kontrol() -> list[dict]`

```
her açık tahmin için (isabet IS NULL ve gecersizlesme_kosulu NOT NULL):
    koşulu ayrıştır → alan, op, eşik
    alanın GÜNCEL değerini al:
        close        → db.fiyat_serisi(iid, 1)[-1]["close"]
        rsi14, sma*  → analysis.technical_snapshot
        hacim_kat    → son hacim / 20 gün ortalaması
        car_t        → analysis.events son ölçüm
    koşul sağlanıyorsa → tetiklenen listesine ekle
```

Kritik ayrıntılar:

- **Gösterge motoru TEK.** `technical_snapshot` kullan; tarayıcının kendi
  RSI'ını hesaplamasından çıkan hatanın (MSFT 84.8 vs 70.9) tekrarı olmasın.
- **Fiyat kaynağı tek.** `db.fiyat_serisi()` üzerinden git, doğrudan
  `prices` sorgulama — para birimi karışır.
- **Bir kez tetiklenir.** Yeni kolon: `tez_bozuldu_ts TEXT`. Doluysa tekrar
  bildirim gönderme. Aksi halde eşiğin altında kalan bir kağıt her gün
  alarm üretir ve kullanıcı bildirimleri kapatır.
- **Tahmin ölçümü etkilenmez.** Tez bozulması `isabet`'i değiştirmez;
  tahmin ufku dolunca normal şekilde puanlanır. İki ayrı mekanizma.

Şema göçü (`user_version` artık var, kullan):

```sql
ALTER TABLE predictions ADD COLUMN tez_bozuldu_ts TEXT;
```

### 1.6 Kabul kriteri

- Gramere uymayan koşul kaydedilmiyor; reddedilme `panel_runs.hata`'ya yazılıyor
- Eşik geçilince **bir kez** bildirim gidiyor, ertesi gün gitmiyor
- Bildirim metni orijinal tezi ve tarihi içeriyor
- Tez bozulması tahmin puanlamasını değiştirmiyor
- Testler: (a) geçersiz gramer reddi, (b) tetikleme, (c) tekrar
  tetiklememe, (d) gösterge değerinin `technical_snapshot`'tan geldiği

### 1.7 Tuzaklar

- **Serbest metin koşul kabul etme.** Kontrol edilemeyen alan, ölü
  konfigürasyonun yeni biçimidir.
- **Koşulu zorunlu yapma.** Uydurma eşik, hiç eşik olmamasından kötü.
- **`close < 1520 EUR` gibi birimli girdi.** Ayrıştırıcı reddetsin;
  para birimi enstrümanın kendisinden gelir.

---

## İŞ 2 — Proaktif ritim

### 2.1 Ne

Günde tek koşu yerine üç koşu. **İkisi LLM'siz.**

| Saat (Europe/Istanbul) | Ad | İçerik | LLM |
|---|---|---|---|
| 09:30 | `sabah` | gece ABD/Asya + Avrupa açılışı; tarayıcı + tez kontrolü | ❌ |
| 18:00 | `ogle` | Avrupa + BIST kapanışı; tarayıcı + tez kontrolü | ❌ |
| 22:15 | `nabiz` | ABD kapanışı; **tam panel** (mevcut) | ✅ |

### 2.2 Neden ikisi LLM'siz

Üç tam panel = abonelik kullanımının üçe katlanması, karşılığında üç kat
bilgi yok. Daha önemlisi: sabah ve öğlen bildirimlerinin içeriği
**yoruma ihtiyaç duymuyor.** "ROSE günlük oynaklığının 2,8 katı düştü,
hacim teyitli, portföy ağırlığın %18" cümlesi deterministik ve tam.
Modelden geçirmek onu daha doğru yapmaz, yalnızca daha uzun yapar.

Bu, projenin kurucu ayrımının devamı: deterministik katman hesaplar,
LLM yorumlar. Yorumlanacak bir şey yoksa LLM çağrılmaz.

### 2.3 Nasıl

`Nabiz.calistir()` zaten `panel: bool` parametresi alıyor ve
`panel=False` durumunda sinyalleri döndürüp duruyor. Genişletilecek:

```python
def calistir(self, bildir=True, panel=True, kip="nabiz") -> dict:
    # kip: "sabah" | "ogle" | "nabiz"
```

Hafif kip (`panel=False`) şunları yapar:

1. `Defter.puanla()` — vadesi dolan tahminleri ölç (ucuz, LLM yok)
2. `Tarayici.tara()` + `kaydet()`
3. `Defter.tez_kontrol()` — İŞ 1
4. Bildirim **yalnızca** şu üçünden biri varsa:
   - tez bozuldu
   - portföy riski eşiği aşıldı (`yogunlasma`, `acik_zarar`)
   - portföydeki bir enstrümanda `guc >= BILDIRIM_ESIGI` sinyal

**Üçüncü madde kritik:** hafif koşularda yalnızca **sahip olunan**
enstrümanlar bildirim üretir. Sahip olunmayan bir kağıttaki hareket
akşam panelinde değerlendirilir. Gerekçe: sabah 09:30'da BIST'te bir
kağıdın hareket etmesi, üzerinde pozisyonun yoksa acil değil; portföyünde
bir şey olması acil.

Sessizlik kuralı aynen geçerli: kriter sağlanmıyorsa **hiç mesaj yok.**

### 2.4 Toplama

Hafif koşulardan önce ilgili collector'lar çalışmalı, ama tam zincir değil:

- **09:30** → `prices` (ABD/Avrupa kapanışları), `binance`
- **18:00** → `isyatirim`, `midas`, `prices`, `kap`
- **22:15** → mevcut tam zincir

Ölçülmüş süre 7,8 dk (tam zincir). Kısmi zincirler daha kısa olmalı ama
**ölçülmeli**; `ExitTimeOut` her kip için ayrı ayarlanmalı.

### 2.5 launchd

İki yeni user agent. `com.alipala.finagent.pulse` kalıbı kopyalanır:

- `com.alipala.finagent.sabah` — hafta içi 09:30
- `com.alipala.finagent.ogle` — hafta içi 18:00

`KeepAlive` yok, `RunAtLoad` yok (zamanlanmış iş zamanında koşar ve durur).
`ExitTimeOut` ölçülen süreye göre. **User agent olmalı, daemon değil** —
Claude Max aboneliği `~/.claude` profilinde; akşam koşusu için şart, sabah
ve öğlen için gerekmese de tutarlılık iyi.

`scripts/launchd_install.sh` ve `_uninstall.sh` etiket listesine eklenir.

### 2.6 Kabul kriteri

- Hafif koşuda `panel_runs`'a satır yazılmıyor, LLM çağrısı yok
  (log'da `claude_agent_sdk` izi bulunmamalı)
- Kriter sağlanmayan günde **hiç mesaj gitmiyor**
- Portföyde olmayan enstrüman hafif koşuda bildirim üretmiyor
- Üç kip de aynı `Tarayici` ve `technical_snapshot` çağrılarını kullanıyor
  (ikinci bir eşik tanımı doğmuyor)
- Her kipin duvar saati ölçülüp `ExitTimeOut`'a yansıtılmış

### 2.7 Tuzaklar

- **Hafif koşuda panel çalıştırma.** Bütçe üçe katlanır.
- **"Bir şey olmadı" mesajı gönderme.** Sessizlik geçerli çıktı.
- **Sabah koşusunu 09:00'dan önceye alma.** Avrupa açılışı 09:00 TSİ değil;
  BIST 10:00, Amsterdam 10:00 TSİ. 09:30'da elde olan şey gece ABD
  kapanışı ve Asya — bu yeterli ve kasıtlı.

---

## İŞ 3 — Sade dil ve kademeli detay

### 3.1 Ne

Kullanıcıya giden her mesaj iki katmanlı olur:

- **Sade katman** (varsayılan): 3-5 satır, terimsiz, doğrudan
- **Teknik katman** (istenirse): mevcut çıktının aynısı

Telegram'da sade katman gönderilir, altında `🔍 Teknik detay` butonu durur.
Sohbet katmanında varsayılan sade; "neden", "detay", "nasıl hesapladın"
gibi bir istek gelirse teknik katmana iner.

### 3.2 Nasıl — tek geçiş, iki katman

**Kritik karar: ikinci bir model çağrısı YOK.**

Sadeleştirmeyi ayrı bir LLM turuna vermek, ölçülen şey ile söylenen şey
arasında bir sürüklenme kanalı açar — bu döngünün ana temasının tam
karşılığı. Sade katman, teknik katmanı üreten **aynı cevapta** üretilir.

`HAKEM` prompt'una eklenecek çıktı yapısı:

```
### SADE
(3-5 satır. Terim yok. Kısaltma yok. Kullanıcı piyasa terimi bilmiyor
 varsayılır.)

### TEKNIK
(mevcut çıktı, aynen)

```json
{...}
```
```

Ayrıştırma `_json_cek` ile aynı kalıpta: başlık işaretine göre böl,
bölünemezse **tamamı teknik sayılır ve sade katman gönderilmez.**
Sessizce yarım mesaj göndermektense tam teknik mesaj gitsin.

### 3.3 EN ÖNEMLİ KURAL — sadeleştirme belirsizliği kaybetmemeli

Buradaki gerçek tehlike, terimlerin atılması değil. Sadeleştirme sırasında
model **belirsizlik ifadelerini de atma** eğilimindedir ve sonuç
olduğundan emin görünür:

| Teknik | ❌ Kötü sade | ✅ Doğru sade |
|---|---|---|
| RSI 78, hacim teyidi yok | "Aşırı alım, düzeltme gelebilir" | "Son dönemde hızlı yükselmiş. Bu tek başına bir şey söylemiyor — yükselişe katılan işlem hacmi düşük, yani harekete katılım zayıf." |
| CAR +%3,1, t=1,2 | "Haber fiyatı %3 yukarı itti" | "Haber gününde fiyat yükselmiş ama bu, normal dalgalanmadan ayırt edilemiyor." |
| Yoğunlaşma %31 | "Riskli, azalt" | "Paranın %31'i tek bir kağıtta. O kağıtta olan her şey portföyünün üçte birini doğrudan etkiliyor." |

Kötü örneklerin ortak yanı: teknik katmanda **olmayan** bir kesinlik
eklemeleri. "Düzeltme gelebilir" bir tahmindir; "RSI 78" değildir.

Prompt'a açık kural:

> SADE katman TEKNIK katmandan daha kesin konuşamaz. Teknik katmanda
> geçmeyen hiçbir yön iddiası, tahmin ya da öneri sade katmanda
> görünemez. Sade katmanın işi terimi açmak, sonucu keskinleştirmek değil.
> Emin olmadığın bir şeyi sadeleştirirken emin hâle getirme.

### 3.4 Ölçülebilir hâle getir

Bu kuralın tutulup tutulmadığı **kontrol edilebilir** ve edilmeli —
aksi halde ölçülmemiş bir varsayım olarak kalır (bkz. hakem JSON'u
tartışması).

Basit bir kontrol: sade katmanda tahmin dili (`gelebilir`, `yükselir`,
`düşer`, `beklenir`, `olacak`) geçiyorsa ve teknik katmanda karşılığı
(`yukari`/`asagi` yönü) yoksa → `panel_runs.hata`'ya `sade_kesinlik_ihlali`
yaz. Sayaç birikince örüntü görünür.

Bu, JSON⊆özet kontrolüyle aynı kalıp: ucuz, ileriye dönük, ve varsayımı
ölçüme çeviriyor.

### 3.5 Telegram tarafı

- Sade katman `send_message` ile gider
- `reply_markup`: `{"inline_keyboard": [[{"text": "🔍 Teknik detay",
  "callback_data": "det:<run_ts>"}]]}`
- Callback geldiğinde `panel_runs.ham_metin`'den teknik katman çekilip
  gönderilir — **yeniden üretilmez**, saklanan metin kullanılır
- 3800 karakter sınırı teknik katmanda geçerli; mevcut bölme mantığı kullanılır

`ham_metin` zaten saklanıyor, ek şema gerekmiyor.

### 3.6 Sohbet katmanı

`chat.py` SYSTEM_PROMPT kural 21 şu an yalnızca "kısa ve dolu yaz" diyor,
seviye ayarı yok. Eklenecek:

> VARSAYILAN SEVİYE SADE. Terim kullanman gerekiyorsa aynı cümlede bir
> kez aç ("RSI — son dönemdeki yükseliş hızını ölçen gösterge"). Kullanıcı
> "detay", "neden", "nasıl hesapladın" derse tam teknik seviyeye geç:
> sayılar, kaynaklar, hesap adımları. Seviye düşürürken belirsizliği
> kaybetme — sade anlatım daha kesin anlatım değildir.

### 3.7 Kabul kriteri

- Hakem cevabı iki bölüme ayrılabiliyor; ayrılamıyorsa teknik katman
  tek başına gidiyor (sessiz yarım mesaj yok)
- Telegram'da varsayılan mesaj sade; teknik detay butonla geliyor
- Teknik katman `ham_metin`'den okunuyor, yeniden üretilmiyor
- Sade katmanda geçen ama teknikte olmayan yön iddiası sayaca yazılıyor
- Sohbet varsayılanı sade, "detay" isteğiyle teknikleşiyor
- Test: kesinlik ihlali tespitinin çalıştığı (sahte metinle)

### 3.8 Tuzaklar

- **İkinci LLM çağrısıyla sadeleştirme.** Sürüklenme kanalı.
- **Sade katmanı teknikten türetmeye çalışan kural tabanlı çevirici.**
  Terim sözlüğü kurup değiştirmek bağlamı kaybeder ve tam da yukarıdaki
  kötü örnekleri üretir.
- **Teknik katmanı tamamen gizlemek.** Buton her zaman dursun; sayıyı
  görmek isteyen görebilmeli, yoksa güven kaybolur.

---

## Uygulama sırası ve bağımlılıklar

```
0  _atilanlari_isle düzeltmesi        bağımsız, 20 dk
1  tez.py + gramer + kontrol          bağımsız
   → şema: tez_bozuldu_ts
   → HAKEM prompt'una gramer (üretilerek)
2  hafif kip + launchd                İŞ 1'e bağlı (tez kontrolü içeriyor)
3  sade/teknik katman                 bağımsız, ama İŞ 2 ile aynı
                                      prompt dosyasına dokunuyor —
                                      art arda yapılırsa çakışma azalır
```

İŞ 1 ve İŞ 3 paralel gidebilir. İŞ 2, İŞ 1 bitmeden anlamlı değil çünkü
hafif koşunun asıl içeriği tez kontrolü.

Üçü bittikten sonra kapanış belgesindeki hat kaldığı yerden devam eder:
**İş Yatırım derinlik ölçümü → backtest (güç analizi) → P0-3 → P0-4 → P1'ler.**

---

## Son not — neden bu sıra

Kullanıcı "günde en az bir al/sat sinyali" istedi. Verilmedi, çünkü
ölçülmüş isabet oranı yok ve 5 günlük ufukta komisyon sonrası başabaş
~%55 isabet gerektiriyor — bu sayı projenin kendi belgelerinde.

Bunun yerine verilen şey, **karar anını getiren ama tahmin içermeyen**
bir bildirim sınıfı: önceden beyan edilmiş bir eşiğin gerçekleşmesi.
Kullanıcı açısından işlevi benzer, epistemik yükü çok daha düşük, ve
isabet oranı ölçüldüğünde geçersizleşmiyor — aksine, o zaman üstüne
taban oran eklenebilir.

Backtest bittiğinde bu bildirimlere *"bu kurulum bu evrende tarihsel
olarak şunu yaptı (n=X, aralık %A-%B)"* satırı eklenir. O noktada
sistem gerçekten tavsiye veriyor olur — ölçülmüş bir tavsiye.
