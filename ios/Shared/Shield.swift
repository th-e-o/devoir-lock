import DeviceActivity
import FamilyControls
import Foundation
import ManagedSettings

extension DeviceActivityName {
    /// Tous les jours de 00:00 à 23:59 : le début d'intervalle rebloque le téléphone à minuit.
    static let daily = Self("daily")
    /// Déblocage manuel temporaire : la fin d'intervalle rebloque.
    static let adminUnlock = Self("adminUnlock")
}

extension ManagedSettingsStore.Name {
    static let devoir = Self("devoir")
}

/// Pose ou lève le bouclier Screen Time sur toutes les apps sauf celles autorisées.
enum Shield {
    private static var store: ManagedSettingsStore { ManagedSettingsStore(named: .devoir) }

    /// Applique l'état courant (appelé par l'app et par l'extension de surveillance).
    static func refresh() {
        if SharedState.isUnlocked { unlock() } else { lock() }
    }

    /// Tant que le parent n'a pas choisi les apps autorisées (dont Devoir du jour), on ne bloque rien :
    /// sinon l'app elle-même pourrait se retrouver derrière le bouclier.
    static var isConfigured: Bool { !SharedState.allowedApps.applicationTokens.isEmpty }

    static func lock() {
        let store = store
        let allowed = SharedState.allowedApps
        guard isConfigured else {
            unlock()
            return
        }
        store.shield.applicationCategories = .all(except: allowed.applicationTokens)
        store.shield.webDomainCategories = .all(except: allowed.webDomainTokens)
        applyPermanentRestrictions(store)
    }

    static func unlock() {
        let store = store
        store.shield.applicationCategories = nil
        store.shield.webDomainCategories = nil
        applyPermanentRestrictions(store)
    }

    /// Restrictions maintenues même quand le téléphone est débloqué.
    private static func applyPermanentRestrictions(_ store: ManagedSettingsStore) {
        store.application.denyAppRemoval = true
        store.dateAndTime.requireAutomaticDateAndTime = true
    }

    /// Lève tout (avant désinstallation).
    static func removeAll() {
        store.clearAllSettings()
        DeviceActivityCenter().stopMonitoring()
    }

    static func startDailyMonitoring() throws {
        let schedule = DeviceActivitySchedule(
            intervalStart: DateComponents(hour: 0, minute: 0),
            intervalEnd: DateComponents(hour: 23, minute: 59),
            repeats: true
        )
        try DeviceActivityCenter().startMonitoring(.daily, during: schedule)
    }

    /// Débloque jusqu'à `until` ; l'extension rebloquera à la fin de l'intervalle (15 min minimum).
    static func adminUnlock(until: Date) throws {
        SharedState.adminUnlockUntil = until
        let calendar = Calendar.current
        let parts: Set<Calendar.Component> = [.year, .month, .day, .hour, .minute, .second]
        let schedule = DeviceActivitySchedule(
            intervalStart: calendar.dateComponents(parts, from: Date()),
            intervalEnd: calendar.dateComponents(parts, from: until),
            repeats: false
        )
        let center = DeviceActivityCenter()
        center.stopMonitoring([.adminUnlock])
        try center.startMonitoring(.adminUnlock, during: schedule)
        refresh()
    }
}
