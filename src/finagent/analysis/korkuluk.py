"""
KORKULUKLAR (plan adim 5/5, 9 Eki) — alim oncesi kontrol listesi, karar
gunlugu ve ceyrek incelemesi.

NEDEN
-----
Olcumler alim tarafinda kenar olmadigini gosterdi (hafiza:
strateji-kenari-yok). Bir portfoy sirketinin degeri tahminden degil
surecten gelir: karar ANINDA yazilan gerekce, ve sonradan o gerekceyle
yuzlesmek. Insan hafizasi kararin gerekcesini sonuca gore yeniden yazar;
yazili kayit yazmaz.

ZORUNLU DEGIL (Ali 9 Eki)
-------------------------
Ali bekleme suresi ve zorunlu beyan ISTEMEDI. Kontrol listesi eksigi
SOYLER, hicbir emri ya da onayi ENGELLEMEZ. Engelleyen bir korkuluk
kullaniciyi botu atlatmaya iter (ips.py ile ayni ilke).

KONTROL LISTESI — DORT SORU
--------------------------
  tez            neden bu kagit, neden simdi
  gecersizlesme  ne olursa yanildigimi anlarim
  boyut          politikaya gore payi (ips.alim_kontrolu — hesap tekrar yazilmaz)
  cikis          ne zaman/nasil cikarim (hedef, sure, stop)
Tez/gecersizlesme/cikis son `GECERLILIK_GUN` icindeki karar kaydindan
gelir; yoksa 'eksik'.

CEYREK INCELEMESI
-----------------
Ceyrekteki para hareketleri ve islemler (BUX dokumu + IBKR emirleri),
kayitli kararlarin SONUCU (kayit anindaki fiyattan bugune, ayni pencerede
S&P 500 ile), kayitsiz islem sayisi, ceyrekte tarihi gelen plan adimlari,
bugunku politika ve risk durumu, ve uc yazili soru. Ceyreklik getiri
OLCULMEZ: ceyrek basinda BUX'un guvenilir bir kaydi yok (ekran kayitlari
seyrek); uydurulmaz, soylenir.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

GECERLILIK_GUN = 30          # karar kaydi bu kadar gun kontrol listesini doldurur
ESLESME_GUN = 7              # islem <-> karar kaydi eslestirme penceresi
YONLER = ("al", "sat", "tut")
KIYAS = "VUSA"

MADDELER = (
    ("tez", "Neden bu kağıt, neden şimdi? (1 cümle)"),
    ("gecersizlesme", "Ne olursa yanıldığını anlarsın?"),
    ("boyut", "Politikana göre ne kadar?"),
    ("cikis", "Ne zaman / nasıl çıkarsın? (hedef, süre ya da stop)"),
)

YAZILI_SORULAR = (
    "Bu çeyrekte politikandan saptığın bir karar oldu mu? Neden?",
    "En iyi ve en kötü kararın hangisiydi — sonuç mu iyiydi, gerekçe mi?",
    "Önümüzdeki çeyrek için TEK bir değişiklik ne olsun?",
)


# ---------------------------------------------------------------- donem

def ceyrek(gun: date) -> tuple[date, date, str]:
    """SAF. `gun`un icinde oldugu ceyrek: (bas, bit, '2026 Ç4')."""
    c = (gun.month - 1) // 3
    bas = date(gun.year, 3 * c + 1, 1)
    bit = (date(gun.year + (c == 3), (3 * c + 3) % 12 + 1, 1) - timedelta(days=1))
    return bas, bit, f"{gun.year} Ç{c + 1}"


def onceki_ceyrek(gun: date) -> tuple[date, date, str]:
    bas, _, _ = ceyrek(gun)
    return ceyrek(bas - timedelta(days=1))


# ---------------------------------------------------------------- karar gunlugu

def _enstruman(db, sembol: str):
    r = db.query("""SELECT id, symbol, name FROM instruments WHERE UPPER(symbol) = ?
                    ORDER BY CASE venue WHEN 'BUX' THEN 0 WHEN 'BINANCE' THEN 1 ELSE 2 END
                    LIMIT 1""", ((sembol or "").strip().upper(),))
    return r[0] if r else None


def _son_fiyat(db, iid: int, gun: date | None = None) -> tuple[float | None, str | None]:
    b = db.fiyat_serisi(iid, 10, bitis=str(gun) if gun else None)
    if not b:
        return None, None
    return float(b[-1]["close"]), b[-1]["currency"]


def karar_dogrula(veri: dict) -> dict:
    """SAF. Model/kullanici girdisini normalleştirir; gecersizse ValueError."""
    sem = (veri.get("sembol") or "").strip().upper()
    yon = (veri.get("yon") or "").strip().lower()
    if not sem:
        raise ValueError("sembol zorunlu")
    if yon not in YONLER:
        raise ValueError(f"yon {YONLER} olmali: {yon!r}")
    metin = {k: (veri.get(k) or "").strip() or None
             for k in ("tez", "gecersizlesme", "cikis_plani", "kaynak")}
    if not any(metin[k] for k in ("tez", "gecersizlesme", "cikis_plani")):
        raise ValueError("tez, gecersizlesme ya da cikis_plani'ndan en az biri dolu olmali")
    t = veri.get("tutar_eur")
    if t is not None:
        t = float(t)
        if t <= 0:
            raise ValueError("tutar_eur pozitif olmali")
    return {"sembol": sem, "yon": yon, "tutar_eur": t, **metin}


def karar_yaz(db, sahip: str, veri: dict, ts: str | None = None) -> int:
    """Dogrulanmis karari yazar; o anki son kapanisi da (sonuc olcumu icin)."""
    v = karar_dogrula(veri)
    e = _enstruman(db, v["sembol"])
    fiyat, para = _son_fiyat(db, e["id"]) if e else (None, None)
    ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db._conn:
        cur = db._conn.execute(
            """INSERT INTO karar_gunlugu (sahip, ts, sembol, yon, tutar_eur, tez,
               gecersizlesme, cikis_plani, kaynak, fiyat, fiyat_para, instrument_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sahip, ts, v["sembol"], v["yon"], v["tutar_eur"], v["tez"],
             v["gecersizlesme"], v["cikis_plani"], v["kaynak"], fiyat, para,
             e["id"] if e else None))
    return int(cur.lastrowid)


