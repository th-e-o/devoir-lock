import ManagedSettings
import ManagedSettingsUI
import UIKit

/// Texte affiché sur les apps bloquées.
class ShieldConfig: ShieldConfigurationDataSource {
    private var configuration: ShieldConfiguration {
        ShieldConfiguration(
            backgroundBlurStyle: .systemMaterial,
            icon: UIImage(systemName: "pencil.and.ruler"),
            title: ShieldConfiguration.Label(text: "Devoir du jour à faire", color: .label),
            subtitle: ShieldConfiguration.Label(
                text: "Ouvre l'app « Devoir du jour », fais l'exercice et envoie ta copie pour débloquer le téléphone.",
                color: .secondaryLabel
            ),
            primaryButtonLabel: ShieldConfiguration.Label(text: "OK", color: .white),
            primaryButtonBackgroundColor: .systemBlue
        )
    }

    override func configuration(shielding application: Application) -> ShieldConfiguration {
        configuration
    }

    override func configuration(shielding application: Application, in category: ActivityCategory) -> ShieldConfiguration {
        configuration
    }

    override func configuration(shielding webDomain: WebDomain) -> ShieldConfiguration {
        configuration
    }

    override func configuration(shielding webDomain: WebDomain, in category: ActivityCategory) -> ShieldConfiguration {
        configuration
    }
}
