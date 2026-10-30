#!/usr/bin/env bash
# Mise à jour automatique (lancée toutes les 5 min par devoir-update.timer, en root).
#
# 1. Récupère origin/main ; s'il n'y a rien de nouveau, s'arrête.
# 2. Lance les tests du nouveau commit dans un dossier et un environnement Python séparés.
# 3. Si les tests passent : met à jour le code, redémarre le serveur, vérifie /health
#    (retour à la version précédente si le serveur ne répond pas).
# 4. Prévient les parents sur Telegram dans tous les cas (une seule fois par commit en échec).
set -uo pipefail

REPO=/opt/devoir-lock
SERVER=$REPO/server
ENV_FILE=$SERVER/.env
CHECK_DIR=/home/devoir/verif-maj
CHECK_VENV=/home/devoir/venv-verif
STATE=/home/devoir/.derniere-maj-en-echec
as_devoir() { runuser -u devoir -- "$@"; }

env_value() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }

notify() {
    local token ids
    token=$(env_value TELEGRAM_BOT_TOKEN)
    ids=$(env_value PARENT_CHAT_IDS | tr ',' ' ')
    for id in $ids; do
        curl -s -o /dev/null "https://api.telegram.org/bot$token/sendMessage" \
            --data-urlencode "chat_id=$id" --data-urlencode "text=$1" || true
    done
}

healthy() {
    for _ in $(seq 1 20); do
        curl -sf http://127.0.0.1:8000/health >/dev/null && return 0
        sleep 1
    done
    return 1
}

as_devoir git -C "$REPO" fetch -q origin main || exit 0
current=$(as_devoir git -C "$REPO" rev-parse HEAD)
target=$(as_devoir git -C "$REPO" rev-parse origin/main)
[ "$current" = "$target" ] && exit 0
[ -f "$STATE" ] && [ "$(cat "$STATE")" = "$target" ] && exit 0  # déjà signalé

short=${target:0:7}
subject=$(as_devoir git -C "$REPO" log -1 --format=%s "$target")

# --- tests dans une copie séparée -------------------------------------------
as_devoir git -C "$REPO" worktree remove --force "$CHECK_DIR" 2>/dev/null
rm -rf "$CHECK_DIR"
as_devoir git -C "$REPO" worktree prune
as_devoir git -C "$REPO" worktree add -q --detach "$CHECK_DIR" "$target"
[ -x "$CHECK_VENV/bin/python" ] || as_devoir python3 -m venv "$CHECK_VENV"
log=$(cd "$CHECK_DIR/server" && as_devoir "$CHECK_VENV/bin/pip" install -q -r requirements-dev.txt 2>&1 \
      && as_devoir "$CHECK_VENV/bin/python" -m pytest -q -p no:cacheprovider 2>&1)
tests_ok=$?
as_devoir git -C "$REPO" worktree remove --force "$CHECK_DIR"

if [ $tests_ok -ne 0 ]; then
    echo "$target" > "$STATE"
    notify "❌ Mise à jour $short non installée : les tests échouent.
« $subject »
$(echo "$log" | tail -n 15)"
    exit 1
fi

# --- installation --------------------------------------------------------------
if ! as_devoir git -C "$REPO" merge -q --ff-only "$target"; then
    echo "$target" > "$STATE"
    notify "❌ Mise à jour $short impossible : le code sur le VPS a été modifié à la main (git status)."
    exit 1
fi
as_devoir "$SERVER/.venv/bin/pip" install -q -r "$SERVER/requirements.txt"
systemctl restart devoir

if healthy; then
    rm -f "$STATE"
    notify "✅ Serveur mis à jour : $short « $subject »"
else
    echo "$target" > "$STATE"
    as_devoir git -C "$REPO" reset -q --hard "$current"
    as_devoir "$SERVER/.venv/bin/pip" install -q -r "$SERVER/requirements.txt"
    systemctl restart devoir
    notify "⚠️ Mise à jour $short annulée : le serveur ne répondait plus après redémarrage. Retour à ${current:0:7}."
    exit 1
fi
