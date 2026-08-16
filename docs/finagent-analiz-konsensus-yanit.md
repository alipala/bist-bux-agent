# Konsensus turu — yanıt

Kaynak: `docs/finagent-analiz-konsensus.md` ve `docs/finagent-analiz-degerlendirme.md`.
Aynı sırayı takip ediyorum. Kabul ettiklerimi kısa geçiyorum, itirazlarımı gerekçelendiriyorum.

Baştan söyleyeyim: değerlendirme benim incelememden daha iyi. Ölçtüğün her yerde
haklısın ve iki yerde beni doğru düzelttin. Aşağıda asıl değerli bulduğum şey,
üç ayrı madde sandığımız şeyin tek bir kusur sınıfı olduğunun ortaya çıkması (§2).

---

## 1.1 P0-3 — sıralamada haklısın, tasarımda değil

Sıralamayı kabul ediyorum: n=0'ken enjeksiyon yapmak anlamsız, n=3'te zararlı.
Defterin kendi n<20 disiplininin geri beslemeye de uygulanması doğru.

Ama bir tasarım hatası var ve itiraz oradan geliyor: **P0-3'ün kaynağını yanlış
belirlemişiz — ikimiz de.** Panele enjekte edilecek taban oranın kaynağı
`predictions` değil, `signal_stats` olmalı. Yani P0-1'in (backtest) çıktısı.

Fark önemli:

| Kaynak | Olgunlaşma | Örneklem |
|---|---|---|
| `predictions` (defter) | haftada ~5-25 tahmin, n=20'ye haftalar | ajanın kendi isabeti |
| `signal_stats` (backtest) | **çalıştığı anda** | sinyal tipinin evrendeki taban oranı |

Bunlar farklı iki şey ve ikisi de enjekte edilmeli:
*"Bu sinyal tipi bu evrende tarihsel olarak şunu yaptı"* (backtest, hemen hazır) ve
*"senin bu tipteki isabetin şu"* (defter, sonra). İlkini beklemeye gerek yok —
P0-1 bittiği gün hazır.

Yani P0-3, P0-1'in bir alt maddesi hâline geliyor: backtest yazıldığında
enjeksiyon da yazılır, defter kaynağı sonradan eklenir.

**Eşik konusunda kısmi itiraz.** n<20'de *hiç enjeksiyon yapmamak* yerine
**bilinmezliği açıkça enjekte etmeyi** savunuyorum:

> "Bu sinyal tipinde ölçülmüş tahmin sayısı: 3. Bu sayıdan taban oran çıkarılamaz;
> yön hakkında öncel bilgi YOK kabul et."

Gerekçe: sessizlik nötr değil. Model bir öncel olmadan çalışmıyor — kendi
eğitim öncelini kullanıyor ve bunu beyan etmiyor. Projenin her yerinde
"veri yok"u zorla söyletiyoruz (`veri_durumu` aracının varlık sebebi bu);
burada da aynısı geçerli. Nokta tahmini n≥20'de, **bilinmezlik beyanı her zaman**.

Eşiğin kendisi (20) doğru mertebede: n=20, p=0.5'te Wilson aralığı kabaca
±%22 — yani n=20 bile bir yön iddiası için zayıf. 20'yi "konuşmaya başlama"
eşiği olarak tut, "güvenme" eşiği olarak değil.

**Rasyonalizasyon sorusuna cevap (senin §5.2):** Hayır, gerekçen sağlam.
Ama gerekçenin bir özelliği var: **süresi dolmuyor.** "Henüz ölçülecek şey yok"
cümlesi, döngü bozuk kaldığı sürece her gün doğru kalır ve maddeyi süresiz
erteler. Kuyrukta bir konum yerine bir **tetikleyici** ver:
*"herhangi bir sinyal tipinde n≥20 olduğu gün P0-3'ün defter kaynağı devreye girer."*
Ertelemenin sorunu niyet değil, zorlayıcı bir sınırın olmaması.

---

## 1.2 P1-8 — düzeltmeyi kabul ediyorum, ve düzeltmen sandığından geniş

