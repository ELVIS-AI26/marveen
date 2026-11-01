#!/usr/bin/env python3
"""Fireflies felvetelek rendszerezese: cim, cimke, projekt-hozzarendeles.

A telefonrol ad-hoc inditott felvetelek NINCSENEK naptari esemenyhez kotve
(calendar_id = null), ezert a cim csak a datum ("Aug 27, 04:04 PM"). A metaadat
viszont MEGVAN: a Fireflies minden felvetelhez general magyar kulcsszavakat es
osszefoglalot. Ez a szkript abbol dolgozik, es a SOMA ugyfel- es projektnevekhez
parositja.

  python3 scripts/fireflies-cimkezo.py            # SZARAZ FUTAS, csak kiirja mit tenne
  python3 scripts/fireflies-cimkezo.py --write    # visszairja a cimeket a Fireflies-ba
  python3 scripts/fireflies-cimkezo.py --limit 20 --json out.json

A --write CSAK a tulaj kifejezett engedelyevel futtathato.
"""
import json, sys, re, argparse, unicodedata, urllib.request, urllib.error, hashlib, urllib.parse, time

ROOT = "/home/nmeth_laszlo/marveen"
FF_KEY = open(ROOT + "/store/.fireflies-api-key").read().strip()

def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))

def ff(q, variables=None):
    body = {"query": q}
    if variables: body["variables"] = variables
    r = urllib.request.Request("https://api.fireflies.ai/graphql",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + FF_KEY})
    try:
        return json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:400]}

def soma_session():
    c = dict(re.findall(r'^([A-Z_]+)=(.*)$', open(ROOT+'/store/.soma-creds').read(), re.M))
    k = dict(re.findall(r'^([A-Z_]+)=(.*)$', open(ROOT+'/store/.soma-api-creds').read(), re.M))
    base = c['SOMA_URL'].rstrip('/')
    tok = json.load(urllib.request.urlopen(
        f"{base}/webservice.php?operation=getchallenge&username={urllib.parse.quote(k['SOMA_API_USER'])}"))['result']['token']
    d = urllib.parse.urlencode({'operation':'login','username':k['SOMA_API_USER'],
        'accessKey': hashlib.md5((tok+k['SOMA_API_KEY']).encode()).hexdigest()}).encode()
    sess = json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/webservice.php", data=d)))['result']['sessionName']
    return base, sess

def soma_query(base, sess, sql):
    u = f"{base}/webservice.php?operation=query&sessionName={urllib.parse.quote(sess)}&query={urllib.parse.quote(sql)}"
    return json.load(urllib.request.urlopen(u)).get('result', [])

STOP = {"kft","zrt","bt","nyrt","es","az","egy","budapest","hu","projekt","epulet",
        "irodahaz","hotel","plaza","center","kozpont","rendszer","the","and"}

def tokens(name):
    """Egy ugyfel- vagy projektnevbol hasznalhato horgony-szavak."""
    out = set()
    for w in re.split(r"[^0-9a-zA-Zaaeeiioouuoou]+", norm(name)):
        if len(w) >= 4 and w not in STOP and not w.isdigit():
            out.add(w)
    return out

