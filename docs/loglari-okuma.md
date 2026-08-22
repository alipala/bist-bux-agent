# Logları terminalden okuma

Hangi dosyanın ne tuttuğu ve gürültüyü nasıl eleyeceğin.

## Hangi log neyi tutuyor

| Dosya | İçerik | Kim yazıyor |
|---|---|---|
| `data/agent.log` | **Her şeyin ana akışı** — bot, worker, collector, şema | Python logger |
| `data/bot.log` | Telegram botu ve sohbet worker'ları | `run.py bot` |
| `data/gunici.log` | Gün içi koşu (30 dk'da bir, koruma + tez + taktik) | `run_gunici.sh` |
| `data/pulse.log` | Zamanlanmış koşular: sabah / öğle / kapanış / nabız | `run_kosu.sh` |

## Günlük kullanım

```bash
cd ~/github/bist-bux-agent

# Canlı izle (en çok işe yarayan)
tail -f data/agent.log

# Kütüphane gürültüsünü ele — asıl olayı görürsün
tail -f data/agent.log | grep -viE "bundled|site-packages|_warn_if|CanUseTool"

# Son gün içi koşu ne yaptı
tail -40 data/gunici.log

# Bugünkü hatalar
grep -E "ERROR|CRITICAL" data/agent.log | tail -20
```

## Belirli bir şeyi aramak

```bash
# Bir sembolün geçtiği her yer
grep -i "ASELS" data/agent.log | tail -20

# Panel kesildi mi, bütçe kısıldı mı
grep -E "SURE SINIRINDA|butcesi.*kisildi|butcesi yetmiyor" data/pulse.log | tail

# Kabuk bir koşuyu öldürdü mü
grep "sn asildi, olduruluyor" data/pulse.log | tail

# Taktik üretildi mi
grep -E "gunici-tarayici|taktikci" data/gunici.log | tail -20

# Bekçi ne zaman alarm gönderdi
grep "BILDIRIM GONDERILDI" data/*.log | tail
```

## Koşu izleri — "gerçekten çalıştı mı"

Log satırı bir koşunun **başladığını** gösterir; iz dosyası **bittiğini**.
Öldürülen koşu iz bırakmaz.

```bash
ls -la data/bot/kosu/          # sabah / ogle / kapanis / nabiz / gunici
cat data/bot/kosu/ogle.json    # son öğle koşusunun özeti
```

## Süreçler

```bash
launchctl list | grep finagent          # yüklü işler ve son çıkış kodu
ps -eo pid,lstart,etime,command | grep "run.py"
```

`launchctl list` çıktısında ikinci sütun son çıkış kodudur: `0` temiz,
`-15` SIGTERM ile öldürülmüş.

## Not

Bu dosyalar döndürülmüyor (log rotation yok); `agent.log` şu an ~1,8 MB.
Büyürse `/temizle` komutu eski medyayı siler ama logları silmez.
