# finagent — IBKR için strateji katmanı (tek doküman, basit)

> **⛔ GEÇERSİZ — UYGULANMAYACAK.**
> Bu belgenin yerine `docs/finagent-strateji-motoru.md` geçti (2026-08-27).
> Emir katmanı hakkında yazdıkları (Adım 6) doğrulandı ve doğru; **strateji ve veri
> katmanı (Adım 0–5) bu depoyla tutarsız** çıktı. Yedi maddelik gerekçe yeni belgenin
> §9'unda, her biri `dosya:satır` ya da ölçümle.
> Burada tutuluyor çünkü yeni belge ona atıf yapıyor — **kaynak olarak değil, karşılaştırma
> olarak.** Buradan hiçbir adım uygulanmayacak.

Tarih: 2026-08-27
Hedef: IBKR'de (interactivebrokers.ie) işlem gören hisse/ETF'ler için ölçülmüş bir kural seti; sonunda emir otomasyonuna giden kapı.
Kapsam DIŞI: kripto, BIST (ayrı görev), yeni veri kaynağı, yeni kütüphane, TradingView.

Üç ilke:
1. Her adım tek başına biter ve test edilir; bitmeden sonraki adıma geçilmez.
2. Hiçbir kural ölçülmeden tarayıcıya sinyal olmaz; hiçbir sinyal ölçülmeden emre dönüşmez.
3. `risk.allow_order_execution: false` Adım 6.3'e kadar DEĞİŞMEZ.

---

## Adım 0 — IBKR evreni ve veri derinliği (ön koşul)

- Evren = `sahip=ali` portföyü + izleme listesi + şu sabit ETF listesi: QQQ, SPY, VUSA/VUAA, CNDX, IWDA, EXS1 (DAX), AEX ETF. Liste `settings.yaml → ibkr.strateji_evreni` altında; kod içinde liste YOK.
- Yahoo `sources.prices.range: "2y"` bu evren için `"10y"` olur (yalnızca strateji evreni; geri kalan 2y kalır). 2 yıl tek rejimdir, backtest yapılamaz.
- Ölçüm: evrendeki her sembol için bar sayısı ve ilk tarih → `docs/ibkr-evren.md`. 1.500 bardan az olan sembol backtest dışı, tabloda "sığ" etiketi.

Bitiş: tablo var, her sembolde bar sayısı yazıyor.

---

## Adım 1 — `compute_indicators`'a 5 sütun

Dosya: `src/finagent/analysis/indicators.py`

| Sütun | Hesap |
|---|---|
| `mom_21` | `close.pct_change(21)` |
| `mom_63` | `close.pct_change(63)` |
| `mom_126` | `close.pct_change(126)` |
| `mom_252` | `close.pct_change(252)` |
| `sigma_ewm20` | `close.pct_change().ewm(span=20).std() * sqrt(252)` |

- `technical_snapshot`'a beşi de girer. EMA12/26 kaldırılır (hiçbir yerde okunmuyor).
- Yetersiz barda `None`; sessiz atlama yok.
- Test: 300 barlık sentetik seride beşi dolu; 100 barlık seride `mom_126/252` `None`.
- Bu adımda tarayıcı DEĞİŞMEZ.

---

## Adım 2 — `run.py trend`'e dört parametre

Mevcut Donchian backtest'ine, varsayılanlar bugünkü davranışı korur:

| Parametre | Seçenek | Anlam |
|---|---|---|
| `--giris` | `donchian` (varsayılan) / `tsmom` | `tsmom`: `mom_63/126/252` işaretlerinin ortalaması > 0 ise giriş (1 ay hariç — çok gürültülü) |
| `--rejim` | `yok` (varsayılan) / `sma200` | kapanış < SMA200 iken yeni giriş yok |
| `--boyut` | `sabit` (varsayılan) / `vol` | ağırlık = `0.10 / sigma_ewm20`, tavan %25 |
| `--maliyet_bps` | sayı, varsayılan 0 | tek yön, giriş+çıkışta düşülür |

Çıkış kuralı iki giriş için de aynı: Donchian 10 gün / 2N stop. Yeni çıkış kuralı YOK.
Mevcut kontroller aynen: rastgele giriş kıyası, aylık gözlem birimi, `min(stop, open)` dolumu.
Çıktıya eklenir: parametreler, maliyet sonrası aylık ortalama, rastgeleye fark (puan), aylık t, en kötü ay, maksimum düşüş.

Regresyon testi: `--giris donchian --rejim yok --boyut sabit --maliyet_bps 0` BIST'te bugünkü sayıları (6.208 işlem, +5,85 / +2,97) birebir vermeli. Vermiyorsa dur.

---

## Adım 3 — Koşular (IBKR evreni, 10 yıl)

Her koşu iki maliyetle: 10 bps ve 30 bps (IBKR komisyonu düşük; spread için pay).

| # | Giriş | Rejim | Boyut |
|---|---|---|---|
| A | donchian | yok | sabit (referans) |
| B | donchian | sma200 | sabit |
| C | donchian | sma200 | vol |
| D | tsmom | yok | sabit |
| E | tsmom | sma200 | vol |

Sonuç tek tablo: `docs/backtest-ibkr-2026-08.md`. Sayı yaz, yorum yazma.
Ek: aynı beş koşu 2016–2020 ve 2021–2026 olarak ikiye bölünmüş (walk-forward yerine en basit bölme).

---

## Adım 4 — Kabul kuralı: tanımlayıcı → sinyal