"En az iki farklı makro rejim" formülasyonunu kabul ediyorum. 285/300 ölçümü
benim gerekçemi çürütüyor: bireysel drawdown ile rejim çeşitliliğini
karıştırmışım. Sonuç aynı, gerekçe seninki.

İş Yatırım sorusuna dürüst cevap: **varsayımdı, dayanağım yoktu.**
`lookback_days: 250` × 1.6'yı gördüm ve bunu bir *kaynak tavanı* sandım; oysa
bizim seçtiğimiz bir pencere. Kaynağın gerçekte ne verdiğini hiç ölçmedim.

Burada asıl söylemek istediğim şu: bu hata ile §2'de bulduğun ölü konfigürasyon
**aynı hata.** Binance tablosundaki "1000 bar" da, İş Yatırım'daki "5+ yıl" da
`settings.yaml`'ı gerçekliğin tarifi sanmaktan geliyor. Yani §2 benim
kaçırdığım ayrı bir bulgu değil — **§1.1'deki hatamın teşhisi.** Bu, §2'yi
listede yukarı taşıyor, aşağı değil.

---

## 2. Ölü konfigürasyon — ayrı madde değil, ortak kök

Sıralamada 3. sıra doğru (ucuz, tek dosya, düğmelerin gerçekten düğme olması).
Ama P2-11 ile birleştirme sorusuna cevabım: **evet birleşmeli, ama refactor
olarak değil, değişmez (invariant) olarak.**

Üç şeyi tek başlık altında görüyorum:

| Belirti | Nerede |
|---|---|
| Prompt "FX yok" diyor, `fx` aracı var | `chat.py:79` |
| Prompt "BIST + Avrupa ETF'leri" diyor, portföy kripto ağırlıklı | `strategist.py:20` |
| YAML `daily_bars: 1000` diyor, kod başka yola bakıyor | `settings.yaml` ↔ `binance.py` |

Hepsi aynı sınıf: **beyan edilen durum ile gerçek durumun sessizce ayrışması.**
Üçü de testlerden geçerek bugüne geldi, çünkü hiçbir test beyanı gerçekle
karşılaştırmıyor.

Doğru düzeltme temizlik değil, iki değişmez:

1. `s.get()` ile okunan her yol ya YAML'da tanımlı olmalı ya da açıkça
   kayıtlı bir kod varsayılanı olmalı. Test: kaynak kodu tara, YAML'a karşı
   karşılaştır, fark varsa kırıl. 22 sapmanın hepsini bugün yakalar,
   23.'yü yarın yakalar.
2. Prompt'ta beyan edilen yetenek listesi kodla **üretilsin**, elle yazılmasın
   (bkz. §4.2).

Yani "ölü konfigürasyon temizliği" 3. sırada kalsın, ama çıktısı bir liste
değil **bir test** olsun. Yoksa altı ay sonra aynı belgeyi tekrar yazarız.

---

## 3.1 `signal_id` — gözden kaçtı, katılıyorum

Bilerek dışarıda bırakmadım; §5.8'de soru olarak sordum ve maddeye çevirmeyi
atladım. Gerekçen doğru: bağ olmadan backtest ile defter iki ayrı ada kalır.
P0-2 göçüne dahil.

Bunun bir sonucu daha var: `signal_id` varsa **hangi sinyal tipinin tahmine
dönüştüğü** de ölçülebilir hâle gelir. Şu an "panel hangi sinyalleri
yorumlamaya değer buldu" sorusu cevapsız — bu, sinyal tiplerinin kendi
isabetinden ayrı ve bilgi taşıyan bir metrik (panel eleme yapıyor mu, yoksa
kendisine verilen her şeye görüş mü üretiyor).

## 3.2 Uygulanabilirlik bayrağı — ayrı madde

§3.5'in parçası değil, çünkü iki farklı arıza:

- **§3.5 (pozisyon bağlamı)**: gözlem doğru ve uygulanabilir, ama kullanıcı onu
  boyutlandıramıyor. Çözümü **yeni hesap** (oynaklık, portföydeki örtüşme).
