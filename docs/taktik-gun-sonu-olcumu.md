# Taktik gün sonu ölçümü — tasarım önerisi

**1 Eylül 2026.** Ali'nin isteği: gün içi taktikler, ilgili borsa kapandıktan
sonra kontrol edilsin ve sistem kendi önerilerinin ne kadar tuttuğunu
analiz etsin. Varılmak istenen nokta: **önerilerin tutarlılık analizi ve
doğruluk oranı.**

Bu belge önce mevcut durumu ölçüyor, sonra alanın yerleşik yöntemlerini
özetliyor, sonra tasarımı ve karar gerektiren noktaları koyuyor.

---

## 1. Mevcut durum — ölçüldü, varsayılmadı

Taktikler `predictions` tablosuna yazılıyor; giriş, stop ve bir **ufuk**
taşıyorlar. `Defter.puanla()` onları puanlıyor ve `_tetiklendi()` girişin
tetiklenip tetiklenmediğine bakıyor. Ama puanlama kapısı şu:

```python
if len(sonrasi) < p["ufuk_gun"]:   # ufuk dolmadiysa ATLA
    continue
```

Ölçüm **ufuk dolunca** yapılıyor, gün sonunda değil. Sonucu:

```
179 taktik           21 Agu - 1 Eyl
ufuklar              3, 7, 10, 14, 20, 30 gun
OLCULEN              1
taktik_tetiklendi=1  0
```

179 taktiğin **biri** ölçülmüş. Karnedeki `1/20` bu. Bu tempoyla eşiğe
ulaşmak aylar alır; band o süre boyunca "sicil yok" demeye devam eder.

Taktik türleri ve güven dağılımı:

```
bekle   85        guven 0,50  32
alim    66              0,55  25
koruma  28              0,60   8
                        0,65+  8   (0,75'e kadar)
```

**Güven dar bir bantta.** Model neredeyse her zaman "yazı-turadan biraz
iyi" diyor. Bu ölçülmemiş bir alan: `guven` hiçbir yerde puanlanmıyor.

---

## 2. Alanın yerleşik yöntemleri

### 2.1 Üç bariyer (triple-barrier)

López de Prado'nun *Advances in Financial Machine Learning* (2018) ile
yerleşen yöntem: bir işleme üç sınır konur — üstte kâr al, altta zarar
kes, ve sağda **zaman sınırı**. Sonuç, fiyatın hangi bariyere **önce**
değdiğiyle etiketlenir.

Bizim için önemi: **bu tam olarak bizim veri modelimiz.** `taktik_giris`,
`taktik_stop` ve `ufuk_gun` üç bariyerin karşılığı. Eksik olan tek şey
*yolun* değerlendirilmesi — hangi bariyere önce değdiğine bakılmıyor,
yalnızca ufuk sonundaki fiyata bakılıyor.

Sabit ufuklu etiketlemenin sorunu literatürde açık: gerçekte olan şeyi
değil, o günkü kapanışı ölçüyor. Üç bariyer "işlemde gerçekte ne olurdu"
sorusuna cevap veriyor.

### 2.2 Taban oran olmadan isabet oranı anlamsız

Analist tavsiyelerine dair yerleşik bulgu: yön çağrılarının isabet oranı,
**kıyas ölçütü ve stil maruziyeti hesaba katılınca** çoğu zaman tesadüfe
yakın çıkıyor. Ayrıca sistematik bir iyimserlik yanlılığı var.

Bunun bizdeki karşılığı doğrudan: "%63 isabet" tek başına hiçbir şey
söylemez. Karşılaştırılması gereken şey **hiçbir şey yapmamak** ve
**endeksin kendisi**. İkincisinin altyapısı bugün kuruldu
(`endeks_karsilastir`, göreli hareket).

### 2.3 Kalibrasyon — Brier skoru

Bir tahmin olasılık taşıyorsa (bizde `guven`), yalnızca yön değil
**olasılığın doğruluğu** da ölçülür. Brier skoru, olasılık ile
gerçekleşen sonuç arasındaki ortalama kare hatadır; ikili bir problemde
0,25 civarı "rastgele" demektir.

