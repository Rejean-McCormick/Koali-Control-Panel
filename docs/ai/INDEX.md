<!-- kOA Doc Server · Docs IA · lot 03 · projet : Koali-Control-Panel · proposition dérivée à valider -->
# Navigation IA — Koali-Control-Panel

**Objet constaté :** Console locale de développement supervisant les produits Koali, workspaces, builds et diagnostics délégués.

**Frontière de responsabilité :** Le dépôt kOA-Linux possède ses builds et l’admission hôte; Koali Spaces possède la composition des espaces; LevelUpDiag possède ses verdicts.

**Source :** `SmartDump_20261120_221507.zip` → `Koali-Control-Panel_Code_snapshot.zip` (20 novembre 2026). Le chemin Windows est une déclaration de snapshot, pas une vérification de l'état actuel.

**Statut :** Documentation de navigation dérivée de sources propriétaires, à revoir. Ce document ne vaut ni profil d'ingénierie mesuré, ni certification, ni permission d'exécution.

## Ordre de lecture — fichiers réellement présents dans la capture

1. [`README.md`](../../README.md) — SHA-256 `a8574ab6c728414c…` dans la capture.
2. [`docs/README.md`](../../docs/README.md) — SHA-256 `ee7b66916d7a018d…` dans la capture.
3. [`docs/BRANCH08_FINAL_READINESS.md`](../../docs/BRANCH08_FINAL_READINESS.md) — SHA-256 `6b9dcb574d5f5786…` dans la capture.
4. [`docs/backends-and-workspaces.md`](../../docs/backends-and-workspaces.md) — SHA-256 `8f7e01f31c282668…` dans la capture.
5. [`koali-control.json`](../../koali-control.json) — SHA-256 `d18381582323b8ef…` dans la capture.

## Parcours recommandé pour une intervention assistée par IA

1. Identifier la question, le sous-système concerné et les responsabilités du propriétaire.
2. Lire les documents ci-dessus **dans cet ordre**; consulter les contrats et consignes propriétaires plus récents si le checkout a évolué.
3. Sélectionner un profil de développement et son backend, exécuter les prévols documentés, déléguer les commandes au propriétaire, archiver les résultats sans les promouvoir.
4. Établir les vérifications effectivement disponibles dans la révision locale, consigner les résultats et ne conclure qu'à l'intérieur du périmètre couvert.
5. Proposer les changements; demander une décision explicite avant toute écriture à effet, déploiement, publication ou promotion de preuves.

## Risque propre à ce dépôt

La console ne peut ni forcer un worktree sale ni fabriquer une autorisation de publication ou d’activation.

## Compléments

- [`AI_CONTEXT.md`](AI_CONTEXT.md) : règles de travail et frontières de confiance.
- `AGENTS.md` : s'il existe déjà chez le propriétaire, ses consignes priment sur ces propositions; sinon l'overlay peut en ajouter un sans remplacement.
- `PROFIL_INGENIERIE.md` : optionnel, uniquement après exécution réelle de RepoSurveyor sur un dépôt local et accord explicite pour sa copie.
- Le Framework Kristal v11 est un standard documentaire **facultatif** : lire le profil réellement verrouillé, sans imposer de migration.