- **§3.2 (uygulanabilirlik)**: gözlem hiç uygulanabilir değil, kullanıcı emir
  veremiyor. Çözümü **mevcut bayrağı iki katmana taşımak** — sıfır hesap.

Maliyetleri ve sahipleri farklı. Ayrıca ikincisi P2-11'in rapor şablonu
düzenlemesiyle aynı dosyada bitiyor; oraya iliştir.

---

## 4.1 Persona — yazılı ayrım yeterli değil, ama tek politika da doğru eksen değil

"Rapor = gözlem, sohbet = görüş" bir **kanal** ayrımı. Kanalların iki ucunda
aynı kullanıcı var ve ikisini de okuyor. Bunun öğrettiği davranış şu:
*"gerçek görüş istiyorsan sohbette sor."* Yani kullanıcı, daha temkinli
katmanın etrafından dolaşmayı öğrenir. Bu bir tasarım hatası olur.

Ayrımın gerçek ekseni kanal değil, **kanıt gücü**:

> Görüş, kanıt taşıdığı her yerde verilir; kanıt taşımadığı hiçbir yerde verilmez.
> İddianın gücü kanıtın gücüyle sınırlıdır. Emir iletme yetkisi hiçbir katmanda yok.

Rapor ile sohbetin gerçek farkı persona değil **derinlik bütçesi**: rapor 50
enstrümanı tarıyor, her birine ayıracak muhakemesi yok; sohbet tek konuya
iniyor. Bunu böyle yaz — "rapor görüş vermez" diye değil, "rapor tarama
yoğunluğunda çalışır, tek enstrümanda derinleşmek için sohbete geç" diye.
Sonuç pratikte seninkine yakın çıkar, ama kullanıcıya öğrettiği şey farklı.

## 4.2 Prompt testleri — daha güçlü değişmez var

Önerdiğin iki test doğru. Ama ikincisini genelleştirmenin daha iyi yolu
**test etmemek, imkânsız kılmak**:

Prompt'ların "neye erişebilirsin" bölümü elle yazılmasın, `ARAC_ADLARI` ve
araç açıklamalarından **import zamanında üretilsin**. O zaman `chat.py` kural 9
tipi bir sapma yazılamaz hâle gelir — test edilecek bir şey kalmaz.

Test, üretilemeyen kısımlar için: kademe tanımları, para birimi kuralları,
belirsizlik dili. Onlar için senin (a) maddendeki "tek metinden geliyor mu"
testi doğru araç.

Genel ilke, §2'deki config testiyle aynı: **sürüklenmeyi yakalamaktansa
üretimle imkânsız kıl; imkânsız kılamadığını test et.**

---

## 5. Savunmacılık — üç noktaya cevap

**1. "Tablo eski" demen.** Savunmacı değil. Ölçülmüş bir olguyu düzelttin ve
sonucumu yine de kabul ettin. Aksine burada kendini az savundun: Binance'i
3 yıla çıkarmış olman, "tek makro rejim" tespitini **güçlendiriyor** — 3 yıllık
kripto verisi de tek rejim. Kendi işini lehine değil, aleyhine kullanmışsın.

**2. P0-3'ü sona atman.** Rasyonalizasyon değil; ama gerekçenin süresi dolmuyor
(yukarıda §1.1). Tetikleyici koy, mesele biter.

**3. Ölü konfigürasyonu ayrı başlık yapman.** Fazla ağırlık vermemişsin, **az**
vermişsin. Onu "incelemecinin kaçırdığı bir kusur" diye çerçevelemişsin; oysa
o, incelemecinin **neden yanlış bir tablo yazdığının açıklaması** — ve bu hata
bir gün içinde dışarıdan bir incelemede gerçekleşti. Depoda epistemik bir
tuzak var ve kanıtı elimizde. Başlığı hak ediyor.

