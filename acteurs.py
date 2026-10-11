#!/usr/bin/env python3
"""Récupère photos et filmographies d'acteurs français.

Source principale : TMDB, en français (fr-FR). Photo de secours : Wikidata /
Wikimedia Commons. Écrit data/acteurs.json (lu par build.py pour le catalogue
Nuvio) et acteurs/index.html (page avec photos et filmographies).

La liste des acteurs est dans data/acteurs_liste.json (modifiable).
La clé TMDB (v3 ou jeton v4) est lue dans la variable d'environnement
TMDB_API_KEY. Sans clé, le script ne fait rien.
"""
import datetime
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
LISTE = os.path.join(ROOT, "data", "acteurs_liste.json")
OUT = os.path.join(ROOT, "data", "acteurs.json")
PAGE = os.path.join(ROOT, "acteurs", "index.html")

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"
UA = "sorties-addon/1.0 (https://github.com/Auguste76/sorties)"
MAX_PAR_TYPE = 40

FAMILLES = [
    ("thriller", {53, 80, 9648}),
    ("sf", {878, 14, 10765}),
    ("comedie", {35}),
    ("drame", {18, 99, 36, 10749}),
]
EXCLU_TV = {10767, 10763, 10764}  # talk-show, actualités, télé-réalité
SELF_RE = re.compile(r"\b(self|lui-m[eê]me|elle-m[eê]me|himself|herself)\b", re.I)


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def get_json(url, headers=None, tries=4):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(2 + 3 * i)
                continue
            raise
        except urllib.error.URLError:
            time.sleep(2 + 3 * i)
    return None


def tmdb(path, **params):
    key = os.environ["TMDB_API_KEY"].strip()
    headers = {}
    params.setdefault("language", "fr-FR")
    if len(key) > 40:
        headers["Authorization"] = "Bearer " + key
    else:
        params["api_key"] = key
    time.sleep(0.05)
    return get_json(f"{API}{path}?{urllib.parse.urlencode(params)}", headers)


def trouver(nom):
    r = tmdb("/search/person", query=nom, include_adult="false") or {}
    res = [p for p in r.get("results", []) if p.get("known_for_department") == "Acting"]
    exact = [p for p in res if norm(p.get("name", "")) == norm(nom)]
    cands = exact or res
    return max(cands, key=lambda p: p.get("popularity", 0)) if cands else None


def photo_wikidata(nom):
    base = "https://www.wikidata.org/w/api.php?"
    r = get_json(base + urllib.parse.urlencode(
        {"action": "wbsearchentities", "search": nom, "language": "fr", "type": "item", "limit": 5, "format": "json"}))
    for it in (r or {}).get("search", []):
        e = get_json(base + urllib.parse.urlencode(
            {"action": "wbgetentities", "ids": it["id"], "props": "claims", "format": "json"}))
        claims = ((e or {}).get("entities", {}).get(it["id"], {}) or {}).get("claims", {})
        occ = set()
        for c in claims.get("P106", []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
            if isinstance(v, dict) and "id" in v:
                occ.add(v["id"])
        if "Q33999" in occ and claims.get("P18"):  # Q33999 = acteur
            f = claims["P18"][0]["mainsnak"]["datavalue"]["value"]
            return "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(f) + "?width=500"
    return None


def famille(genre_ids):
    g = set(genre_ids or [])
    for nom, ids in FAMILLES:
        if g & ids:
            return nom
    return None


def court(texte, n=220):
    t = (texte or "").strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    point = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: point + 1] if point > 80 else cut.rstrip() + "…"


def imdb_id(mt, tid, ids):
    cle = f"{mt}:{tid}"
    if cle not in ids:
        r = tmdb(f"/{mt}/{tid}/external_ids") or {}
        ids[cle] = r.get("imdb_id") or ""
    return ids[cle]


def filmographie(pid, ids):
    c = tmdb(f"/person/{pid}/combined_credits") or {}
    cands = []
    for it in c.get("cast", []):
        mt = it.get("media_type")
        if mt not in ("movie", "tv"):
            continue
        if mt == "tv" and set(it.get("genre_ids") or []) & EXCLU_TV:
            continue
        role = (it.get("character") or "").strip()
        if SELF_RE.search(role):
            continue
        date = it.get("release_date") or it.get("first_air_date") or ""
        if not date or (it.get("vote_count") or 0) < 5:
            continue
        cands.append((date, mt, it, role))
    cands.sort(key=lambda x: x[0], reverse=True)
    out = {"films": [], "series": []}
    vus, essais = set(), 0
    for date, mt, it, role in cands:
        cle_liste = "films" if mt == "movie" else "series"
        if len(out[cle_liste]) >= MAX_PAR_TYPE or essais >= 3 * MAX_PAR_TYPE:
            if all(len(v) >= MAX_PAR_TYPE for v in out.values()) or essais >= 3 * MAX_PAR_TYPE:
                break
            continue
        essais += 1
        tt = imdb_id(mt, it["id"], ids)
        if not tt or (tt, mt) in vus:
            continue
        vus.add((tt, mt))
        desc = (f"Rôle : {role}. " if role else "") + court(it.get("overview"))
        out[cle_liste].append({
            "id": tt,
            "type": "movie" if mt == "movie" else "series",
            "name": it.get("title") or it.get("name") or "",
            "description": desc.strip(),
            "year": date[:4],
            "famille": famille(it.get("genre_ids")),
            "date": date,
            "role": role,
            "tmdb": f"https://www.themoviedb.org/{mt}/{it['id']}?language=fr-FR",
        })
    return out


