# Kapanış — dış inceleme döngüsü

Bu belge bir tur açmıyor, kapatıyor. Soru yok, cevap beklenmiyor. İçinde
üç şey var: bu döngünün ne ürettiği, hangi işlerin devredildiği, ve
döngünün kendisinden çıkan aktarılabilir ilkeler.

Zincir: `finagent-analiz-feedback.md` → `finagent-analiz-degerlendirme.md`
→ `finagent-analiz-konsensus.md` → `finagent-analiz-konsensus-yanit.md`
→ `finagent-goc-inceleme.md` → `finagent-durum.md` → bu belge.

---

## 1. Döngü ne üretti

Dört turda kapanan kusurlar — hepsi kodda, hepsi testli:

- **Defter artık çelişkiyi saklıyor.** `ajan` benzersizliğe girdi; teknik
  "aşağı", risk "yukarı" dediğinde ikisi de kayıtta kalıyor. Önce yalnızca
  en yüksek güvenli tutuluyordu ve bu, projenin kendi "çelişki en değerli
  çıktıdır" ilkesini deftere hiç geçirmiyordu.
- **Karne kullanıcının okuduğu şeyi ölçüyor.** Hakem ayrı puanlanıyor.
- **Kümelenme düzeltildi.** 56 tahmin / 26 küme; Wilson aralığı 1,47 kat dar
  çıkıyordu. Bu kusuru dışarıdan gelen inceleme değil, kodu gören taraf buldu.
- **Panel çıktısı artık gözlemlenebilir.** `panel_runs` ham metni, JSON
  durumunu, sessizliği, atılan sayaçlarını tutuyor. "Kaç turda kaybettik"
  sorusu bir daha cevapsız kalmayacak.
- **Göç güvenliği.** FK `finally` ile geri açılıyor, sayım işlem içinde.
  Bunu kanıtlarken üçüncü kusur çıktı: Python `sqlite3` eski kipte `ALTER`
  ve `CREATE`'i otomatik commit ediyor — dış incelemenin önerdiği düzeltme
  tek başına yetmezdi ve yarım göçü veri kaybına çevirecek bir temizlik
  adımına yol açabilirdi.
- **Kripto evreni** 10 → 67 enstrüman, üç süzgeç de kaynaktan.
- **`signal_id`** backtest ile defteri birbirine bağlayacak köprü kuruldu.

Ölçüm döngüsü **hâlâ meyve vermedi** — 64 tahminin hiçbiri puanlanmadı,
ilk puanlama 20 Ağustos civarı. Bu bir eksiklik değil, takvim.

---

## 2. Üç market gerçekte ne kadar kapsanıyor

Veri toplama açısından üçü de var. **Analiz derinliği eşit değil** ve bu
ayrım yazılmazsa "üç market kapsanıyor" cümlesi olduğundan fazlasını
vaat eder.

| | BUX (EUR) | Kripto (USDT/USD) | Midas/BIST (TRY) |
|---|---|---|---|
| Enstrüman | 19 | 46 + 21 referans | 251 |
| Fiyat geçmişi | 2 yıl | 3 yıl / 1 yıl | **13,5 ay** |
| Portföy pozisyonu | ✅ gerçek | ✅ gerçek | ❌ **hiç akmadı** |
| Kademe 1 kaynak | SEC/XBRL ✅ | ❌ karşılığı yok | KAP ✅ |
| Temel veri | XBRL ✅ | tokenomik | midasbilanco ✅ (sigorta/finans hariç) |
| Piyasa vekili | AEX ✅ | BTC ⚠️ (bkz. §3) | XU100 ✅ |
| Reel getiri | gerekmez | gerekmez | ❌ **TÜFE yok** |

Üç somut sonuç:

**BIST analitik olarak en zayıf ayak.** 13,5 ay, 5 günlük ufuklu bir sinyal
tipi için bile ince örneklem. Backtest'in "BIST ölçülemez" demesi bekleniyor
ve bu doğru cevap olacak.

**BIST'te nominal getiri yanıltıcı.** Makro katman (P1-7) olmadan "THYAO %40
yükseldi" cümlesi, aynı dönem TÜFE %35 ise bir şey söylemiyor. Reel ve USD
bazlı getiri hesaplanana kadar BIST yorumları bu uyarıyla okunmalı.

**Midas portföy yolu uçtan uca hiç denenmedi.** Hesap var, bakiye yok, hiç
pozisyon akmadı. Piyasa verisi çalışıyor; portföy tarafı ilk gerçek ekran
görüntüsünde test edilmiş olacak.

Ayrıca ETF look-through (P1-9) yok: CNDX/VUSA/RBOT'un bileşenleri
toplanmadığı için gerçek sektör maruziyeti ölçülemiyor, yalnızca çıkarılıyor.

---

## 3. Devredilenler

Karar verilmiş, tartışma kapalı. Sıra mutabık.

### Önce — 20 Ağustos'tan (ilk puanlama) önce

1. **Referans coin piyasa vekili.** `kriptoevren.py` referans coin'i
   `currency="USD"` yazıyor; `db.piyasa_vekili()` para birimine göre eşleşiyor
   → `USD → QQQ`. 21 coin'in anormal getirisi Nasdaq-100 regresyonuyla
   hesaplanıp "piyasa modeli" diye beyan ediliyor olabilir. Doğrula
   (`piyasa_vekili(<CRYPTO id>)` → QQQ mi), doğruysa vekil seçimini para
   birimine değil `asset_type`'a bağla.
2. **`karne()`'ye `vekilsiz_n`.** Vekilsiz puanlanan tahminler ham getiriyle
   ölçülüyor; ayrım veride var (`piyasa_getiri_pct IS NULL`), karnede yok.
