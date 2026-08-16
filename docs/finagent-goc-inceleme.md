# Hedefli inceleme — ff774a1 · 153860d

Beş sorunun sırasıyla. İki yerde somut hata buldum (§1a, §4); ikisi de sessiz
başarısızlık sınıfından, yani tam olarak bu turda kapatmaya çalıştığımız şey.

Önce şunu söyleyeyim: kümelenme kusurunu **kendin bulman** bu döngünün
çalıştığının kanıtı. 56 tahmin / 26 küme, √2.15 ≈ 1,47 — tasarım etkisi
düzeltmesi hem doğru hem doğru mertebede. İnceleme turunun üretebileceği en
iyi şey buydu ve dışarıdan gelmedi.

---

## 1. Göç doğruluğu

Genel yapı doğru: şemadan önce çalışması, `ajan` kolonu varsa erken dönmesi,
`gerekce` önekinden geri kazanım, çözülemeyeni `bilinmiyor` bırakıp uydurmaması,
kayıt sayısı denetimi. Test de gerçek eski şemayı kuruyor — doğru test.

**Yabancı anahtarı geçici kapatman kabul edilebilir mi:** Evet. Gerekçen doğru
ve pragma'yı işlem dışında çalıştırman kritik (SQLite işlem içinde sessizce
yok sayar — bunu bilmen iyi). Ama uygulamada iki kusur var.

### 1a. `PRAGMA foreign_keys = ON` geri dönüşü garantili değil ⚠️

```python
self._conn.execute("PRAGMA foreign_keys = OFF")
with self.tx() as c:
    ...
self._conn.execute("PRAGMA foreign_keys = ON")   # buraya ulaşılmayabilir
```

`with self.tx()` içinde herhangi bir şey patlarsa istisna yukarı gider ve
**bağlantı FK denetimi kapalı olarak yaşamaya devam eder.** Bu bağlantı bot
sürecinin ömrü boyunca açık. Yani başarısız bir göç, sürecin geri kalanında
tüm yabancı anahtar denetimini sessizce kapatıyor — göçün kendisinden çok
daha geniş bir hasar yüzeyi.

`try/finally` gerekli. Bu, göçün patlamasını beklemek değil; patladığında
hasarın göçle sınırlı kalmasını sağlamak.

### 1b. Kayıt denetimi işlemin *dışında* — koruma değil, otopsi ⚠️

```python
with self.tx() as c:
    ... INSERT ...
    c.execute("DROP TABLE predictions_eski")
# ↑ commit oldu
yeni_sayi = ...
if yeni_sayi != eski_sayi:
    raise RuntimeError("kayit kaybi")
```

`RuntimeError` attığında eski tablo **çoktan silinmiş ve işlem commit edilmiş**
oluyor. Denetim kaybı bildiriyor, engellemiyor.

Sayımı `DROP`'tan önce ve **işlemin içinde** yap; `raise` o zaman geri sarar:

```python
with self.tx() as c:
    c.execute("ALTER TABLE ... RENAME TO predictions_eski")
    c.execute("CREATE TABLE predictions (...)")
    c.execute("INSERT INTO predictions ... SELECT ... FROM predictions_eski")
    n_yeni = c.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    n_eski = c.execute("SELECT COUNT(*) FROM predictions_eski").fetchone()[0]
    if n_yeni != n_eski:
        raise RuntimeError(...)          # ← rollback, veri yerinde
    c.execute("DROP TABLE predictions_eski")
```

Şu anki hâlin testi geçiyor çünkü test mutlu yolu koşuyor. Kayıp senaryosunu
test etmek zor; doğru cevap testi güçlendirmek değil, **kaybı yapısal olarak
imkânsız kılmak.**

### 1c. `predictions` tanımı artık iki yerde

Göç tabloyu satır içi `CREATE TABLE` ile kuruyor; `schema.sql` da aynı tabloyu
tanımlıyor ve `IF NOT EXISTS` yüzünden hiç çalışmıyor. P2-10 kolonları
`schema.sql`'e eklenirse göçteki kopya sessizce geride kalır ve **eski
veritabanları eksik kolonla yaşar.**

Bu, üç turdur konuştuğumuz sürüklenme sınıfının üçüncü örneği: aynı gerçeğin
iki yerde beyan edilmesi. En ucuz çözüm bir test — göçün ürettiği kolon
kümesi ile `schema.sql`'in ürettiği kümenin özdeş olduğunu doğrulayan.
(Boş DB'ye `init_schema`, eski şemalı DB'ye `init_schema`, iki
`PRAGMA table_info` karşılaştır.)

