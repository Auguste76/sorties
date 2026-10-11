#!/usr/bin/env python3
"""Génère l'addon de catalogue Stremio/Nuvio « Sorties ».

Usage : python3 build.py --date AAAA-MM-JJ

Entrées facultatives, déposées dans entrees/ (lues puis supprimées) :
  aujourdhui.json  sorties du jour -> ajoutées à l'historique glissant (7 jours)
  avenir.json      titres à venir cette semaine -> remplace la liste précédente
  departs.json     titres qui quittent une plateforme -> remplace la liste
  suivies.json     séries suivies -> remplace la liste
Un fichier absent laisse la liste précédente intacte. Un fichier présent
mais vide ([]) la vide.

Chaque entrée : {"id": "tt...", "type": "movie"|"series", "name": "...",
  "description": "...", "year": 2026, "genres": ["Thriller", "Policier"],
  "date": "AAAA-MM-JJ"}   (date : sortie, ou départ pour departs.json)
Genres reconnus : Action, Animation, Aventure, Comédie, Policier, Documentaire,
Drame, Fantastique, Horreur, Romance, Science-fiction, Thriller, Famille,
Historique, Guerre (quelques variantes anglaises sont acceptées). L'ancien
champ "famille" (thriller|sf|comedie|drame) sert de secours si "genres" manque.

L'état (listes + cache des identifiants IMDb) est dans data/etat.json.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "data", "etat.json")
ACTEURS = os.path.join(ROOT, "data", "acteurs.json")  # produit par acteurs.py
ENTREES = os.path.join(ROOT, "entrees")
MANIFEST = os.path.join(ROOT, "manifest.json")
CATALOG = os.path.join(ROOT, "catalog")
META = os.path.join(ROOT, "meta")

KEEP_DAYS = 7
CAPS = {"historique": 60, "avenir": 40, "departs": 40, "suivies": 40}
GENRES_CANON = [
    "Action", "Animation", "Aventure", "Comédie", "Policier", "Documentaire", "Drame",
    "Fantastique", "Horreur", "Romance", "Science-fiction", "Thriller", "Famille",
    "Historique", "Guerre",
]
# Variantes acceptées (clés normalisées : minuscules, sans accents ni ponctuation)
SYNONYMES = {
    "comedie": "Comédie", "comedy": "Comédie", "crime": "Policier", "polar": "Policier",
    "science fiction": "Science-fiction", "sci fi": "Science-fiction", "sf": "Science-fiction",
    "fantasy": "Fantastique", "epouvante horreur": "Horreur", "epouvante": "Horreur",
    "horror": "Horreur", "history": "Historique", "histoire": "Historique", "war": "Guerre",
    "family": "Famille", "adventure": "Aventure", "documentary": "Documentaire",
    "drama": "Drame", "romantique": "Romance", "suspense": "Thriller", "mystere": "Thriller",
    "mystery": "Thriller",
}
# Ancien système de familles, utilisé seulement si une entrée n'a pas de genres
FAMILLE_VERS_GENRE = {"thriller": "Thriller", "sf": "Science-fiction", "comedie": "Comédie", "drame": "Drame"}
SITE = "https://auguste76.github.io/sorties"

# (type, id, nom, liste source, filtre genre)
CATALOGS = [
    ("movie", "sorties-films", "Sorties de la semaine", "historique", True),
    ("series", "sorties-series", "Sorties de la semaine", "historique", True),
    ("movie", "avenir-films", "À venir cette semaine", "avenir", True),
    ("series", "avenir-series", "À venir cette semaine", "avenir", True),
    ("movie", "depart-films", "Part bientôt", "departs", False),
    ("series", "depart-series", "Part bientôt", "departs", False),
    ("series", "suivies-series", "Mes séries suivies", "suivies", False),
]

ID_RE = re.compile(r"^tt\d{7,10}$")


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_PAR_NORM = {norm(g): g for g in GENRES_CANON}
_PAR_NORM.update(SYNONYMES)


def genres_de(e):
    """Genres canoniques d'une entrée (ordre de GENRES_CANON, sans doublon)."""
    trouves = set()
    for g in e.get("genres") or []:
        canon = _PAR_NORM.get(norm(str(g)))
        if canon:
            trouves.add(canon)
    if not trouves and e.get("famille") in FAMILLE_VERS_GENRE:
        trouves.add(FAMILLE_VERS_GENRE[e["famille"]])
    return [g for g in GENRES_CANON if g in trouves]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def clean(entry):
    """Valide une entrée ; renvoie None si elle est inutilisable."""
    try:
        e = {
            "id": str(entry["id"]).strip(),
            "type": entry["type"],
            "name": str(entry["name"]).strip(),
        }
    except (KeyError, TypeError):
        return None
    if not ID_RE.match(e["id"]) or e["type"] not in ("movie", "series") or not e["name"]:
        return None
    for k in ("description", "year", "famille", "date"):
        if entry.get(k):
            e[k] = entry[k]
    if isinstance(entry.get("genres"), list):
        e["genres"] = [str(g) for g in entry["genres"]]
    return e


