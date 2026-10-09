"""
IBKR MCP — DOGRUDAN baglanti (`mcp-public`), claude.ai'siz.

NEDEN (2026-10-06): bulutta (Railway) Claude `setup-token` ile calisir ve o
token "can only make model requests ... can't fetch claude.ai connectors"
(Anthropic belgesi). claude.ai IBKR baglayicisi orada YUKLENMEZ. IBKR'nin
"her MCP istemcisine acik" ucu bu boslugu kapatir:

    https://api.ibkr.com/v1/api/mcp-public    (OAuth 2.1 + PKCE + DCR)

Ayni 34 arac (olculdu). Sabit argumanli cagrilar artik MODELSIZ gider —
claude.ai yolunda her cagri bir Haiku oturumuydu (~11 sn, ~0,018 USD).

OLCULEN UC KURAL (6 Eki, Ali'nin hesabinda):

1. USER-AGENT SART. IBKR'nin onundeki Akamai UA'siz istege 403 veriyor
   (`curl -H 'User-Agent:'` -> 403, `-A ...` -> 200). `mcp` 1.29'un auth
   istekleri UA tasimiyor; kutuphane metadata'yi okuyamayip yanlis yedek
   adrese (/register) dusuyordu. Burada HER istek `UA` tasir.
2. ACCESS TOKEN 299 SN. Her cagridan once omur kontrol edilir; bitmesine
   `YENILEME_PAYI_SN`den az kaldiysa yenilenir.
3. REFRESH TOKEN TEK KULLANIMLIK VE DONER. Eskisini yeniden kullanmak
   ZINCIRI IPTAL ETTI ("Refresh token has been revoked"; o anki access
   token da 401 aldi) ve yeniden insan onayi gerekti. Bu yuzden:
     * yenileme DOSYA KILIDI altinda (bot + isciler + zamanli kosular ayri
       surec; ikisi ayni refresh token'i harcarsa zincir olur),
     * yeni token DISKE YAZILMADAN kullanilmaz (atomik yazim),
     * ayni zinciri iki makine (Mac + Railway) ASLA birlikte kullanmaz.

Yetki dustugunde (refresh gecersiz) `YetkiHatasi` — mesaj COZUMU soyler:
`run.py ibkr-baglan` (iki adim: url -> geri donus adresi).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .istemci import (DurumBilinmiyorHatasi, IbkrHatasi, UlasilamadiHatasi,
                      YetkiHatasi)

log = logging.getLogger(__name__)

SUNUCU = "https://api.ibkr.com/v1/api/mcp-public"
YETKI_KOKU = "https://api.ibkr.com"
KAYIT_URL = YETKI_KOKU + "/oauth2/register"
YETKI_URL = YETKI_KOKU + "/oauth2/authorize"
TOKEN_URL = YETKI_KOKU + "/oauth2/api/v1/token"
UA = "finagent-bot/1.0"
KAPSAM = "mcp.read mcp.write"
# Geri donus adresi YEREL: bulutta tarayici bu adrese ULASAMAZ ve sayfa
# "acilamadi" der — ama adres cubugundaki URL gecerlidir; kullanici onu
# `ibkr-baglan --geri` ile verir (Claude Code'un uzak oturum yolu ayni).
GERI_DONUS = "http://127.0.0.1:53682/callback"
YENILEME_PAYI_SN = 60
HTTP_SURE_SN = 30.0


class YetkiDustu(YetkiHatasi):
    """Refresh token gecersiz/iptal: insan onayi olmadan donulmez."""


def token_yolu() -> Path:
    """
    Token dosyasi. Ortamdan tasinir (`IBKR_MCP_TOKEN`), yoksa botun durum
    dizini (`BOT_STATE_DIR`, bulutta kalici volume). Repo icine YAZILMAZ.
    """
    if p := os.getenv("IBKR_MCP_TOKEN"):
        return Path(p)
    if d := os.getenv("BOT_STATE_DIR"):
        return Path(d) / "ibkr_mcp_token.json"
    return Path(__file__).resolve().parents[3] / "data" / "bot" / "ibkr_mcp_token.json"


def _atomik_yaz(yol: Path, veri: dict) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_name(yol.name + f".{os.getpid()}.tmp")
    fd = os.open(gecici, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(veri, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(gecici, yol)


@contextlib.contextmanager
def _kilit(yol: Path):
    """Surecler arasi TEK yenileyici. Surec olurse cekirdek kilidi birakir."""
    import fcntl
    yol.parent.mkdir(parents=True, exist_ok=True)
    with open(yol.with_name(yol.name + ".lock"), "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _post(url: str, data: dict | None = None, json_govde: dict | None = None,
          _http=None):
    import httpx
    h = _http or httpx
    return h.post(url, data=data, json=json_govde,
                  headers={"User-Agent": UA}, timeout=HTTP_SURE_SN)


def _token_yaniti(y: dict, eski: dict | None = None) -> dict:
    alanlar = ("access_token", "refresh_token", "expires_in", "scope",
               "token_type")
    t = {k: y[k] for k in alanlar if k in y}
    if "refresh_token" not in t and eski and eski.get("refresh_token"):
        # Donmeyen sunucuda eski refresh token gecerli kalir. IBKR'de
        # olculen davranis DONMEK; bu dal yalnizca savunma.
        t["refresh_token"] = eski["refresh_token"]
    return t


def erisim_tokeni(zorla: bool = False, *, yol: Path | None = None,
                  simdi=time.time, _http=None) -> str:
    """
    Gecerli bir access token doner; gerekirse KILIT ALTINDA yeniler.

    `zorla`: sunucu 401 dediyse omre bakmadan yenile (saat kaymasi ya da
    sunucu tarafli iptal).
    """
    yol = yol or token_yolu()
    with _kilit(yol):
        if not yol.exists():
            raise YetkiDustu(
                f"IBKR bulut token dosyasi yok ({yol}). Baglanti kurulmamis: "
                "`run.py ibkr-baglan` ile onay ver.")
        d = json.loads(yol.read_text())
        t = d.get("tokens") or {}
        kalan = (float(d.get("alindi_ts") or 0) + float(t.get("expires_in") or 0)
                 - simdi())
        if not zorla and t.get("access_token") and kalan > YENILEME_PAYI_SN:
            return t["access_token"]
        if not t.get("refresh_token"):
            raise YetkiDustu("IBKR bulut: refresh token yok — `run.py ibkr-baglan`")
        istemci = (d.get("client") or {}).get("client_id")
        try:
            r = _post(TOKEN_URL, data={
                "grant_type": "refresh_token",
                "refresh_token": t["refresh_token"],
                "client_id": istemci, "resource": SUNUCU}, _http=_http)
        except Exception as e:                       # noqa: BLE001
            # Istek GITMEMIS olabilir ya da cevabi kaybolmus olabilir. Ikinci
            # durumda sunucu tokeni DONDURMUS ve biz yenisini kaybetmis
            # olabiliriz — bu da zinciri oldurur, ama SESSIZ degil: bir
            # sonraki deneme `invalid_grant` alir ve YetkiDustu soyler.
            raise UlasilamadiHatasi(f"IBKR token yenilenemedi (ag): {e}") from e
        if r.status_code == 200:
            yeni = _token_yaniti(r.json(), t)
            d["tokens"] = yeni
            d["alindi_ts"] = simdi()
            _atomik_yaz(yol, d)           # KULLANMADAN ONCE diske
            log.info("[ibkr-mcp] token yenilendi (omur %s sn)", yeni.get("expires_in"))
            return yeni["access_token"]
        govde = r.text[:300]
        if r.status_code in (400, 401) and "invalid_grant" in govde:
            raise YetkiDustu(
                "IBKR bulut yetkisi dustu (refresh token gecersiz: "
                f"{_detay(govde)}). Yeniden onay gerekiyor: `run.py ibkr-baglan`.")
        raise UlasilamadiHatasi(f"IBKR token yenilenemedi: http {r.status_code} {govde}")


def _detay(govde: str) -> str:
    try:
        return json.loads(govde).get("detail") or govde[:120]
    except Exception:                                # noqa: BLE001
        return govde[:120]


# ---------------------------------------------------------------- arac cagrisi
def _http_401(e: BaseException) -> bool:
    """Istisna (ya da ExceptionGroup icindeki biri) HTTP 401 mi?"""
    import httpx
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code == 401
    alt = getattr(e, "exceptions", None)
    return bool(alt) and any(_http_401(x) for x in alt)


async def _tek_cagri(token: str, arac: str, argumanlar: dict) -> tuple[bool, list]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    h = {"Authorization": f"Bearer {token}", "User-Agent": UA}
    async with streamablehttp_client(SUNUCU, headers=h) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            y = await s.call_tool(arac, argumanlar)
    bloklar = [{"type": "text", "text": c.text} for c in y.content
               if getattr(c, "type", None) == "text"]
    return bool(y.isError), bloklar


async def cagir_async(arac: str, argumanlar: dict | None = None, *,
                      yazma: bool, sure_sn: float = 60.0,
                      _cagri=None, _token=None) -> list[dict]:
    """
    Tek arac cagrisi. Doner: icerik bloklari (`[{"type":"text",...}]`) —
    `mcp_kanal.ham_ayristir`in bekledigi bicim.

    401: token BIR KEZ zorla yenilenip tekrar denenir. 401 HTTP katmaninda
    istek ISLENMEDEN donduğu icin yazma aracinda da tekrar guvenli.
    Zaman asimi: yazmada `DurumBilinmiyorHatasi` (istek ulasmis olabilir).
    """
    import anyio
    cagri = _cagri or _tek_cagri
    tok = _token or (lambda z=False: erisim_tokeni(z))
    arg = dict(argumanlar or {})
    try:
        with anyio.fail_after(sure_sn):
            token = await anyio.to_thread.run_sync(tok)
            try:
                hata, bloklar = await cagri(token, arac, arg)
            except BaseException as e:               # noqa: BLE001
                if not _http_401(e):
                    raise
                log.warning("[ibkr-mcp] %s 401 — token zorla yenileniyor", arac)
                token = await anyio.to_thread.run_sync(lambda: tok(True))
                hata, bloklar = await cagri(token, arac, arg)
    except TimeoutError as e:
        sinif = DurumBilinmiyorHatasi if yazma else UlasilamadiHatasi
        raise sinif(f"{arac}: {sure_sn:.0f} sn icinde yanit yok"
                    + (" — istek IBKR'ye ULASMIS OLABILIR" if yazma else "")) from e
    except IbkrHatasi:
        raise
    except BaseException as e:                       # noqa: BLE001
        if _http_401(e):
            raise YetkiHatasi(f"{arac}: IBKR bulut 401 (yenilemeden sonra da)") from e
        sinif = DurumBilinmiyorHatasi if yazma else UlasilamadiHatasi
        raise sinif(f"{arac}: {type(e).__name__}: {str(e)[:200]}") from e
    if hata:
        from .mcp_kanal import hata_siniflandir
        metin = "".join(b["text"] for b in bloklar)
        raise hata_siniflandir(metin, yazma)(f"{arac}: {metin[:300]}")
    return bloklar


async def araclar_async(*, _token=None) -> dict[str, dict]:
    """Sunucunun arac listesi: ad -> {aciklama, sema}. Sema kaymasi kontrolu
    ve sohbet vekil araclari icin (sema elle KOPYALANMAZ)."""
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    token = await anyio.to_thread.run_sync(_token or erisim_tokeni)
    h = {"Authorization": f"Bearer {token}", "User-Agent": UA}
    async with streamablehttp_client(SUNUCU, headers=h) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            liste = (await s.list_tools()).tools
    return {t.name: {"aciklama": t.description or "", "sema": t.inputSchema}
            for t in liste}


# ---------------------------------------------------------------- ilk baglanti
def _pkce() -> tuple[str, str]:
    dogrulayici = secrets.token_urlsafe(64)[:96]
    meydan = base64.urlsafe_b64encode(
        hashlib.sha256(dogrulayici.encode()).digest()).rstrip(b"=").decode()
    return dogrulayici, meydan


def baglanti_baslat(*, yol: Path | None = None, _http=None) -> str:
    """
    1. adim: istemci kaydi (DCR) + PKCE. Onay URL'sini doner; yarim durum
    `<token>.bekleyen.json`a (0600) yazilir. Mevcut token dosyasina
    DOKUNMAZ — onay tamamlanana kadar eski zincir (varsa) calisir.
    """
    yol = yol or token_yolu()
    r = _post(KAYIT_URL, json_govde={
        "client_name": "finagent-bot", "redirect_uris": [GERI_DONUS],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
        "scope": KAPSAM}, _http=_http)
    if r.status_code not in (200, 201):
        raise IbkrHatasi(f"IBKR istemci kaydi basarisiz: http {r.status_code} {r.text[:200]}")
    istemci = r.json()
    dogrulayici, meydan = _pkce()
    durum = secrets.token_urlsafe(32)
    _atomik_yaz(yol.with_name(yol.name + ".bekleyen.json"),
                {"client": istemci, "dogrulayici": dogrulayici, "durum": durum,
                 "ts": time.time()})
    return YETKI_URL + "?" + urlencode({
        "response_type": "code", "client_id": istemci["client_id"],
        "redirect_uri": GERI_DONUS, "state": durum,
        "code_challenge": meydan, "code_challenge_method": "S256",
        "resource": SUNUCU, "scope": KAPSAM})


def baglanti_tamamla(geri_url: str, *, yol: Path | None = None,
                     simdi=time.time, _http=None) -> dict:
    """
    2. adim: tarayicinin yonlendigi adres (sayfa acilmasa da adres cubugu)
    -> kod -> token. `state` eslesmezse REDDEDILIR (baska bir akisin kodu).
    """
    yol = yol or token_yolu()
    bek_yol = yol.with_name(yol.name + ".bekleyen.json")
    if not bek_yol.exists():
        raise IbkrHatasi("Bekleyen IBKR baglantisi yok — once `ibkr-baglan` (1. adim)")
    bek = json.loads(bek_yol.read_text())
    q = parse_qs(urlparse(geri_url.strip()).query)
    if q.get("error"):
        raise IbkrHatasi(f"IBKR onayi reddedildi: {q['error'][0]}")
    kod, durum = (q.get("code") or [None])[0], (q.get("state") or [None])[0]
    if not kod:
        raise IbkrHatasi("Adreste `code` yok — tarayicinin adres cubugundaki TAM adresi ver")
    if durum != bek["durum"]:
        raise IbkrHatasi("`state` eslesmedi — bu adres BASKA bir baglanti akisina ait")
    r = _post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": kod,
        "redirect_uri": GERI_DONUS, "client_id": bek["client"]["client_id"],
        "code_verifier": bek["dogrulayici"], "resource": SUNUCU}, _http=_http)
    if r.status_code != 200:
        raise IbkrHatasi(f"IBKR token alinamadi: http {r.status_code} {r.text[:200]}")
    with _kilit(yol):
        d = {"client": bek["client"], "tokens": _token_yaniti(r.json()),
             "alindi_ts": simdi()}
        _atomik_yaz(yol, d)
    bek_yol.unlink(missing_ok=True)
    return {"expires_in": d["tokens"].get("expires_in"),
            "refresh_token": bool(d["tokens"].get("refresh_token")),
            "scope": d["tokens"].get("scope")}


def durum(*, yol: Path | None = None, simdi=time.time) -> dict[str, Any]:
    """Token dosyasinin ozeti — TOKEN BASILMAZ."""
    yol = yol or token_yolu()
    if not yol.exists():
        return {"dosya": str(yol), "var": False}
    d = json.loads(yol.read_text())
    t = d.get("tokens") or {}
    return {"dosya": str(yol), "var": True,
            "istemci": bool((d.get("client") or {}).get("client_id")),
            "refresh_token": bool(t.get("refresh_token")),
            "erisim_kalan_sn": round(float(d.get("alindi_ts") or 0)
                                     + float(t.get("expires_in") or 0) - simdi()),
            "kapsam": t.get("scope")}


# ---------------------------------------------------------------- vekil sunucu
SEMA_ONBELLEK_SN = 24 * 3600


def _sema_onbellegi() -> Path:
    return token_yolu().with_name("ibkr_mcp_sema.json")


async def semalar_async(*, _araclar=None) -> dict[str, dict]:
    """
    Arac semalari — 24 saat dosyada onbellek (her sohbet mesaji ayri
    surec; her seferinde `list_tools` ~1 sn eklerdi). Sema ELLE KOPYALANMAZ:
    sunucu degisirse en gec bir gun icinde yenisi gelir.
    """
    yol = _sema_onbellegi()
    with contextlib.suppress(Exception):
        d = json.loads(yol.read_text())
        if time.time() - float(d.get("ts", 0)) < SEMA_ONBELLEK_SN and d.get("araclar"):
            return d["araclar"]
    araclar = await (_araclar or araclar_async)()
    with contextlib.suppress(Exception):
        _atomik_yaz(yol, {"ts": time.time(), "araclar": araclar})
    return araclar


async def vekil_sunucu_async(kisa_adlar, *, yazma_izni: frozenset = frozenset(),
                             _semalar=None, _cagir=None):
    """
    Claude oturumuna IBKR araclarini SUREC ICI bir MCP sunucusuyla sunar
    (ad: `mcp__ibkr__<arac>`). claude.ai baglayicisinin yerini tutar ama
    YALNIZCA verilen araclar gorunur — oturum digerlerini HIC gormez.

    Arac hatasi `is_error` ile modele doner (model argumani duzeltebilir);
    son hata `son_hata[arac]`a yazilir — cagiran "model ne gordu" sorusunu
    metinden tahmin etmek zorunda kalmasin.
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool
    semalar = await (_semalar or semalar_async)()
    cagir = _cagir or cagir_async
    son_hata: dict[str, str] = {}
    from .bulut_katalog import KATALOG
    yazma_araclari = {k for k, v in KATALOG.items() if v["yazma"]}
    araclar = []
    for ad in kisa_adlar:
        if ad not in semalar:
            raise IbkrHatasi(f"IBKR bulut sunucusunda '{ad}' araci yok (sema kaymasi?)")
        bilgi = semalar[ad]

        def _yap(ad=ad):
            async def _h(args):
                # IKINCI KILIT (9 Eki): yazma araci vekilde GORUNUR (Telegram
                # onay kapisi onu yakalayip onaya sunsun diye) ama izinsiz
                # CALISMAZ. Kapi bir gun atlanirsa bile istek IBKR'ye gitmez.
                if ad in yazma_araclari and ad not in yazma_izni:
                    son_hata[ad] = "onaysiz yazma reddedildi"
                    return {"content": [{"type": "text", "text":
                            f"HATA: {ad} bir YAZMA araci; bu kanaldan onaysiz "
                            "calistirilmaz (Telegram onayina sunulmali)."}],
                            "is_error": True}
                try:
                    bloklar = await cagir(ad, args, yazma=ad in yazma_izni)
                    son_hata.pop(ad, None)
                    return {"content": bloklar}
                except IbkrHatasi as e:
                    son_hata[ad] = str(e)
                    return {"content": [{"type": "text", "text": f"HATA: {e}"}],
                            "is_error": True}
            return _h

        araclar.append(tool(ad, bilgi["aciklama"][:1000], bilgi["sema"])(_yap()))
    sunucu = create_sdk_mcp_server(name="ibkr", version="1.0.0", tools=araclar)
    sunucu_bilgi = {"sunucu": sunucu, "son_hata": son_hata}
    return sunucu_bilgi