Bizde `guven` yazılıyor ama **hiç puanlanmıyor**. Ve dağılımı 0,50-0,55'te
kümelenmiş: model kendi güvenini neredeyse hiç ayrıştırmıyor. Brier
ölçümü bunu görünür kılar.

### 2.4 Gecikme (lag) disiplini

Sinyal üretildiği barda doldurulmuş varsaymak **uygulanabilir değildir**.
Literatürde Lag 0 (aynı bar) açıkça "implementable değil" diye
işaretleniyor ve Sharpe oranının lag arttıkça belirgin düştüğü
gösteriliyor.

Bizim için: taktik saat 11:00'de yayımlandıysa, ölçüm **yayımdan sonraki**
barlardan başlamalı. Yayım barındaki fiyatla doldurulmuş saymak, kendi
kendimizi kandırmak olur.

### 2.5 Çoklu deneme

Deflated Sharpe Ratio (Bailey & López de Prado) ve ilgili literatürün
ortak uyarısı: her parametre varyasyonu bir deneme sayılır, ve deneme
sayısı arttıkça tesadüfen iyi görünen bir sonuç bulma olasılığı artar.

Bizde: 11 günde 179 taktik. Karne isabet oranını **güven aralığıyla**
veriyor (%45,5-78,1) — bu doğru ve korunmalı. Aralık %50'yi içerdiği
sürece "işe yarıyor" denemez.

