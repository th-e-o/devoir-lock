import CryptoKit
import FamilyControls
import SwiftUI

/// Écran parent protégé par code : apps autorisées, déblocage manuel, désactivation.
struct AdminView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @AppStorage("failedPinAttempts") private var failedAttempts = 0
    @AppStorage("pinLockedUntil") private var pinLockedUntil: Double = 0
    @State private var pin = ""
    @State private var unlockedAdmin = false
    @State private var showPicker = false
    @State private var selection = SharedState.allowedApps
    @State private var info: String?
    @State private var confirmRemoval = false

    var body: some View {
        NavigationStack {
            Form {
                if unlockedAdmin { adminSections } else { pinSection }
                if let info { Section { Text(info) } }
            }
            .navigationTitle("Parent")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Fermer") { dismiss() } }
            }
            .familyActivityPicker(isPresented: $showPicker, selection: $selection)
            .onChange(of: selection) { newValue in
                SharedState.allowedApps = newValue
                Shield.refresh()
            }
            .confirmationDialog("Désactiver le contrôle ?", isPresented: $confirmRemoval, titleVisibility: .visible) {
                Button("Désactiver", role: .destructive) { removeControl() }
            } message: {
                Text("Toutes les restrictions seront levées et l'application pourra être supprimée.")
            }
        }
    }

    private var pinSection: some View {
        Section {
            SecureField("Code parent", text: $pin).keyboardType(.numberPad)
            Button("Valider", action: checkPin)
        }
    }

    @ViewBuilder
    private var adminSections: some View {
        Section("Pendant le blocage") {
            Button("Choisir les apps autorisées") { showPicker = true }
            Text("\(selection.applicationTokens.count) app(s) autorisée(s). Sélectionnez les apps une par une "
                + "(les catégories entières ne sont pas prises en compte) et incluez au moins Devoir du jour, "
                + "Téléphone et Messages. Tant que la liste est vide, rien n'est bloqué.")
                .font(.footnote)
        }
        Section("Déblocage manuel") {
            Button("Débloquer pendant 1 heure") { unlock(until: Date().addingTimeInterval(3600)) }
            Button("Débloquer jusqu'à minuit") {
                let midnight = Calendar.current.startOfDay(for: Date().addingTimeInterval(86_400))
                unlock(until: midnight.addingTimeInterval(-60))
            }
            Button("Annuler le déblocage manuel") {
                SharedState.adminUnlockUntil = .distantPast
                model.refreshLocalState()
                info = "Déblocage manuel annulé."
            }
        }
        Section("État") {
            Text("Autorisation Screen Time : \(model.authorized ? "accordée" : "NON accordée")")
            Text("Mode : \(model.isChildMode ? "compte enfant (Partage familial)" : "individuel")")
            if !model.isChildMode {
                Text("En mode individuel, l'élève peut retirer l'autorisation dans Réglages > Temps d'écran.")
                    .font(.footnote).foregroundStyle(.orange)
            }
            Text("Serveur : \(Config.serverURL)").font(.footnote)
        }
        Section {
            Button("Désactiver le contrôle (avant suppression)", role: .destructive) { confirmRemoval = true }
        }
    }

    private func checkPin() {
        let now = Date().timeIntervalSince1970
        guard now >= pinLockedUntil else {
            info = "Trop d'essais, réessayez plus tard."
            return
        }
        let digest = SHA256.hash(data: Data(pin.utf8)).map { String(format: "%02x", $0) }.joined()
        if digest == Config.adminPinSHA256 {
            failedAttempts = 0
            unlockedAdmin = true
            info = nil
        } else {
            failedAttempts += 1
            if failedAttempts >= 5 {
                failedAttempts = 0
                pinLockedUntil = now + 15 * 60
            }
            info = "Code incorrect."
        }
        pin = ""
    }

    private func unlock(until: Date) {
        do {
            try Shield.adminUnlock(until: until)
            model.refreshLocalState()
            info = "Téléphone débloqué."
        } catch {
            info = "Impossible : \(error.localizedDescription)"
        }
    }

    private func removeControl() {
        Shield.removeAll()
        AuthorizationCenter.shared.revokeAuthorization { _ in }
        model.refreshLocalState()
        info = "Contrôle désactivé."
    }
}
