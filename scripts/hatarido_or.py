#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hatarido-or: osszeszedi az ELSYS ajanlatok hataridejet.

Forrasok:
  1. A Drive-on az ELSYS_01_Ajanlatok/<ev> alatti projektmappak
     "00 Ajanlatkero_e_mail" almappaiban levo .eml fajlok szovegebol
     kiparszolt ajanlatadasi hatarido.
  2. store/hatarido-manual.json  -- kezi bejegyzesek/feluliras
     [{"projekt": "...", "hatarido": "2026-08-25", "megjegyzes": "..."}]

Cache: store/hatarido-cache.json (fileId -> parszolt eredmeny), hogy ne
kelljen minden reggel ujra letolteni es elemezni ugyanazokat a leveleket.

Futtatas:
  .venv-gdocs/bin/python scripts/hatarido_or.py            # riport
  .venv-gdocs/bin/python scripts/hatarido_or.py --json     # gepi kimenet
  .venv-gdocs/bin/python scripts/hatarido_or.py --refresh  # cache eldobasa
"""
import json, os, re, sys, email, datetime
from email import policy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "store")
CACHE = os.path.join(STORE, "hatarido-cache.json")
MANUAL = os.path.join(STORE, "hatarido-manual.json")
AJANLATOK_FOLDER = "1-CXf54wEAloU7BVui4mURso9a_fmkOi3"   # ELSYS_01_Ajanlatok

HONAP = {"január":1,"február":2,"március":3,"április":4,"május":5,"június":6,
         "július":7,"augusztus":8,"szeptember":9,"október":10,"november":11,"december":12}

# a hataridot jelzo kornyezo szavak, sullyal
KULCS = [("árajánlat", 5), ("ajánlatuk", 4), ("ajánlatot", 3), ("ajánlat", 2),
         ("küldjék meg", 4), ("megküldés", 3), ("beadás", 3), ("határid", 4),
         ("leadás", 3), ("benyújt", 3)]
# ezek NEM az ajanlat hataridejet jelolik
TILTO = [("visszajelzés", -6), ("szándék", -6), ("helyszíni bejárás", -4),
         ("kérdés", -3), ("kiegészítő", -3)]


def _svc():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    t = json.load(open(os.path.join(STORE, ".gdocs-oauth.json")))
    c = Credentials(token=None, refresh_token=t["refresh_token"], client_id=t["client_id"],
                    client_secret=t["client_secret"],
                    token_uri="https://oauth2.googleapis.com/token",
                    scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=c, cache_discovery=False)


def find_dates(text):
    """(datum, pozicio, nyers) harmasok a szovegbol."""
    out = []
    for m in re.finditer(r"(\d{4})\.?\s*(" + "|".join(HONAP) + r")\s*(\d{1,2})", text, re.I):
        try:
            out.append((datetime.date(int(m.group(1)), HONAP[m.group(2).lower()], int(m.group(3))),
                        m.start(), m.group(0)))
        except ValueError:
            pass
    for m in re.finditer(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", text):
        try:
            out.append((datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                        m.start(), m.group(0)))
        except ValueError:
            pass
    return out


# mondathatar: pont/pontosvesszo/kerdojel + szokoz + NAGYBETU, vagy sorvege.
# Fontos: a magyar datum ("2026. augusztus") NEM mondathatar, mert kisbetu koveti.
MONDATVEG = re.compile(r"[.;!?]\s+(?=[A-ZÁÉÍÓÖŐÚÜŰ])|\n\s*\n")


def mondat(text, pos):
    """A datumot tartalmazo MONDAT. Fix karakterablak helyett, mert a szomszedos
    mondat kulcsszavai (pl. 'visszajelzest kerunk') elrontjak a pontozast."""
    lo = 0
    for m in MONDATVEG.finditer(text, 0, pos):
        lo = m.end()
    hi = len(text)
    m = MONDATVEG.search(text, pos)
    if m:
        hi = m.start() + 1
    return text[lo:hi]


def score(text, pos):
    """Mennyire valoszinu, hogy ez a datum az ajanlat hatarideje."""
    ctx = mondat(text, pos).lower()
    s = 0
    for kw, w in KULCS:
        if kw in ctx:
            s += w
    for kw, w in TILTO:
        if kw in ctx:
            s += w
    return s


def parse_eml(raw):
    msg = email.message_from_string(raw, policy=policy.default)
    body = None
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            body = part.get_content(); break
    if body is None:
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                import html as h
                body = re.sub(r"<[^>]+>", " ", h.unescape(part.get_content())); break
    body = body or ""
    subj = msg.get("Subject") or ""
    cands = []
    for d, pos, raw_txt in find_dates(body):
        sc = score(body, pos)
        mnd = " ".join(mondat(body, pos).split())[:200]
        cands.append({"datum": d.isoformat(), "pont": sc, "nyers": raw_txt, "kornyezet": mnd})
    cands.sort(key=lambda c: (-c["pont"], c["datum"]), reverse=False)
    cands.sort(key=lambda c: (-c["pont"], [-int(x) for x in c["datum"].split("-")]))
    best = cands[0] if cands and cands[0]["pont"] > 0 else None
    return {"targy": subj, "hatarido": best["datum"] if best else None,
            "bizonyitek": best["kornyezet"] if best else None,
            "pont": best["pont"] if best else 0,
            "tovabbi": [c for c in cands[1:4]]}


def collect(refresh=False):
    """Egyetlen lekerdezessel osszeszedi az OSSZES .eml-t, majd a szulomappakbol
    visszafejti a projekt nevet. Igy nem kell projektenkent kulon API-hivas."""
    cache = {} if refresh or not os.path.exists(CACHE) else json.load(open(CACHE))
    svc = _svc()
    emls, page = [], None
    while True:
        r = svc.files().list(q="name contains '.eml' and trashed=false",
                             fields="nextPageToken,files(id,name,modifiedTime,parents)",
                             pageSize=200, pageToken=page,
                             includeItemsFromAllDrives=True, supportsAllDrives=True).execute()
        emls += r.get("files", [])
        page = r.get("nextPageToken")
        if not page:
            break
    # szulomappa -> projektnev feloldas, gyorsitotarazva
    fld = {}
    def nev(fid):
        if fid in fld:
            return fld[fid]
        try:
            f = svc.files().get(fileId=fid, fields="id,name,parents",
                                supportsAllDrives=True).execute()
        except Exception:
            fld[fid] = ("?", None); return fld[fid]
        fld[fid] = (f.get("name", "?"), (f.get("parents") or [None])[0])
        return fld[fid]

    rows = []
    for e in emls:
        par = (e.get("parents") or [None])[0]
        projekt = "?"
        if par:
            n1, gp = nev(par)
            # ha a szulo az "00 Ajanlatkero_e_mail", a nagyszulo a projekt
            projekt = n1
            if gp and re.match(r"^\d\d\s", n1 or ""):
                projekt = nev(gp)[0]
        key = e["id"] + "|" + e["modifiedTime"]
        if key in cache:
            info = cache[key]
        else:
            try:
                raw = svc.files().get_media(fileId=e["id"]).execute().decode("utf-8", "replace")
                info = parse_eml(raw)
            except Exception as ex:
                info = {"hiba": str(ex)[:160], "hatarido": None, "pont": 0,
                        "targy": e["name"], "bizonyitek": None, "tovabbi": []}
            cache[key] = info
        rows.append({"projekt": projekt, "fajl": e["name"], **info})
    json.dump(cache, open(CACHE, "w"), ensure_ascii=False, indent=0)
    return rows, cache


def manual_entries():
    if not os.path.exists(MANUAL):
        return []
    try:
        return json.load(open(MANUAL))
    except Exception:
        return []


def manual_rows():
    # "lezarva": true -> a tetel a fajlban MARAD (megorzi a tortenetet), de mar nem
    # jelentjuk. Igy egy visszamondott vagy leadott ajanlat nem kialt farkast minden
    # reggel, es kesobb sem kell talalgatni, mi tortent vele.
    return [{"projekt": x.get("projekt", "?"), "fajl": "kézi bejegyzés",
             "targy": x.get("megjegyzes", ""), "hatarido": x.get("hatarido"),
             "bizonyitek": "kézzel megadva", "pont": 99, "tovabbi": []}
            for x in manual_entries() if not x.get("lezarva")]


def riport(rows, ma=None):
    ma = ma or datetime.date.today()
    van = [r for r in rows if r.get("hatarido")]
    for r in van:
        r["nap"] = (datetime.date.fromisoformat(r["hatarido"]) - ma).days
    van.sort(key=lambda r: r["nap"])
    lejart = [r for r in van if r["nap"] < 0]
    ma_jar = [r for r in van if r["nap"] == 0]
    kozeli = [r for r in van if 0 < r["nap"] <= 7]
    tavoli = [r for r in van if r["nap"] > 7]
    nincs = [r for r in rows if not r.get("hatarido")]
    return {"ma": ma.isoformat(), "lejart": lejart, "ma_jar": ma_jar,
            "kozeli": kozeli, "tavoli": tavoli, "nincs_hatarido": nincs}


def szoveg(rep):
    L = []
    def blokk(cim, rows, mutasd_bizonyitekot=False):
        if not rows:
            return
        L.append(cim)
        for r in rows:
            nap = r["nap"]
            jel = f"{abs(nap)} napja lejárt" if nap < 0 else ("MA" if nap == 0 else f"{nap} nap")
            L.append(f"  {r['hatarido']}  {jel:16} {r['projekt'][:52]}")
            if mutasd_bizonyitekot and r.get("bizonyitek"):
                L.append(f"      \"{r['bizonyitek'][:120]}\"")
        L.append("")
    blokk("LEJÁRT", rep["lejart"], True)
    blokk("MA JÁR LE", rep["ma_jar"], True)
    blokk("A KÖVETKEZŐ 7 NAPBAN", rep["kozeli"], True)
    blokk("KÉSŐBB", rep["tavoli"])
    if rep["nincs_hatarido"]:
        L.append(f"HATÁRIDŐ NÉLKÜL ({len(rep['nincs_hatarido'])} ajánlatkérés, nem találtam dátumot)")
        for r in rep["nincs_hatarido"][:8]:
            L.append(f"  {r['projekt'][:60]}")
    return "\n".join(L) if L else "Nincs nyilvántartott határidő."


if __name__ == "__main__":
    refresh = "--refresh" in sys.argv
    rows, _ = collect(refresh=refresh)
    bejegyzesek = manual_entries()
    kezi = manual_rows()

    def kulcs(nev):
        return (nev or "")[:28].lower()

    # LEZART tetel: a projekt MINDEN auto-sorat elnyomjuk, a datumosat is. Enelkul egy
    # leadott vagy visszamondott ajanlat orokke ott maradna a LEJART blokkban (A-344), a
    # datum nelkuli pedig a "nincs hatarido" listaban (A-361) -- mert a lezart bejegyzes
    # kiesett a kezi sorok kozul, es igy mar nem nyomta el a sajat auto-parjat sem.
    lezart = {kulcs(x.get("projekt")) for x in bejegyzesek if x.get("lezarva")}
    # ELO kezi hatarido: csak a projekt datum NELKULI auto-sorat nyomja el (feluliras).
    elo = {kulcs(x.get("projekt")) for x in bejegyzesek if not x.get("lezarva")}

    rows = [r for r in rows
            if kulcs(r["projekt"]) not in lezart
            and (r.get("hatarido") or kulcs(r["projekt"]) not in elo)]
    rows += kezi
    rep = riport(rows)
    if "--json" in sys.argv:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(szoveg(rep))
