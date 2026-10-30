# Devoir du jour

Le téléphone de l'élève (iPhone) reste bloqué tant que le devoir de maths du jour n'est pas fait et validé.

```
 iPhone (Screen Time)                       Serveur maison (Python)                 Parent (Telegram)
 ────────────────────                       ───────────────────────                 ─────────────────
 affiche l'énoncé (LaTeX)        ◄──────── génère le devoir avec Claude ◄────────  pilote le programme
 photographie le cahier          ────────► reçoit la copie              ────────►  ✅ / ❌ sur la photo
                                           15 min sans réponse → Claude corrige
 lève le bouclier (jeton signé)  ◄──────── jeton ECDSA valable pour la journée
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
- **Blocage** : via l'API Screen Time d'Apple (FamilyControls / ManagedSettings). Toutes les apps
  affichent un écran « Devoir du jour à faire », sauf celles que le parent autorise. Les appels
  d'urgence restent toujours possibles. Une extension système remet le blocage à minuit,
  même si l'app est fermée.

## ⚠️ Avant de commencer : trois conditions côté Apple

1. **Un Mac avec Xcode**, indispensable pour compiler une app iPhone.
2. **Un compte Apple Developer** (99 €/an). L'API Screen Time (*Family Controls*) n'est
   probablement pas accessible avec un compte gratuit. Pour un usage familial, une installation
   directe depuis Xcode suffit. Une distribution par TestFlight ou l'App Store exige en plus
   de demander à Apple l'autorisation « Family Controls (Distribution) ».
3. **Le compte Apple de l'élève doit être un compte *enfant* de votre Partage familial**
   (mode `child`, recommandé). Dans ce mode, l'élève ne peut ni retirer l'autorisation ni
   supprimer l'app sans l'accord du parent. **Un élève de 18 ans ou plus ne peut plus être un
   compte enfant.** Il reste alors le mode `individual`, où l'élève donne lui-même
   l'autorisation et peut la retirer dans Réglages > Temps d'écran : le blocage devient un
   engagement moral plus qu'une contrainte. Un code « Temps d'écran » défini par le parent sur
   l'iPhone peut compliquer ce contournement, mais je ne peux pas garantir qu'il protège ce
   réglage. À tester.

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
  le serveur et l'iPhone. Sinon, un reverse proxy HTTPS (Caddy). Les jetons de déblocage sont signés,
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

## 3. Application iPhone

Sur le Mac :

```bash
brew install xcodegen
cd ios
cp Config.xcconfig.example Config.xcconfig   # équipe Apple, identifiants, URL du serveur, DEVICE_TOKEN, clé publique, code parent
xcodegen generate
open DevoirDuJour.xcodeproj
```

Dans Xcode, vérifiez que la *capability* **Family Controls** et l'**App Group** sont actifs sur les trois
cibles : l'app, l'extension `DevoirMonitor` qui rebloque à minuit, et l'extension `DevoirShield` qui
affiche l'écran de blocage. Branchez l'iPhone de l'élève, puis lancez l'app (▶︎).

## 4. Mise en route sur l'iPhone

1. Ouvrez l'app et appuyez sur **Autoriser**. En mode `child`, le parent approuve avec son identifiant Apple.
2. Faites un **appui long sur le titre « Devoir du jour »** et entrez le code parent.
3. **Choisissez les apps autorisées** pendant le blocage. Sélectionnez-les une par une et incluez
   **Devoir du jour**, Téléphone et Messages. Tant que cette liste est vide, rien n'est bloqué : c'est
   une sécurité pour que l'app ne se bloque pas elle-même.
4. L'écran parent permet aussi de débloquer 1 h ou jusqu'à minuit, et de tout désactiver avant de supprimer l'app.

## Ce qui est bloqué, et les limites

- Pendant le blocage, toutes les apps et tous les sites sont recouverts par l'écran
  « Devoir du jour à faire », sauf les apps autorisées.
- L'app ne peut pas être supprimée et la date et l'heure restent automatiques, même quand le
  téléphone est débloqué.
- Il n'y a **pas de mode kiosque** comme sur Android : l'élève garde l'écran d'accueil, le centre de
  contrôle, etc. Seules les apps sont bloquées.
- **Le déblocage n'a lieu qu'à l'ouverture de l'app.** iOS ne laisse pas une app tourner en arrière-plan
  pour interroger le serveur. Après l'envoi de sa copie, l'élève rouvre « Devoir du jour » pour voir
  la décision. Si le parent annule une validation, le rebloquage se fait aussi à la prochaine ouverture,
  ou à minuit au plus tard. Des notifications push pourraient l'automatiser, mais demanderaient de
  configurer APNs.
- **Hors ligne** : un iPhone bloqué reste bloqué. Un iPhone déjà débloqué le reste jusqu'à minuit.
  Le code parent marche hors ligne.
- **Triche** : l'élève peut chercher la solution sur un autre appareil. La correction par l'IA rejette
  les écrans photographiés et les corrigés imprimés, mais pas un corrigé recopié à la main. Le regard du
  parent reste le meilleur garde-fou.
- **Panne du serveur ou de l'API** : le parent est prévenu sur Telegram. `/debloquer` et le code parent
  restent disponibles.

## Coûts

Le modèle utilisé est `claude-opus-5-5`, modifiable avec `CLAUDE_MODEL`. Chaque jour, il y a une génération,
plus une correction si le parent ne répond pas dans les 15 min. Ordre de grandeur : 0,20 à 0,60 $ par jour,
surtout en tokens de sortie (raisonnement et corrigé détaillé), plus la conversation de supervision.
C'est une estimation : vérifiez la consommation réelle dans la console Anthropic.