---

## 2. Hakemin JSON vermesi yanlılık yaratır mı

Endişen meşru ama yanlış yöne bakıyorsun sanırım.

**Uydurma tarafı düşük risk.** JSON blogu özetten *sonra* geliyor ve prompt
"özette geçmeyen sembol JSON'da OLMAMALI" diyor. Yani yapısal çıktı, düzyazının
aşağısında; yanlılık oluşacaksa önce düzyazıyı bozması gerekir. Boş listeyi de
açıkça meşrulaştırmışsın. Bu tasarım doğru.

**Asıl sorun ters yönde: sessizlik artık ölçülemiyor.** Hakem "bugün kayda
değer bir şey yok" derse — ki bu *doğru ve tercih edilen* cevap — deftere sıfır
kayıt girer. Yani sistem **konuştuğu günlerde ölçülüyor, sustuğu günlerde
ölçülmüyor.** İyi susmak karneye hiç yansımıyor.

Bu, `_json_cek` yanlılığının aynısı, bir kat yukarıda: ölçüm popülasyonu
modelin kendi davranışına göre seçiliyor.

Üstelik ölçmek bedava, çünkü artık `panel_runs` var:

- `json_durum='ok' AND gorus_sayisi=0` = **hakem sessiz kalmayı seçti.** Say.
- O günlerin en güçlü sinyallerini ufuk sonunda ölçüp konuşulan günlerle
  karşılaştır. Sessiz günlerde sonraki anormal hareketlerin büyüklüğü
  konuşulan günlerle aynıysa, sessizlik disiplin değil ıskalama.

Sorduğun yanlılığı da geriye dönük ölçebilirsin artık: `ham_metin` elde
olduğu için **JSON'daki semboller ⊆ özetteki semboller** kontrolü
çalıştırılabilir. İhlal varsa yapısal çıktının prompt'u ezdiğini görürsün.
Bir sayaç ekle; ölçülmemiş varsayım ölçülmüş olur.

---

## 3. Karne yalnızca hakemi ölçüyor — doğru, ama eksik raporlanıyor

**Seçim doğru.** Hem istatistiksel (enstrüman-gün başına tek çağrı) hem
anlamsal (kullanıcının okuduğu şey). "Ajanlar 4/4 tutmuş, hakem ıskalamış →
karne %0" **istenen** davranış: gönderilen tavsiye yanlıştı.

**İki sayı raporlanmalı mı:** Hayır, üç. Ama farklı adlarla ve farklı amaçlarla:

| Sayı | Ne cevaplıyor | Nerede |
|---|---|---|
| `karne` (hakem) | "Sana gönderdiğim özet ne kadar isabetli" | manşet |
| `ajan_karnesi` | "Hangi mercek daha iyi çalışıyor" | tanı |
| **hakem–panel sapması** | "Hakem bilgi mi üretiyor, yok mu ediyor" | **yok, eklenmeli** |

Üçüncüsü senin verdiğin örneğin ta kendisi ve şu an görünmez: ajanların
çoğunluğu bir yön derken hakem tersini söyleyip yanılıyorsa, hakem katmanı
**bilgi imha ediyor** demektir. Bu, düzeltilebilir bir kusur ve karne
tasarımının şu hâliyle hiç sinyal vermiyor. Sorgu basit: aynı
(enstrüman, gün) kümesinde `ajan<>'hakem'` çoğunluk yönü ile `ajan='hakem'`
yönünü karşılaştır, ikisinin `isabet` değerini yan yana koy.

### Bir hata: `_gonder()` karnenin notunu çöpe atıyor ⚠️

`journal.karne()` artık "hiç ölçüm yok" ile "hakem çağrısı yok ama 30 ajan
tahmini puanlandı" ayrımını özenle kuruyor ve `not` alanına yazıyor.
`runner._gonder()` ise:

```python
else:
    alt.append("\n\n<i>Karne: henuz puanlanmis tahmin yok.</i>")
```

Sabit metin; `karne["not"]` hiç okunmuyor. Yani defter katmanında kurduğun
ayrım kullanıcıya **hiç ulaşmıyor** ve kullanıcı yanıltıcı cümleyi görüyor.
Test defter seviyesinde geçiyor, davranış bildirim seviyesinde bozuk —
az önce kapattığımız sınıfın bir kat yukarıdaki tekrarı.

Ayrıca `puanla()` şunu döndürüyor: `{"olculen": N, **self.karne()}`.
`olculen` **tüm** puanlananları sayıyor, `olcum` **yalnızca hakemi**. Aynı
sözlükte, benzer adlarla, farklı popülasyonlar. Bu yanlış okunur —
`olculen_toplam` / `olcum_hakem` gibi ayrıştır.