def read_input(name):
    path = os.path.join(ENTREES, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    os.remove(path)
    out, seen = [], set()
    for entry in raw:
        e = clean(entry)
        if e and (e["id"], e["type"]) not in seen:
            seen.add((e["id"], e["type"]))
            out.append(e)
    return out


def dd_mm(iso):
    try:
        return datetime.date.fromisoformat(iso).strftime("%d/%m")
    except (TypeError, ValueError):
        return None


def meta(e, source):
    m = {
        "id": e["id"],
        "type": e["type"],
        "name": e["name"],
        "poster": f"https://images.metahub.space/poster/medium/{e['id']}/img",
        "posterShape": "poster",
        "background": f"https://images.metahub.space/background/medium/{e['id']}/img",
    }
    desc = e.get("description", "")
    d = dd_mm(e.get("date"))
    if source == "avenir" and d:
        desc = f"Sortie le {d}. {desc}".strip()
    elif source == "departs" and d:
        desc = f"Quitte la plateforme le {d}. {desc}".strip()
    if desc:
        m["description"] = desc
    if e.get("year"):
        m["releaseInfo"] = str(e["year"])
    g = genres_de(e)
    if g:
        m["genres"] = g
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args()
    today = datetime.date.fromisoformat(args.date)

    state = load_json(
        STATE,
        {"historique": [], "avenir": [], "departs": [], "suivies": [], "ids": {}, "hash": ""},
    )

    # 1. Historique glissant
    new = read_input("aujourdhui.json")
    if new is not None:
        for e in new:
            e["ajoute"] = today.isoformat()
        keys = {(e["id"], e["type"]) for e in new}
        old = [e for e in state["historique"] if (e["id"], e["type"]) not in keys]
        state["historique"] = new + old
    limit = (today - datetime.timedelta(days=KEEP_DAYS - 1)).isoformat()
    state["historique"] = [e for e in state["historique"] if e.get("ajoute", "") >= limit]
    state["historique"].sort(key=lambda e: e.get("ajoute", ""), reverse=True)  # stable

    # 2. Listes remplacées
    for fname, key in (("avenir.json", "avenir"), ("departs.json", "departs"), ("suivies.json", "suivies")):
        lst = read_input(fname)
        if lst is not None:
            state[key] = lst

    # 3. Cache des identifiants IMDb
    for key in ("historique", "avenir", "departs", "suivies"):
        for e in state[key]:
            state["ids"][f"{e['type']}|{norm(e['name'])}|{e.get('year', '')}"] = e["id"]

    # 4. Génération des catalogues
    shutil.rmtree(CATALOG, ignore_errors=True)
    shutil.rmtree(META, ignore_errors=True)
    blob = hashlib.sha256()
    counts = {}
    options_genre = {}  # id de catalogue -> genres présents (menu du filtre)
    for typ, cid, _name, source, has_genre in CATALOGS:
        items = [e for e in state[source] if e["type"] == typ][: CAPS[source]]
        metas = [meta(e, source) for e in items]
        counts[cid] = len(metas)
        base = os.path.join(CATALOG, typ, cid + ".json")
        save_json(base, {"metas": metas})
        blob.update(json.dumps(metas, sort_keys=True).encode())
        if has_genre:
            present = [g for g in GENRES_CANON if any(g in m.get("genres", []) for m in metas)]
            options_genre[cid] = present
            for opt in present:
                sub = [m for m in metas if opt in m.get("genres", [])]
                save_json(os.path.join(CATALOG, typ, cid, f"genre={opt}.json"), {"metas": sub})

    # 4b. Filmographies d'acteurs (données de acteurs.py) : un menu « acteur »
    acteurs = load_json(ACTEURS, {"acteurs": []}).get("acteurs", [])
    noms = [a["nom"] for a in acteurs]
    acteur_catalogs = []
    if noms:
        for typ, cid, cle in (("movie", "acteurs-films", "films"), ("series", "acteurs-series", "series")):
            par_acteur = {}
            for a in acteurs:
                lst = [e for e in (clean(x) for x in a.get(cle, [])) if e and e["type"] == typ]
                par_acteur[a["nom"]] = lst[: CAPS["historique"]]
            tous, vus = [], set()
            for lst in par_acteur.values():
                for e in lst:
                    if e["id"] not in vus:
                        vus.add(e["id"])
                        tous.append(e)
            tous.sort(key=lambda e: e.get("date", ""), reverse=True)
            metas = [meta(e, "acteurs") for e in tous[: CAPS["historique"]]]
            counts[cid] = len(metas)
            save_json(os.path.join(CATALOG, typ, cid + ".json"), {"metas": metas})
            blob.update(json.dumps(metas, sort_keys=True).encode())
            for nom, lst in par_acteur.items():
                sub = [meta(e, "acteurs") for e in lst]
                save_json(os.path.join(CATALOG, typ, cid, f"genre={nom}.json"), {"metas": sub})
                blob.update(json.dumps(sub, sort_keys=True).encode())
            acteur_catalogs.append({
                "type": typ,
                "id": cid,
                "name": "Filmographies (choisir un acteur)",
                "extra": [{"name": "genre", "options": noms, "isRequired": False}],
                "extraSupported": ["genre"],
            })

    # 4c. Fiches acteurs : un catalogue d'acteurs (photo = affiche) + une fiche détaillée par acteur
    if acteurs:
        fiches = []
        for a in acteurs:
            slug = norm(a["nom"]).replace(" ", "-")
            aid = "acteur:" + slug
            films = [e for e in (clean(x) for x in a.get("films", [])) if e and e["type"] == "movie"][:15]
            series = [e for e in (clean(x) for x in a.get("series", [])) if e and e["type"] == "series"][:8]
            lignes = []
            if a.get("naissance"):
                lignes.append("Né(e) le " + a["naissance"] + (" à " + a["lieu"] if a.get("lieu") else ""))
            if a.get("bio"):
                lignes.append(a["bio"])
            if films:
                lignes.append("Films : " + " · ".join(f"{e['name']} ({e.get('year', '?')})" for e in films))
            if series:
                lignes.append("Séries : " + " · ".join(f"{e['name']} ({e.get('year', '?')})" for e in series))
            liens = [{"name": e["name"], "category": "Filmographie", "url": f"stremio:///detail/{e['type']}/{e['id']}"}
                     for e in films + series]
            fiche = {
                "id": aid, "type": "movie", "name": a["nom"],
                "poster": a.get("photo"), "posterShape": "poster", "background": a.get("photo"),
                "description": "\n\n".join(lignes), "links": liens,
            }
            save_json(os.path.join(META, "movie", aid + ".json"), {"meta": fiche})
            blob.update(json.dumps(fiche, sort_keys=True).encode())
            fiches.append({k: fiche[k] for k in ("id", "type", "name", "poster", "posterShape")})
        counts["acteurs-fiches"] = len(fiches)
        save_json(os.path.join(CATALOG, "movie", "acteurs-fiches.json"), {"metas": fiches})
        acteur_catalogs.append({"type": "movie", "id": "acteurs-fiches", "name": "Acteurs (photos)"})

    # 5. Manifest (version incrémentée seulement si le contenu a changé)
    old_manifest = load_json(MANIFEST, {"version": "0.1.0"})
    version = old_manifest.get("version", "0.1.0")
    digest = blob.hexdigest()
    if digest != state.get("hash"):
        parts = version.split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        version = ".".join(parts)
    state["hash"] = digest

    catalogs = []
    for typ, cid, name, _source, has_genre in CATALOGS:
        c = {"type": typ, "id": cid, "name": name}
        if has_genre and options_genre.get(cid):
            c["extra"] = [{"name": "genre", "options": options_genre[cid], "isRequired": False}]
            c["extraSupported"] = ["genre"]
        catalogs.append(c)
    catalogs.extend(acteur_catalogs)
    manifest = {
        "id": "fr.auguste.sorties",
        "version": version,
        "name": "Sorties films et séries",
        "description": "Sorties de la semaine, titres à venir, départs des plateformes et séries suivies, en France.",
        "logo": f"{SITE}/logo.png",
        "resources": ["catalog", "meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt", "acteur:"],
        "catalogs": catalogs,
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }
    save_json(MANIFEST, manifest)
    save_json(STATE, state)

    # 6. Contrôle : tout doit se relire
    for root, _dirs, files in os.walk(CATALOG):
        for f in files:
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                json.load(fh)
    json.load(open(MANIFEST, encoding="utf-8"))
    print(json.dumps({"version": version, "catalogues": counts}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERREUR build.py : {exc}", file=sys.stderr)
        sys.exit(1)
