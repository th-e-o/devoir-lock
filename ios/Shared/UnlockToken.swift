import CryptoKit
import Foundation

/// Jeton signé par le serveur (ECDSA P-256 / SHA-256) : base64url(payload).base64url(signature DER).
/// Le payload contient l'identifiant de l'appareil et la date : un jeton ne débloque que ce jour-là.
enum UnlockToken {
    static func isValidForToday(_ token: String?, now: Date = Date()) -> Bool {
        guard let token, !token.isEmpty else { return false }
        let parts = token.split(separator: ".").map(String.init)
        guard parts.count == 2,
              let body = base64URLDecode(parts[0]),
              let signatureBytes = base64URLDecode(parts[1]),
              let keyBytes = Data(base64Encoded: Config.publicKey),
              let key = try? P256.Signing.PublicKey(derRepresentation: keyBytes),
              let signature = try? P256.Signing.ECDSASignature(derRepresentation: signatureBytes),
              key.isValidSignature(signature, for: body),
              let payload = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
        else { return false }
        return payload["device"] as? String == Config.deviceID && payload["date"] as? String == today(now)
    }

    static func today(_ now: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: now)
    }

    private static func base64URLDecode(_ value: String) -> Data? {
        var base64 = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        return Data(base64Encoded: base64)
    }
}