Ekleyeceğim bir tane daha var, senin listenden: **§C.6'nın ("ölçülemiyor")
kendisi bir bulgu.** `_json_cek`'in iz bırakmaması yüzünden geçmiş
cevaplanamıyor. Bu, "gözlemlenebilirlik yoksa hata sınıfı görünmez" ilkesinin
örneği ve P0-2'yi haklı çıkarmaktan fazlasını yapıyor: **panelin ham çıktısı
saklanmalı** (şu an `analysis_runs`'ta ajan metni yok). Sayaç ileriye dönük
çözüyor; ham metin saklamak, gelecekteki bilmediğimiz soruları da çözüyor.

---

## 6. Sıralamada bir iç tutarsızlık

Kendi sıranda 9. madde diyor ki *"P2-10 (tez) P0-2 göçüyle aynı anda yapılırsa
tek şema değişikliğine iner."* Doğru — ama o zaman 9. sırada olamaz.

Öneri: **şema göçü tek seferde ve baştan tam yapılsın**, mantık sonra gelsin.

1. adımda `predictions`'a eklenecekler:
`ajan`, `signal_id`, `tez`, `gecersizlesme_kosulu`, `izlenecek_esik`
(+ atılan görüşlerin kaydı için ayrı tablo ya da `predictions`'ta
`atildi` bayrağı).

Kolon eklemek bedava, göç tekrarı değil. `tez` kolonu bir hafta boş dursun;
P2-10 sırası geldiğinde yalnızca yazma ve kontrol mantığı eklenir, şemaya
dokunulmaz.

---

## 7. Backtest'in ilk çıktısı hakkında — ölçümün değiştirdiği bir tasarım

§C.1'deki sayı P0-1'in tasarımını değiştiriyor, sadece takvimini değil:
**342 enstrümanın 282'sinde 500 bardan az var; ≥500 barlı yalnızca 60.**
BIST'te 251 sembolde 13,5 ay.

Bu şu demek: backtest'in ilk sorusu *"bu sinyal tipinin isabeti nedir"* değil,
**"bu sinyal tipi bu veriyle ölçülebilir mi"** olmalı. Yani ilk çıktı bir
isabet tablosu değil, bir **güç analizi**: tür × venue kırılımında kaç
sinyal-günü var, Wilson aralığı ne kadar geniş, hangi hücre konuşulabilir
hangisi konuşulamaz.

Somut sonuç: `signal_stats` tablosunda `n` ve `guven_araligi` zorunlu alan
olsun, ve `taban_oran` aracı **aralığı da döndürsün**. Aralık geniş olduğunda
model bunu görmeli — nokta tahmini tek başına verilirse, kendi ölçüm
belirsizliğimizi modele sahte kesinlik olarak servis etmiş oluruz. Bu, defterin
n<20 disiplininin backtest karşılığı.

BIST hücresinin ilk turda "ölçülemez" çıkması muhtemel ve bu bir başarısızlık
değil, doğru cevap — P1-8'in gerekçesini de sayıyla desteklemiş olur.

---

## 8. Mutabakat özeti

Senin §6'daki listeye ek olarak kapanan noktalar:

- P0-3 sıralamada geri, **ama kaynağı defter değil backtest**; bilinmezlik
  beyanı her zaman enjekte edilir, nokta tahmini n≥20'de
- P1-8 gerekçesi: makro rejim çeşitliliği. İş Yatırım derinliği **varsayımdı**,
  ölçülmeli
- Ölü konfigürasyon = bayat prompt = tek kusur sınıfı; çıktısı temizlik değil
  **iki değişmez test**
- `signal_id` P0-2 göçüne girer; ayrıca "sinyal→tahmin dönüşüm oranı" metriğini
  açar
- Uygulanabilirlik bayrağı ayrı madde, P2-11 rapor şablonuyla aynı dosyada
- Persona: kanal değil **kanıt gücü** ekseni + derinlik bütçesi
- Prompt yetenek listesi üretilsin, test edilmesin
- Şema göçü **tek seferde tam** (P0-2 + P2-10 kolonları birlikte)
- Panelin ham çıktısı saklanmalı
- `signal_stats` güven aralığı zorunlu; ilk çıktı güç analizi

Açık kalan tek şey İş Yatırım'ın gerçek derinliği. Onu ölçmeden P1-8'in
takvimi yazılamaz.
