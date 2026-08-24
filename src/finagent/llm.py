"""
LLM hata teshisi.

SORUN
-----
claude-agent-sdk, altta yatan sebep ne olursa olsun ayni opak mesaji
firlatiyor:

    Exception: Claude Code returned an error result: success

Gercek sebep bambaska olabilir: API kredisi bitmis, anahtar gecersiz,
kota dolmus, ag yok. Bu mesaj kullaniciya gosterildiginde hicbir sey
anlatmiyor ve "kod bozuldu" izlenimi veriyor — oysa gercek ornekte sebep
ANTHROPIC_API_KEY'in kredisinin tukenmesiydi.

COZUM
-----
Hata alindiginda API'ye kucuk bir teshis istegi atip GERCEK sebebi
ogreniyoruz ve kullaniciya ne yapmasi gerektigini soyluyoruz.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

OPAK_IZLER = ("error result: success", "unknown error", "process exited")


def kullanilabilir(settings) -> tuple[bool, str]:
    """
    LLM cagrisi YAPILABILIR mi? (hizli kontrol, ag istegi yok)

    DIKKAT: "ANTHROPIC_API_KEY var mi" diye sormak ARTIK YANLIS. Abonelik
    modunda anahtar bilerek ortamdan siliniyor (bkz. config.py); eski kontrol
    bu durumda "LLM yok" diyerek goruntu okumayi ve analizi kapatiyordu.
    """
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False, "claude-agent-sdk kurulu degil."

    mod = str(settings.get("analysis.llm.auth", "abonelik") or "abonelik").lower()
    if mod in ("abonelik", "subscription", "oauth"):
        return True, "abonelik"
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False, ("auth: api_key secili ama ANTHROPIC_API_KEY yok (.env). "
                       "Ya anahtari ekle ya da auth: abonelik yap.")
    return True, "api_key"


def abonelik_saglik() -> tuple[bool, str]:
    """
    Abonelik (OAuth) yolu calisiyor mu? Kucuk bir CLI cagrisiyla olculur.

    API'ye HTTP istegi atmak burada yanlis olurdu: abonelik modunda ortamda
    API anahtari yok, dolayisiyla o yol zaten 401 dondururdu. Tek gecerli
    test, gercekte kullanilan yolu denemektir.
    """
    try:
        import anyio
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError:
        return False, "claude-agent-sdk kurulu degil."

    async def _dene() -> bool:
        async for _ in query(prompt="1",
                             options=ClaudeAgentOptions(allowed_tools=[], max_turns=1)):
            pass
        return True

    try:
        anyio.run(_dene)
        return True, "Claude aboneligi (claude.ai girisi) uzerinden calisiyor."
    except Exception as e:                            # noqa: BLE001
        return False, _yoklama_hatasi(e)


# Kimlik/oturum sorununu ISARET EDEN izler. Yalnizca bunlar gorulunce
# "giris yap" denir.
_KIMLIK_IZLERI = ("unauthorized", "authentication", "not logged in",
                  "login", "oauth", "credential", "api key", "api_key",
                  "forbidden", "401", "403", "expired", "token")


def _yoklama_hatasi(e: Exception) -> str:
    """
    Yoklama DUSTU — ama NEDEN dustugunu bilmiyorsak SOYLEMEYIZ.

    OLCULEN ARIZA (2026-08-24 08:14, kullaniciya gitti):

        Panel calismadi: Abonelik yolu calismiyor: Claude Code returned
        an error result: success
        Terminalde `claude` komutunu calistirip giris yapman gerekebilir.

    Bu YANLIS TESHIS. O sirada abonelik SAGLAMDI — ayni gun elle
    olculdu, `abonelik_saglik()` 12,2 sn'de "calisiyor" dondu. Kullanici
    olmayan bir giris sorununu kovalamaya yonlendirildi.

    Kok sebep: bu dal, yoklamanin HANGI sebeple dustugune bakmadan
    "giris yapman gerekebilir" diyordu. Oysa `error result: success`
    bir kimlik hatasi DEGIL — SDK'nin `is_error=True` ama `errors` bos
    ve `subtype="success"` gelen CELISKILI bir CLI cercevesini
    yazdirmasi (`query.py`: `"; ".join(errors) or str(subtype)`).

    Ayrim bu projenin tekrar eden dersi: "BAKAMADIM" ile "YOK" ayri
    seylerdir; burada da "yoklama cevap vermedi" ile "aboneligin bozuk"
    ayri iddialardir.
    """
    ham = str(e)
    if any(iz in ham.lower() for iz in _KIMLIK_IZLERI):
        return ("Abonelik yolu calismiyor — KIMLIK sorunu gorunuyor: "
                f"{ham[:160]}\n"
                "Terminalde `claude` komutunu calistirip giris yapman "
                "gerekebilir. Alternatif: config/settings.yaml -> "
                "analysis.llm.auth: api_key")
    return ("Abonelik yolu SU AN cevap vermedi (sebep BELIRSIZ): "
            f"{ham[:160]}\n"
            "Bunun bir giris/kimlik sorunu OLDUGUNU gosteren bir iz YOK; "
            "gecici bir CLI/servis arizasi olabilir. Once tekrar dene — "
            "surerse `claude` ile girisini kontrol et.")


def api_saglik(settings=None) -> tuple[bool, str]:
    """
    (saglikli_mi, aciklama). AKTIF kimlik yolunu olcer.
    """
    if settings is not None:
        mod = str(settings.get("analysis.llm.auth", "abonelik") or "abonelik").lower()
        if mod in ("abonelik", "subscription", "oauth"):
            return abonelik_saglik()

    anahtar = os.getenv("ANTHROPIC_API_KEY")
    if not anahtar:
        # Ayar okunamadi ama anahtar da yok -> abonelik yolunda olmaliyiz.
        return abonelik_saglik()
    try:
        import httpx
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": anahtar, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1,
                  "messages": [{"role": "user", "content": "."}]},
            timeout=20.0,
        )
    except Exception as e:                            # noqa: BLE001
        return False, f"Anthropic API'ye ulasilamadi: {type(e).__name__}: {e}"

    if r.status_code < 300:
        return True, "API erisimi calisiyor."

    try:
        mesaj = (r.json().get("error") or {}).get("message", "")
    except Exception:                                 # noqa: BLE001
        mesaj = r.text[:200]
    dusuk = mesaj.lower()

    if "credit balance is too low" in dusuk:
        return False, ("Anthropic API KREDISI BITMIS.\n"
                       "console.anthropic.com -> Plans & Billing -> kredi yukle.\n"
                       "Kredi gelene kadar: rapor ve ozet `--no-llm` ile "
                       "uretilebilir (teknik tablo + kaynak listesi calisir), "
                       "sohbet ve goruntu okuma calismaz.")
    if r.status_code in (401, 403) or "authentication" in dusuk or "invalid x-api-key" in dusuk:
        return False, ("ANTHROPIC_API_KEY gecersiz veya iptal edilmis. "
                       ".env icindeki anahtari yenile.")
    if r.status_code == 429 or "rate" in dusuk:
        return False, "Anthropic API hiz siniri asildi. Birkaç dakika sonra tekrar dene."
    return False, f"Anthropic API hatasi ({r.status_code}): {mesaj[:180]}"


def anlasilir_hata(e: Exception, settings=None) -> str:
    """
    SDK istisnasini kullaniciya anlamli bir mesaja cevirir.

    Opak hatalarda gercek sebebi API'ye sorarak ogrenir; aksi halde
    kullaniciya "error result: success" gibi anlamsiz bir metin gider.
    """
    ham = str(e)

    # BAGLAM TASMASI AYIRT EDILIR. Tek turda cok arac cagrilinca SDK
    # baglam hatasi veriyordu ve kullanici jenerik "Cevap uretemedim"
    # goruyordu — ne oldugunu ve ne yapacagini bilmeden.
    #
    # SIKISTIRMA EKLENMEDI, bilerek: baglam TURLAR ARASI BIRIKMIYOR
    # (her tur yeni ajan dongusu, gecmis 8 turluk duz metin), yani
    # sikistirilacak bir sey yok. Sorun tek turda cekilen VERI HACMI.
    if any(iz in ham.lower() for iz in
           ("context window", "context length", "too many tokens",
            "maximum context", "prompt is too long", "exceeds the maximum")):
        return ("Soru cok genis — tek seferde cok fazla veri cektim. "
                "Daha dar sor: tek sembol, tek donem "
                "(ornek: 'ASML son 30 gun' gibi).")

    if any(iz in ham.lower() for iz in OPAK_IZLER):
        saglikli, aciklama = api_saglik(settings)
        if not saglikli:
            return aciklama
        return (f"LLM cagrisi basarisiz oldu ama API erisimi calisiyor. "
                f"Ham hata: {ham[:180]}")
    return f"{type(e).__name__}: {ham[:200]}"
