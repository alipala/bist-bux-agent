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
                for k, nd in (("adet", 4), ("ort_maliyet", 2), ("son_fiyat", 2),
                              ("deger", 2), ("kar_zarar", 2), ("kar_zarar_%", 2),
                              ("agirlik_%", 1)):
                    r[k] = _fmt(p.get(k), nd)
                rows.append(r)
        return rows

    def _cards(self, bundle: dict) -> list[dict]:
        cards = []
        tot = (bundle.get("portfoy") or {}).get("toplam") or {}
        if tot.get("deger"):
            cards.append({"label": "Portfoy Degeri", "value": _fmt(tot["deger"]), "cls": ""})
            cards.append({"label": "Toplam K/Z", "value": _fmt(tot.get("kar_zarar")),
                          "cls": _cls(tot.get("kar_zarar"))})
        tek = bundle.get("teknik", [])
        movers = [t for t in tek if isinstance(t.get("getiri_1g_%"), (int, float))]
        if movers:
            best = max(movers, key=lambda t: t["getiri_1g_%"])
            worst = min(movers, key=lambda t: t["getiri_1g_%"])
            cards.append({"label": "Gunun Yildizi",
                          "value": f"{best['symbol']} {best['getiri_1g_%']:+.2f}%",
                          "cls": _cls(best["getiri_1g_%"])})
            cards.append({"label": "Gunun Kaybedeni",
                          "value": f"{worst['symbol']} {worst['getiri_1g_%']:+.2f}%",
                          "cls": _cls(worst["getiri_1g_%"])})
        cards.append({"label": "KAP / Haber",
                      "value": f"{len(bundle.get('kap', []))} / {len(bundle.get('haber', []))}",
                      "cls": ""})
        return cards

    def _technicals(self, bundle: dict) -> list[dict]:
        out = []
        for t in bundle.get("teknik", []):
            r = dict(t)
            r["cls1"] = _cls(t.get("getiri_1g_%"))
            r["cls5"] = _cls(t.get("getiri_5g_%"))
            r["cls20"] = _cls(t.get("getiri_20g_%"))
            for k in ("kapanis", "sma50", "sma200"):
                r[k] = _fmt(t.get(k))
            for k in ("getiri_1g_%", "getiri_5g_%", "getiri_20g_%"):
                r[k] = _fmt(t.get(k))
            r["rsi14"] = _fmt(t.get("rsi14"), 1)
            out.append(r)
        return out

    # ------------------------------------------------------------------
    def _markdown(self, title, date_str, bundle, analysis_md, disclaimer) -> str:
        L = [f"# {title}", "", f"*{date_str} — uretim: "
             f"{datetime.now().strftime('%H:%M')}*", "", "---", ""]

        pos = self._flat_positions(bundle)
        if pos:
            L += ["## Pozisyonlar", "",
                  "| Sembol | Hesap | Adet | Ort.Mal. | Son | Deger | K/Z | K/Z % | Agirlik |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
            L += [f"| **{p['sembol']}** | {p['hesap']} | {p['adet']} | {p['ort_maliyet']} | "
                  f"{p['son_fiyat']} | {p['deger']} | {p['kar_zarar']} | "
                  f"{p['kar_zarar_%']} | {p['agirlik_%']} |" for p in pos]
            L.append("")

        tek = self._technicals(bundle)
        if tek:
            L += ["## Teknik Tablo", "",
                  "| Sembol | Kapanis | 1G % | 5G % | 20G % | RSI | SMA50 | SMA200 | Trend |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
            L += [f"| **{t['symbol']}** | {t['kapanis']} | {t['getiri_1g_%']} | "
                  f"{t['getiri_5g_%']} | {t['getiri_20g_%']} | {t['rsi14']} | "
                  f"{t['sma50']} | {t['sma200']} | {t.get('trend','')} |" for t in tek]
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
            positions=self._flat_positions(bundle),
            technicals=self._technicals(bundle),
            body_html=body_html,
            disclaimer=disclaimer,
        )
