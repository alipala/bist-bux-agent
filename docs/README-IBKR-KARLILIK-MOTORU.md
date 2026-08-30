# IBKR Kârlılık Motoru — Tartışma ve Uygulama Taslağı

> **Belgenin amacı:** Bu metin, mevcut IBKR al-sat motorunun nasıl daha ölçülebilir,
> daha güvenli ve kârlı olma ihtimali daha yüksek bir sisteme dönüştürülebileceğini
> tartışmak için hazırlanmıştır. Hem yatırım/algoritma uzmanı olmayan birinin
> anlayacağı dilde özet verir hem de başka bir local agent'ın kendi uygulama
> önerisini geliştirebilmesi için teknik karar noktalarını açık bırakır.
>
> **Önemli:** Hiçbir strateji kâr garantisi vermez. Buradaki hedef “kesin kazanan
> formül” bulmak değil; gerçekçi maliyetlerden sonra ölçülebilen, yanlışlanabilen,
> risk sınırları belli bir sistem kurmaktır.

## 1. Kısa sonuç

Mevcut sistemin en güçlü tarafı emir güvenliğidir; en zayıf tarafı ise stratejinin
para kazandırdığına dair kanıttır.

Motor bugün:

- Günlük fiyatlardan kendi alım adayını üretebiliyor.
- Pozisyon büyüklüğü ve teorik stop hesaplayabiliyor.
- Mevcut IBKR pozisyonları için çıkış sinyali üretebiliyor.
- Emri insan onayı olmadan gönderemiyor.

Fakat bugünkü Donchian 20/10 + 2 ATR stratejisi için güvenilir bir kârlılık kanıtı
yoktur. On yıllık testte işlem başına ortalama pozitif görünse de aynı hisselerdeki
rastgele giriş kontrolü daha iyi sonuç vermiştir. Ayları bağımsız gözlem kabul eden
sonuç da negatiftir. Canlı veride henüz skorlanmış strateji sinyali yoktur.

Bu nedenle önerilen sıra şudur:

1. Önce ölçüm ve portföy simülasyonu düzeltilmeli.
2. Küçük sermaye için düşük frekanslı ETF stratejisi kurulmalı.
3. Tekil hisselerde fiyat momentumu ile temel veri değişimi birleştirilmeli.
4. SEC/KAP gibi resmî olaylar önce risk filtresi olarak kullanılmalı.
5. Genel haber ve LLM katmanı ancak ayrı katkısı kanıtlanırsa alım sinyaline
   dönüştürülmeli.

## 2. Basit sözlük

Bu belgede kullanılan terimlerin sade karşılıkları:

- **Momentum:** Son dönemde diğerlerine göre daha güçlü yükselen varlığın bir süre
  daha güçlü kalabileceği düşüncesi.
- **Trend:** Fiyatın genel yönü. Örneğin fiyatın 200 günlük ortalamanın üstünde
  olması uzun vadeli yükseliş göstergesi sayılabilir.
- **Alpha:** Piyasanın veya uygun karşılaştırma endeksinin üzerinde kalan getiri.
- **Benchmark:** Sonucu kıyasladığımız basit alternatif; örneğin SPY veya QQQ alıp
  tutmak.
- **Slippage:** Karar verilen fiyat ile emrin gerçekten gerçekleştiği fiyat
  arasındaki fark.
- **Drawdown:** Portföyün önceki zirvesinden gördüğü en büyük düşüş.
- **Survivorship bias:** Bugün hayatta olan şirketleri geçmişte de varmış gibi
  test ederek iflas eden veya endeksten çıkan şirketleri dışarıda bırakma hatası.
- **Walk-forward test:** Bir dönemde kuralları seçip daha sonraki, görülmemiş
  dönemde test etmek; sonra pencereyi ileri taşımak.
- **Point-in-time veri:** Geçmişte bir karar verilirken gerçekten bilinebilecek
  veri. Sonradan düzeltilmiş veya gelecekte eklenmiş bilgi kullanılmaz.

## 3. Mevcut durumun önemli bulguları

### 3.1 Fiyat stratejisi

Mevcut ana sistem:

- Önceki 20 günün en yükseğinin üzerinde kapanışta alım adayı üretir.
- 20 günlük ATR'nin 2 katını stop mesafesi olarak kullanır.
- 10 günlük düşük seviyede veya stop koşulunda çıkış sinyali üretir.
- Yalnızca long, yani yükseliş yönlü çalışır.
- Günde en fazla iki aday seçer.
- İşlem başına yaklaşık `%1` risk hedefler.

Bu kurallar anlaşılır ve deterministiktir. Sorun, tek başına yeterli ekonomik
üstünlük göstermemiş olmasıdır.

### 3.2 Geriye dönük test sonucu

2016-09-01 ile 2026-08-28 arasındaki ABD koşumunda:

- 30.129 uygulanabilir işlem oluşmuştur.
- Varsayılan `%0,4` gidiş-dönüş maliyetle işlem başına beklenti `+%0,314` görünür.
- Aynı sembollerde rastgele giriş kontrolü `+%0,721` üretmiştir.
- Kuralın rastgeleye farkı `-%0,353` olmuştur.
- Aylık eşit ağırlıklı ortalama `-%0,133` olmuştur.
- Pozitif ay oranı `%46,7`, aylık t-istatistiği `-0,58` olmuştur.

Yani “işlem ortalaması pozitif” demek tek başına yanıltıcıdır. Az sayıdaki büyük
kazanç, çok sayıdaki küçük kaybı işlem-ağırlıklı ortalamada gizleyebilir.

### 3.3 Canlı kanıt

Denetim anındaki yerel veritabanında:

