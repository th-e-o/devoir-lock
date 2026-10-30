import Foundation

/// Valeurs injectées dans Info.plist depuis Config.xcconfig.
enum Config {
    private static func value(_ key: String) -> String {
        (Bundle.main.object(forInfoDictionaryKey: key) as? String ?? "").trimmingCharacters(in: .whitespaces)
    }

    static var serverURL: String { value("DevoirServerURL").trimmingCharacters(in: CharacterSet(charactersIn: "/")) }
    static var deviceToken: String { value("DevoirDeviceToken") }
    static var deviceID: String { value("DevoirDeviceID") }
    static var publicKey: String { value("DevoirPublicKey") }
    static var adminPinSHA256: String { value("DevoirAdminPinSHA256").lowercased() }
    static var appGroup: String { value("DevoirAppGroup") }
    /// "child" (Partage familial) ou "individual".
    static var authorizationMode: String { value("DevoirAuthorization") }
}