**Kaynaklar:**
[Triple-barrier yöntemi](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/) ·
[Deflated Sharpe Ratio (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) ·
[Sinyal bozunumu ve lag](https://arxiv.org/pdf/2504.18600) ·
[Analist isabet oranları](https://www.bitget.com/wiki/how-accurate-are-stock-analysts) ·
[Üç bariyerin uygulaması](https://link.springer.com/article/10.1186/s40854-025-00866-w)

---

## 3. Tasarım

### 3.1 Ne ölçüyoruz — ve ne ölçmüyoruz

Gün sonu kontrolü, ufuk puanlamasının **yerine geçmez**. İkisi farklı
soruları cevaplar ve karıştırılırsa ikisi de anlamsızlaşır:

| | soru | geri bildirim |
|---|---|---|
| **Ufuk puanlaması** (var) | tez doğru muydu | 3-30 gün |
| **Gün sonu kontrolü** (yeni) | taktik uygulanabilir miydi, seansı geçti mi | aynı akşam |

Gün sonu kontrolü **beceri ölçmez, uygulanabilirlik ve dayanıklılık
ölçer.** Bu ayrım tasarımın merkezi.

### 3.2 Tetikleyici

`piyasa.seans_kapandi_mi(borsa, tarih)` — bugün yazıldı. Her taktiğin
kendi borsası kapandıktan sonra ölçülür; BIST 18:00, ABD 16:00 New York.
Tek bir "gün sonu" yok, **borsa başına** var.

### 3.3 Tür başına "tuttu" tanımı

Üç bariyer çerçevesiyle, yayım barından **sonraki** barlar üzerinde:

**`alim`** — üç sonuç:
- `giris_tetiklenmedi`: fiyat giriş seviyesine hiç değmedi → taktik
  uygulanamazdı. Bu bir isabet değil, **ölçülemez** bir gündür.
- `stop_yendi`: giriş sonrası stop'a değdi → taktik seansı geçemedi.
- `ayakta`: giriş tetiklendi, stop yenmedi → seansı geçti.

**`koruma`** — elde olan bir kağıdı korumak:
- `stop_yendi` / `dayandi`. Giriş yok, yalnızca stop yolu ölçülür.

**`bekle`** — en zor tanım ve **bilerek ayrı tutulmalı**:
- "Bekle" bir işlem değil, işlem *yapmama* önerisi. Aynı gün "tuttu mu"
  sorusunun karşılığı: o gün bir giriş fırsatı kaçtı mı? Bu, taktiğin
  kendi seviyesi olmadığı için ölçülemez.
- **Önerim: `bekle` gün sonu ölçümüne HİÇ girmesin.** 85 taktiğin (%47)
  ölçüme girmemesi, uydurma bir tanımla ölçülmesinden iyidir.

### 3.4 Yanına konması zorunlu iki kıyas

Gün sonu isabeti tek başına yayımlanmamalı. Her ölçümün yanında:

1. **Endeks hareketi** — o gün piyasa ne yaptı (bugün kuruldu).
2. **Taban oran** — aynı gün, aynı borsada, taktik verilmeyen kağıtların
   ne kadarı aynı ölçütü geçti. "Taktiklerimin %60'ı ayakta kaldı" ancak
   piyasanın %80'i ayakta kaldıysa **kötü** bir sonuçtur.

İkincisi kritik ve genelde atlanan şey. Araştırmanın da işaret ettiği
nokta bu: kıyassız isabet oranı tesadüfü beceri gibi gösterir.

### 3.5 Kalibrasyon

Her ölçülen taktiğin `guven` değeriyle sonucu eşleştirip **Brier skoru**
tutulur. Bugünkü dağılım (0,50-0,55'te kümelenme) göz önüne alınırsa ilk
sonucun "model güvenini ayrıştırmıyor" çıkması muhtemel — ve bunu
**bilmek** istiyoruz.

### 3.6 Gecikme kuralı

Ölçüm, taktiğin `olusma_ts`'inden **sonraki ilk bardan** başlar. Yayım
barı dahil edilmez. Bu kural koda gömülmeli ve testle bağlanmalı; aksi
halde sonuçlar sistematik olarak iyimser çıkar.

### 3.7 Raporlama

Gün sonu ölçümü **ayrı bir karne** olarak tutulur, mevcut karneye
karışmaz. Mesajda:

```
Bugun 4 taktik olculdu · 2 ayakta · 1 stop yendi · 1 giris tetiklenmedi
Endeks: -%0,4 · ayni gun taban oran: %71 ayakta
Brier (guven kalibrasyonu): 0,26   [n=4 — SONUC CIKARMA]
```

Örneklem küçükken **açıkça söylenmeli**. Mevcut karne bunu zaten yapıyor
("ORNEKLEM YETERSIZ") ve o disiplin korunmalı.

---

## 4. Karar gereken noktalar

Bunları ben seçersem, ölçtüğüm şey senin kastettiğin şey olmayabilir:

1. **`bekle` ölçüme girsin mi?** Önerim hayır (%47'si düşer ama tanım
   uydurulmaz). Alternatif: yalnızca "o gün giriş fırsatı doğdu mu"
   diye ölçmek — ama bu taktiğin kendi seviyesi olmadan yapılamaz.

2. **Stop/giriş yoluna hangi barlardan bakılacak?** Saatlik barlar var
   (BIST, ABD, kripto). Saatlik yeterli mi, yoksa gün içi en yüksek/en
   düşük yeterli mi? Saatlik daha doğru ama kapsamı dar.

3. **Taban oran hangi evrenden?** Aynı borsadaki tüm taranan kağıtlar mı,
   yoksa yalnızca eşiği geçen adaylar mı? İkincisi daha adil bir kıyas
   ama örneklemi küçük.

4. **Fren bu ölçüme de bağlansın mı?** Mevcut fren ufuk karnesine bakıyor
   (20 ölçüm, %50 eşik). Gün sonu karnesi çok daha hızlı dolacağı için
   frene bağlamak cazip — ama farklı bir şeyi ölçtüğü için aynı eşik
   uygulanamaz.

---

## 5. Yapmayı önermediklerim

**Aynı karneye karıştırmak.** Gün sonu ölçümü kolay, ufuk ölçümü zor.
Aynı kovada birleşirlerse isabet oranı yukarı kayar ve hiçbir şey ifade
etmez.

**Gün sonu isabetini "kural çalışıyor" diye okumak.** Ölçtüğü şey
uygulanabilirlik ve dayanıklılık; getiri kenarı değil. Bu deponun dört
strateji sınavında öğrendiği ders burada da geçerli
(`[[strateji-kenari-yok]]`).

**Yayım barını ölçüme katmak.** Literatürde açıkça "implementable değil"
diye işaretli.
