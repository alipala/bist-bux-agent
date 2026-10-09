"""
IBKR Client Portal Gateway'e ICERIDEN giris (9 Eki olcum). Tarayici gateway ile
AYNI kapsayicida (127.0.0.1) — Mac'teki duzenin aynisi. Kimlik bilgileri
ortamdan (IBKR_KULLANICI, IBKR_SIFRE); HICBIR yere yazdirilmaz. 2FA (IBKR
Mobile) insanin telefonunda onaylanir; bu betik yalnizca bekler.

BASARI OLCUTU "Client login succeeds" YAZISI DEGIL (ilk uzaktan denemede o yazi
cikti ama oturum kurulmamisti): gateway'in /iserver/auth/status'u
authenticated=true demeli.
"""
import json, os, sys, time
from playwright.sync_api import sync_playwright

KOK = "https://127.0.0.1:5000"
K, S = os.environ.get("IBKR_KULLANICI"), os.environ.get("IBKR_SIFRE")
if not K or not S:
    sys.exit("IBKR_KULLANICI / IBKR_SIFRE tanimli degil")
BEKLE = int(os.environ.get("GIRIS_BEKLE_SN", "180"))


def gizle(t: str) -> str:
    return t.replace(K, "<kullanici>").replace(S, "<sifre>")


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(ignore_https_errors=True)
    pg = ctx.new_page()
    pg.goto(KOK + "/", wait_until="load", timeout=60000)
    pg.wait_for_selector("#xyz-field-username", timeout=30000)
    pg.fill("#xyz-field-username", K)
    pg.fill("#xyz-field-password", S)
    pg.click("button.xyz-button-login")
    print("[giris] gonderildi — IBKR Mobile'da 2FA ONAYI bekleniyor", flush=True)
    son, t0, ok = "", time.time(), False
    while time.time() - t0 < BEKLE:
        time.sleep(3)
        try:
            metin = " ".join(pg.inner_text("body").split())[:300]
        except Exception as e:                       # sayfa gecisi
            metin = f"(sayfa degisiyor: {type(e).__name__})"
        if metin != son:
            print(f"[giris] {int(time.time()-t0)} sn | {pg.url[:80]} | {gizle(metin)}", flush=True)
            son = metin
        if "Client login succeeds" in metin:
            ok = True
            break
    if not ok:
        pg.screenshot(path="/tmp/giris_hata.png", full_page=True)
        print("[giris] SURE DOLDU — 'succeeds' gorulmedi; ekran /tmp/giris_hata.png", flush=True)
    b.close()

# GERCEK OLCUT: gateway oturumu
import ssl, urllib.request
bag = ssl._create_unverified_context()
for _ in range(10):
    time.sleep(3)
    istek = urllib.request.Request(KOK + "/v1/api/iserver/auth/status", data=b"{}", method="POST",
                                   headers={"User-Agent": "finagent/1.0", "Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(istek, context=bag, timeout=15).read() or b"{}")
    except Exception as e:
        r = {"hata": str(e)[:120]}
    print("[giris] auth/status:", {k: r.get(k) for k in ("authenticated", "connected", "competing", "hata")}, flush=True)
    if r.get("authenticated"):
        print("[giris] SONUC: OTURUM ACIK", flush=True)
        sys.exit(0)
print("[giris] SONUC: OTURUM ACILMADI", flush=True)
sys.exit(1)
