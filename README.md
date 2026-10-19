# Twitch Notifier

Un « twitchdrops.app » personnalisable, utilisable par tous ceux qui ont le lien :

- **un site** (GitHub Pages) qui liste toutes les campagnes de drops en cours et à venir, groupées par jeu, avec recherche, tri et détail des récompenses ;
- **les jeux sont détectés automatiquement** : chaque nouveau jeu qui lance des drops apparaît tout seul (badge « NOUVEAU ») ;
- **chaque visiteur coche ses jeux** (★), stockés dans son propre navigateur (IndexedDB) : pas de compte, rien n'est envoyé ;
- **les jeux sans campagne en cours restent visibles** (« En pause ») et peuvent être mis en favori pour ne pas rater la prochaine. On peut même suivre un jeu par son nom avant qu'il ait jamais eu de drops ;
- **alertes publiques** : un sujet ntfy par jeu. Chaque visiteur s'abonne, depuis le panneau 🔔 Alertes, aux sujets de ses favoris ;
- **bandeau « depuis ta dernière visite »** listant les nouvelles campagnes de tes favoris ;
- **lien de transfert** pour retrouver ses favoris sur un autre appareil ;
- **alertes privées pour toi** (le propriétaire), relevé toutes les 30 min (GitHub Actions, gratuit, sans serveur) par **ntfy**, **Discord** et/ou **email** quand :
  - une campagne d'un jeu suivi **démarre** ;
  - une campagne d'un jeu suivi est **annoncée** ;
  - une campagne se **termine bientôt** (6 / 12 / 24 / 48 h avant, ou jamais) ;
  - **un nouveau jeu** propose des drops (tous jeux confondus).
- **si ça casse, tu es prévenu** (jeton expiré, API Twitch modifiée) au lieu d'un silence radio, et le site affiche un bandeau d'erreur.

Aucune dépendance : Python 3.10+ standard, une page HTML unique.

---

## Installation (≈ 15 min)

### 1. Créer le dépôt

Dépôt : `badjams/twitchnotifier` — site : https://badjams.github.io/twitchnotifier/

> **Public ou privé ?** En public, Actions et Pages sont gratuits et illimités. En privé, Pages demande un abonnement GitHub Pro et Actions est limité à 2 000 min/mois (ce projet en consomme ~1 500). Le contenu public se limite aux campagnes Twitch et à ta liste de jeux suivis ; aucun secret n'est dans le dépôt.

### 2. Jeton Twitch

Sur ton PC :

```bash
python scripts/login.py
```

Va sur l'adresse affichée, entre le code, valide avec ton compte Twitch. Copie le jeton affiché dans **Settings → Secrets and variables → Actions → New repository secret** : `TWITCH_TOKEN`.

Aucun mot de passe ne transite par le script. Le jeton donne accès à ton compte Twitch : garde-le uniquement dans ce secret.

### 3. Canaux d'alerte (mets ceux que tu veux)

| Canal | Secrets | Où les trouver |
|---|---|---|
| **ntfy** | `NTFY_TOPIC` (+ `NTFY_SERVER`, `NTFY_TOKEN` si serveur perso) | Choisis un nom de sujet long et difficile à deviner (ex. `drops-k7f2q9x`), abonne-toi dans l'appli ntfy. Par défaut : ntfy.sh. |
| **Discord** | `DISCORD_WEBHOOK` | Salon → Modifier → Intégrations → Webhooks → Nouveau → Copier l'URL |
| **Email** | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_TO` (+ `MAIL_FROM`) | Gmail : `smtp.gmail.com`, port `587`, un **mot de passe d'application** (pas ton vrai mot de passe). Port `465` = SSL. |

### 4. Alertes publiques pour les visiteurs (optionnel)

Déjà activé dans `docs/config.json` avec le préfixe `twitchnotifier-dz7n7k` (change-le si tu veux, ou vide-le pour désactiver) :

```json
"public": { "ntfy_prefix": "twitchnotifier-dz7n7k", "ntfy_server": "https://ntfy.sh", "announced": true }
```

Chaque jeu aura son sujet (`twitchnotifier-dz7n7k-rust`, `twitchnotifier-dz7n7k-destiny-2`…), plus `twitchnotifier-dz7n7k-nouveaux-jeux`. Le relevé y publie chaque annonce et chaque démarrage de campagne, pour **tous** les jeux ; les visiteurs s'abonnent à ceux qui les intéressent. Laisse le préfixe vide pour désactiver.

> **Limites de ntfy.sh** : le service gratuit limite le nombre de messages par jour pour les envois anonymes, et toutes les publications partent de GitHub. Avec beaucoup de jeux, crée un compte ntfy.sh (secret `NTFY_TOKEN`) ou héberge ton propre serveur ntfy (`ntfy_server`).

Les sujets ntfy publics sont lisibles par quiconque connaît leur nom : n'y publie rien de privé (ici, uniquement des infos de campagnes Twitch, déjà publiques).

### 5. Activer le site

**Settings → Pages → Build and deployment → Deploy from a branch → `main` / `/docs`**.
Ton tableau de bord sera sur `https://<ton-pseudo>.github.io/<dépôt>/`.

