# Déploiement sur un VPS OVH avec votre nom de domaine

On suppose un VPS sous **Debian 12 ou Ubuntu 24.04**, servi à l'adresse `devoir.deltastat.fr`.

## 1. DNS

Chez le gestionnaire de votre domaine (espace client OVH > Noms de domaine > Zone DNS, si le domaine
est chez OVH), ajoutez un enregistrement :

| Type | Sous-domaine | Cible |
|---|---|---|
| A | `devoir` | adresse IPv4 du VPS |

La propagation prend de quelques minutes à quelques heures. Pour vérifier : `dig +short devoir.deltastat.fr`.

## 2. Sécuriser le VPS

```bash
# depuis votre ordinateur : copier votre clé SSH puis se connecter
ssh-copy-id debian@IP_DU_VPS
ssh debian@IP_DU_VPS

sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw unattended-upgrades python3-venv git curl debian-keyring debian-archive-keyring apt-transport-https

# pare-feu : SSH + HTTP (certificat) + HTTPS uniquement
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable

# connexion SSH par clé uniquement
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

## 3. Installer le serveur

### 3a. Utilisateur dédié et clé de déploiement GitHub

Le dépôt est privé : le VPS a besoin d'une clé de lecture seule (*deploy key*).

```bash
sudo useradd --system --create-home --home-dir /home/devoir --shell /usr/sbin/nologin devoir
sudo -u devoir mkdir -p -m 700 /home/devoir/.ssh
sudo -u devoir ssh-keygen -t ed25519 -N "" -C "vps-devoir" -f /home/devoir/.ssh/id_ed25519
sudo cat /home/devoir/.ssh/id_ed25519.pub
```

Copiez la ligne affichée (`ssh-ed25519 AAAA… vps-devoir`), puis sur GitHub : dépôt `devoir-lock` >
**Settings > Deploy keys > Add deploy key**. Titre « VPS OVH », collez la clé, et laissez
*Allow write access* **décoché**.

### 3b. Récupérer le code et installer Python

```bash
sudo install -d -o devoir -g devoir /opt/devoir-lock
sudo -u devoir env GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" \
    git clone git@github.com:th-e-o/devoir-lock.git /opt/devoir-lock
cd /opt/devoir-lock/server
sudo -u devoir python3 -m venv .venv
sudo -u devoir .venv/bin/pip install -r requirements.txt
```

### 3c. Configuration et démarrage

```bash
sudo -u devoir cp .env.example .env
sudo chmod 600 .env
sudo nano .env
```

Dans `nano`, remplissez `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN` et `DEVICE_TOKEN`, et laissez
`DATA_DIR=./data`. Pour enregistrer : Ctrl+O, Entrée, puis Ctrl+X pour quitter.

```bash
sudo cp deploy/devoir.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now devoir
journalctl -u devoir -f      # vérifier le démarrage (Ctrl+C pour quitter)
```

## 4. HTTPS avec Caddy

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

sudo cp /opt/devoir-lock/server/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Vérification depuis n'importe où : `https://devoir.deltastat.fr/health` doit afficher `{"ok":true}`.

## 5. Finaliser

- Envoyez `/start` au bot Telegram, mettez l'identifiant obtenu dans `PROF_CHAT_IDS` (`.env`),
  puis `sudo systemctl restart devoir`.
- L’adresse `https://devoir.deltastat.fr` est déjà renseignée dans le README (page élève et Raccourci).

## 6. Mises à jour automatiques

Toutes les 5 minutes, le VPS regarde s'il y a un nouveau commit sur `main`. S'il y en a un, il lance
les tests dans une copie à part, installe la nouvelle version seulement s'ils passent, vérifie que le
serveur répond, et vous prévient sur Telegram. Si le serveur ne répond plus, il revient à la version
précédente.

```bash
sudo cp /opt/devoir-lock/server/deploy/devoir-update.service /opt/devoir-lock/server/deploy/devoir-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now devoir-update.timer
```

Pour vérifier : `systemctl list-timers devoir-update.timer` (prochain passage) et
`sudo journalctl -u devoir-update -n 30` (dernières exécutions).

## Entretien

- **Mettre à jour à la main** (inutile avec l'étape 6) :
  `cd /opt/devoir-lock && sudo -u devoir git pull && sudo systemctl restart devoir`
- **Sauvegarder** `/opt/devoir-lock/server/data`. Ce dossier contient la base (programme, historique) et
  les photos des copies.
- **Logs** : `journalctl -u devoir --since today`
