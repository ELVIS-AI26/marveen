#!/usr/bin/env python3
"""
Google Drive browser helper.

Lets an agent list/search Drive folders and files from the command line,
using the same OAuth user token as scripts/gdocs.py (store/.gdocs-oauth.json,
scope already includes https://www.googleapis.com/auth/drive). No separate
auth step needed if gdocs.py is already authorized.

Usage (run with a venv that has the deps from scripts/gdocs-requirements.txt):
  gdrive.py whoami                     # print the authorized account's email
  gdrive.py list --folder <id_or_url>  # list a folder's direct children
  gdrive.py list                       # list items at "My Drive" root
  gdrive.py recent [n]                 # n most recently modified files (default 20)
  gdrive.py search <term>              # name-contains search, Drive-wide

Output is JSON (one object per call): a list of {id, name, mimeType,
modifiedTime, webViewLink, parents} entries, or for whoami a single
{"email": "..."} object.
"""
import sys, os, json, re
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/documents",
          "https://www.googleapis.com/auth/drive"]
STORE = os.path.join(os.path.dirname(__file__), "..", "store")
OAUTH_TOKEN = os.path.join(STORE, ".gdocs-oauth.json")
TOKEN_URI = "https://oauth2.googleapis.com/token"

FOLDER_MIME = "application/vnd.google-apps.folder"
FIELDS = "files(id,name,mimeType,modifiedTime,webViewLink,parents)"

def _svc():
    with open(OAUTH_TOKEN) as f:
        t = json.load(f)
    creds = UserCredentials(
        token=None, refresh_token=t["refresh_token"],
        client_id=t["client_id"], client_secret=t["client_secret"],
        token_uri=TOKEN_URI, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def _folder_id(s):
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", s)
    return m.group(1) if m else s

def _list_files(svc, **kwargs):
    out = []
    page_token = None
    while True:
        resp = svc.files().list(
            fields=f"nextPageToken,{FIELDS}",
            pageToken=page_token,
            **kwargs,
        ).execute()
        out.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out

def cmd_whoami():
    svc = _svc()
    about = svc.about().get(fields="user(emailAddress,displayName)").execute()
    print(json.dumps(about.get("user", {}), ensure_ascii=False, indent=2))

def cmd_list(folder=None):
    svc = _svc()
    if folder:
        fid = _folder_id(folder)
        q = f"'{fid}' in parents and trashed = false"
    else:
        q = "'root' in parents and trashed = false"
    files = _list_files(svc, q=q, orderBy="folder,name",
                         pageSize=1000, supportsAllDrives=True,
                         includeItemsFromAllDrives=True)
    print(json.dumps(files, ensure_ascii=False, indent=2))

def cmd_recent(n=20):
    svc = _svc()
    files = _list_files(svc, q="trashed = false", orderBy="modifiedTime desc",
                         pageSize=n, supportsAllDrives=True,
                         includeItemsFromAllDrives=True)
    print(json.dumps(files[:n], ensure_ascii=False, indent=2))

def cmd_search(term):
    svc = _svc()
    safe = term.replace("'", "\\'")
    q = f"name contains '{safe}' and trashed = false"
    files = _list_files(svc, q=q, orderBy="modifiedTime desc",
                         pageSize=200, supportsAllDrives=True,
                         includeItemsFromAllDrives=True)
    print(json.dumps(files, ensure_ascii=False, indent=2))

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "whoami":
        cmd_whoami()
    elif cmd == "list":
        folder = None
        if len(sys.argv) > 2 and sys.argv[2] == "--folder":
            folder = sys.argv[3]
        cmd_list(folder)
    elif cmd == "recent":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        cmd_recent(n)
    elif cmd == "search":
        if len(sys.argv) < 3:
            print("search needs a term"); sys.exit(1)
        cmd_search(sys.argv[2])
    else:
        print(f"unknown command: {cmd}\n{__doc__}"); sys.exit(1)

if __name__ == "__main__":
    main()
