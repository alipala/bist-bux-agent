"""
launchd plist'lerinden supercronic crontab'i URETIR — takvimin tek kaynagi
plist'ler kalir (bekci `kacirilan_kosular` da onlari okuyor; elle yazilmis
ikinci bir takvim AYRISIRDI).

Kural: `bot` ve `gateway` surekli surecler, cron'a girmez. StartCalendarInterval
-> "dk sa * * gun"; StartInterval -> "*/N * * * *" (yalnizca 60'in bolenleri;
degilse PATLAR — yaklasik bir takvim sessiz bir kayma olurdu).
Mac'in mutlak yolu (/Users/.../bist-bux-agent) kapsayicida /app olur.
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
MAC_KOK = "/Users/alipala/github/bist-bux-agent"
SUREKLI = {"bot", "gateway"}


def satirlar(plist_dizini: Path = KOK / "launchd", hedef_kok: str = "/app") -> list[str]:
    out = ["CRON_TZ=Europe/Amsterdam"]
    for yol in sorted(plist_dizini.glob("*.plist")):
        d = plistlib.loads(yol.read_bytes())
        kip = str(d["Label"]).rsplit(".", 1)[-1]
        if kip in SUREKLI:
            continue
        komut = " ".join(str(a).replace(MAC_KOK, hedef_kok) for a in d["ProgramArguments"])
        komut = f"cd {hedef_kok} && {komut}"
        sc = d.get("StartCalendarInterval")
        if sc:
            for g in ([sc] if isinstance(sc, dict) else sc):
                gun = g.get("Weekday")
                out.append(f"{g.get('Minute', 0)} {g.get('Hour', '*')} * * "
                           f"{'*' if gun is None else gun % 7} {komut}")
        elif d.get("StartInterval"):
            n = int(d["StartInterval"])
            if n % 60 or 60 % (n // 60) and n // 60 < 60:
                raise ValueError(f"{kip}: StartInterval {n} cron'a birebir cevrilemez")
            out.append(f"*/{n // 60} * * * * {komut}")
        else:
            raise ValueError(f"{kip}: takvimsiz plist ({yol.name})")
    if len(out) < 2:
        raise ValueError("hic zamanlanmis is yok")
    return out


if __name__ == "__main__":
    try:
        print("\n".join(satirlar()))
    except Exception as e:                           # noqa: BLE001
        print(f"crontab uretilemedi: {e}", file=sys.stderr)
        sys.exit(1)