- 14 strateji kaydı vardır.
- Bunların hiçbiri sonuçlanıp skorlanmamıştır.
- Dört IBKR emir kaydından yalnızca biri gerçekleşmiştir.
- Yapılandırılmış gerçek dolum fiyatı ve komisyon alanları henüz yeterli değildir.

Bu yüzden canlı isabet oranı, gerçek slippage, net beklenti veya canlı maksimum
düşüş hesaplanamamaktadır.

## 4. Önerilen ana mimari

Yeni motor dört ayrı sorumluluğa bölünmelidir:

```text
Veri ve olay toplama
        ↓
Sinyal/alpha motorları
        ↓
Portföy ve risk motoru
        ↓
IBKR emir ve mutabakat motoru
```

Bu ayrım önemlidir:

- Sinyal motoru “hangi varlık daha cazip?” sorusuna cevap verir.
- Portföy motoru “ne kadar almalıyım ve toplam riskim ne?” sorusuna cevap verir.
- Emir motoru “IBKR'de bunu güvenli ve izlenebilir nasıl uygularım?” sorusuna
  cevap verir.
- Bir sinyalin güçlü olması risk kurallarını geçersiz kılamaz.

## 5. İlk öneri: küçük sermaye için ETF momentum motoru

Mevcut kayıtlarda çok küçük kesirli bir ABD emrinde tek yön komisyon yaklaşık
`%0,94` ölçülmüştür. Böyle küçük emirlerde sık tekil hisse alıp satmak stratejinin
olası üstünlüğünü komisyona bırakır.

Bu nedenle ilk otonom strateji olarak daha az işlem yapan ETF modeli düşünülmelidir.

### 5.1 Örnek evren

- Riskli: SPY, QQQ, IWM ve gerekirse birkaç sektör ETF'i.
- Savunma: kısa vadeli ABD tahvili/nakit vekili, IEF ve gerekirse GLD.
- İlk sürümde kaldıraç ve short kullanılmamalıdır.

### 5.2 Örnek karar yöntemi

Her hafta ölçüm yapılır, fakat yalnızca ayda bir veya iki haftada bir yeniden
dengeleme yapılır:

1. Her ETF'in 6 ve 12 aylık momentumu hesaplanır.
2. Fiyatın 200 günlük veya 10 aylık ortalamanın üzerinde olup olmadığına bakılır.
3. Trend filtresini geçenler kendi aralarında sıralanır.
4. En güçlü bir veya iki varlık seçilir.
5. Pozisyonlar gerçekleşen volatiliteye göre küçültülür veya büyütülür.
6. Hiçbiri trend filtresini geçmiyorsa savunma/nakit durumuna geçilir.
7. Emir bir sonraki seansın gerçekçi fiyatıyla modellenir.
8. IBKR `/whatif` maliyeti kabul edilen eşiği aşıyorsa işlem yapılmaz.

Bu modelin avantajları:

- Daha az işlem ve daha düşük toplam maliyet.
- Veri geçmişi daha temiz ve kolay doğrulanabilir.
- Gerçek portföy simülasyonu daha kolaydır.
- Haber katmanı olmadan da bağımsız bir baseline oluşturur.

Dezavantajı, tekil şirket olaylarından yararlanamaması ve uzun süre piyasadan
geride kalabilmesidir.

## 6. İkinci öneri: Quality–Event Momentum

Yeterli sermaye ve geçmiş veri oluşunca tekil hisseler için dört parçalı bir sıralama
sistemi kurulabilir.

### 6.1 Göreli fiyat momentumu

Yalnızca “fiyat yükseldi mi?” değil, “sektörüne ve piyasaya göre daha güçlü mü?”
sorusu sorulmalıdır.

Önerilen özellikler:

- 12–1 aylık getiri: son bir ayı dışarıda bırakan 12 aylık momentum.
- 6–1 aylık getiri.
- Sektör ETF'i ve QQQ/SPY etkisinden arındırılmış artık momentum.
- Fiyatın 100 ve 200 günlük ortalamalara göre durumu.
- Mevcut 20 günlük Donchian kırılımı, ana strateji yerine kısa vadeli teyit.

### 6.2 Temel veri ivmesi

SEC XBRL verilerinden sadece şirketin “iyi” olup olmadığı değil, son dönemde
iyileşip iyileşmediği ölçülmelidir:

- Ciro büyümesinde hızlanma.
- Brüt ve operasyonel marj değişimi.
- Operasyonel nakit akışı değişimi.
- Nakit akışı ile muhasebe kârının uyumu.
- Tahakkukların yükselmesi veya düşmesi.
- Net borç değişimi.
- Hisse sayısındaki artış ve sulandırma.

Buradaki önemli eksik, analist beklentisidir. Yalnız XBRL gerçekleşen verisiyle
“beklentiyi aştı” denemez. Gerçek earnings surprise isteniyorsa lisanslı konsensüs
verisi gerekir. Alternatif olarak şirketin kendi geçmişindeki aynı çeyreğe göre
standartlaştırılmış sürpriz kullanılabilir; bunun adı açıkça yazılmalıdır.

### 6.3 Resmî olay skoru

İlk aşamada sadece birincil kaynaklar işlem mantığına girmelidir:

- SEC 8-K / 6-K özel durumları.
- 10-Q / 10-K finansal raporları.
- Guidance artışı veya düşüşü.
- Önemli sözleşme veya müşteri kaybı.
- Geri alım, sermaye artırımı veya sulandırma.
- Finansal yeniden beyan/restatement.
- Yönetici değişimi.
- Düzenleyici soruşturma veya dava.
- Birleşme ve satın alma gelişmeleri.

### 6.4 Risk ve fiyat teyidi

Olumlu açıklama tek başına alım nedeni olmamalıdır. Haber zaten fiyatlanmış veya
piyasa tarafından olumsuz yorumlanmış olabilir.

Kontrol edilebilecek noktalar:

