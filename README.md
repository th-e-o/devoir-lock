# Devoir du jour

Le téléphone de l'élève reste bloqué tant que le devoir de maths du jour n'est pas fait et validé.

```
 Téléphone (Android, mode kiosque)          Serveur maison (Python)                 Parent (Telegram)
 ─────────────────────────────────          ───────────────────────                 ─────────────────
 affiche l'énoncé (LaTeX)        ◄──────── génère le devoir avec Claude ◄────────  pilote le programme
 photographie le cahier          ────────► reçoit la copie              ────────►  ✅ / ❌ sur la photo
                                           15 min sans réponse → Claude corrige
 se débloque avec un jeton signé ◄──────── jeton ECDSA valable pour la journée
```

- **Devoir** : chaque jour, Claude génère un exercice de prépa (≈ 45 min, style concours,
  questions progressives) à partir du *programme* et de l'historique (il revient sur les
  notions ratées). Le corrigé n'est jamais envoyé au téléphone.
- **Supervision** : le parent parle à l'IA depuis Telegram (« on attaque les séries entières
  lundi, insiste sur le rayon de convergence ») ; l'IA réécrit le programme, peut consulter
  l'historique et régénérer le devoir du jour.
- **Validation** : la copie arrive sur Telegram avec le corrigé de référence et deux boutons.
  Sans réponse au bout de 15 min, Claude lit les photos et décide (note, retour à l'élève). Le parent
  peut toujours renverser la décision ensuite, et un refus peut être accompagné d'un message.
- **Blocage** : l'app est « propriétaire de l'appareil » (Device Owner). Tant que le devoir n'est pas
  validé, seuls l'app, le téléphone, l'appareil photo et les apps que vous autorisez s'ouvrent.
  Les appels d'urgence restent toujours possibles. Tout se rebloque à minuit.

## 1. Bot Telegram

1. Dans Telegram, ouvrez **@BotFather** → `/newbot` → notez le jeton.
2. Vous récupérerez votre identifiant de chat à l'étape 2 en envoyant `/start` au bot.

## 2. Serveur (Raspberry Pi, NAS, PC allumé en permanence ou petit VPS)

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # remplir ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, DEVICE_TOKEN
set -a; . ./.env; set +a
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- Envoyez `/start` au bot : il répond avec l'identifiant du chat. Mettez-le dans `PARENT_CHAT_IDS`
  (plusieurs parents : séparés par des virgules), puis redémarrez.
- Au démarrage, le serveur affiche la **clé publique** à copier dans l'app (aussi sur `GET /api/public-key`).
  La clé privée est créée dans `data/signing_key.pem` : sauvegardez-la, sinon il faudra recompiler l'app.
- Service permanent : `deploy/devoir.service` (systemd).
- Accès depuis l'extérieur de la maison (4G) : le plus simple est [Tailscale](https://tailscale.com) sur
  le serveur et le téléphone. Sinon, un reverse proxy HTTPS (Caddy). Les jetons de déblocage sont signés,
  donc un faux serveur ne peut pas débloquer le téléphone.
- Tests : `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`

Les deux premiers jours, utilisez `/programme` et la conversation avec l'IA pour décrire la progression
(chapitres faits, chapitre en cours, points faibles). Sinon, l'IA suit la progression habituelle de MPSI.

### Commandes Telegram

| Commande | Effet |
|---|---|
| `/statut` | état du jour |
| `/devoir`, `/corrige` | énoncé, corrigé |
| `/debloquer` | débloque pour la journée (malade, vacances…) |
| `/regenerer [consignes]` | remplace le devoir du jour (`/regenerer plus court, sur les intégrales`) |
| `/programme` | affiche le programme |
| `/nouvelle` | repart d'une conversation vierge avec l'IA |
| texte libre | conversation avec l'IA qui gère le programme |

## 3. Application Android

Il faut [Android Studio](https://developer.android.com/studio), ou au moins le SDK Android et `adb`.

```bash
cd android
cp devoir.properties.example devoir.properties   # URL du serveur, DEVICE_TOKEN, clé publique, code PIN parent
./gradlew assembleRelease
```

`EXTRA_ALLOWED_PACKAGES` liste les apps utilisables pendant le blocage, par exemple l'app SMS pour
écrire aux parents. Le téléphone et l'appareil photo sont toujours autorisés.

## 4. Installation sur le téléphone de l'élève

Le passage en *Device Owner* exige qu'**aucun compte** ne soit présent sur le téléphone au moment de
l'installation. Il n'est pas nécessaire de réinitialiser : supprimez temporairement le compte Google
dans Paramètres > Comptes, puis remettez-le après.

1. Activez *Options pour les développeurs* (7 appuis sur « Numéro de build »), puis *Débogage USB*.
2. Branchez le téléphone, puis lancez :
   ```bash
   cd android && ./scripts/installer.sh
   ```
3. Rajoutez le compte Google.
4. Écran parent : faites un **appui long sur le titre « Devoir du jour »** et entrez le code PIN. Il permet de :
   - débloquer 1 h ou jusqu'à minuit ;
   - interdire le débogage USB (à faire une fois que tout marche) ;
   - désactiver complètement le contrôle (avant de désinstaller l'app).

## Ce qui est bloqué, et les limites

Pendant le blocage :
- l'élève ne peut pas quitter l'app : mode kiosque, boutons Accueil et Applications récentes inactifs ;
- le mode sans échec, la réinitialisation d'usine, l'ajout d'utilisateurs et le changement d'heure
  sont interdits ;
- l'app ne peut pas être désinstallée, et ses données ne peuvent pas être effacées ;
- une fois l'option activée, le débogage USB est interdit.

Limites connues :
- **Appels d'urgence** : toujours possibles, et c'est voulu.
- **Hors ligne** : sans réseau, un téléphone bloqué reste bloqué. Un téléphone déjà débloqué le reste
  jusqu'à minuit. Le code PIN parent marche hors ligne.
- **Triche** : l'élève peut chercher la solution sur un autre appareil. La correction par l'IA rejette
  les écrans photographiés et les corrigés imprimés, mais pas un corrigé recopié à la main. Le regard du
  parent reste le meilleur garde-fou.
- **Panne du serveur ou de l'API** : le parent est prévenu sur Telegram. `/debloquer` et le code PIN
  restent disponibles.
- **Constructeurs** : les surcouches Xiaomi, Huawei et Samsung tuent parfois les alarmes en arrière-plan.
  Le rebloquage se fait alors au premier retour sur l'écran d'accueil plutôt qu'à minuit pile.

## Coûts

Le modèle utilisé est `claude-opus-5-5`, modifiable avec `CLAUDE_MODEL`. Chaque jour, il y a une génération,
plus une correction si le parent ne répond pas dans les 15 min. Ordre de grandeur : 0,20 à 0,60 $ par jour,
surtout en tokens de sortie (raisonnement et corrigé détaillé), plus la conversation de supervision.
C'est une estimation : vérifiez la consommation réelle dans la console Anthropic.