# HELYSZIN-SZOTAR. A SOMA projektnevek gyakorisagi elemzesebol szuletett, KEZZEL
# valogatva. Ami sok projektnevben szerepel ES nem szakszo, az visszatero helyszin
# (festo 200x, green house 85x, auchan 44x, airport 35x, dunavecse 32x, agora 30x...).
#
# KET ZSAKUTCA, amit vegigprobaltam 2026-08-27-en, hogy ne kelljen ujra:
#  (a) MINDEN SOMA ugyfel- es projektnev tokenizalasa -> minden beszelgetesre talalt
#      valamit, mert az "automatika", "vilagitas", "eloszto" szakszavak nevkent
#      viselkedtek. Hasznalhatatlan zaj.
#  (b) Ritkasag (IDF) szerinti szures -> pont forditva sult el: a RITKA szo az
#      egyedi projektnev-toredek, a GYAKORI viszont eppen a visszatero helyszin.
# A mukodo megoldas a szo szerinti egyezes egy rovid, kezzel gondozott listaval.
SITES = {
    "festo": "Festo", "ariba": "Festo (Ariba e-aukcio)",
    "green house": "Green House", "greenhouse": "Green House",
    "auchan": "Auchan", "airport hotel": "Airport Hotel", "dunavecse": "Dunavecse",
    "agora": "Agora", "duna plaza": "Duna Plaza", "dunaplaza": "Duna Plaza",
    "duna plaza": "Duna Plaza", "schneider": "Schneider", "corvin": "Corvin",
    "msci": "MSCI", "bosch": "Bosch", "hankook": "Hankook", "eiffel": "Eiffel",
    "krisztina": "Krisztina Palace", "arena mall": "Arena Mall", "spar": "SPAR",
    "richter": "Richter", "continental": "Continental", "atrium": "Atrium",
    "allee": "Allee", "waikiki": "Allee LC Waikiki", "infopark": "Infopark",
    "mlsz": "MLSZ", "kozmo": "Kozmo Hotel", "viragpiac": "Viragpiac",
    "csepel": "Csepel Plaza", "vertes": "Vertes Center", "dorottya": "Dorottya",
    "tokod": "Tokod", "seregelyes": "Seregelyes", "bokay": "Bokay Residence",
    "janus henderson": "Janus Henderson", "eleven center": "Eleven Center",
    "h2o": "H2O", "vaci ut": "Vaci ut", "palotai": "belso (tulajdonosi)",
}

def build_dictionary():
    return SITES

def classify(t, sites):
    s = t.get('summary') or {}
    kws = [str(x) for x in (s.get('keywords') or [])]
    blob = norm(" ".join(kws) + " " + (s.get('short_summary') or ""))
    found = []
    for pat, disp in sites.items():
        if pat in blob and disp not in found:
            found.append(disp)
    # Atfedo talalatok osszevonasa. A "festo" es az "ariba" is Festo-t jelol, ezert
    # a ketto egyutt "Festo / Festo (Ariba e-aukcio)" cimet adott. Ha az egyik nev
    # a masik eleje, csak a BOVEBBET tartjuk meg.
    found = [d for d in found if not any(o != d and o.startswith(d) for o in found)]
    return kws, found

# A Fireflies auto-cime a datum maga: "Aug 26, 10:06 AM" (a webm feltoltesnel
# "Feb 18_ 01_46 PM.webm"). Ami NEM ilyen, azt EMBER nevezte el -- ahhoz nem nyulunk.
AUTO_TITLE = re.compile(r"^[A-Z][a-z]{2} \d{2}[,_] \d{2}[:_]\d{2} [AP]M(\.\w+)?$")

# A SAJAT formatumunk: "2026-08-27 | Festo | temak". Ha mar igy all a cim, keszen
# van -- ettol a napi futas idempotens: csak az uj, datum-cimu felvetelekhez nyul.
OWN_TITLE = re.compile(r"^\d{4}-\d{2}-\d{2} \| .+ \| ")

def skip_reason(t, kws):
    """Miert NEM irjuk at ezt a felvetelt. None = atirhato."""
    title = (t.get("title") or "").strip()
    if OWN_TITLE.match(title):
        return "mar rendezve"
    if not AUTO_TITLE.match(title):
        return "kezzel elnevezett"
    if not kws:
        return "nincs kulcsszo"
    return None