def kararlar(db, sahip: str, bas: date | None = None, bit: date | None = None,
             sembol: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM karar_gunlugu WHERE sahip = ?", [sahip]
    if bas:
        q += " AND substr(ts,1,10) >= ?"; a.append(str(bas))
    if bit:
        q += " AND substr(ts,1,10) <= ?"; a.append(str(bit))
    if sembol:
        q += " AND sembol = ?"; a.append(sembol.strip().upper())
    return [dict(r) for r in db.query(q + " ORDER BY ts", tuple(a))]


# ---------------------------------------------------------------- kontrol listesi

def kontrol_listesi(db, settings, sahip: str, sembol: str, tutar_eur: float | None = None,
                    bugun: date | None = None) -> dict | None:
    """
    ALIM oncesi dort soru. Politika yoksa None (boyut sorusu ondan gelir).
    Doner {maddeler: [{ad, soru, durum: tamam|eksik|uyari, bilinen}],
    eksik: [ad], karar_kaydi: id|None, plan: [...]}.
    """
    from . import ips
    p = ips.politika(settings, sahip)
    if p is None:
        return None
    bugun = bugun or date.today()
    sem = (sembol or "").strip().upper()
    son = [k for k in kararlar(db, sahip, bas=bugun - timedelta(days=GECERLILIK_GUN),
                               sembol=sem) if k["yon"] == "al"]
    k = son[-1] if son else {}
    a = ips.alim_kontrolu(db, settings, sahip, sem, tutar_eur)
    maddeler = []
    for ad, soru in MADDELER:
        if ad == "boyut":
            if not tutar_eur:
                m = {"durum": "eksik", "bilinen": f"şu an portföyün %{a['yeni_pay_%']}"
                     if a.get("yeni_pay_%") is not None else None}
            elif a["ihlaller"]:
                m = {"durum": "uyari", "bilinen": "; ".join(a["ihlaller"])}
            else:
                m = {"durum": "tamam", "bilinen": f"alımdan sonra payı %{a['yeni_pay_%']}"}
        else:
            alan = "cikis_plani" if ad == "cikis" else ad
            deger = k.get(alan)
            m = {"durum": "tamam" if deger else "eksik", "bilinen": deger}
        maddeler.append({"ad": ad, "soru": soru, **m})
    return {"sembol": sem, "maddeler": maddeler,
            "eksik": [m["ad"] for m in maddeler if m["durum"] != "tamam"],
            "karar_kaydi": k.get("id"),
            "plan": ips.siradaki_adimlar(p, bugun),
            "not": "Zorunlu değil; eksik madde karar ANINDA yazılmazsa sonradan "
                   "gerekçe sonuca göre yeniden yazılır."}


def emir_uyarisi(db, settings, sahip: str, sembol: str) -> str | None:
    """IBKR alim ozetine TEK satir (engel degil). Eksik yoksa None."""
    try:
        k = kontrol_listesi(db, settings, sahip, sembol, tutar_eur=None)
    except Exception as e:                                # noqa: BLE001
        log.warning("[korkuluk] kontrol listesi okunamadi: %s", e)
        return None
    if not k:
        return None
    eksik = [a for a in k["eksik"] if a != "boyut"]
    if not eksik:
        return None
    ad = {"tez": "tez", "gecersizlesme": "ne olursa yanıldığın", "cikis": "çıkış planı"}
    return ("Kontrol listesi: " + ", ".join(ad[x] for x in eksik)
            + " yazılmamış (zorunlu değil; 'karar notu' diyerek ekleyebilirsin)")


# ---------------------------------------------------------------- ceyrek incelemesi

def _bux_islemleri(db, sahip: str, bas: date, bit: date) -> list[dict]:
    """BUX dokumunden islemler: (gun, varlik, yon) basina EUR toplam."""
    rows = db.query(
        """SELECT substr(ts,1,10) gun, varlik, transfer, SUM(tutar) eur,
                  SUM(kar_zarar) kz
           FROM hesap_hareketi WHERE sahip=? AND hesap='bux' AND kategori='trades'
           AND transfer IN ('CASH_DEBIT','CASH_CREDIT') AND substr(ts,1,10) BETWEEN ? AND ?
           GROUP BY 1, 2, 3 ORDER BY 1""", (sahip, str(bas), str(bit)))
    return [{"gun": r["gun"], "hesap": "bux", "varlik": r["varlik"],
             "yon": "al" if r["transfer"] == "CASH_DEBIT" else "sat",
             "eur": round(abs(float(r["eur"])), 2),
             # Satista BUX'un KENDI hesapladigi gerceklesen kar/zarar (EUR).
             **({"kar_zarar_eur": round(float(r["kz"]), 2)}
                if r["transfer"] == "CASH_CREDIT" and r["kz"] is not None else {})}
            for r in rows]


def _ibkr_islemleri(db, sahip: str, bas: date, bit: date) -> list[dict]:
    rows = db.query(
        """SELECT substr(e.gonderim_ts,1,10) gun, COALESCE(i.symbol, e.conid) sembol, e.yon,
                  e.adet, e.referans_fiyat, e.para_birimi
           FROM emirler e LEFT JOIN instruments i ON i.id = e.instrument_id
           WHERE e.sahip=? AND e.durum='gerceklesti'
           AND substr(e.gonderim_ts,1,10) BETWEEN ? AND ? ORDER BY 1""",
        (sahip, str(bas), str(bit)))
    return [{"gun": r["gun"], "hesap": "ibkr", "varlik": r["sembol"],
             "yon": "al" if r["yon"] == "BUY" else "sat",
             "tutar": round(float(r["adet"]) * float(r["referans_fiyat"] or 0), 2) or None,
             "para": r["para_birimi"]} for r in rows]


def _ilk_kelime(s: str | None) -> str:
    return ((s or "").strip().split() or [""])[0].lower()


def islem_karar_eslesmesi(islemler: list[dict], kayitlar: list[dict], adlar: dict) -> list[dict]:
    """
    SAF. Her isleme ±ESLESME_GUN icinde AYNI YONDE bir karar kaydi var mi?
    BUX dokumu sembol degil AD tasir ("Moderna"); `adlar` sembol -> ad.
    Eslesme ad/sembolun ILK KELIMESIYLE (beyan: ad bazli, kaba).
    """
    out = []
    for i in islemler:
        g = date.fromisoformat(i["gun"])
        v = _ilk_kelime(i["varlik"])
        bul = None
        for k in kayitlar:
            if k["yon"] != i["yon"]:
                continue
            if abs((date.fromisoformat(k["ts"][:10]) - g).days) > ESLESME_GUN:
                continue
            if v and v in (_ilk_kelime(k["sembol"]), _ilk_kelime(adlar.get(k["sembol"]))):
                bul = k["id"]
                break
        out.append({**i, "karar_kaydi": bul})
    return out


def karar_sonucu(db, k: dict, bugun: date) -> dict:
    """Kayit anindaki fiyattan bugune getiri; ayni pencerede S&P 500 (VUSA)."""
    out = {"id": k["id"], "gun": k["ts"][:10], "sembol": k["sembol"], "yon": k["yon"],
           "tez": k["tez"], "gecersizlesme": k["gecersizlesme"]}
    if not k.get("instrument_id") or not k.get("fiyat"):
        return {**out, "olculemedi": "kayit aninda fiyat yoktu"}
    simdi, _ = _son_fiyat(db, k["instrument_id"], bugun)
    if not simdi:
        return {**out, "olculemedi": "bugunku fiyat yok"}
    g = (simdi / float(k["fiyat"]) - 1) * 100
    out["getiri_%"] = round(g, 1)
    e = _enstruman(db, KIYAS)
    if e:
        b0, _ = _son_fiyat(db, e["id"], date.fromisoformat(k["ts"][:10]))
        b1, _ = _son_fiyat(db, e["id"], bugun)
        if b0 and b1:
            kg = (b1 / b0 - 1) * 100
            out["sp500_%"] = round(kg, 1)
            # SATIS kararinda "iyi" = sattigin kagit endeksten KOTU gitti.
            fark = g - kg
            out["karar_lehine_puan"] = round(fark if k["yon"] == "al" else -fark, 1)
    return out


def ceyrek_incelemesi(db, settings, sahip: str, bas: date, bit: date, etiket: str,
                      bugun: date | None = None) -> dict:
    from . import ips
    bugun = bugun or date.today()
    son = min(bit, bugun)
    akis = db.query(
        """SELECT kategori, SUM(tutar) t, COUNT(*) n FROM hesap_hareketi
           WHERE sahip=? AND kategori IN ('deposits','withdrawals')
           AND substr(ts,1,10) BETWEEN ? AND ? GROUP BY 1""", (sahip, str(bas), str(son)))
    para = {r["kategori"]: {"eur": round(abs(float(r["t"])), 2), "adet": r["n"]} for r in akis}
    dokum_son = db.query("SELECT MAX(substr(ts,1,10)) t FROM hesap_hareketi WHERE sahip=?",
                         (sahip,))[0]["t"]
    islemler = sorted(_bux_islemleri(db, sahip, bas, son) + _ibkr_islemleri(db, sahip, bas, son),
                      key=lambda i: i["gun"])
    kayitlar = kararlar(db, sahip, bas - timedelta(days=ESLESME_GUN), son)
    adlar = {}
    for k in kayitlar:
        e = _enstruman(db, k["sembol"])
        if e:
            adlar[k["sembol"]] = e["name"]
    islemler = islem_karar_eslesmesi(islemler, kayitlar, adlar)
    donem_kararlari = [k for k in kayitlar if str(bas) <= k["ts"][:10] <= str(son)]
    sonuc = [karar_sonucu(db, k, bugun) for k in donem_kararlari]
    p = ips.politika(settings, sahip)
    plan = [{"tarih": a["tarih"], "metin": a["metin"]}
            for a in (p or {}).get("plan") or [] if str(bas) <= str(a["tarih"]) <= str(son)]
    pol = None
    try:
        d = ips.durum(db, settings, sahip)
        if d and d.get("toplam_eur"):
            pol = {"toplam_eur": d["toplam_eur"],
                   "ihlal": [f"{x['sembol']} {_ys(x['pay_%'], False)}" for x in d["tek_hisse_ihlali"]]
                   + [f"tema {x['tema']} {_ys(x['pay_%'], False)}" for x in d["tema_ihlali"][:3]]
                   + ([f"+{len(d['tema_ihlali']) - 3} tema daha"] if len(d["tema_ihlali"]) > 3 else []),
                   "sapan_sinif": [f"{x['sinif']} {x['fark_puan']:+.1f} puan".replace(".", ",")
                                   for x in d["dagilim"] if x["sapma_asildi"]]}
    except Exception as e:                                # noqa: BLE001
        log.warning("[korkuluk] politika durumu okunamadi: %s", e)
    risk_ = None
    try:
        from . import risk
        risk_ = risk.kart_ozeti(risk.portfoy_riski(db, settings, sahip, bugun))
    except Exception as e:                                # noqa: BLE001
        log.warning("[korkuluk] risk okunamadi: %s", e)
    return {
        "etiket": etiket, "bas": str(bas), "bit": str(bit), "olcum_gunu": str(son),
        "para": para, "dokum_son": dokum_son,
        "islemler": islemler,
        "kayitsiz_islem": sum(1 for i in islemler if not i["karar_kaydi"]),
        "gerceklesen_kar_zarar_eur": (round(sum(i["kar_zarar_eur"] for i in islemler
                                                if "kar_zarar_eur" in i), 2)
                                      if any("kar_zarar_eur" in i for i in islemler) else None),
        "kararlar": sonuc, "plan_adimlari": plan, "politika": pol,
        # ALAN ADI DONEMI SOYLER: e2e'de model 1 yillik en derin dususu
        # "ceyrekteki en derin dusus" diye aktardi (9 Eki).
        "risk_bugun": ({"kotu_ay_eur": risk_["kotu_ay_eur"],
                        "son_1_yil_en_derin_dusus_%": risk_["en_derin_%"],
                        "en_buyuk_risk": risk_["risk_ilk"][:1]} if risk_ else None),
        "politika_yazilis": str((p or {}).get("yazilis_tarihi") or "") or None,
        "politika_sonradan": bool(p and p.get("yazilis_tarihi")
                                  and str(p["yazilis_tarihi"]) > str(bit)),
        "yazili_sorular": list(YAZILI_SORULAR),
        "not": ("Ceyreklik getiri olculmedi (ceyrek basinda guvenilir BUX kaydi yok). "
                "Politika ve risk BUGUNKU durum. BUX islemleri dokumden; dokum "
                f"{dokum_son or 'yok'} tarihine kadar. Islem-karar eslesmesi ad bazli."),
    }


def _tl(v) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _ys(v, isaret: bool = True) -> str:
    """Turkce yuzde: 5.5 -> '+%5,5' (isaret=False -> '%5,5')."""
    s = f"{abs(v):.1f}".replace(".", ",")
    return (("+" if v > 0 else "−" if v < 0 else "") if isaret else "") + f"%{s}"


def _tr_sayi(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def inceleme_metni(d: dict) -> str:
    """Telegram HTML — yazili inceleme. Sayi uydurmaz; olculemeyeni soyler."""
    import html
    e = html.escape
    L = [f"🗂 <b>Çeyrek incelemesi — {e(d['etiket'])}</b>",
         f"<i>{d['bas']} → {d['bit']}</i>", ""]
    p = d["para"]
    if p:
        L.append("💶 Para: " + " · ".join(
            f"{'yatırma' if k == 'deposits' else 'çekme'} {_tl(v['eur'])} € ({v['adet']})"
            for k, v in sorted(p.items())))
    isl = d["islemler"]
    if isl:
        L.append(f"🔁 İşlem: <b>{len(isl)}</b> — karar notu olmayan <b>{d['kayitsiz_islem']}</b>")
        for i in isl[:8]:
            tutar = f"{_tl(i['eur'])} €" if i.get("eur") is not None else (
                f"{_tr_sayi(i['tutar'])} {i.get('para') or ''}" if i.get("tutar") else "")
            kz = (f" ({'+' if i['kar_zarar_eur'] > 0 else '−'}{_tr_sayi(abs(i['kar_zarar_eur']))} €)"
                  if i.get("kar_zarar_eur") else "")
            L.append(f"  • {i['gun'][5:]} {e(str(i['varlik']))} {i['yon']} {tutar}{kz}"
                     + ("" if i["karar_kaydi"] else " <i>(not yok)</i>"))
        if len(isl) > 8:
            L.append(f"  … ve {len(isl) - 8} işlem daha")
        g = d.get("gerceklesen_kar_zarar_eur")
        if g is not None:
            L.append(f"  BUX satışlarında gerçekleşen: <b>{'+' if g > 0 else '−'}{_tr_sayi(abs(g))} €</b>")
    else:
        L.append(f"🔁 İşlem yok (döküm {e(str(d['dokum_son'] or 'yok'))} tarihine kadar).")
    if d["kararlar"]:
        L.append("")
        L.append("📝 <b>Kayıtlı kararların sonucu</b> (kayıt anından bugüne):")
        for k in d["kararlar"]:
            if "getiri_%" not in k:
                L.append(f"  • {e(k['sembol'])} {k['yon']} — ölçülemedi ({e(k['olculemedi'])})")
                continue
            lp = k.get("karar_lehine_puan")
            sp = (f", S&P 500 {_ys(k['sp500_%'])}, kararın lehine "
                  f"{('+' if lp > 0 else '−' if lp < 0 else '')}{abs(lp):.1f} puan".replace(".", ",")
                  if "sp500_%" in k else "")
            L.append(f"  • {k['gun'][5:]} {e(k['sembol'])} {k['yon']}: {_ys(k['getiri_%'])}{sp}")
            if k.get("gecersizlesme"):
                L.append(f"    <i>'Yanıldığımı şundan anlarım': {e(k['gecersizlesme'])}</i>")
    if d["plan_adimlari"]:
        L.append("")
        L.append("📅 Bu çeyrekteki plan adımları:")
        L += [f"  • {a['tarih'][5:]} {e(a['metin'][:90])}" for a in d["plan_adimlari"]]
    if d["politika"]:
        pol = d["politika"]
        L.append("")
        if d.get("politika_sonradan"):
            L.append(f"<i>Politikan {e(d['politika_yazilis'])} tarihinde yazıldı — bu çeyrek "
                     "ondan önce; aşağıdaki satır yalnızca bugünkü durum, bu çeyreğin notu değil.</i>")
        L.append(f"📐 Politika (bugün, {_tl(pol['toplam_eur'])} €): "
                 + ("tavan aşımı " + ", ".join(e(x) for x in pol["ihlal"]) if pol["ihlal"]
                    else "tavan aşımı yok")
                 + (" · hedeften sapan: " + ", ".join(e(x) for x in pol["sapan_sinif"])
                    if pol["sapan_sinif"] else ""))
    if d["risk_bugun"]:
        r = d["risk_bugun"]
        ilk = r["en_buyuk_risk"][0] if r["en_buyuk_risk"] else None
        L.append(f"⚖️ Risk: kötü ayda ≈ {_tl(-r['kotu_ay_eur'])} €"
                 + (f", riskin {_ys(ilk['risk_payi_%'], False)}'i {e(ilk['sembol'])}" if ilk else ""))
    L.append("")
    L.append("✍️ <b>Yazılı inceleme</b> — cevaplarsan onayınla hafızaya alırım (zorunlu değil):")
    L += [f"  {i}. {e(s)}" for i, s in enumerate(d["yazili_sorular"], 1)]
    L.append("")
    # Kullaniciya giden not TURKCE KARAKTERLI (araca giden `not` ASCII kalir).
    L.append(f"<i>Çeyreklik getiri ölçülmedi: çeyrek başında güvenilir BUX kaydı yok. "
             f"Politika ve risk bugünkü durum. BUX işlemleri dökümden "
             f"({e(str(d['dokum_son'] or 'döküm yok'))} tarihine kadar); işlem-not "
             f"eşleşmesi ada göre.</i>")
    return "\n".join(L)