def construire(nom, ids):
    p = trouver(nom)
    if not p:
        print(f"Introuvable sur TMDB : {nom}")
        return None
    d = tmdb(f"/person/{p['id']}") or {}
    photo = IMG + d["profile_path"] if d.get("profile_path") else None
    source_photo = "TMDB" if photo else None
    if not photo:
        photo = photo_wikidata(nom)
        source_photo = "Wikimedia Commons" if photo else None
    f = filmographie(p["id"], ids)
    return {
        "nom": nom,
        "tmdb": p["id"],
        "photo": photo,
        "sourcePhoto": source_photo,
        "naissance": d.get("birthday") or "",
        "lieu": d.get("place_of_birth") or "",
        "bio": court(d.get("biography"), 420),
        "films": f["films"],
        "series": f["series"],
    }


def ecrire_page(acteurs, maj):
    e = html.escape
    cartes = []
    for a in acteurs:
        img = (f'<img src="{e(a["photo"])}" alt="{e(a["nom"])}" loading="lazy">'
               if a.get("photo") else '<div class="nophoto">?</div>')
        infos = " · ".join(x for x in (a.get("naissance"), a.get("lieu")) if x)
        lignes = []
        for titre, cle, etiquette in (("Films", "films", "Film"), ("Séries", "series", "Série")):
            if not a.get(cle):
                continue
            li = "".join(
                f'<li><span class="an">{e(str(x.get("year", "")))}</span> '
                f'<a href="{e(x["tmdb"])}" rel="noopener">{e(x["name"])}</a>'
                + (f' <span class="role">— {e(x["role"])}</span>' if x.get("role") else "")
                + "</li>"
                for x in a[cle]
            )
            lignes.append(f"<details><summary>{titre} ({len(a[cle])})</summary><ul>{li}</ul></details>")
        cartes.append(
            f'<article class="carte"><div class="photo">{img}</div><div class="corps">'
            f'<h2>{e(a["nom"])}</h2><p class="infos">{e(infos)}</p>'
            f'<p class="bio">{e(a.get("bio", ""))}</p>{"".join(lignes)}</div></article>'
        )
    page = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acteurs français</title>
<style>
:root{{--bg:#f8fafc;--fg:#0f172a;--mut:#64748b;--card:#fff;--bd:#e2e8f0;--ac:#0369a1}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0b1220;--fg:#e2e8f0;--mut:#94a3b8;--card:#131c2e;--bd:#24304a;--ac:#38bdf8}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}}
header,main,footer{{max-width:960px;margin:0 auto;padding:16px}}
h1{{margin:.2em 0}}.mut{{color:var(--mut);font-size:.9em}}
.carte{{display:flex;gap:16px;background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:14px;margin:14px 0}}
.photo img,.nophoto{{width:110px;height:165px;object-fit:cover;border-radius:10px;background:var(--bd)}}
.nophoto{{display:flex;align-items:center;justify-content:center;font-size:2em;color:var(--mut)}}
.corps{{flex:1;min-width:0}}h2{{margin:0 0 2px;font-size:1.2em}}.infos{{margin:0;color:var(--mut);font-size:.85em}}
.bio{{font-size:.92em}}summary{{cursor:pointer;color:var(--ac);margin:4px 0}}ul{{margin:6px 0 10px;padding-left:18px}}
li{{margin:2px 0;font-size:.92em}}.an{{color:var(--mut);font-variant-numeric:tabular-nums}}.role{{color:var(--mut)}}a{{color:var(--ac)}}
@media(max-width:560px){{.carte{{flex-direction:column}}.photo img,.nophoto{{width:100%;height:240px}}}}
</style></head><body>
<header><h1>Acteurs français</h1><p class="mut">Photos et filmographies, mis à jour le {e(maj)}.</p></header>
<main>{"".join(cartes)}</main>
<footer class="mut">Données : TMDB (en français) ; photos de secours : Wikimedia Commons. Ce produit utilise l'API TMDB mais n'est ni approuvé ni certifié par TMDB.</footer>
</body></html>
"""
    os.makedirs(os.path.dirname(PAGE), exist_ok=True)
    with open(PAGE, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    if not os.environ.get("TMDB_API_KEY"):
        print("TMDB_API_KEY absente : rien à faire.")
        return 0
    with open(LISTE, encoding="utf-8") as f:
        noms = [unicodedata.normalize("NFC", n.strip()) for n in json.load(f) if n.strip()]
    etat = {"acteurs": [], "ids": {}}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            etat = json.load(f)
    ids = etat.get("ids", {})
    anciens = {a["nom"]: a for a in etat.get("acteurs", [])}
    acteurs, echecs = [], 0
    for nom in noms:
        try:
            a = construire(nom, ids)
        except Exception as exc:  # noqa: BLE001
            print(f"Échec pour {nom} : {type(exc).__name__}")
            echecs += 1
            a = anciens.get(nom)
        if a:
            acteurs.append(a)
    if not acteurs:
        print("Aucun acteur récupéré : clé TMDB refusée ou service indisponible ?", file=sys.stderr)
        return 1
    maj = datetime.date.today().strftime("%d/%m/%Y")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"majLe": maj, "acteurs": acteurs, "ids": ids}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    ecrire_page(acteurs, maj)
    print(json.dumps({"acteurs": len(acteurs), "echecs": echecs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
