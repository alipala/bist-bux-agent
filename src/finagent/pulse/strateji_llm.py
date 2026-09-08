"""
STRATEJI — LLM YORUM KOLU. Sinyali BASTIRAMAZ, YANINA yazilir.

NEDEN AYRI MODUL
----------------
`pulse/strateji.py` LLM'i ICE AKTARAMAZ (belge §3.2 kural 1) ve bunu
bir AST testi bagliyor: alan mantigi model saglayicisi degisince
degismemeli. Yorum kolu bu yuzden burada. Belge §3.3 "tek yeni dosya"
diyordu; §7'nin istedigi paralel kol o kurala sigmiyor — kurali delmek
yerine IKINCI bir dosya aciliyor ve bagimlilik yonu korunuyor.

NEDEN PARALEL, SUZGEC DEGIL — BELGENIN MERKEZI KARARI
-----------------------------------------------------
LLM araya SUZGEC olarak girseydi ("kural al dedi ama model begenmedi,
gec") olculen sey artik kural degil KURAL+LLM BILESIMI olurdu ve ikisi
bir daha ayrilamazdi: kotu bir sonucta kimin hatasi oldugu
soylenemezdi.

Paralel yazildiginda ESLESTIRILMIS KIYAS doguyor: ayni sinyal, iki
karar, iki ayri defter satiri (`ajan='strateji'` ve
`ajan='strateji_llm'`). Dort hafta sonra "LLM'in 'bekle' dedigi N
sinyalin kaci gercekten kotuydu" sorusu SAYIYLA cevaplaniyor.

LLM'in VETO HAKKI, karnesi kurali gectiginde verilir — `taktikci`
freninin aynasi. Bugun yok.

MODEL HESAP YAPMAZ
------------------
`seviye.py:5-11` bunu OLCULMUS bir vakayla yaziyor: model bir fiyat
bari GORMEDEN 335 pencerelik istatistik tablosu uretti ve sayilar
KALIBRELIYDI. Yani uydurma, gurultu gibi gorunmuyor — dogru gibi
gorunuyor. Bu yuzden modele verilen sey OLCULMUS SEVIYELER; ham seri
verilmiyor ve modelden sayi HESAPLAMASI istenmiyor. `giris`/`stop`
kuralin urettigi degerlerden AYNEN aliniyor, modelin yazdigindan
DEGIL.

TEK CAGRI, SEMBOL BASINA DEGIL
------------------------------
36 kirilim = 36 cagri demek olurdu. Gunun butun kirilimlari TEK
istemde gidiyor: hem ucuz hem model gunun tamamini gorerek
karsilastiriyor. Cikti sembol basina ayri satir.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

AJAN = "strateji_llm"

# Modelin verebilecegi iki karar. Uctuncu bir secenek YOK: "sat"
# bu katmanda anlamsiz (aciga satis yok) ve serbest metin, defterin
# `yon` alanina cevrilemez.
KARARLAR = ("al", "bekle")

_SABLON = """Sen bir TREND TAKIBI GOZDEN GECIRICISISIN.

Deterministik bir kural (Donchian <GIRIS>/<CIKIS> +
<STOPN>N) bugun asagidaki sembollerde ALIM sinyali uretti. Senin isin
bu sinyalleri ONAYLAMAK ya da BEKLE demek.

MUTLAK KURALLAR
1. HESAP YAPMA. Sana OLCULMUS seviyeler VE OLCULMUS ORANLAR veriliyor:
   kapanis, 20 gunluk yuksek, 2N stop, 10 gunluk dip, devir, ve
   `kirilim_marji_%` / `stop_mesafesi_%` / `dip_mesafesi_%`.
   Yuzdeleri YENIDEN HESAPLAMA — verilen degeri OLDUGU GIBI kullan.
   Kendi ortalamani, RSI'ini ya da hedefini de hesaplama. Fiyat serisi
   sana VERILMEDI cunku hesap senin isin degil.
2. SAYI UYDURMA. Elinde olmayan bir olcuye (bilanco, haber, analist
   hedefi) atifta bulunma. Yalnizca verilen alanlardan konus.
3. KARARIN SINYALI DUSURMEZ. "bekle" desen de kural kendi kararini
   yazacak; senin sozun onun YANINA yazilir ve ikisi ayri ayri
   puanlanir. Yani cekingen davranmanin bir odulu yok, cesur
   davranmanin da cezasi — dogru olmaya calis.
4. GEREKCE KISA VE OLCUYE BAGLI olsun (en fazla 200 karakter).
   "Momentum guclu" degil; "kapanis 20G yuksegin %1,2 uzerinde, stop
   mesafesi %5,3" gibi.

CIKTI — YALNIZCA JSON:
{"yorumlar": [{"sembol": "XXX", "karar": "al|bekle",
               "guven": 0.0-1.0, "gerekce": "..."}]}

