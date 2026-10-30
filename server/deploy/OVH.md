# Déploiement sur un VPS OVH avec votre nom de domaine

On suppose un VPS sous **Debian 12 ou Ubuntu 24.04** et un sous-domaine, par exemple
`devoir.mondomaine.fr`. Remplacez-le partout ci-dessous.

## 1. DNS

Chez le gestionnaire de votre domaine (espace client OVH > Noms de domaine > Zone DNS, si le domaine
est chez OVH), ajoutez un enregistrement :

| Type | Sous-domaine | Cible |
|---|---|---|
| A | `devoir` | adresse IPv4 du VPS |

La propagation prend de quelques minutes à quelques heures. Pour vérifier : `dig +short devoir.mondomaine.fr`.

## 2. Sécuriser le VPS

```bash
# depuis votre ordinateur : copier votre clé SSH puis se connecter
ssh-copy-id debian@IP_DU_VPS
ssh debian@IP_DU_VPS

sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw unattended-upgrades python3-venv git debian-keyring debian-archive-keyring apt-transport-https curl

# pare-feu : SSH + HTTP (certificat) + HTTPS uniquement
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable

# connexion SSH par clé uniquement
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

## 3. Installer le serveur

```bash
sudo useradd --system --create-home --home-dir /opt/devoir-lock --shell /usr/sbin/nologin devoir
sudo -u devoir git clone https://github.com/VOTRE-COMPTE/devoir-lock.git /opt/devoir-lock
cd /opt/devoir-lock/server
sudo -u devoir python3 -m venv .venv
sudo -u devoir .venv/bin/pip install -r requirements.txt

sudo -u devoir cp .env.example .env
sudo -u devoir nano .env     # ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, DEVICE_TOKEN, DATA_DIR=/opt/devoir-lock/data
sudo chmod 600 .env

sudo cp deploy/devoir.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now devoir
journalctl -u devoir -f      # vérifier le démarrage (Ctrl+C pour quitter)
```

Le dépôt GitHub est privé : pour `git clone`, créez une *deploy key* en lecture seule
(GitHub > dépôt > Settings > Deploy keys), ou copiez le dossier avec `scp`.

## 4. HTTPS avec Caddy

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

sudo cp /opt/devoir-lock/server/deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/devoir.example.fr/devoir.mondomaine.fr/' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Vérification depuis n'importe où : `https://devoir.mondomaine.fr/health` doit afficher `{"ok":true}`.

## 5. Finaliser

- Envoyez `/start` au bot Telegram, mettez l'identifiant obtenu dans `PARENT_CHAT_IDS` (`.env`),
  puis `sudo systemctl restart devoir`.
- Utilisez `https://devoir.mondomaine.fr` comme `VOTRE-SERVEUR` dans le README (page élève et Raccourci).

## Entretien

- **Mettre à jour** :
  `cd /opt/devoir-lock && sudo -u devoir git pull && sudo systemctl restart devoir`
- **Sauvegarder** `/opt/devoir-lock/data`. Ce dossier contient la base (programme, historique) et
  les photos des copies.
- **Logs** : `journalctl -u devoir --since today`
