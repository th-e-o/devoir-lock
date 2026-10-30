# Devoir du jour

L'iPhone de l'élève refuse d'ouvrir les apps de distraction tant que le devoir de maths du jour n'est
pas fait et validé. Tout est gratuit côté Apple : il n'y a pas d'app à installer, seulement une page web
et l'app **Raccourcis** intégrée à l'iPhone.

```
 iPhone                                     Serveur maison (Python)                 Parent (Telegram)
 ──────                                     ───────────────────────                 ─────────────────
 page web : énoncé (LaTeX)        ◄──────── génère le devoir avec Claude ◄────────  pilote le programme
 photo du cahier                  ────────► reçoit la copie              ────────►  ✅ / ❌ sur la photo
                                            15 min sans réponse → Claude corrige
 Raccourci à l'ouverture          ────────► « bloque » ou « libre »
 d'Instagram, YouTube… : si « bloque », retour à la page du devoir
```

- **Devoir** : chaque jour, Claude génère un exercice de prépa (≈ 45 min, style concours,
  questions progressives) à partir du *programme* et de l'historique (il revient sur les
  notions ratées). Le corrigé n'est jamais montré à l'élève.
- **Supervision** : le parent parle à l'IA depuis Telegram (« on attaque les séries entières
  lundi, insiste sur le rayon de convergence ») ; l'IA réécrit le programme, peut consulter
  l'historique et régénérer le devoir du jour.