- Olaydan sonraki ilk gerçekçi işlem fiyatı.
- Piyasa ve sektör etkisinden arındırılmış fiyat tepkisi.
- Hacim artışı.
- ATR ve gap büyüklüğü.
- Piyasa/sector trendi.
- VIX ve gerçekleşen volatilite.

## 7. Haber ve LLM katmanı nasıl kurulmalı?

### 7.1 Mevcut iyi parçalar

Repository'de kullanılabilecek önemli yapı taşları vardır:

- Genel RSS ve JSON-LD toplama: `src/finagent/collectors/news.py`
- Doğrulanmış şirket adıyla haber arama: `src/finagent/collectors/stocknews.py`
- Birincil SEC dosyalamaları: `src/finagent/collectors/edgar.py`
- XBRL finansallar: `src/finagent/collectors/xbrl.py`
- Fed ve TCMB takvimi: `src/finagent/collectors/takvim.py`
- Haber dosyası hazırlama: `src/finagent/analysis/haber_ilgi.py`
- Piyasa modeline göre olay/CAR analizi: `src/finagent/analysis/events.py`

Kaynakların kademe 1–4 olarak ayrılması ve aynı olayın farklı haberlerinin bağımsız
sinyal sayılmaması doğru tasarım yönleridir.

### 7.2 Mevcut haber verisinin sınırı

Yerel veritabanında 7.145 haber vardır. Bunların 2.514'ü kademe 1–2, yalnızca
493'ü güvenilir ve sembole bağlıdır. Bu 493 kaydın 477'si Ağustos 2026'ya aittir.
SEC/KAP disclosure arşivi de ağırlıkla Temmuz–Ağustos 2026 dönemindedir.

Bu veriyle geçmiş haber modeli eğitmek veya haber stratejisinin kârlı olduğunu
söylemek doğru değildir. Model büyük ölçüde tek ayın piyasa koşulunu öğrenir.

Öncelik:

1. SEC'in resmî toplu arşivinden geçmiş dosyalamaları geri doldurmak.
2. Gerçek yayın/kabul zamanlarını saklamak.
3. Genel haber için lisanslı point-in-time geçmiş veri bulmak veya sistemi ileriye
   dönük en az 6–12 ay çalıştırmak.
4. Güncellenen haberlerin eski sürümünü silmeyip versiyonlamak.

