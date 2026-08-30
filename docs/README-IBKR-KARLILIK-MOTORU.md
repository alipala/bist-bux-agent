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