3. **İlk karne kripto-only olacak.** `puanla()` bar sayıyor; kripto 7
   bar/hafta, hisse 5. Karneye venue kırılımı ekle ya da ilk mesajda
   kapsamı beyan et.

### Ölç, sonra karar ver

4. **Toplama süresi.** 10 → 67 kripto enstrümanından sonra gecelik zincir,
   saatlik kripto ve nabız yeniden ölçülmedi. `ExitTimeOut` 20 dk, eski
   ölçüm 13,7 dk.
5. **Panel gündemi.** `signals` 23 → 166, `PANEL_ADAY` hâlâ 12. Top 12'nin
   venue dağılımı ölçülmedi; kripto gündemi ele geçirdiyse portföyün ana
   gövdesi analiz dışı kalıyor demektir. Hata değil, ödünleşim — ama
   ölçülmediği için ödünleşim olduğu bilinmiyor.
6. **Atılan görüş sayaçlarının ajan kırılımı.** `ajan` alanı düşürme anında
   sözlükte zaten var; koşu toplamı bu bilgiyi yutuyor.

### Ana hat

7. **İş Yatırım derinlik ölçümü** — P1-8'in takvimi buna bağlı, hâlâ bilinmiyor
8. **Backtest (P0-1)** — `fiyat_serisi`'ne opsiyonel `bitis`; ilk çıktı isabet
   tablosu değil **güç analizi**; `signal_stats`'ta `n` ve `guven_araligi`
   zorunlu
9. **P0-3** — kaynağı `signal_stats`; bilinmezlik beyanı her zaman, nokta
   tahmini n≥20'de; defter kaynağı için tetikleyici: herhangi bir sinyal
   tipinde n≥20 olduğu gün
10. **P0-4** geçmiş araçları → **P1-5** valuation → **P1-6** risk →
    **P1-7** makro → **P1-9** ETF → **P2-10** tez alarmı →
    **P2-11** prompt konsolidasyonu → **P2-12/13/14**

### Yapısal borç

11. **`PRAGMA user_version`.** Göç durumu kolon varlığından çıkarsanıyor.
    Sırada iki göç daha var (`signal_stats`, makro); üçüncü ve dördüncü
    kendi durum tespitini icat etmeden önce koymanın maliyeti bir satır.
12. **Ölü konfigürasyon.** 62 ayar yolunun 22'sinin YAML'da karşılığı yok.
    Çıktı temizlik değil **değişmez test** olmalı.
13. **Bayat prompt satırları** hâlâ duruyor: `chat.py` kural 9,
    `strategist.py` başlığı, `portfolio.py:56`.

---

## 4. Döngüden çıkan ilkeler

Bunlar bu projeye özgü değil; başka işlerde de geçerli.

**Beyan edilen durum ile gerçek durum sessizce ayrışır.** Üç ayrı belirti,
tek kök: prompt "FX yok" diyor ama araç var; YAML `daily_bars: 1000` diyor
ama kod başka yola bakıyor; `settings.yaml` kaynak tavanı sanılıyor ama
bizim seçtiğimiz pencere. Dış incelemeci bu hatayı bir gün içinde yaptı.
Çözüm temizlik değil, üretim veya değişmez test — **sürüklenmeyi yakalamaktansa
imkânsız kıl.**

**Ölçüm popülasyonu modelin davranışına göre seçilirse metrik yalan söyler.**
`_json_cek` sessizce boş dönünce, formatı bozan koşular ölçüm dışı kalıyordu.
Hakem sustuğunda deftere sıfır kayıt giriyor, yani iyi susmak karneye
yansımıyor. İkisi aynı sınıf ve ikisi de "hata yok" gibi görünür.

**Varsayılan davranışı belgeden okuyup olgu sanma.** `with tx()` bir işlem
açıyor sanıldı; Python `sqlite3` DDL'i otomatik commit ediyordu. Fark ancak
**arıza enjekte edilerek** görüldü. Kritik yolda "muhtemelen böyle çalışır"
yeterli değil.

**Kolon eklemek bedava, göç tekrarı değil.** Şema değişikliği tek seferde tam
yapıldı; `tez` ve `gecersizlesme_kosulu` bir hafta boş duracak ama P2-10
sırası geldiğinde şemaya dokunulmayacak.

**Ertelemenin gerekçesi süresi dolmayan bir gerekçeyse, tetikleyici koy.**
"Henüz ölçülecek şey yok" cümlesi döngü çalışmadığı sürece her gün doğru
kalır.

---

## 5. Döngüyü neden kapatıyoruz

Marjinal getiri düştü. İlk turda dış inceleme 14 gerçek madde üretti. Son iki
turda ürettiklerim giderek daha çok "şunu doğrula" biçimine dönüştü — çünkü
kalan sorular veriye bakmadan cevaplanamıyor.

Buna karşılık kodu gören tarafın bulduğu iki kusur — kümelenme ve DDL
otomatik commit — dışarıdan görülemezdi ve ikisi de dış incelemenin
bulduklarından daha derindi. İkincisi doğrudan dış incelemenin **eksik bir
tavsiyesini** düzeltti.

Bu, döngünün başarısızlığı değil sonucu: iki taraf da işini yaptı, ve artık
soruyu daha iyi soran taraf veriye bakan taraf. Bundan sonrası ölçüm işi.

Bir tur daha gerekirse doğru zamanı belli: **backtest'in ilk güç analizi
çıktığında.** O çıktı, sistemin hangi sinyal tipleri hakkında konuşmaya
hakkı olduğunu söyleyecek — ve o soru dışarıdan bakmaya değer.