Her sembol icin TAM BIR satir. Atladigin sembol "gorus vermedi" sayilir
ve karnene GIRMEZ — yani atlamak bir kacamak degil, olcumden cikmak."""


def _oran(pay, payda) -> float | None:
    """`(pay/payda - 1) * 100`, hesaplanamiyorsa None (sifir DEGIL)."""
    try:
        p, q = float(pay), float(payda)
    except (TypeError, ValueError):
        return None
    return round((p / q - 1) * 100, 2) if q else None


def gorunur_alanlar(gorusler: list[dict]) -> list[dict]:
    """
    Modele giden kayit — IC ALANLAR GONDERILMIYOR.

    `instrument_id`, `conid`, `adet` gibi alanlar karar icin gereksiz;
    gonderilmesi modele "bunlari da kullan" demek olurdu.

    ORANLAR ONCEDEN HESAPLANIYOR — MODEL BOLME YAPMASIN.
    Ilk surumde yalnizca ham seviyeler gonderiliyordu ve model
    yuzdeleri KENDI hesapliyordu. Sahada denendi (2026-08-28, 36
    kirilim): alti bagimsiz kontrolun altisi da ondaligina kadar
    DOGRUYDU. Yani model iyi hesapladi — ama her gun kontrol
    edilemez ve LLM aritmetigi bir RISK YUZEYI. `seviye.py`nin dersi
    aynen burada gecerli: "LLM'e verilecek sey OLCULMUS seviyeler,
    hesaplanacak ham veri degil". Bir adim ileri goturuluyor: yalnizca
    seviyeler degil, KARARDA KULLANILAN ORANLAR da olculmus geliyor.

    Uc oran secildi cunku modelin kendi gerekcelerinde tam bunlari
    kullandigi gorulduu:
      `kirilim_marji_%`  girisin 20G yuksegin ne kadar uzerinde oldugu
                         (buyukse "uzamis giris")
      `stop_mesafesi_%`  2N stopun ne kadar asagida oldugu
      `dip_mesafesi_%`   10G dipin ne kadar asagida oldugu
    """
    out = []
    for g in gorusler:
        sv = g.get("seviyeler") or {}
        kapanis, stop = g.get("giris"), g.get("stop")
        out.append({
            "sembol": g.get("sembol"),
            "kapanis": kapanis,
            "yirmi_gun_yuksek": sv.get("donchian_giris"),
            "stop_2n": stop,
            "on_gun_dip": sv.get("donchian_cikis"),
            "para_birimi": sv.get("para_birimi"),
            "devir_medyan": sv.get("devir"),
            "bar_ts": sv.get("bar_ts"),
            # OLCULMUS ORANLAR — model bunlari YENIDEN HESAPLAMAYACAK.
            "kirilim_marji_%": _oran(kapanis, sv.get("donchian_giris")),
            "stop_mesafesi_%": _oran(stop, kapanis),
            "dip_mesafesi_%": _oran(sv.get("donchian_cikis"), kapanis),
        })
    return out


async def _cagir(settings, istem: str, sistem: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    model = settings.get("analysis.llm.strategist_model", "claude-opus-5")
    # ARAC YOK (`allowed_tools=[]`). Iki gerekce:
    #   1. Karar icin gereken her sey istemde; arac cagirmak yalnizca
    #      sure ve para harcardi.
    #   2. §8 sinavi TARIHE CITLENMIS kosacak ve bu depoda arac
    #      yuzeyi tarihe citlenemiyor (`haberler` en yeniyi donduruyor).
    #      Araci simdiden kapatmak, sinav kolunu AYRI kurmak zorunda
    #      kalmamak demek — yani olculen sey ile sinanan sey AYNI.
    opts = ClaudeAgentOptions(system_prompt=sistem, model=model,
                              allowed_tools=[], max_turns=1,
                              max_buffer_size=4 * 1024 * 1024)
    parcalar = []
    async for m in query(prompt=istem, options=opts):
        ic = getattr(m, "content", None)
        if not ic or isinstance(ic, str):
            continue
        for b in ic:
            if getattr(b, "text", None):
                parcalar.append(b.text)
    return "\n".join(parcalar).strip()


def sistem_metni() -> str:
    """
    Prompt'un TAM METNI — tek kaynak.

    §8 sinavi "prompt'un tam metni koşumdan ONCE dondurulacak" diyor;
    metni buradan okumak, dondurulan sey ile kosan seyin AYRI OLMAMASINI
    garanti eder. Elle kopyalanan bir prompt, sessizce ayrisir.
    """
    from ..analysis.trend_takip import CIKIS_PENCERE, GIRIS_PENCERE, STOP_N
    # `%` BICIMLENDIRME KULLANILMIYOR — SAHADA KIRILDI (2026-08-28).
    # Prompt'a `kirilim_marji_%` gibi alan adlari eklenince `%(...)s`
    # bicimlendirici onlari yer tutucu sandi ve `sistem_metni()`
    # TypeError ile patladi. `.format()` de calismaz: metinde JSON
    # ornegi var, yani SUSLU PARANTEZ de dolu. Duz `replace` ikisine de
    # dayanikli — prompt metni hem `%` hem `{}` icerecek ve iceriyor.
    return (_SABLON.replace("<GIRIS>", str(GIRIS_PENCERE))
            .replace("<CIKIS>", str(CIKIS_PENCERE))
            .replace("<STOPN>", f"{STOP_N:g}"))


def gorusleri_kur(yorumlar, gorusler: list[dict], ufuk: int) -> list[dict]:
    """
    Model ciktisini `journal.kaydet` sozlesmesine cevirir. SAF.

    `giris`/`stop` KURALIN degerlerinden aliniyor, modelin yazdigindan
    DEGIL: model hesap yapmiyor ve iki kol AYNI seviyelerde
    olculmezse eslestirilmis kiyas anlamsizlasir.

    Modelin ATLADIGI sembol satir URETMEZ — "gorus vermedi" ile
    "bekle dedi" ayri seyler ve ikincisi bir karardir.
    """
    kural = {str(g.get("sembol")): g for g in gorusler}
    out = []
    for y in (yorumlar or []):
        if not isinstance(y, dict):
            continue
        sem = str(y.get("sembol") or "").strip().upper()
        karar = str(y.get("karar") or "").strip().lower()
        g = kural.get(sem)
        if not g or karar not in KARARLAR:
            # BILINMEYEN SEMBOL YA DA KARAR SESSIZCE DUZELTILMEZ.
            # "al" gibi okunan bir metni "al" saymak, modelin
            # soylemedigi seyi soylemis gibi kaydetmek olurdu.
            log.info("[strateji_llm] atlandi: sembol=%r karar=%r", sem, karar)
            continue
        try:
            guven = min(1.0, max(0.0, float(y.get("guven", 0.5))))
        except (TypeError, ValueError):
            guven = 0.5
        out.append({
            "ajan": AJAN,
            "sembol": sem,
            "yon": "yukari" if karar == "al" else "notr",
            "tur": "alim" if karar == "al" else "bekle",
            "ufuk_gun": ufuk,
            "guven": guven,
            "gerekce": f"[{karar}] " + str(y.get("gerekce") or "")[:300],
            "tez": g.get("tez"),
            "gecersizlesme_kosulu": g.get("gecersizlesme_kosulu"),
            "izlenecek_esik": None,
            # SEVIYELER KURALDAN. Model hesap yapmiyor; iki kol ayni
            # sayilarla olculuyor ki fark KARARDAN gelsin.
            "giris": g.get("giris"),
            "stop": g.get("stop"),
            "giris_kaynak": g.get("giris_kaynak"),
            "stop_kaynak": g.get("stop_kaynak"),
            # Bar tarihi kural gorusunden — LLM satiri da ayni bara ait.
            "bar_ts": g.get("bar_ts"),
        })
    return out


async def yorumla(settings, gorusler: list[dict], ufuk: int) -> dict:
    """
    Gunun kirilimlarina LLM yorumu. Doner:
    `{"gorusler": [...], "hata": str|None, "istem": str}`

    HATA SINYALI DUSURMEZ: model cagrilamazsa kural kolu yine yazilir
    ve mesaj yine gider. Yorum bir EKLENTIDIR, on kosul degil.
    """
    if not gorusler:
        return {"gorusler": [], "hata": None, "istem": ""}
    sistem = sistem_metni()
    istem = ("### BUGUNUN KIRILIMLARI (kural uretti)\n```json\n"
             + json.dumps(gorunur_alanlar(gorusler), ensure_ascii=False,
                          indent=1) + "\n```")
    try:
        ham = await _cagir(settings, istem, sistem)
    except Exception as e:                                 # noqa: BLE001
        log.warning("[strateji_llm] cagri basarisiz: %s: %s",
                    type(e).__name__, e)
        return {"gorusler": [], "hata": f"{type(e).__name__}: {e}",
                "istem": istem}

    from .agents import _json_cek
    veri = _json_cek(ham, anahtar="yorumlar")
    yorumlar = veri.get("yorumlar") if isinstance(veri, dict) else None
    if not isinstance(yorumlar, list):
        # AYRISTIRILAMAYAN CIKTI SESSIZCE BOS SAYILMAZ: sebep doner ve
        # mesaja yazilir. "LLM yorum vermedi" ile "LLM cevabi
        # okunamadi" ayri seyler.
        return {"gorusler": [], "hata": "cikti JSON olarak okunamadi",
                "istem": istem}
    kuruldu = gorusleri_kur(yorumlar, gorusler, ufuk)
    log.info("[strateji_llm] %d kirilim -> %d yorum (%d atlandi)",
             len(gorusler), len(kuruldu), len(yorumlar) - len(kuruldu))
    return {"gorusler": kuruldu, "hata": None, "istem": istem}
