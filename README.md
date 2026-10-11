# Sorties films et séries

Addon de catalogue statique (Stremio / Nuvio), publié par GitHub Pages :
https://auguste76.github.io/sorties/manifest.json

Catalogues : Sorties de la semaine (7 jours glissants), À venir cette semaine,
Part bientôt, Mes séries suivies. Filtre par genre sur les deux premiers.

Les fichiers de `catalog/` et `manifest.json` sont générés par `build.py`
(voir l'en-tête du script). Ne pas les modifier à la main.

## Acteurs

`acteurs.py` récupère photos et filmographies (TMDB en français, photo de
secours Wikidata) pour les noms de `data/acteurs_liste.json`, puis écrit
`data/acteurs.json` et la page `acteurs/index.html`. `build.py` en tire les
catalogues « Filmographies (choisir un acteur) ». Le workflow
`.github/workflows/acteurs.yml` le fait chaque lundi ; il demande le secret
`TMDB_API_KEY` (Settings > Secrets and variables > Actions).
