import FamilyControls
import Foundation

/// État partagé entre l'app et l'extension de surveillance (App Group).
enum SharedState {
    private static var defaults: UserDefaults {
        UserDefaults(suiteName: Config.appGroup) ?? .standard
    }

    /// Dernier jeton de déblocage reçu du serveur (valable uniquement pour sa date).
    static var unlockToken: String? {
        get { defaults.string(forKey: "unlockToken") }
        set { defaults.set(newValue, forKey: "unlockToken") }
    }

    /// Déblocage manuel par le parent, jusqu'à cette date.
    static var adminUnlockUntil: Date {
        get { Date(timeIntervalSince1970: defaults.double(forKey: "adminUnlockUntil")) }
        set { defaults.set(newValue.timeIntervalSince1970, forKey: "adminUnlockUntil") }
    }

    /// Apps restant utilisables pendant le blocage (choisies par le parent).
    static var allowedApps: FamilyActivitySelection {
        get {
            guard let data = defaults.data(forKey: "allowedApps"),
                  let selection = try? JSONDecoder().decode(FamilyActivitySelection.self, from: data)
            else { return FamilyActivitySelection() }
            return selection
        }
        set { defaults.set(try? JSONEncoder().encode(newValue), forKey: "allowedApps") }
    }

    /// Dernier état JSON renvoyé par le serveur (affichage hors ligne).
    static var cachedState: Data? {
        get { defaults.data(forKey: "cachedState") }
        set { defaults.set(newValue, forKey: "cachedState") }
    }

    static var isUnlocked: Bool {
        UnlockToken.isValidForToday(unlockToken) || Date() < adminUnlockUntil
    }
}
