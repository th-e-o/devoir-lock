import DeviceActivity
import Foundation

/// Extension lancée par le système aux bornes des intervalles surveillés, même si l'app est fermée.
class Monitor: DeviceActivityMonitor {
    override func intervalDidStart(for activity: DeviceActivityName) {
        super.intervalDidStart(for: activity)
        // À minuit, le jeton de la veille n'est plus valable : le bouclier revient.
        Shield.refresh()
    }

    override func intervalDidEnd(for activity: DeviceActivityName) {
        super.intervalDidEnd(for: activity)
        if activity == .adminUnlock {
            SharedState.adminUnlockUntil = .distantPast
        }
        Shield.refresh()
    }
}