### 6. Premier relevé

**Actions → Relevé des drops → Run workflow**. Coche « Envoyer une alerte de test » une première fois pour vérifier tes canaux, puis relance sans la cocher.

Le premier vrai relevé recense tous les jeux sans t'alerter pour chacun (juste un message « Surveillance active »).

### 7. Tes alertes privées (propriétaire)

Les ★ de chaque visiteur restent dans son navigateur. Pour que **tes** ★ déclenchent tes alertes privées (Discord, email, ntfy perso), envoie-les dans le dépôt :

1. GitHub → **Settings → Developer settings → Fine-grained tokens → Generate new token**
2. *Repository access* : **uniquement ce dépôt** · *Permissions* : **Contents → Read and write** · expiration au choix
3. Sur ton site → ⚙︎ → **Propriétaire du site** → colle le jeton → **Enregistrer dans le dépôt**

Ça écrit tes favoris et tes réglages d'alerte dans `docs/config.json` et relance un relevé. Le jeton reste dans ton navigateur et n'est envoyé qu'à api.github.com. « Reprendre les jeux du dépôt » fait l'inverse, pratique sur un nouvel appareil.

Les visiteurs voient ce panneau mais ne peuvent rien en faire sans jeton d'écriture sur ton dépôt.

---

## Quand ça casse

**« Jeton Twitch refusé »** → relance `python scripts/login.py` et remplace le secret `TWITCH_TOKEN`.

**« Hash périmé » / « Twitch a changé son API »** → Twitch renouvelle parfois les identifiants de ses requêtes. Récupère les nouveaux dans [`constants.py` de TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner/blob/master/constants.py) (entrées `ViewerDropsDashboard` et `DropCampaignDetails`) et colle-les dans `docs/config.json`, sans toucher au code :

```json
"api": {
  "hashes": {
    "ViewerDropsDashboard": "nouveau_hash",
    "DropCampaignDetails": "nouveau_hash"
  }
}
```

Tu reçois au plus une alerte d'erreur toutes les 12 h.

**Plus aucun relevé ?** GitHub désactive les tâches planifiées d'un dépôt sans activité depuis 60 jours ; ici chaque relevé fait un commit, donc ça ne devrait pas arriver. Sinon : onglet Actions → réactiver.

---

## Bon à savoir

- Les données viennent de l'**API interne de Twitch** (la même que l'appli Android et TwitchDropsMiner), en lecture seule, deux requêtes groupées toutes les 30 min. Ce n'est pas une API officielle : Twitch peut la modifier, et son usage automatisé est toléré mais pas garanti par les conditions d'utilisation.
- Le rythme de 30 min est un compromis ; GitHub peut retarder les tâches planifiées de quelques minutes aux heures chargées.
- Pour l'héberger chez toi plutôt que sur GitHub : `python scripts/check_drops.py` dans un cron + n'importe quel serveur web statique sur `docs/`.

## Tester en local

```bash
# Simulation complète sans réseau ni envoi
python scripts/check_drops.py --fixture tests/sample_campaigns.json --dry-run

# Vrai relevé, sans envoyer d'alerte
TWITCH_TOKEN=xxx python scripts/check_drops.py --dry-run

# Le site
cd docs && python -m http.server 8000   # puis http://localhost:8000
```

## Fichiers

```
docs/index.html          le site
docs/config.json         tes jeux suivis, tes réglages, le préfixe des sujets publics
docs/data/*.json         campagnes, catalogue des jeux (y compris en pause), état du relevé
state/state.json         alertes déjà envoyées (anti-doublon)
scripts/check_drops.py   relevé + logique d'alertes
scripts/twitch.py        accès à l'API Twitch
scripts/notify.py        ntfy, Discord, email
scripts/login.py         connexion Twitch par code
.github/workflows/drops.yml
```
