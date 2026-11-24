# Consignes agents — Koali-Control-Panel

> Proposition rédigée à partir du SmartDump du 20 novembre 2026. Ce fichier ne remplace pas une décision, un contrat ni une politique du propriétaire. En cas de consigne plus récente, **la consigne propriétaire prévaut**.

- Commencer par [`docs/ai/INDEX.md`](docs/ai/INDEX.md), puis [`docs/ai/AI_CONTEXT.md`](docs/ai/AI_CONTEXT.md).
- Domaine : Console locale de développement supervisant les produits Koali, workspaces, builds et diagnostics délégués.
- Autorité : Le dépôt kOA-Linux possède ses builds et l’admission hôte; Koali Spaces possède la composition des espaces; LevelUpDiag possède ses verdicts.
- Précaution principale : La console ne peut ni forcer un worktree sale ni fabriquer une autorisation de publication ou d’activation.
- Avant de modifier, relire les sources propriétaire listées dans l'index, les éventuels locks et les tests du dépôt actif.
- Ne lancer ni effet distant, ni déploiement, ni publication, ni migration, ni promotion de preuves sans approbation explicite.
- Les rapports RepoSurveyor sont des **profils d'ingénierie**, pas des certificats de maturité. Ne pas les inventer.
- Le Framework Kristal v11 reste facultatif et non bloquant, sauf contrat adopté explicitement par ce propriétaire.