---

## 4. Sayaç vs. `atildi` bayrağı — gerekçen doğru, ama sayaçlar yazılmıyor ⚠️

Gerekçene katılıyorum: görüş `ham_metin` içinde duruyor, kullanılmayan kolon
açmak ölü konfigürasyon sınıfının kendisi olurdu. Doğru karar.

Ama uygulamada sayaçlar da ölü görünüyor. `_kosuyu_yaz()`'ın INSERT'ü:

```sql
INSERT INTO panel_runs (run_ts, ajan, ham_metin, json_durum, gorus_sayisi, hata)
```

`atilan_sembol_yok`, `atilan_seri_yok`, `atilan_cakisma` listede yok →
DEFAULT 0. Ve `Defter.kaydet()`'in döndürdüğü rapor `runner`'da yalnızca
`log.info` ile yazılıyor, `panel_runs`'a **UPDATE edilmiyor.**

Yani üç kolon da sürekli 0. Bayrak yerine sayaç seçmenin gerekçesi
"kullanılmayan kolon açmayalım"dı; şu an kullanılmayan üç kolon var.

Sebep anlaşılır: `panel_runs`'ı `Panel` yazıyor, `kaydet()`'i `runner`
çağırıyor — sayaçlar modül sınırının yanlış tarafında doğuyor. İki çözümden
biri: `kaydet()` raporunu `panel_runs`'a UPDATE et (run_ts + ajan ile), ya da
sayaçları `panel_runs`'tan çıkarıp ayrı bir `defter_yazim` kaydına al.

Kontrol: `SELECT SUM(atilan_cakisma) FROM panel_runs` — 5 koşudan sonra 0 ise
teşhis doğru.

---

## 5. Testi değiştirmek doğru hamle miydi

**Evet, ama tek başına yetmiyor.**

Kural: bir test, **şartname** değiştiği için değiştirilir; **uygulama**
değiştiği için değiştirilmez. Burada şartname gerçekten değişti — "karne tüm
tahminleri ölçer" → "karne gönderilen çağrıyı ölçer." Bu bir davranış kararı,
testin de onu takip etmesi doğru.

Riskli olan kısım şu: eski test **iki** şey iddia ediyordu.

1. Karne tüm tahminleri sayar → **artık geçersiz**, silinmesi doğru
2. Küçük örneklemde güven aralığı **geniş** çıkar (`ust - alt > 40`) →
   **hâlâ geçerli**, ve yeni testlerde göremiyorum

İkincisi değişmemiş bir değişmezdi ve testle birlikte gitmiş olabilir.
`test_karne_kumelenmeyi_saymaz` `olcum`, `kaynak`, `bagimsiz_kume` ve
`isabet_%` iddia ediyor; aralık genişliğini iddia etmiyor.
`test_karne_hakem_yoksa_sessiz_kalmaz` da etmiyor.

Kontrol et: n=1'de `guven_araligi_%` genişliği hâlâ test ediliyor mu?
Edilmiyorsa geri koy — ayrı bir testte, çünkü iki değişmez tek teste
bağlanınca biri diğerini de götürüyor.

**Genel kural, yazmaya değer:** bir test davranış değiştiği için
güncelleniyorsa, diff'te *hangi iddiaların korunduğu* açıkça görünmeli.
"Testler geçiyor" bir iddianın sessizce silindiğini göstermez.

---

## Özet — yapılacaklar

| # | İş | Boyut |
|---|---|---|
| 1a | Göçte `try/finally` ile FK'yı geri aç | 3 satır |
| 1b | Kayıt denetimini işlem içine, `DROP`'tan önce al | 10 satır |
| 4 | `atilan_*` sayaçlarını gerçekten yaz (ya da kaldır) | küçük |
| 3 | `_gonder()` `karne["not"]`'u kullansın; `olculen`/`olcum` adlarını ayır | küçük |
| 5 | Küçük örneklem aralık genişliği testini geri koy | küçük |
| 1c | Göç şeması ↔ `schema.sql` özdeşlik testi | küçük |
| 2 | Sessizlik sayacı (`gorus_sayisi=0 AND json_durum='ok'`) + JSON⊆özet kontrolü | küçük |
| 3+ | Hakem–panel sapma metriği | orta |

1a ve 1b bugün. Diğerleri sıradaki işle birlikte gidebilir.

Bunlar bittiğinde defter turu kapanmış olur; sıradaki iş İş Yatırım derinlik
ölçümü, sonra backtest.
