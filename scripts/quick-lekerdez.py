#!/usr/bin/env python3
"""QUiCK (quick.riport.co.hu) publikus API -- CSAK OLVASAS.

A kintlevoseg kerdeset a SOMA nem tudja megvalaszolni (a szamlazott-netto mezo
1563 rekordon ertelmetlen), a QUiCK viszont igen: onnan latszik, mi lett
kiszamlazva, mi folyt be, es mi jart le.

Hitelesites: Authorization: Token <t>, a token a store/.quick-creds fajlban
(QUICK_TOKEN kulcs, chmod 600). A token SOSEM kerul a kimenetbe.

A szkript szandekosan csak GET-et hasznal. Iras (create/update/delete) a
publikus API-n letezik, de ide NEM kerul bele: a hatarvonal az volt, hogy
olvasunk, nem irunk.

Hasznalat:
  quick-lekerdez.py kintlevoseg [--mind]   lejart es nyitott kimeno szamlak, korositve
  quick-lekerdez.py egyenlegek             banki egyenlegek (pulse)
  quick-lekerdez.py partner <szotoredek>   partner kereses
  quick-lekerdez.py szamlak <szotoredek>   egy partner nyitott szamlai
  quick-lekerdez.py nyers <ut> [q=v ...]   barmely GET vegpont nyersen
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

BASE = "https://api.quick.riport.co.hu"
CREDS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "store", ".quick-creds")


def _token():
    try:
        with open(CREDS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("QUICK_TOKEN"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    sys.exit("nincs QUICK_TOKEN a %s fajlban" % CREDS)


def get(path, **params):
    url = BASE + "/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Token " + _token(),
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        sys.exit("HTTP %s a %s vegponton: %s" % (e.code, path, body))


def all_pages(path, key=None, **params):
    """Vegiglapoz. Az /1/incomes/ a listat egy dict `incomes` kulcsa alatt adja."""
    out = []
    page = 1
    while True:
        d = get(path, page=page, page_size=100, **params)
        res = d.get("results")
        rows = res.get(key, []) if (isinstance(res, dict) and key) else res
        out.extend(rows or [])
        if not d.get("next"):
            return out
        page += 1


def _huf(x):
    try:
        return "{:,}".format(int(float(x))).replace(",", " ")
    except (TypeError, ValueError):
        return str(x)


def kintlevoseg(mind=False):
    rows = all_pages("1/incomes/", key="incomes", paid_status=1)
    rows += all_pages("1/incomes/", key="incomes", paid_status=3)
    rows = [r for r in rows if not r.get("is_cancelled")]
    ma = date.today()
    buckets = {"lejart 90+": [], "lejart 61-90": [], "lejart 31-60": [],
               "lejart 1-30": [], "meg nem jart le": []}
    for r in rows:
        due = r.get("due_at")
        d = (ma - datetime.strptime(due, "%Y-%m-%d").date()).days if due else 0
        if d <= 0:
            b = "meg nem jart le"
        elif d <= 30:
            b = "lejart 1-30"
        elif d <= 60:
            b = "lejart 31-60"
        elif d <= 90:
            b = "lejart 61-90"
        else:
            b = "lejart 90+"
        r["_nap"] = d
        buckets[b].append(r)

    ossz = sum(float(r.get("gross_amount") or 0) for r in rows)
    print("NYITOTT KIMENO SZAMLAK: %d db, brutto %s Ft (%s)" %
          (len(rows), _huf(ossz), ma.isoformat()))
    print()
    for name in ["lejart 90+", "lejart 61-90", "lejart 31-60", "lejart 1-30",
                 "meg nem jart le"]:
        b = sorted(buckets[name], key=lambda r: -r["_nap"])
        if not b:
            continue
        s = sum(float(r.get("gross_amount") or 0) for r in b)
        print("== %s: %d db, %s Ft" % (name, len(b), _huf(s)))
        for r in (b if (mind or name.startswith("lejart")) else b[:5]):
            print("   %-11s %-42s %12s Ft  esedekes %s (%+d nap)" % (
                r.get("invoice_number") or "-",
                (r.get("partner_name") or "-")[:42],
                _huf(r.get("gross_amount")), r.get("due_at") or "-", -r["_nap"]))
        if not mind and not name.startswith("lejart") and len(b) > 5:
            print("   ... es meg %d db (--mind mutatja mind)" % (len(b) - 5))
        print()


def egyenlegek():
    d = get("1/pulse/")
    print("BANKI EGYENLEGEK, osszesen: %s" % _huf(d.get("summary")))
    for a in d.get("accounts", []):
        print("  %-28s %14s %s   (frissitve %s)" % (
            (a.get("name") or "-")[:28], _huf(a.get("current_balance")),
            a.get("currency"), (a.get("last_updated") or "")[:10]))


def partner(toredek):
    rows = all_pages("1/partners/")
    t = toredek.lower()
    hit = [p for p in rows if t in (p.get("name") or "").lower()]
    for p in hit:
        print("%-9s %-45s ado %s  %s %s" % (
            p.get("id"), (p.get("name") or "")[:45], p.get("tax_number") or "-",
            p.get("zip_code") or "", p.get("city") or ""))
    print("-- %d talalat %d partnerbol" % (len(hit), len(rows)))


def szamlak(toredek):
    rows = all_pages("1/incomes/", key="incomes", paid_status=1)
    t = toredek.lower()
    hit = [r for r in rows if t in (r.get("partner_name") or "").lower()]
    ossz = sum(float(r.get("gross_amount") or 0) for r in hit)
    for r in sorted(hit, key=lambda r: r.get("due_at") or ""):
        print("%-11s %-40s %12s Ft  esedekes %s" % (
            r.get("invoice_number") or "-", (r.get("partner_name") or "")[:40],
            _huf(r.get("gross_amount")), r.get("due_at") or "-"))
    print("-- %d nyitott szamla, brutto %s Ft" % (len(hit), _huf(ossz)))


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "kintlevoseg":
        kintlevoseg("--mind" in args)
    elif cmd == "egyenlegek":
        egyenlegek()
    elif cmd == "partner":
        partner(args[1])
    elif cmd == "szamlak":
        szamlak(args[1])
    elif cmd == "nyers":
        params = dict(a.split("=", 1) for a in args[2:] if "=" in a)
        print(json.dumps(get(args[1], **params), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
