import FamilyControls
import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var state: DeviceState?
    @Published private(set) var unlocked = SharedState.isUnlocked
    @Published private(set) var authorized = AuthorizationCenter.shared.authorizationStatus == .approved
    @Published var pages: [Data] = []
    @Published private(set) var sending = false
    @Published var lastError: String?
    @Published var message: String?

    init() {
        if let cached = SharedState.cachedState {
            state = try? Api.decode(cached)
        }
    }

    var isChildMode: Bool { Config.authorizationMode != "individual" }

    func requestAuthorization() async {
        do {
            try await AuthorizationCenter.shared.requestAuthorization(for: isChildMode ? .child : .individual)
            try Shield.startDailyMonitoring()
            Shield.refresh()
        } catch {
            lastError = "Autorisation Screen Time refusée : \(error.localizedDescription)"
        }
        refreshLocalState()
    }

    func refreshLocalState() {
        authorized = AuthorizationCenter.shared.authorizationStatus == .approved
        if authorized { Shield.refresh() }
        unlocked = SharedState.isUnlocked
    }

    /// Interroge le serveur ; le serveur fait foi (sans jeton, l'ancien est oublié).
    func sync() async {
        let wasUnlocked = SharedState.isUnlocked
        do {
            let (newState, raw) = try await Api.today()
            state = newState
            SharedState.cachedState = raw
            SharedState.unlockToken = newState.unlockToken
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
        refreshLocalState()
        if unlocked && !wasUnlocked {
            message = "Devoir validé, bonne journée !"
        }
    }

    func submit() async {
        guard !pages.isEmpty else { return }
        sending = true
        defer { sending = false }
        do {
            try await Api.submit(pages: pages)
            pages = []
            message = "Copie envoyée"
            await sync()
        } catch {
            message = "Envoi impossible : \(error.localizedDescription)"
        }
    }

    var statusText: String {
        if !Shield.isConfigured {
            return "⚠️ Blocage inactif : un parent doit choisir les apps autorisées "
                + "(appui long sur le titre « Devoir du jour »)."
        }
        if unlocked {
            return "✅ Devoir du jour validé. Le téléphone est débloqué jusqu'à minuit."
        }
        guard let state, state.date == UnlockToken.today() else { return "Connexion au serveur…" }
        switch state.status {
        case "en_preparation":
            return "Le devoir du jour est en préparation…"
        case "en_correction":
            return "⏳ Copie envoyée, en attente de correction. Tu peux renvoyer une version corrigée si besoin."
        case "valide":
            return "Devoir validé par le serveur, mais le jeton de déblocage est refusé "
                + "(vérifier la clé publique, l'identifiant d'appareil et l'heure du téléphone)."
        default:
            if state.derniereCopie?.status == "refuse" {
                let feedback = state.derniereCopie?.feedback ?? ""
                return "❌ Copie refusée. " + (feedback.isEmpty ? "Reprends le devoir et renvoie-le." : feedback)
            }
            return "Fais le devoir sur ton cahier, photographie chaque page puis envoie."
        }
    }
}
