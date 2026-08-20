# M4 — Yerel semantik arama: kurulum ve ölçüm görevleri

**Karar:** `embeddinggemma` (Ollama, 622 MB, 768 boyut) + FTS5 trigram, RRF ile hibrit.
model2vec elendi. bge-m3 seçilmedi — TR-MTEB'de embeddinggemma'nın gerisinde ve iki katı boyutta.

**Kapsam:** yalnızca sohbet arşivi arama katmanı (`sohbet_ara`). `pipeline.analyze()`,
`sources.py`, `predictions` tablolarına dokunulmuyor.

**Sıralama zorunlu.** T1 (altın küme) T3–T6'dan önce yazılacak ve sonradan
değiştirilmeyecek — ölçümü sonuca göre ayarlamamak için.

---

## T0 — Ortam doğrulama

Tahmin etme, çalıştır. Herhangi biri ✗ ise dur ve bildir; sonraki göreve geçme.

- [ ] Projenin kullandığı Python'da `import numpy` çalışıyor mu?
      (Python 3.14 / x86_64 — torch ve onnxruntime'da wheel yoktu, numpy'de olduğu
      varsayılmayacak.) Çalışmıyorsa: saf Python kosinüs da 156 tur için yeterli,
      ama bunu bilerek seç, keşfederek değil.