Bir kurulum tarayıcıya sinyal olarak girer ancak:
1. Rastgeleye fark ≥ +2 puan
2. 30 bps'de aylık ortalama pozitif
3. Aylık t ≥ 2,5
4. İki alt dönemde de 1–2 sağlanıyor

Karar Ali'nin; ajan tabloyu sunar. Geçen kurulum `settings.yaml → ibkr.strateji` altına yazılır (giriş/rejim/boyut). Tarayıcı bu ayarı okur; kod içinde sabit YOK.

---

## Adım 5 — Walk-forward: son 3 ay "canlıymış gibi"

Amaç: 8 hafta beklemeden, kuralın canlıda bozulup bozulmadığını görmek.

- Kesme tarihi: `--bitis 2026-05-31`. Parametreler (Donchian 20/10, SMA200, vol %10) bu tarihe kadarki veriyle sabittir; sonraki bar hiçbir hesaba girmez.
- 2026-06-01'den bugüne her işlem günü için döngü: o güne kadarki seriyle sinyal üret → ertesi gün açılışla dolum → mevcut çıkış kuralı → gerçekleşen fiyatla puanla. Maliyet 10 ve 30 bps.
- Çıktı `docs/walkforward-2026-06-08.md`: her emir satır satır (tarih, sembol, yön, ağırlık, dolum, çıkış, getiri) + özet (işlem sayısı, isabet, ortalama getiri, backtest aylık beklentisiyle fark, rastgele girişle fark).

**Look-ahead (geleceğe bakma) koruması — zorunlu:**
- Tarih kesmesi TEK yerden: `db.fiyat_serisi(..., bitis=)`; SMA200, `sigma_ewm20`, rastgele giriş, hepsi aynı kesik seriyi görür. Kodda sabit tarih YOK.
- Test: `--bitis 2026-05-31` ile hesaplanan SMA200 == tam seride 31 Mayıs satırındaki SMA200; `--bitis 2026-06-15` ile hesaplanan == 15 Haziran satırı. Eşit değilse sızıntı var, sonuç geçersiz.

Kabul (Adım 6'ya geçiş): 3 ayda ≥ 8 işlem, ortalama getiri backtest aylık ortalamasının yarısının altında değil, rastgele girişe karşı pozitif. 3 ay küçük örnektir; soru "tuttu mu" değil "backtest'ten aşırı sapıyor mu".

---

## Adım 6 — Emir: kağıt hesapta ilk gün, canlıda 4 hafta sonra

Kısıtlar (repoda yazılı): IBKR bireysel hesapta OAuth yok, CPGW her sabah elle giriş ister → "sabah bir giriş, gün içi otomatik". `emir.py` → `gonder()` yalnızca `OnayFisi` ile; fiş 180 sn; POST yeniden denenmez. Bu yapıya dokunulmaz.

**6.1 Kağıt hesap — Adım 5 geçer geçmez, ertesi gün.**
- IBKR paper kullanıcısı `settings.yaml → ibkr.kagit_kullanici`. Sistem 17:45 sinyalini ertesi sabah açılışta `pending/` + Telegram Kaydet/İptal ile gönderir; Ali dokunur.
- Her emir `predictions`'a `ajan='strateji'` ile; dolum fiyatı vs sinyal fiyatı kaydedilir (gerçek maliyet ölçümü).
- 4 hafta. Çıktı: kağıt dolum vs walk-forward beklentisi tablosu.

**6.2 Canlı hesap, onaylı — 6.1 tablosu backtest'ten sapmıyorsa.**
- Aynı akış, canlı kullanıcı. Her emir tek dokunuş. Süresiz varsayılan bu.

**6.3 Canlı, onaysız — ölçüm tetiklemeli.**
Şu dördü `settings.yaml`'dan ve hepsi sağlanınca açılır:
- `ibkr.otomatik: true`
- tek pozisyon ≤ %10, günde ≤ 3 emir, `ibkr.otomatik_limit_eur` tavanı
- 6.2'de ≥ 20 onaylı emir ve ortalama dolum sapması ≤ 15 bps
- 6.2'de gerçekleşen getiri, Adım 3 backtest aylık ortalamasının yarısının altına düşmemiş
`allow_order_execution: true` yalnızca bu aşamada. İlk 4 hafta her akşam Telegram'a "bugün otomatik gönderilenler" özeti; tek bir sapma alarmı sonrası `otomatik: false`'a otomatik dönüş.

---

## Yapılmayacaklar

- MACD, Bollinger, Stochastic, Ichimoku, ADX: eklenmeyecek (örneklem dışı kanıt yok; mevcut bilginin tekrarı).
- Kesitsel momentum (hisseleri birbirine göre sıralama): evren 30 isimden küçük, anlamsız.
- Kısa vadeli geri dönüş: likit ABD/AB'de maliyet sonrası kalmıyor; IBKR için yok.
- BIST: bu dokümanda yok, ayrı görev.
- Saatlik katman, TradingView, IBKR haber: yok.
- `emir.py`'nin onay yapısına dokunulmaz.

---

## Bitiş ölçütü (tüm doküman)

- Adım 0–3 tabloları `docs/` altında.
- Adım 4'ten geçen kurulum `settings.yaml`'da, tarayıcı oradan okuyor.
- Adım 5 walk-forward tablosu var, look-ahead testi geçmiş.
- 6.1 kağıt hesap 4 haftası bitmiş, tablo var.
- 6.3 yalnızca dört şart `settings.yaml`'da sağlanınca; koddan açılamaz.
