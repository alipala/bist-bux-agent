"""
Kalici Playwright oturumu.

Tasarim karari: sifre kodda/ .env'de TUTULMAZ.
Kullanici `python run.py login --site midas` ile bir kez elle giris yapar
(2FA dahil), cerezler ./.browser_profile icinde kalir. Sonraki tum
calistirmalar bu profili yeniden kullanir.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:  # sadece tip kontrolu icin; calisma aninda import edilmez
    from playwright.sync_api import BrowserContext, Page

log = logging.getLogger(__name__)

# Playwright'in "otomasyon" izini azaltan basit yamalar.
# Amac tespit sistemlerini kandirmak degil; normal tarayici davranisina
# yakin durup gereksiz bot-challenge tetiklememek.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US']});
"""


class BrowserSession:
    """Persistent context sarmalayicisi. `with` ile kullanilir."""

    def __init__(self, settings, headless: bool | None = None):
        self.s = settings
        self._pw = None
        self.context: "BrowserContext | None" = None
        self.headless = (
            settings.get("browser.headless", False) if headless is None else headless
        )

    def __enter__(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # noqa: BLE001
            raise SystemExit(
                "Playwright kurulu degil.\n"
                "  pip install -r requirements.txt\n"
                "  playwright install chromium"
            ) from e

        profile: Path = self.s.profile_dir
        profile.mkdir(parents=True, exist_ok=True)

        self._pw = sync_playwright().start()
        vp = self.s.get("browser.viewport", {"width": 1440, "height": 900})

        kwargs = dict(
            user_data_dir=str(profile),
            headless=self.headless,
            slow_mo=self.s.get("browser.slow_mo_ms", 0),
            locale=self.s.get("browser.locale", "tr-TR"),
            timezone_id=self.s.get("timezone", "Europe/Istanbul"),
            viewport={"width": vp["width"], "height": vp["height"]},
            args=["--disable-blink-features=AutomationControlled"],
        )
        ua = self.s.get("browser.user_agent")
        if ua:
            kwargs["user_agent"] = ua

        self.context = self._pw.chromium.launch_persistent_context(**kwargs)
        self.context.set_default_navigation_timeout(
            self.s.get("browser.nav_timeout_ms", 45000)
        )
        self.context.add_init_script(_STEALTH_JS)
        log.info("Tarayici acildi (profil: %s, headless=%s)", profile, self.headless)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()
        log.info("Tarayici kapatildi.")

    # ------------------------------------------------------------------
    @contextmanager
    def page(self, url: str | None = None) -> Iterator["Page"]:
        assert self.context is not None, "BrowserSession 'with' blogu icinde kullanilmali."
        pg = self.context.new_page()
        try:
            if url:
                pg.goto(url, wait_until="domcontentloaded")
            yield pg
        finally:
            pg.close()

    def is_logged_in(self, url: str, logged_in_selector: str, timeout_ms: int = 8000) -> bool:
        """Verilen sayfada 'giris yapilmis' isaretcisini arar."""
        if not logged_in_selector or logged_in_selector == "TODO":
            log.warning("login_check selector'i tanimli degil -> dogrulama atlandi.")
            return False
        with self.page(url) as pg:
            try:
                pg.wait_for_selector(logged_in_selector, timeout=timeout_ms)
                return True
            except Exception:
                return False


def interactive_login(settings, site: str) -> None:
    """
    Tek seferlik elle giris akisi.
    Tarayici acilir, kullanici kendi kimlik bilgilerini KENDI girer,
    Enter'a basinca oturum profile yazilmis olur.
    """
    url = settings.sel(f"{site}.portfolio_url") or settings.get(f"sources.{site}.base_url")
    if not url:
        raise SystemExit(f"'{site}' icin URL bulunamadi (config/selectors.yaml).")

    print("\n" + "=" * 68)
    print(f"  {site.upper()} — tek seferlik giris")
    print("=" * 68)
    print("  1) Acilan tarayicida kendi kullanici adin/sifren ile giris yap.")
    print("  2) 2FA/SMS dogrulamasi varsa onu da tamamla.")
    print("  3) Portfoy sayfasini gordugunde bu terminale donup ENTER'a bas.")
    print("\n  NOT: Sifreni bu programa yazmiyorsun; sadece tarayiciya giriyorsun.")
    print("       Oturum cerezleri yerel profilde saklanir.\n")

    with BrowserSession(settings, headless=False) as bs:
        with bs.page(url) as pg:
            input("  Giris tamamlandiginda ENTER'a bas... ")
            try:
                pg.wait_for_timeout(1500)  # storage flush icin kisa bekleme
            except Exception:
                pass

    print(f"\n  ✓ Oturum kaydedildi: {settings.profile_dir}")
    print("    Artik 'python run.py collect --site %s' calistirabilirsin.\n" % site)
