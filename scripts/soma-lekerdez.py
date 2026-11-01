#!/usr/bin/env python3
"""SOMA CRM -- CSAK OLVASO kapu az elsys-ajanlat sub-agensnek.

MIERT LETEZIK: a SOMA API-kulcs (store/.soma-api-creds) egyetlen, TELJES jogu
hozzaferes -- a webservice `create`/`update`/`delete` muveleteivel egyutt. Ha a
sub-agens kozvetlenul hasznalna, akkor irhatna is a CRM-be, es a rekordirasbol
ertesites megy emberekhez. Nemeth Laszlo alapszabalya (kifele nem kommunikalunk)
ezt tiltja. Ezert a kulcs nala nem elerheto, es ez a kapu SEMMILYEN irast nem
tesz lehetove: csak `query`, csak a lentebbi modulokra.

HASZNALAT:
  soma-lekerdez.py modulok
  soma-lekerdez.py mezok <modul>
  soma-lekerdez.py keres <modul> <szoveg> [darab]   -- nevre keres
  soma-lekerdez.py sql "select ... from Potentials where ...;"
"""
import hashlib
import json
import re
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Csak ezek a modulok. Az ajanlatkesziteshez ennyi kell; a HR-, szamlazasi- es
# felhasznalo-modulok szandekosan kimaradnak.
MODULES = {
    "Potentials": "Ajanlatok / uzleti lehetosegek",
    "Project": "Projektek",
    "Accounts": "Ceges partnerek",
    "Contacts": "Kapcsolattartok",
    "Products": "Termekek",
    "Vendors": "Beszallitok",
    "Quotes": "Arajanlatok",
}

NAME_FIELD = {
    "Potentials": "potentialname", "Project": "projectname",
    "Accounts": "accountname", "Contacts": "lastname",
    "Products": "productname", "Vendors": "vendorname",
    "Quotes": "subject",
}


def _creds():
    c = dict(re.findall(r'^([A-Z_]+)=(.*)$',
                        open(os.path.join(ROOT, 'store/.soma-creds')).read(), re.M))
    k = dict(re.findall(r'^([A-Z_]+)=(.*)$',
                        open(os.path.join(ROOT, 'store/.soma-api-creds')).read(), re.M))
    return c['SOMA_URL'].rstrip('/'), k['SOMA_API_USER'], k['SOMA_API_KEY']


def _session():
    base, user, key = _creds()
    tok = json.load(urllib.request.urlopen(
        "%s/webservice.php?operation=getchallenge&username=%s"
        % (base, urllib.parse.quote(user)), timeout=25))['result']['token']
    data = urllib.parse.urlencode({
        'operation': 'login', 'username': user,
        'accessKey': hashlib.md5((tok + key).encode()).hexdigest()}).encode()
    r = json.load(urllib.request.urlopen(
        urllib.request.Request("%s/webservice.php" % base, data=data), timeout=25))
    return base, r['result']['sessionName']


def _query(sql):
    base, sess = _session()
    u = "%s/webservice.php?operation=query&sessionName=%s&query=%s" % (
        base, urllib.parse.quote(sess), urllib.parse.quote(sql))
    r = json.load(urllib.request.urlopen(u, timeout=40))
    if not r.get("success"):
        raise SystemExit("SOMA hiba: %s" % json.dumps(r.get("error"), ensure_ascii=False)[:300])
    return r["result"]


def _check_sql(sql):
    """Csak SELECT, csak engedelyezett modulra. Az iras itt hal meg."""
    s = sql.strip()
    if not s.lower().startswith("select"):
        raise SystemExit("TILTOTT: csak SELECT futtathato, ez a kapu nem ir a CRM-be")
    for bad in ("create", "update", "delete", "insert", "drop", "alter", ";--"):
        if re.search(r"\b%s\b" % bad, s, re.I):
            raise SystemExit("TILTOTT: iro muvelet (%s) nem futtathato" % bad)
    m = re.search(r"\bfrom\s+([A-Za-z_]+)", s, re.I)
    if not m or m.group(1) not in MODULES:
        raise SystemExit("TILTOTT: csak ezek a modulok: %s" % ", ".join(sorted(MODULES)))
    if not s.endswith(";"):
        s += ";"   # a vTiger query-nel a lezaro pontosvesszo kotelezo
    return s


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "modulok"

    if cmd == "modulok":
        for k in sorted(MODULES):
            print("%-12s %s" % (k, MODULES[k]))
        return 0

    if cmd == "mezok":
        base, sess = _session()
        u = "%s/webservice.php?operation=describe&sessionName=%s&elementType=%s" % (
            base, urllib.parse.quote(sess), urllib.parse.quote(a[1]))
        if a[1] not in MODULES:
            raise SystemExit("TILTOTT: csak ezek a modulok: %s" % ", ".join(sorted(MODULES)))
        r = json.load(urllib.request.urlopen(u, timeout=30))
        for f in r.get("result", {}).get("fields", []):
            print("%-28s %s" % (f.get("name"), f.get("label")))
        return 0

    if cmd == "keres":
        mod, text = a[1], a[2]
        n = a[3] if len(a) > 3 else "20"
        if mod not in MODULES:
            raise SystemExit("TILTOTT: csak ezek a modulok: %s" % ", ".join(sorted(MODULES)))
        fld = NAME_FIELD[mod]
        sql = "select * from %s where %s like '%%%s%%' limit %s;" % (
            mod, fld, text.replace("'", ""), int(n))
        for row in _query(sql):
            print(json.dumps(row, ensure_ascii=False))
        return 0

    if cmd == "sql":
        for row in _query(_check_sql(a[1])):
            print(json.dumps(row, ensure_ascii=False))
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
