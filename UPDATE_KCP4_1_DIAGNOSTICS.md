# Koali Control Panel 4.1 — Diagnostics et réactivité

Cette livraison modifie uniquement **Koali-Control-Panel**. Elle s'appuie sur
LevelUpDiag-Koali 2.6 (N13) et sur l'ajout du store dans koa-linux livrés séparément.

## Diagnostics

L'onglet Diagnostics propose **Koali System** et **Store / N13**. Ces boutons
lancent les campagnes `koali-system` et `store` ; ils n'installent pas les logiciels.
Le moteur LevelUpDiag conserve les verdicts et les preuves de couverture partielle.

| Campagnes | Exécution par défaut | Cible |
|---|---|---|
| Koali System / Store, N12 / N13 | Python Windows sur Windows ; Linux natif sur Linux | Configuration de campagne de LevelUpDiag (`campaign_targets`, `koali_system.root`, `store.root`) |
| Noyau, validation, release, N10 | Backend du workspace actif | Chemin du workspace transmis explicitement avec `--target` |

Le Control Panel ne se connecte pas automatiquement à une VM. Le diagnostic du
store lancé sous Windows contrôle les sources ; les contrôles Linux s'exécutent
**dans la session graphique de la VM**, conformément à `docs/STORE_TESTS.md` de
LevelUpDiag. Ne pas confondre WSL et la VM cible.

Configuration facultative dans `diagnostics.levelupdiag` :

```json
{
  "roots": {
    "windows": "C:\\mycode\\kOA-Linux\\LevelUpDiag-Koali",
    "native_linux": "/home/USER/work/LevelUpDiag-Koali",
    "wsl": "/home/USER/work/LevelUpDiag-Koali"
  },
  "campaign_backends": {
    "koali-system": "native",
    "koali-system-debug": "native",
    "store": "native",
    "N12": "native",
    "N13": "native"
  }
}
```

Adapter les chemins à la machine. Si `roots` ne contient pas le backend demandé,
le chemin `root` historique reste utilisé. `native` signifie l'hôte courant ; les
valeurs explicites supportées sont `windows`, `native_linux`, `wsl`.
Les configurations existantes reçoivent les nouveaux noms de campagne au chargement.
Les paramètres des campagnes et chemins de produits restent gérés par LevelUpDiag.

L'adaptateur emploie des commandes PowerShell littérales sous Windows et Bash sous
Linux/WSL. Le marqueur du rapport utilise `load_config(tool_root, target)` et la
même sélection de cible que LevelUpDiag. Les détails sont lus dans `runs/<run_id>`
et acceptés uniquement pour le même run, au lieu d'être mélangés depuis `latest`.
Les résultats d'une autre campagne ne sont pas affichés comme ceux de l'action.

## Réactivité et processus

- Un seul rafraîchissement d'état général et un seul rafraîchissement produit
  peuvent être en cours ; les verrous sont libérés même après erreur.
- La boucle d'événements traite au plus 200 éléments / 20 ms avant de rendre
  la main à Tk. Les écritures de logs sont groupées.
- L'affichage conserve environ 5 000 lignes. Le fichier est renouvelé lorsqu'il
  dépasse 5 Mio, avec une sauvegarde `.log.1`.
- Les nouvelles commandes longues utilisent un groupe/session de processus.
  Sous Linux natif, Stop et timeout terminent ce groupe. Sous Windows, la
  terminaison utilise `taskkill /T /F`. L'arrêt de commandes est demandé sans
  bloquer le thread graphique.
- Limite WSL : arrêter l'arbre Windows de `wsl.exe` ne constitue pas une preuve
  d'arrêt de tous les services Linux détachés. Les commandes d'arrêt propres aux
  services restent nécessaires ; aucune distribution WSL n'est arrêtée globalement.

## Installation

Fermer le Control Panel. Sauvegarder le dépôt et `koali-control.json`, puis reporter
les fichiers modifiés listés dans `CHANGES_KCP4_1.json` dans le dépôt existant.
Conserver les chemins et réglages locaux du JSON ; les nouveaux noms de campagne
sont ajoutés automatiquement par le chargement de configuration. L'archive est un
snapshot complet, pas un script qui fusionne automatiquement les réglages locaux.

Relancer `LAUNCH.cmd`. Vérifier Koali System et Store / N13 avec LevelUpDiag 2.6,
puis une campagne du noyau sur le workspace habituel.

## Validation

```sh
python -m unittest discover -s tests -q
```

Consulter `VALIDATION_KCP4_1.txt` pour les résultats effectivement exécutés.
La session graphique Windows, PowerShell, WSL, le cycle QEMU et les campagnes
LevelUpDiag complètes restent à vérifier sur les machines cibles. Les tests du
résolveur de rapport utilisent une configuration simulée explicitement limitée
au contrat de lecture ; ils ne constituent pas un test de l'ensemble de LevelUpDiag.
