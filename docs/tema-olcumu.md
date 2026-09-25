# Tema ölçümü (IBKR MCP Faz 5.0) — 2026-09-25

Tek seferlik ölçüm. `get_company_themes` BUX + IBKR'deki 14 hisse için bir
kez çağrıldı (commit'siz betik, yalnızca okuma). Ağırlıklar 25 Eyl son
anlık görüntülerinden, EUR (QCOM USD × 0,85). Portföy 5.406 EUR: hisse
3.918 (%72), ETF 1.385 (CNDX, VUSA, RBOT, 4GLD, SPACEX), nakit ~103.

## Sonuç

Bir tema birden çok şirkete bağlı; yüzdeler TOPLANMAZ.

| tema | toplam portföyün % | hisselerin % | şirketler |
|---|---|---|---|
| Semiconductor Chips | 54,9 | 75,8 | ASML, NVDA, MRVL, QCOM, AVGO |
| AI Infrastructure | 54,3 | 74,9 | ASML, NVDA, MRVL |
| Artificial Intelligence | 51,7 | 71,4 | ASML, AMZN, TSLA, MSFT, NOW, CRWD |
| Semiconductor Equipment | 40,3 | 55,7 | ASML |
| Data Centers | 21,6 | 29,9 | NVDA, AMZN, MSFT, MRVL, CRWD, PLTR, AVGO |

Bu bir ALT SINIR: CNDX ve VUSA'nın içindeki NVDA/AVGO/MSFT (içerik
açılımı) sayılmadı. Yarı iletken/AI ailesine bağlı 9 hisse portföyün
%59'u.

## Devam şartı

Ön kayıt: "en büyük üç temadan en az biri bizim sınıflandırmamızdan 10
puandan fazla farklı ya da bizde görünmeyen ortak bir risk".

**Bizde sınıflandırma YOK.** CPGW pozisyon yanıtındaki `sector` alanı okunuyor
ama saklanmıyor; BUX'ta hiç yok. (a) maddesi sıfıra karşı kıyaslandığı için
kendiliğinden geçiyor. Bu dejenere bir geçiş, o yüzden karar
otomatik uygulanmadı ve Ali'ye bırakıldı. İçerik açısından ise şart
anlamlı olarak da sağlanıyor: toplam portföyün yarısından fazlası tek bir
temada (yarı iletken) ve bu oran hiçbir bot çıktısında görünmüyordu.

## Sahada bulunan

- **Tema, şirketin ANA listelemesine bağlı.** ASML'in ABD conid'i
  (117902840, NASDAQ) boş liste döndü, Amsterdam conid'i (117589399) altı
  tema döndü. Boş yanıt "tema yok" demek değil. AVTX de boş döndü, ana
  listeleme denenmedi.
- Yanıt: `{"linked_themes": [{name, key, description, linked_companies,
  total_count}]}`; çağrı başı ~13 sn.