- **Validation** : la copie arrive sur Telegram avec le corrigé de référence et deux boutons.
  Sans réponse au bout de 15 min, Claude lit les photos et décide (note, retour à l'élève). Le parent
  peut toujours renverser la décision ensuite, et un refus peut être accompagné d'un message.
- **Blocage** : une automatisation de l'app Raccourcis se déclenche à l'ouverture des apps choisies
  et interroge le serveur. Tant que le devoir n'est pas validé, elle renvoie aussitôt vers la page du
  devoir. Une fois le devoir validé, plus rien ne se passe jusqu'à minuit. **Aucune action
  quotidienne n'est nécessaire** : le code Temps d'écran du parent se règle une seule fois et sert
  uniquement à empêcher de toucher à la configuration.

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
- Service permanent : `deploy/devoir.service` (systemd).
- **Le serveur doit être joignable en HTTPS depuis Internet**, pas seulement depuis le wifi de la maison.
  Sinon, l'élève n'aurait qu'à couper le wifi ou le VPN pour que le Raccourci échoue. Deux solutions gratuites :
  [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) (`tailscale funnel 8000`, qui donne une adresse
  `https://machine.nom.ts.net`) ou [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).
  Tout est protégé par le code d'accès `DEVICE_TOKEN` : prenez une valeur longue et aléatoire.
- Tests : `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`

Les deux premiers jours, utilisez `/programme` et la conversation avec l'IA pour décrire la progression
(chapitres faits, chapitre en cours, points faibles). Sinon, l'IA suit la progression habituelle de MPSI.

## 3. Page de l'élève

Sur l'iPhone, ouvrez dans Safari `https://VOTRE-SERVEUR/eleve#k=CODE`, où `CODE` est la valeur de
`DEVICE_TOKEN`. Puis touchez Partager > **Sur l'écran d'accueil** : la page s'ouvre ensuite comme une app.

La page affiche l'énoncé et la réponse du parent ou de l'IA. Le bouton photo ouvre directement l'appareil
photo, sans accès à la photothèque, et les photos sont réduites avant l'envoi.

## 4. Le Raccourci qui bloque

### 4a. Créer le raccourci « Devoir »
App **Raccourcis** > onglet *Raccourcis* > **+**, nommez-le « Devoir » et ajoutez dans l'ordre :

1. **Obtenir le contenu de l'URL** : `https://VOTRE-SERVEUR/api/statut?k=CODE`
2. **Si** : *Contenu de l'URL* **est** `bloque`
3. Dans le « Si » :
   - **Afficher la notification** : « Devoir du jour à faire » (facultatif) ;
   - **Ouvrir les URL** : `https://VOTRE-SERVEUR/eleve#k=CODE`
4. Laissez le « Sinon » vide.

### 4b. Créer l'automatisation
Onglet *Automatisation* > **Nouvelle automatisation** > **App** :
- choisissez les apps à bloquer (Instagram, TikTok, YouTube, Snapchat, jeux, Netflix…). **N'ajoutez
  pas Safari**, car la page du devoir s'y ouvre ;
- cochez **Est ouverte** et **Exécuter immédiatement** ;
- action : **Exécuter le raccourci** > « Devoir ».

Vérifiez : ouvrez Instagram, vous devez être renvoyé sur la page du devoir. Ensuite, `/debloquer` sur
Telegram, puis rouvrez Instagram : il doit s'ouvrir normalement.

### 4c. Verrouiller la configuration (parent, une seule fois)
Sur l'iPhone de l'élève, dans **Réglages > Temps d'écran** :
1. **Verrouiller les réglages de Temps d'écran** : choisissez un code que vous seul connaissez.
   Vous ne le changerez jamais et il n'est pas demandé au quotidien.
2. **Limites d'app** > Ajouter une limite > *Raccourcis* > **1 min par jour**, avec
   **Bloquer à la fin de la limite** : l'élève ne peut plus ouvrir Raccourcis pour supprimer l'automatisation.
3. **Contenu et confidentialité** (activer) > Achats sur l'iTunes Store et l'App Store >
   **Suppression d'apps : Refuser**.
4. Facultatif : **Installation d'apps : Refuser**, pour qu'une nouvelle app ne contourne pas la liste.
   Il faudra alors votre code pour chaque installation.

**À tester une fois tout en place**, car je n'ai pas pu le vérifier moi-même :
- l'automatisation fonctionne-t-elle encore quand l'app Raccourcis a atteint sa limite ?
- l'élève ne peut-il vraiment plus la supprimer ni la désactiver ?

## Commandes Telegram

| Commande | Effet |
|---|---|
| `/statut` | état du jour |
| `/devoir`, `/corrige` | énoncé, corrigé |
| `/debloquer` | débloque pour la journée (malade, vacances…) |
| `/regenerer [consignes]` | remplace le devoir du jour (`/regenerer plus court, sur les intégrales`) |
| `/programme` | affiche le programme |
| `/nouvelle` | repart d'une conversation vierge avec l'IA |
| texte libre | conversation avec l'IA qui gère le programme |

## Limites (à connaître, et à discuter avec l'élève)

C'est un dispositif d'**engagement**. Il est assez pénible à contourner pour ne pas l'être sur un coup
de tête, mais il n'est pas inviolable :
- **Sites web** : Safari n'est pas bloqué, donc instagram.com ou youtube.com restent accessibles par le navigateur.
- **Réseau** : si le serveur ne répond pas (panne, pas de réseau du tout), le Raccourci échoue et l'app
  s'ouvre. C'est pourquoi le serveur doit être joignable depuis Internet (voir l'étape 2).
- **Retour à l'app** : un retour vers une app déjà ouverte, via le sélecteur d'apps, ne déclenche pas
  toujours l'automatisation.
- **Restauration** : un effacement complet de l'iPhone par ordinateur supprime tout.
- **Triche** : l'élève peut chercher la solution sur un autre appareil. La correction par l'IA rejette
  les écrans photographiés et les corrigés imprimés, mais pas un corrigé recopié à la main.

## Coûts

- Apple, Telegram, Tailscale et Cloudflare : gratuits.
- Serveur : une machine déjà allumée, ou un Raspberry Pi (environ 60 €, une fois).
- API Claude : `claude-opus-5-5` par défaut (`CLAUDE_MODEL`). Comptez de l'ordre de 0,20 à 0,60 $ par jour
  (estimation, à vérifier dans la console Anthropic). Avec `claude-sonnet-5-5`, c'est environ deux fois moins cher.