SEC'in submissions ve XBRL API'leri gerçek zamana yakın güncellenir ve toplu JSON
arşivleri sunar:
[SEC EDGAR API belgeleri](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

### 7.3 LLM'nin doğru görevi

LLM doğrudan “AL/SAT” kararı vermemelidir. Haberi aşağıdaki gibi yapılandırılmış
bir olaya çevirmelidir:

```json
{
  "symbol": "ORNEK",
  "event_type": "guidance_change",
  "direction": "positive",
  "magnitude": null,
  "published_at_utc": "2026-08-31T20:15:00Z",
  "market_session": "after_close",
  "source_tier": 1,
  "entity_confidence": 0.98,
  "novelty": "new_event",
  "expected_horizon": "20_to_60_trading_days",
  "uncertainty": "medium",
  "invalidation": "guidance withdrawn or margins deteriorate",
  "evidence_url": "https://..."
}
```

LLM'nin çıktısı doğrulanmalı ve şu kurallar deterministik olmalıdır:

- Kademe 3–4 haber tek başına işlem açamaz.
- Şirket eşleştirme güveni düşükse haber kullanılmaz.
- Aynı olayın tekrar haberleri tek olay olarak kümelenir.
- Sayısal büyüklük metinde yoksa LLM sayı uyduramaz; `null` döner.
- Kaynak zamanı belirsizse en erken işlem bir sonraki seans kabul edilir.
- LLM güven puanı pozisyon risk limitini aşamaz.

### 7.4 İlk kullanım: haber bir risk filtresi olsun

Haber katmanının kârlı katkısı kanıtlanana kadar:

- Olumsuz resmî dosyalama yeni alımı engellesin.
- Yüksek önem taşıyan fakat sınıflandırılamayan olay pozisyonu küçültsün.
- Normal strateji, bilanço açıklamasından hemen önce yeni pozisyon açmasın.
- Haber akışı bayatsa haber sinyali kapansın; fiyat stratejisi bağımsız çalışsın.
- Pozitif haber yalnızca mevcut fiyat/kalite adayının sırasını yükseltsin.
- Haber tek başına yeni aday yaratmasın.

İleri testte haberli model fiyat-only modeli sürekli geçerse, haber bağımsız alpha
bileşeni olarak değerlendirilebilir.

## 8. Makro katman nasıl kullanılmalı?

Mevcut sistemde SPX, Nasdaq, DXY, US10Y, VIX, altın, petrol ve başka makro seriler
bulunmaktadır. Fed ve TCMB takvimi de vardır.

Makro veri tek tek hisse seçmekten çok toplam portföy riskini ayarlamak için
kullanılmalıdır:

- Gerçekleşen volatilite yükselince bütün pozisyonları orantılı küçültmek.
- Piyasa düşüş trendindeyken yeni long pozisyon riskini azaltmak.
- FOMC gibi önceden bilinen yüksek riskli olaylardan önce yeni işlem sayısını
  sınırlamak.
- Tek bir VIX eşiği yerine tarihsel yüzdelik veya sürekli ölçekleme kullanmak.

Momentum stratejileri, sert piyasa düşüşü sonrası yüksek volatiliteyle gelen hızlı
dönüşlerde büyük kayıp yaşayabilir. Bu nedenle makro katmanın görevi yön tahmininden
önce risk azaltmaktır:
[Momentum Crashes — NBER](https://www.nber.org/papers/w20439) ve
[Volatility Managed Portfolios — NBER](https://www.nber.org/papers/w22208).

## 9. Pozisyon ve portföy yönetimi

Bugünkü motor işlem başına risk ve pozisyon tavsiyesi üretir; ancak gerçek portföy
seviyesinde aşağıdakiler de gerekir:

- Kullanılabilir nakit.
- Aynı anda açık pozisyon sayısı.
- Tek hisse üst sınırı.
- Sektör üst sınırı.
- Toplam gross ve net exposure.
- Benzer hareket eden hisselerin korelasyon riski.
- Günlük ve aylık zarar limiti.
- Portföy drawdown limiti.
- Gerçekleşen ve beklenen işlem maliyeti.

Başlangıç için sabit `%25` tek pozisyon tavanı çok yüksektir. Uygun sınır hesap
büyüklüğüne ve strateji çeşitliliğine bağlıdır; agent bunu ayrı senaryolarla
önermelidir. Örneğin 5 hisselik ve 25 hisselik portföy aynı tek-hisse sınırını
kullanmamalıdır.

Pozisyon büyüklüğü için basit başlangıç seçenekleri:

- Eşit ağırlık.
- Ters volatilite ağırlığı.
- ATR tabanlı sabit risk.
- Sektör başına eşit risk.

İlk sürümde optimizasyon tabanlı karmaşık portföy yerine ters volatilite + sert
yoğunlaşma limitleri daha denetlenebilir olacaktır.

## 10. Emir ve stop tasarımı

Sinyal ile gerçek emir arasında şu gerçekler modellenmelidir:

- Kapanıştan sonra üretilen sinyal aynı kapanıştan gerçekleşemez.
- Limit emir hiç dolmayabilir veya kısmen dolabilir.
- Stop seviyesinin altında gap oluşabilir.
- İnsan onayı gecikebilir.
- Komisyon, bilet büyüklüğüne göre oransal olarak çok değişebilir.

İlk güvenli uygulama:

1. Sinyal kapanıştan sonra kesinleşir.
2. En erken işlem sonraki seans açılışı veya tanımlı bir VWAP penceresidir.
3. IBKR `/whatif` komisyonu ve tahmini toplam maliyet hesaplanır.
4. Maliyet işlemin beklenen avantajının belirli bölümünü aşıyorsa emir verilmez.
5. Dolum sonrası koruyucu broker-native stop/bracket oluşturulur veya sistemin
   neden oluşturmadığı açıkça kayıt altına alınır.
6. Kısmi dolum, iptal ve bilinmeyen durum mutabakatla çözülür.

## 11. Doğru test yöntemi

Stratejiler birbirine karıştırılmadan ayrı ayrı test edilmelidir:

1. Eşlenmiş rastgele kontrol.
2. Fiyat-only momentum.
3. Fiyat + temel veri.
4. Fiyat + temel veri + resmî olay.
5. Fiyat + temel veri + resmî olay + genel haber.

Her aşama bir öncekinin üzerinde gerçek katkı sağlamalıdır. Beşinci model iyi
çıkarsa bunun haberden mi, daha fazla parametreden mi kaynaklandığı ancak bu şekilde
anlaşılır.

### 11.1 Zorunlu test kuralları

- Tarihsel endeks üyeliği point-in-time olmalı.
- Endeksten çıkan ve iflas eden hisseler dahil edilmeli.
- Fiyatlar kurumsal işlemlere göre doğru ayarlanmalı.
- Sinyal zamanı ile gerçek işlem zamanı ayrılmalı.
- Komisyon, spread, slippage ve dolmayan emir modellenmeli.
- Eş zamanlı pozisyonlar ve sınırlı sermaye modellenmeli.
- Çakışan getiri pencereleri bağımsız gözlem sayılmamalı.
- Sonuç işlem bazında değil gün/ay kümeleriyle de raporlanmalı.
- Strateji görünmemiş walk-forward dönemlerde ölçülmeli.
- Çok sayıda parametre denenirse multiple-testing cezası uygulanmalı.

### 11.2 Sağlamlık kontrolleri

Bir strateji:

- İşlemi bir gün geciktirince tamamen çökmemeli.
- Tahmini maliyet iki katına çıkınca bütün avantajını kaybetmemeli.
- Tek bir ay veya en iyi `%5` işlem tarafından taşınmamalı.
- Sadece boğa döneminde değil, en az iki farklı piyasa rejiminde ölçülmeli.
- SPY/QQQ ve eşlenmiş rastgele kontrolden net olarak daha iyi olmalı.
- Birkaç parametrenin küçük değişiminde sonuç tamamen tersine dönmemeli.

## 12. Başarı ölçüleri ve canlıya geçiş kapıları

İsabet oranı ana başarı ölçüsü olmamalıdır. Trend stratejileri düşük isabetle ama
büyük kazananlarla çalışabilir.

### Ana KPI'lar

1. Maliyet sonrası benchmark üzeri bileşik getiri.
2. Aylık information ratio veya alpha güven aralığı.
3. Maksimum drawdown ve Calmar oranı.

### Tanı ölçüleri

- İşlem sayısı ve bağımsız karar günü/ayı.
- Ortalama kazanç ve ortalama kayıp.
- Turnover.
- Komisyon ve slippage kaynaklı getiri kaybı.
- Emir dolum oranı ve kısmi dolum oranı.
- Sektör ve tek hisse yoğunlaşması.
- En iyi `%5` işlemin toplam kâra katkısı.
- Haberli modelin fiyat-only modele ek katkısı.

### Geçici GO/NO-GO ilkeleri

Kesin eşikler hesap büyüklüğü ve kabul edilen risk belirlendikten sonra yazılmalıdır.
Ancak en az şu ilkeler aranmalıdır:

- Walk-forward dönemlerin çoğunda maliyet sonrası alpha pozitif.
- Sonuç uygun benchmark ve rastgele kontrolün üzerinde.
- Bir günlük gecikme ve 2x maliyet stresinde sistem tamamen negatif olmuyor.
- Maksimum drawdown kullanıcının önceden belirlediği sınırı aşmıyor.
- Haber katmanı eklendiğinde sonuç yalnız eğitim döneminde değil test döneminde de
  iyileşiyor.
- Yeterli gölge/paper dönem görülmeden otomatik canlı emir açılmıyor.
- Canlı maliyet ve dolum sapması, backtest varsayımından düzenli olarak kötüleşirse
  motor otomatik olarak risk azaltıyor veya duruyor.

## 13. Önerilen uygulama sırası

### P0 — Ölçüm temelini düzelt

- Sonraki seans dolum modeli.
- ABD backtest'inden BIST limit-down varsayımını kaldırma.
- Sermaye kısıtlı portföy simülatörü.
- Point-in-time evren ve delisted veri.
- CAGR, Sharpe, max drawdown, Calmar ve turnover raporu.
- IBKR gerçek komisyon/slippage veri modeli.

### P1 — Basit ve bağımsız baseline'lar

- ETF momentum baseline'ı.
- Tekil hisse göreli momentum baseline'ı.
- Sabit parametreli walk-forward test.
- Champion/challenger karşılaştırması.

### P2 — Temel veri ve resmî olaylar

- SEC geçmiş submission/XBRL backfill.
- Gerçek kabul/yayın zamanının saklanması.
- Temel veri ivme özellikleri.
- 8-K/6-K/10-Q/10-K olay taksonomisi.
- Olay türü bazında non-overlapping CAR araştırması.

### P3 — Haber ve LLM

- Haber sürümleme ve immutable arşiv.
- Entity resolution güven puanı.
- Olay tekilleştirme/kümeleme.
- LLM yapılandırılmış olay çıkarımı.
- Haber risk filtresi.
- Haberli ve habersiz modelin ileri testi.

### P4 — Kontrollü otomasyon

- Gerçek kill switch.
- IBKR hesabı ile yetkili sahibin kesin eşleşmesi.
- Fail-closed buying power/portfolio kontrolleri.
- Broker-native stop/bracket yaşam döngüsü.
- Günlük zarar ve drawdown durdurucuları.
- Önce shadow, sonra çok küçük canlı, en son kademeli sermaye artışı.

## 14. Diğer local agent için tartışma görevi

Bu belgeyi inceleyecek agent'tan doğrudan kod yazmadan önce aşağıdaki çıktılar
istenmelidir:

1. **Eleştiri:** Bu önerilerde yanlış, gereksiz veya eksik gördüğü noktalar.
2. **Alternatif mimari:** En az iki strateji tasarımı:
   - küçük sermaye için ETF tabanlı,
   - yeterli sermaye için tekil hisse tabanlı.
3. **Mevcut kodla eşleme:** Hangi modüller aynen kullanılabilir, hangileri
   değiştirilmeli, hangileri yeni yazılmalı?
4. **Veri modeli:** Hangi yeni tablolar/alanlar gerekir? Özellikle:
   - olay zamanı ve sürümü,
   - haber kümesi,
   - feature snapshot,
   - portföy günlük equity,
   - emir/dolum maliyeti.
5. **Backtest tasarımı:** Point-in-time evren, yürütme modeli, maliyet modeli,
   benchmark ve istatistik yöntemi.
6. **Risk tasarımı:** Pozisyon, sektör, gross exposure, günlük zarar, drawdown ve
   kill switch önerileri.
7. **Uygulama planı:** Küçük, test edilebilir ve geri alınabilir adımlar.
8. **Kabul testleri:** Her adımın tamamlandığını hangi test ve sayılar gösterecek?
9. **NO-GO kriterleri:** Hangi sonuçlarda strateji terk edilmeli veya yeniden
   tasarlanmalı?

Agent şu sorulara özellikle cevap vermelidir:

- Mevcut sermaye ve ölçülen komisyonlar düşünüldüğünde ETF modeli mi, tekil hisse
  modeli mi önce gelmeli?
- Momentum skoru için hangi ufuklar kullanılmalı ve neden?
- Fiyat momentumu sektör/piyasa etkisinden nasıl arındırılmalı?
- XBRL'den point-in-time özellikler nasıl üretilecek; sonradan açıklanan/düzeltilen
  veri geçmişe sızmadan nasıl saklanacak?
- SEC filing kabul zamanı nasıl alınacak?
- Genel haber için tarihsel arşiv olmadan hangi özellikler yalnız ileri testte
  tutulmalı?
- LLM hatalı sembol veya sayı üretirse hangi deterministik doğrulamalar devreye
  girecek?
- Portföy ve execution simülatörü üretim emir motoruyla hangi ortak kodu
  paylaşmalı?
- İnsan onaylı sistemden tam otomasyona hangi ölçülebilir kapılarla geçilmeli?

## 15. Agent'a verilebilecek kısa prompt

```text
docs/README-IBKR-KARLILIK-MOTORU.md belgesini ve belgede adı geçen mevcut
modülleri incele. Henüz üretim kodunu değiştirme.

Belgedeki önerileri finans, algoritmik işlem, veri kalitesi, portföy riski ve
IBKR execution açısından eleştir. Küçük sermaye için ETF tabanlı ve yeterli
sermaye için tekil hisse tabanlı en az iki alternatif tasarla.

Her alternatif için:
- açık ve deterministik sinyal kuralları,
- gereken point-in-time veri,
- haber/LLM'nin sınırlandırılmış rolü,
- portföy ve risk kuralları,
- gerçekçi backtest ve walk-forward planı,
- benchmark/kontrol grupları,
- maliyet ve execution modeli,
- GO/NO-GO ölçütleri,
- mevcut modüllerden yeniden kullanılacaklar,
- gerekli yeni tablo/modül/testler,
- adım adım implementasyon planı
sun.

Varsayımları açık yaz. Kârlılık garantisi verme. Mevcut veri bir iddiayı
desteklemiyorsa bunu net biçimde belirt. Özellikle haber arşivinin büyük ölçüde
Ağustos 2026'da yoğunlaştığını ve canlı skorlanmış strateji sinyali olmadığını
hesaba kat.
```

## 16. Referanslar

- Mevcut teknik denetim: `artifacts/ibkr-engine-audit/report.html`
- Mevcut strateji belgesi: `docs/finagent-strateji-motoru.md`
- Mevcut momentum deneyi: `docs/momentum-sinavi.md`
- Momentum araştırması: [Jegadeesh & Titman — NBER](https://www.nber.org/papers/w7159)
- Momentum çöküşleri: [Daniel & Moskowitz — NBER](https://www.nber.org/papers/w20439)
- Volatilite kontrollü portföyler: [Moreira & Muir — NBER](https://www.nber.org/papers/w22208)
- Haber bağlamı, risk ve getiri: [Calomiris & Mamaysky — NBER](https://www.nber.org/papers/w24430)
- SEC EDGAR veri API'leri: [SEC resmi belge](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- PEAD literatür özeti: [Post-Earnings-Announcement Drift incelemesi](https://doi.org/10.1016/j.jbef.2020.100446)


---

# 17. İkinci agent'ın değerlendirmesi ve önerisi (2026-08-31)

> **Bu bölüm, §1–16'yı okuyan ve ardından belgede önerilen sınavları
> fiilen KOŞAN agent tarafından eklendi.** §1–16 değiştirilmedi.
> Buradaki her sayı ölçüldü; hiçbiri tahmin değil. Ölçüm betikleri
> `scripts/` altında, dondurulmuş parametreler ve sonuçlar
> `docs/momentum-sinavi.md`'de.

## 17.1 Raporun doğrulanan kısımları

§3'teki sekiz sayısal iddianın hepsi veritabanına karşı doğrulandı:
30.129 uygulanabilir işlem, `+%0,314` beklenti, rastgele `+%0,721`,
fark `−%0,353`, aylık `−%0,133`, pozitif ay `%46,7`, `t=−0,58`,
14 skorlanmamış strateji kaydı, 4 emirden 1'i gerçekleşmiş.

**§7.2'nin haber iddiası doğru ve EKSİK söylenmiş — durum daha kötü:**

```
news         6.426 / 7.145  (%90) 2026-08
disclosures  1.620 / 1.656  (%98) 2026-08
```

Sonuç aynen geçerli: bu veriyle haber stratejisi geriye dönük test
edilemez, yalnızca ileriye dönük biriktirilebilir.

## 17.2 §10 ve §11.2 haklıydı — ve bu belgeden SONRAKİ ölçümleri de vurdu

İki eleştiri sınandı: (a) kapanışta üretilen sinyal aynı kapanıştan
gerçekleşemez, (b) maliyet 2× olunca üstünlük kaybolmamalı. 56 hücrelik
ızgara altı yapılandırmada yeniden koşuldu:

| gecikme | maliyet | getiri kenarı | düşüş kenarı |
|---|---|---|---|
| 0 bar | ×1 | 31/56 (%55) | **45/56 (%80)** |
| 0 bar | ×2 | 31/56 (%55) | **39/56 (%70)** |
| 1 bar | ×1 | 31/56 (%55) | **47/56 (%84)** |
| 1 bar | ×2 | 31/56 (%55) | **39/56 (%70)** |
| 5 bar | ×1 | 32/56 (%57) | **47/56 (%84)** |
| 5 bar | ×2 | 32/56 (%57) | **44/56 (%79)** |

Düşüş bulgusu altı yapılandırmanın altısında da ayakta. Getiri bulgusu
hiçbirinde şansı (%50) geçmiyor.

## 17.3 Belgeden sonra koşulan sınavlar — sonuçları §5 ve §6'yı değiştiriyor

`docs/momentum-sinavi.md` referanslarda var ama **sonuçları yazılmadan
önce** yazılmış. Sonuçlar §5 ve §6'nın dayandığı varsayımı bozuyor.

### A) Kesitsel momentum, ABD hisseleri — SONUÇ KULLANILAMAZ

10 yıl, 510 sembol, çeyreklik. Ham sonuç `+%15,5/çeyrek` (bileşik
`+%18.162`). **Bu bir kenar değil, hayatta kalma yanlılığının ölçümü.**
Kanıt:

```
evren (474 sembol) 10 yıllık eşit ağırlıklı al-tut   +%372
medyan sembol                                        +%166
10 YILDA para kaybeden                                55/474 (%12)
10 kat ve üzeri artan                                 38/474
kıyas SPX                                            +%255
```

Gerçek bir evrende 10 yılda hisselerin %30–40'ı kaybeder. Burada %12,
çünkü kaybedenler endeksten çıkarıldı ve veride yoklar. Kuralın
seçtikleri (NVDA, TSLA, CVNA, MSTR, APP) tam da o "10 kat artan 38"
kovasından. **§6'nın tekil hisse tasarımı bu evrende ölçülemez.**

### B) Sektör ETF rotasyonu (§5'in birinci önerisi) — ELENDİ

11 SPDR sektör ETF'i, 12-1 momentum, çeyreklik, N=2, 106 çeyrek:

| | bileşik | yıllık | en derin düşüş |
|---|---|---|---|
| KURAL | +221,9% | 4,5% | **−49,0%** |
| AL-TUT SPX | +427,3% | 6,5% | −47,6% |

Rastgele ETF seçimini geçiyor ama **al-tut'tan az kazanıp daha derin
düşüyor**. Nakit kapısı 106 çeyreğin 4'ünde çalıştı, hepsi çöküşten
SONRA — dipte satıp toparlanmayı kaçırdı.

### C) Endeks zamanlaması, 8 piyasa, ~400 endeks-yılı — İKİYE AYRILIYOR

Kural: 12 aylık getiri > 0 → tut, değilse nakit. Aylık kontrol.
Kontrol: **dairesel kaydırma** (aşağıda, 17.5).

```
kaydırılmış kontrolü geçen (GETİRİ)  : 31/56 (%55)   [şansta ~%50]
kaydırılmış kontrolü geçen (DÜŞÜŞ)   : 45/56 (%80)   [şansta ~%50]
```

Piyasa düzeyinde çoğunluk: getiri **4/8**, düşüş **7/8** (binom p≈0,035).
Nikkei dahil (1989'dan beri yatay piyasa) düşüş 6/7.

**GETİRİ KENARI YOK. DÜŞÜŞ KENARI VAR** — ve düşüş, **aynı maruziyetteki**
kontrole karşı ölçüldü, yani "daha az yatırımda kaldığı için" değil.

## 17.4 Belgeye dört itiraz

**(1) §13/P0'daki "point-in-time evren ve delisted veri" bir görev değil,
bir SATIN ALMA kararıdır.** CRSP/Compustat/Norgate gerektirir; ücretsiz
kaynağı yoktur. Madde listesinde görev gibi durduğu için yol haritasını
belirsiz süre bloke eder.

**Ve gerekmiyor.** Yanlılık veri satın alarak değil **evren seçerek**
aşılabilir: endeksler ve sektör ETF'leri delist olmuyor, üyelik sabit,
seçim yok. 400 endeks-yılı bu yolla, ücretsiz, bir günde ölçüldü.
Belgenin kaçırdığı en yüksek getirili kısayol budur.

**(2) Maliyet aritmetiği bağlanmamış.** §3.2 backtest'in `%0,4`
varsayımını, §5 gerçek `%0,94`ü söylüyor; ikisi hiç çarpılmıyor.
Ölçülen gerçek maliyet (IBKR `/whatif`, 2026-08-30):

```
tutar 5→100 USD · tam hisse ve kesirli · 3 ayrı kağıt
komisyon = TAM %1,00 tek yön, HER BOYUTTA          -> gidiş-dönüş %2,00
```

Bu bir tarife değil, **Tiered/Fixed'in %1 TAVANI**; küçük emirlerde
asgari ücret (0,35 / 1,00 USD) tavanı aştığı için tavan bağlıyor.
Kritik eşik **35 USD**: altında iki tarife de %1, üstünde Tiered
`0,35 USD` sabitine düşüyor (100 USD'lik bilette %0,35).

**Sonuç, belgedeki en önemli sayı ve yazılmamış:** `+%0,314` beklenti,
gerçek maliyetle **−%1,3** olur. §6'nın tekil hisse kolu bu hesapla
mevcut sermayede yaşayamaz.

**(3) §5.2/5'teki volatiliteye göre pozisyon ölçekleme** 100 EUR'da 1–2
pozisyonla anlamsızdır. 50 EUR'luk bir pozisyon ölçeklenemez.

**(4) §14/§15 ölçümden önce haftalarca şartname istiyor. Sıra ters.**
Ölçüm ucuz (yukarıdaki dört sınav bir günde koştu), şartname pahalı.
Belgenin sorduğu soruların bir kısmı bugünkü veriyle **zaten
cevaplanabilirdi**.

## 17.5 Metodoloji: iki kontrol hatası (ikinci agent bunları tekrarlamasın)

**(a) Kontrol grubunu haksız cezalandırma.** İlk kontrolüm her ay
bağımsız zar atıyordu → ayda `2p(1−p)=%42` durum değişimi → **yılda ~5
işlem**, kural ise 1. Kontrole yılda ~%3 fazladan komisyon yüklüyordum
ve kuralın "üstünlüğünün" bir kısmı kontrolün cezasıydı. Düzeltince
fark `+%3,7` → `+%1,8` düştü.

> **DOĞRU KONTROL — DAİRESEL KAYDIRMA.** Kuralın KENDİ durum dizisini
> rastgele bir noktadan döndür. İşlem sayısı, piyasada kalma oranı ve
> blok uzunlukları AYNI kalır; yalnızca ZAMANLAMA rastgele olur. Kenar
> gerçek bir zamanlama becerisiyse kaydırma onu yok etmelidir.

**(b) Çarpık dağılımda ortalama raporlama.** Kaydırılmış 200 turun
%94'ü kuralın ALTINDA iken *ortalama* kuralın ÜSTÜNDE çıkıyordu —
birkaç şanslı kaydırma ortalamayı çekiyor. **Medyan ve yüzdelik
raporlanmalı.**

**(c) Düşüşü al-tut'a karşı ölçme.** Al-tut hep %100 yatırımda, kural
~%70. Daha az maruziyet düşüşü zaten azaltır; bu beceri değildir.
Düşüş, **aynı maruziyetteki** kaydırılmış kontrole karşı ölçülmeli.

## 17.6 Belgenin sormadığı ve kararı belirleyen soru: İSTATİSTİKSEL GÜÇ

§12 "walk-forward dönemlerin çoğunda maliyet sonrası alpha pozitif"
kapısını koyuyor. **Bu kapı canlıda hiçbir zaman geçilemez.**

Yıllık `σ=%15` oynaklıkta bir `μ` alfayı `t=2` ile görmek için gereken
süre `T=(2σ/μ)²`:

```
μ = %2/yıl   ->  T ≈ 225 yıl
μ = %5/yıl   ->  T ≈  36 yıl
μ = %10/yıl  ->  T ≈   9 yıl
```

Getiri kenarı ancak **backtest'te** doğrulanabilir; canlı hesap bunun
için asla yeterli gözlem üretmez. Ve backtest'te üç kez denendi
(Donchian, kesitsel momentum, sektör rotasyonu), üçünde de çıkmadı.

Düşüş kenarı farklıdır: çöküşler seyrek ama büyüktür, sinyal/gürültü
oranı çok yüksektir, ve 400 endeks-yılında zaten ölçüldü.

**Bu, "alfa arama, riski yönet" sonucunu bir tercih değil, bir güç
hesabı yapar.** Belgenin bütün alfa odaklı mimarisi (§6, §7, §11'in
5 aşaması) doğrulanamayacak bir hedefe kurulmuş.

## 17.7 ÖNERİLEN YÖN

### Hedefi değiştir: alfa motoru değil, RİSK YÖNETİLMİŞ MARUZİYET motoru

Ürün tarifi dürüstçe şudur:

> *"Piyasayla aşağı yukarı aynı kazanırsın, ama çöküşlerde yarısı kadar
> kaybedersin. Yılda ~1 işlem, maliyeti ~%0,4."*

Bu, ölçülmüş ve altı stres yapılandırmasında ayakta kalmış tek bulgudur.
Alfa vaadi yoktur ve verilmemelidir.

### Kural (dondurulmuş, sadeleştirilmiş)

```
EVREN      tek geniş ABD ETF'i (SPY veya muadili)
SINYAL     12 aylık getiri > 0  ->  tut,  değilse  nakit
KONTROL    aylık (ayın son işlem günü kapanışı)
YURUTME    sinyal kapanışta kesinleşir, emir SONRAKI SEANS
           (§10 haklı; 1-5 bar gecikme sonucu bozmuyor, ÖLÇÜLDÜ)
BILET      >= 35 USD  (altında komisyon %1 tavanına yapışır)
BEKLENTI   getiri ~ piyasa · azami düşüş ~ piyasanın YARISI
           işlem ~1/yıl · maliyet ~%0,4/yıl
```

Not: parametre olarak 12 ay dondurulmuş haliyle korunmalı. Izgarada
3–12 ay bandı 15–24'ten iyi görünüyor **ama ızgarayı görüp bandı
daraltmak fitting olur** — yapılmadı, yapılmamalı.

### Uygulama sırası (küçük, geri alınabilir adımlar)

1. **Dolum halkasını kapat.** 4 emrin dördünde de `dolum_fiyat` NULL.
   Tek bir gerçek dolum + kayıt, Adım 5'i kapatır ve canlı slippage'ı
   ilk kez ölçülebilir kılar. **Bu, canlı verinin doğrulayabileceği tek
   şeydir** (alfa değil, YÜRÜTME KALİTESİ).
2. **Kuralı gölgede koştur.** Ayda bir karar üret, deftere yaz, emir
   verme. 3–6 ay.
3. **İnsan onaylı canlıya al.** Mevcut `emirakis` yolu değişmeden.
4. **Otomasyon kapıları: §13/P4 aynen alınmalı** — belgenin en iyi
   bölümü orasıdır. Kill switch, günlük zarar ve drawdown durdurucu,
   broker-native stop, fail-closed buying power, önce gölge sonra çok
   küçük canlı.

### YAPILMAMASI önerilenler (şimdilik)

- **Point-in-time / delisted veri satın alma.** Evren seçerek aşılıyor.
- **§6 Quality-Event Momentum.** Mevcut sermayede maliyetle ölür
  (−%1,3/işlem) ve doğrulanacak alfa ölçülemedi.
- **§7 haber/LLM alfa katmanı.** Verinin %90'ı tek aydan; ileriye
  dönük 6–12 ay biriktirmeden test edilemez. §7.4'teki **risk filtresi**
  rolü meşru ve ucuz — yalnızca o yapılabilir.
- **Yeni kural sınıfları için yanlılıklı evrende arama.** Yeni bir kural
  denenecekse **önce** yanlılıksız bir evren (endeks/ETF) seçilmeli.

### NO-GO ölçütleri

- Gölge dönemde kuralın kararları backtest'in ürettiğinden **sistemli
  olarak** sapıyorsa (yürütme/veri hatası) — dur, sebebi bul.
- Canlı komisyon+slippage, varsayılan `%1,23` gidiş-dönüşü **kalıcı
  olarak** aşıyorsa — bilet büyüklüğünü artır ya da dur.
- Kural, azami düşüşü piyasanınkinin altında tutmuyorsa (tek gerekçesi
  bu) — terk et.
- **Alfa çıkmadı diye terk ETME:** alfa hiç vaat edilmedi.

## 17.8 Yeniden kullanılabilir varlıklar

| ne | nerede |
|---|---|
| Saf momentum çekirdeği (I/O yok) | `src/finagent/analysis/momentum.py` |
| Kabuk: ortak takvim + hizalama | `src/finagent/analysis/momentum_kosu.py` |
| Sağlamlık ızgarası + stres | `scripts/momentum_saglamlik.py` |
| Dünya endeksi arşivi (~400 yıl) | `scripts/endeks_arsiv_cek.py` |
| Sektör ETF verisi | `scripts/etf_veri_cek.py` |
| Düşüş karşılaştırması | `scripts/momentum_dusus.py` |
| ABD trend koşumu | `run.py trend --piyasa abd` |
| Dondurulmuş parametreler + sonuçlar | `docs/momentum-sinavi.md` |

Emir yolu (`bot/emirakis.py`, `ibkr/emir.py`) **değiştirilmemeli**:
`gonder()` bool değil `OnayFisi` alıyor ve bu yapısal koruma bir AST
testiyle kilitli. §4'ün katman ayrımı zaten büyük ölçüde mevcut.