def propose_title(t, kws, found):
    d = str(t.get('dateString') or "")[:10]
    topic = ", ".join(kws[:2]) if kws else "tema nincs"
    # A Fireflies KISZURI a "/" jelet a cimbol (a "|" atmegy). Ket helyszin " / "
    # elvalasztoval igy "Festo Festo" alaku maszatot adott -- ezert "+" a szeparator.
    tag = " + ".join(found) if found else "belso"
    return f"{d} | {tag} | {topic}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()

    sites = build_dictionary()
    print(f"# helyszin-szotar: {len(set(sites.values()))} helyszin\n", file=sys.stderr)

    # A Fireflies a limit-et 50-nel vagja; ennel tobbhoz skip-pel lapozni kell.
    ts, skip = [], 0
    while len(ts) < a.limit:
        page = min(50, a.limit - len(ts))
        q = """{ transcripts(limit: %d, skip: %d) { id title dateString duration calendar_id
                 summary { keywords short_summary } } }""" % (page, skip)
        d = ff(q)
        if "http_error" in d: sys.exit("Fireflies hiba: " + json.dumps(d)[:300])
        batch = d.get("data", {}).get("transcripts", []) or []
        if not batch: break
        ts += batch; skip += len(batch)

    out, skipped = [], []
    for t in ts:
        kws, ranked = classify(t, sites)
        why = skip_reason(t, kws)
        if why:
            skipped.append({"id": t["id"], "regi_cim": t.get("title"),
                            "datum": str(t.get("dateString"))[:16], "ok": why})
            continue
        new = propose_title(t, kws, ranked)
        out.append({"id": t["id"], "regi_cim": t.get("title"), "uj_cim": new,
                    "datum": str(t.get("dateString"))[:16],
                    "perc": round(t.get("duration") or 0),
                    "naptarhoz_kotve": bool(t.get("calendar_id")),
                    "helyszin": ranked, "kulcsszavak": kws[:6]})
        print(f"{str(t.get('dateString'))[:10]} {round(t.get('duration') or 0):>4}p  "
              f"{(t.get('title') or '')[:22]:<22} -> {new}")

    for sk in skipped:
        print(f"{sk['datum'][:10]}       {(sk['regi_cim'] or '')[:22]:<22} -- KIHAGYVA ({sk['ok']})")
    print(f"\n# atirhato: {len(out)}   kihagyva: {len(skipped)}", file=sys.stderr)

    if a.json:
        json.dump({"atirhato": out, "kihagyva": skipped}, open(a.json, "w"), ensure_ascii=False, indent=1)
        print(f"\n# kiirva: {a.json}", file=sys.stderr)

    if a.write:
        print("\n# VISSZAIRAS...", file=sys.stderr)
        # A Fireflies percenkent limital (too_many_requests). A hibauzenet visszaadja,
        # mikor szabad ujra: kivarjuk es UGYANAZT a tetelt ujraprobaljuk. Enelkul a
        # 125-os futasbol csak az elso ~57 ment at, a tobbi nemán elveszett.
        okc = errc = 0
        for o in out:
            for attempt in range(6):
                r = ff("mutation($input: UpdateMeetingTitleInput!){ updateMeetingTitle(input:$input){ title } }",
                       {"input": {"id": o["id"], "title": o["uj_cim"]}})
                errs = r.get("errors") or []
                rl = next((e for e in errs if (e.get("extensions") or {}).get("code") == "too_many_requests"), None)
                if rl:
                    until = (rl["extensions"].get("metadata") or {}).get("retryAfter", 0) / 1000
                    wait = max(5, min(180, until - time.time() + 2))
                    print(f"# rate limit, varok {wait:.0f}s", file=sys.stderr); sys.stderr.flush()
                    time.sleep(wait); continue
                ok = "http_error" not in r and not errs
                break
            else:
                ok = False
            okc += ok; errc += not ok
            print(("OK  " if ok else "HIBA ") + o["uj_cim"], file=sys.stderr)
        print(f"# visszairas kesz: {okc} ok, {errc} hiba", file=sys.stderr)
    else:
        print("\n# SZARAZ FUTAS volt, a Fireflies-ban semmi nem valtozott. "
              "Visszairas: --write (csak engedellyel).", file=sys.stderr)

if __name__ == "__main__":
    main()