- [ ] `ollama serve` ayağa kalkıyor mu, `:11434` cevap veriyor mu?
- [ ] `ollama pull embeddinggemma` — indirme boyutunu ve süresini rapora yaz.
- [ ] Disk: indirmeden önce boş alan. (Son bakışta 19 GB boştu, `~/.cache/huggingface`
      18 GB. Cache temizliği **bu görevin parçası değil** — Ali'ye bırak.)
- [ ] `ollama` üzerinden tek bir Türkçe cümle gömüle biliyor mu, dönen vektör
      768 boyutlu mu? Boyutu ölç, model kartına güvenme.

---

## T1 — Altın küme (implementasyondan ÖNCE)

`tests/altin_kume.json` — 15 kayıt, her biri `{sorgu, beklenen_tur_id, kategori}`.

Gerçek arşivden, gerçek sorgu dilinle yazılacak. Üç kategori dengeli olacak:

| Kategori | Ne test ediyor | Örnek |
|---|---|---|
| `tam_kelime` | Sorgudaki kelime turda birebir geçiyor | "altın hesabı" → 181 gram altın turu |
| `parafraz` | Aynı anlam, farklı kelime | "kripto param nerede duruyor" → portföy turu |
| `kelime_yok` | Kavram var, kelime hiç geçmiyor | "biyoplastik" → "FDCA" geçen Avantium turu |

**Her sorgunun iki varyantı olacak:** şapkalı (`altın hesabımla`) ve şapkasız
(`altin hesabimla`). Önceki turda model2vec ölçümü ASCII sorguyla, FTS5 ölçümü
şapkalı sorguyla yapıldığı için karşılaştırma geçersizdi. Bu tekrarlanmayacak.

Dosya yazılıp commit edildikten sonra **değiştirilmeyecek**. Sonuç kötü çıkarsa
altın küme değil, sistem düzeltilecek.

Metrikler: `recall@3` ve `MRR`. İkisi de her kategori için ayrı raporlanacak —
tek ortalama, hangi hata sınıfının çözüldüğünü gizler.

---

## T2 — Türkçe normalizasyon

`normalize.py`:

- `tr_lower(s)` — Python'un `.lower()`'ı `"I" → "i"` yapar, Türkçede `"ı"` olmalı.
  `İ → i`, `I → ı` özel olarak ele alınacak.
- `tr_fold(s)` — şapka katlama: `ç→c, ğ→g, ı→i, ö→o, ş→s, ü→u`. **Yalnızca leksik
  taraf (FTS5) için.** Gömme tarafına ham metin gidecek; model şapkalı Türkçeyi
  zaten biliyor, katlamak bilgiyi yok eder.

Birim testi: `tr_lower("İSTANBUL") == "istanbul"`, `tr_lower("IŞIK") == "ışık"`.

---

## T3 — Baseline: bugünkü LIKE

Mevcut `sohbet_ara`'yı altın kümeye karşı çalıştır, `recall@3` / `MRR` ölç.
Hiçbir şey değiştirme. Bu satır olmadan sonraki üç görevin kazancı bilinmez.

---

## T4 — FTS5 trigram

```sql
CREATE VIRTUAL TABLE tur_fts USING fts5(
    metin,
    content='turlar',
    content_rowid='id',
    tokenize='trigram'
);
```

- `metin` sütununa `tr_fold(tr_lower(...))` uygulanmış hali yazılacak, sorgu da
  aynı fonksiyondan geçecek. Aksi halde şapkasız sorgu yine ıskalar.
- INSERT / UPDATE / DELETE tetikleyicileri yazılacak. Yeni tur eklendiğinde indeks
  sessizce eskimemeli.
- **DDL auto-commit:** SQLite'ta `CREATE TABLE` açık transaction'ı commit'ler.
  Şema kurulumu ile veri yazımı aynı transaction'da sanılmayacak; migration
  ayrı adım, dönüşü açık kontrol edilecek.
- Yeniden indeksleme idempotent olacak (iki kez çalıştırınca çift kayıt olmasın).

Ölçüm: altın küme, üç kategori ayrı.

---

## T5 — Gömme katmanı

### T5.1 — Prefix doğrulaması (önce bu)

EmbeddingGemma asimetrik şablon kullanır:

```
sorgu  → "task: search result | query: {metin}"
belge  → "title: none | text: {metin}"
```

**Ollama bunu otomatik uyguluyor mu — varsayma, ölç.** Doğrulama yöntemi: aynı
metni bir kez ham, bir kez şablonlu gönder, dönen vektörleri karşılaştır. Aynıysa
Ollama şablon uygulamıyordur ve şablonu sen ekleyeceksin. Farklıysa uyguluyordur,
üstüne bir daha ekleme.

Bu adım atlanırsa model "kötü" görünür ve model2vec turundaki yanlış sonuç
tekrarlanır. Bulguyu rapora yaz.

### T5.2 — Depolama

Vektör deposu **kurulmayacak**. 156 tur × 768 float32 ≈ 480 KB.

```sql
ALTER TABLE turlar ADD COLUMN gomme BLOB;
ALTER TABLE turlar ADD COLUMN gomme_model TEXT;
ALTER TABLE turlar ADD COLUMN gomme_ts TEXT;
```

`gomme_model` alanı zorunlu: model veya prefix şeması değişirse eski vektörler
geçersizdir. Arama sırasında beklenen modelden farklı satır görülürse **sessizce
atlanmayacak, açık hata verilecek**. Karışık vektör uzayında arama, boş sonuçtan
daha kötüdür çünkü makul görünen yanlış sonuç üretir.

### T5.3 — İndeksleme ve arama

- İndeksleme: tüm turlar, batch halinde. Süreyi ölç ve rapora yaz.
- Arama: tüm BLOB'ları oku, normalize edilmiş vektörlerde tek matris çarpımı.
- **Ollama kapalıysa:** `sohbet_ara` boş liste dönmeyecek, açık hata fırlatacak.
  Sessiz `{}` dönüşü bu projede daha önce en yüksek şiddetli hata sınıfı olarak
  işaretlendi.

Ölçüm: altın küme, üç kategori ayrı.

---

## T6 — Hibrit (RRF)

```python
def rrf(fts_siralama, gomme_siralama, k=60):
    puan = defaultdict(float)
    for liste in (fts_siralama, gomme_siralama):
        for sira, tur_id in enumerate(liste, start=1):
            puan[tur_id] += 1.0 / (k + sira)
    return sorted(puan, key=puan.get, reverse=True)
```

Skor normalizasyonu yok, ağırlık ayarı yok. `k=60` standart başlangıç; altın küme
sonucu iyileşmiyorsa oynama, sonucu raporla.

Ölçüm: altın küme, üç kategori ayrı.

---

## T7 — Karar tablosu

Tek tablo, dört satır:

| Yöntem | recall@3 tam_kelime | recall@3 parafraz | recall@3 kelime_yok | MRR | Arama gecikmesi |
|---|---|---|---|---|---|
| LIKE (bugün) | | | | | |
| FTS5 trigram | | | | | |
| embeddinggemma | | | | | |
| Hibrit RRF | | | | | |

Ek olarak: model indirme boyutu, indeksleme süresi, sorgu başına gömme gecikmesi.

**Karar kuralı:** hibrit, FTS5'e göre `kelime_yok` kategorisinde belirgin kazanç
sağlamıyorsa gömme katmanı canlıya alınmaz — FTS5 ile kalınır ve 600 MB model,
arka plan servisi ve ek karmaşıklık taşınmaz. Kazanç varsa hibrit canlıya alınır,
saf gömme yolu değil.

---

## Kapsam dışı

- Reranker (`bge-reranker-v2-m3`) — 156 turda gereksiz
- sqlite-vec / faiss / chroma — 480 KB için indeks yapısı
- turkish-e5-large GGUF dönüşümü — embeddinggemma yetmezse gündeme gelir
- `~/.cache/huggingface` temizliği — Ali'nin kararı

## Rapor formatı

Rapor istenmiyor. T7 tablosu + T0/T5.1 bulguları, o kadar. Ölçülmemiş hiçbir
sayı yazılmayacak; çalıştırılmamış hiçbir adım "çalışır" diye işaretlenmeyecek.
