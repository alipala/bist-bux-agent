"""Gunluk rapor uretimi: Markdown + HTML."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import markdown as md_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)


def _fmt(v, nd: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:,.{nd}f}"
    return str(v)


def _cls(v) -> str:
    if not isinstance(v, (int, float)):
        return ""
    return "up" if v > 0 else ("down" if v < 0 else "")


def _basamak(v) -> int:
    """
    Fiyat basamagi BUYUKLUGE gore. Sabit 2 hane ucuz varlikta veriyi yok
    ediyor: ROSE 0,0055 USD iken kapanis da tum ortalamalar da "0,01"
    cikiyor ve seviye analizi imkansizlasiyordu.
    """
    if not isinstance(v, (int, float)):
        return 2
    a = abs(v)
    return 2 if a >= 100 else 4 if a >= 1 else 6 if a >= 0.01 else 8


def _haber_sayisi(bundle: dict) -> int:
    h = bundle.get("haber") or {}
    return sum(len(v) for v in h.values()) if isinstance(h, dict) else len(h)


_GRUP_ADI = {"endeks": "Endeksler", "emtia": "Emtia", "kur": "Kur",
             "faiz": "Faiz", "risk": "Risk istahi"}
_ROL_SIRA = {"portfoy": 0, "izleme": 1, "bist_takip": 2, "makro": 3}
_ROL_ADI = {"portfoy": "Portfoy", "izleme": "Izleme listesi",
            "bist_takip": "BIST takip", "makro": "Makro"}


class ReportBuilder:
    def __init__(self, settings):
        self.s = settings
        self.env = Environment(
            loader=FileSystemLoader(str(Path(__file__).parent)),
            autoescape=select_autoescape(["html"]),
        )

    # ------------------------------------------------------------------
    def build(self, bundle: dict, analysis_md: str) -> dict[str, Path]:
        date_str = bundle.get("tarih") or datetime.now().strftime("%Y-%m-%d")
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        outdir: Path = self.s.report_dir
        outdir.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}

        formats = self.s.get("report.formats", ["markdown", "html"])
        title = self.s.get("report.title", "Gunluk Piyasa & Portfoy Analizi")
        disclaimer = (self.s.get("risk.disclaimer") or "").strip()

        # ---------- Markdown ----------
        if "markdown" in formats:
            md = self._markdown(title, date_str, bundle, analysis_md, disclaimer)
            p = outdir / f"rapor_{stamp}.md"
            p.write_text(md, encoding="utf-8")
            written["markdown"] = p

        # ---------- HTML ----------
        if "html" in formats:
            html = self._html(title, date_str, bundle, analysis_md, disclaimer)
            p = outdir / f"rapor_{stamp}.html"
            p.write_text(html, encoding="utf-8")
            written["html"] = p
            # her zaman en guncel rapora isaret eden sabit dosya
            (outdir / "latest.html").write_text(html, encoding="utf-8")

        log.info("Rapor yazildi: %s", ", ".join(str(v) for v in written.values()))
        return written

    # ------------------------------------------------------------------
    def _flat_positions(self, bundle: dict) -> list[dict]:
        rows: list[dict] = []
        for acct, info in ((bundle.get("portfoy") or {}).get("hesaplar") or {}).items():
            for p in info.get("pozisyonlar", []) or []:
                r = dict(p)
                r["hesap"] = acct
                r["cls"] = _cls(p.get("kar_zarar"))
                r["clsgun"] = _cls(p.get("gun_degisim_%"))
                for k, nd in (("adet", 4), ("ort_maliyet", 2), ("son_fiyat", 2),
                              ("deger", 2), ("deger_bugun", 2), ("kar_zarar", 2),
                              ("kar_zarar_%", 2), ("gun_degisim_%", 2),
                              ("agirlik_%", 1)):
                    r[k] = _fmt(p.get(k), nd)
                rows.append(r)
        return rows

    def _cards(self, bundle: dict) -> list[dict]:
        cards = []
        tot = (bundle.get("portfoy") or {}).get("toplam") or {}
        if tot.get("deger"):
            # BUGUNKU deger one cikar: anlik goruntu gunlerce eski
            # olabiliyor ama adet x bugunku kapanis GUNCEL.
            bugun = tot.get("deger_bugunku_fiyatla")
            cards.append({"label": "Portfoy (bugunku fiyatla)",
                          "value": _fmt(bugun if bugun else tot["deger"]), "cls": ""})
            # BILINMEYEN K/Z "0.00" DIYE GOSTERILMEZ. Ekran goruntusu
            # mutlak K/Z vermediginde onceki surum sifir yaziyordu ve
            # rapor "bu portfoy hicbir sey kazanmadi" diye okunuyordu.
            kz = tot.get("kar_zarar")
            cards.append({"label": "Toplam K/Z",
                          "value": _fmt(kz) if kz is not None else "—",
                          "cls": _cls(kz)})
        for r in bundle.get("kapanis_paneli", []):
            if r.get("kod") in ("XU100", "SPX") and isinstance(
                    r.get("getiri_1g_%"), (int, float)):
                cards.append({"label": r["kod"],
                              "value": f"{_fmt(r['kapanis'])} ({r['getiri_1g_%']:+.2f}%)",
                              "cls": _cls(r["getiri_1g_%"])})
        tek = bundle.get("teknik", [])
        # Gunun yildizi/kaybedeni PORTFOY ve IZLEME icinden secilir;
        # makro paneli disarida — "gunun yildizi VIX" bilgi degil gurultu.
        movers = [t for t in tek
                  if isinstance(t.get("getiri_1g_%"), (int, float))
                  and t.get("rol") != "makro"]
        if movers:
            best = max(movers, key=lambda t: t["getiri_1g_%"])
            worst = min(movers, key=lambda t: t["getiri_1g_%"])
            cards.append({"label": "Gunun Yildizi",
                          "value": f"{best['symbol']} {best['getiri_1g_%']:+.2f}%",
                          "cls": _cls(best["getiri_1g_%"])})
            cards.append({"label": "Gunun Kaybedeni",
                          "value": f"{worst['symbol']} {worst['getiri_1g_%']:+.2f}%",
                          "cls": _cls(worst["getiri_1g_%"])})
        cards.append({"label": "KAP / Gundem",
                      "value": f"{len(bundle.get('kap', []))} / {_haber_sayisi(bundle)}",
                      "cls": ""})
        return cards

    def _panel(self, bundle: dict) -> list[dict]:
        """Kapanis paneli — gruplar arasi baslik satiriyla."""
        out, onceki = [], None
        for p in bundle.get("kapanis_paneli", []):
            r = dict(p)
            r["grup_basi"] = _GRUP_ADI.get(p.get("grup"), p.get("grup")) \
                if p.get("grup") != onceki else None
            onceki = p.get("grup")
            r["cls1"] = _cls(p.get("getiri_1g_%"))
            r["cls5"] = _cls(p.get("getiri_5g_%"))
            r["cls20"] = _cls(p.get("getiri_20g_%"))
            r["kapanis"] = _fmt(p.get("kapanis"), _basamak(p.get("kapanis")))
            for k in ("getiri_1g_%", "getiri_5g_%", "getiri_20g_%"):
                r[k] = _fmt(p.get(k))
            # Seri bayatsa SESSIZ KALMAZ: gram altin spot vekilinden
            # geliyor ve o seri bir gun geriden gelebiliyor.
            r["bayat"] = (p.get("seri_yasi_gun") or 0) >= 2
            out.append(r)
        return out

    def _technicals(self, bundle: dict) -> list[dict]:
        out, onceki = [], None
        # Rol bazinda gruplu: portfoy once, sonra izleme, sonra BIST takip.
        sirali = sorted(bundle.get("teknik", []),
                        key=lambda t: (_ROL_SIRA.get(t.get("rol"), 9),
                                       t.get("symbol") or ""))
        for t in sirali:
            r = dict(t)
            r["grup_basi"] = _ROL_ADI.get(t.get("rol"), t.get("rol")) \
                if t.get("rol") != onceki else None
            onceki = t.get("rol")
            r["cls1"] = _cls(t.get("getiri_1g_%"))
            r["cls5"] = _cls(t.get("getiri_5g_%"))
            r["cls20"] = _cls(t.get("getiri_20g_%"))
            nd = _basamak(t.get("kapanis"))
            for k in ("kapanis", "sma50", "sma200"):
                r[k] = _fmt(t.get(k), nd)
            for k in ("getiri_1g_%", "getiri_5g_%", "getiri_20g_%"):
                r[k] = _fmt(t.get(k))
            r["rsi14"] = _fmt(t.get("rsi14"), 1)
            out.append(r)
        return out

    # ------------------------------------------------------------------
    def _markdown(self, title, date_str, bundle, analysis_md, disclaimer) -> str:
        L = [f"# {title}", "", f"*{date_str} — uretim: "
             f"{datetime.now().strftime('%H:%M')}*", "", "---", ""]

        panel = self._panel(bundle)
        if panel:
            L += ["## Piyasa Kapanisi", "",
                  "| Kod | Kapanis | 1G % | 5G % | 20G % | Aciklama |",
                  "|---|---:|---:|---:|---:|---|"]
            for r in panel:
                if r.get("grup_basi"):
                    L.append(f"| **{r['grup_basi']}** | | | | | |")
                bayat = f" _(seri {r['seri_yasi_gun']} gun eski)_" if r["bayat"] else ""
                L.append(f"| **{r['kod']}** | {r['kapanis']} {r.get('para_birimi') or ''} | "
                         f"{r['getiri_1g_%']} | {r['getiri_5g_%']} | "
                         f"{r['getiri_20g_%']} | {r['ad']}{bayat} |")
            L.append("")

        pos = self._flat_positions(bundle)
        if pos:
            L += ["## Pozisyonlar", "",
                  "| Sembol | Hesap | Adet | Deger (anlik) | Deger (bugun) | "
                  "Gun % | K/Z | K/Z % | Agirlik |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
            L += [f"| **{p['sembol']}** | {p['hesap']} | {p['adet']} | {p['deger']} | "
                  f"{p['deger_bugun']} | {p['gun_degisim_%']} | {p['kar_zarar']} | "
                  f"{p['kar_zarar_%']} | {p['agirlik_%']} |" for p in pos]
            L.append("")

        tek = self._technicals(bundle)
        if tek:
            L += ["## Teknik Tablo", "",
                  "| Sembol | Kapanis | 1G % | 5G % | 20G % | RSI | SMA50 | SMA200 | Trend |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
            for t in tek:
                if t.get("grup_basi"):
                    L.append(f"| **{t['grup_basi']}** | | | | | | | | |")
                L.append(f"| **{t['symbol']}** | {t['kapanis']} | {t['getiri_1g_%']} | "
                         f"{t['getiri_5g_%']} | {t['getiri_20g_%']} | {t['rsi14']} | "
                         f"{t['sma50']} | {t['sma200']} | {t.get('trend','')} |")
            L.append("")

        L += ["---", "", analysis_md, "", "---", "", f"*{disclaimer}*"]
        return "\n".join(L)

    def _html(self, title, date_str, bundle, analysis_md, disclaimer) -> str:
        body_html = md_lib.markdown(analysis_md, extensions=["tables", "fenced_code", "sane_lists"])
        sources = ", ".join(bundle.get("kaynaklar", [])) or "—"
        return self.env.get_template("template.html").render(
            title=title, date=date_str,
            generated_at=datetime.now().strftime("%H:%M"),
            sources=sources,
            cards=self._cards(bundle),
            panel=self._panel(bundle),
            positions=self._flat_positions(bundle),
            technicals=self._technicals(bundle),
            body_html=body_html,
            disclaimer=disclaimer,
        )
