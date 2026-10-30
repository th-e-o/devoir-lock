#!/usr/bin/env bash
# Installe l'app et la déclare « propriétaire de l'appareil » (Device Owner).
#
# Prérequis sur le téléphone :
#   - Options pour les développeurs > Débogage USB activé, téléphone branché et autorisé ;
#   - AUCUN compte (Google, Samsung…) sur le téléphone : Paramètres > Comptes > tout supprimer
#     (on pourra les rajouter juste après) ;
#   - pas d'autre utilisateur ni de profil professionnel.
set -euo pipefail

PKG=app.devoirdujour
APK=${1:-app/build/outputs/apk/release/app-release.apk}

[ -f "$APK" ] || { echo "APK introuvable : $APK (lancez d'abord ./gradlew assembleRelease)"; exit 1; }

echo "== Téléphone connecté"
adb get-state >/dev/null
adb shell getprop ro.product.model

echo "== Installation de $APK"
adb install -r "$APK"

echo "== Passage en Device Owner"
if ! adb shell dpm set-device-owner "$PKG/.AdminReceiver"; then
    cat <<'MSG'

Échec. Causes habituelles :
  - un compte est encore présent sur le téléphone (Paramètres > Comptes) ;
  - un autre utilisateur ou un profil professionnel existe ;
  - Xiaomi : activer aussi « Débogage USB (paramètres de sécurité) ».
MSG
    exit 1
fi

echo "== Autorisation de reprendre la main au premier plan (rebloquage à minuit)"
adb shell appops set "$PKG" SYSTEM_ALERT_WINDOW allow

echo "== Lancement"
adb shell am start -n "$PKG/.MainActivity"

cat <<'MSG'

Terminé. Vous pouvez maintenant rajouter le compte Google sur le téléphone.
Quand tout fonctionne, ouvrez l'écran parent (appui long sur le titre « Devoir du jour »)
et choisissez « Interdire le débogage USB ».
MSG
