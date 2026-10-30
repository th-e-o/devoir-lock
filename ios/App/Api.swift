import Foundation

struct DeviceState: Codable {
    struct Copy: Codable {
        let status: String
        let feedback: String?
    }

    let date: String
    let status: String
    let titre: String?
    let enonce: String?
    let dureeMin: Int?
    let unlockToken: String?
    let derniereCopie: Copy?
}

struct ApiError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

enum Api {
    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 300
        return URLSession(configuration: configuration)
    }()

    private static func request(_ path: String) throws -> URLRequest {
        guard let url = URL(string: Config.serverURL + path) else {
            throw ApiError(message: "URL du serveur invalide : \(Config.serverURL)")
        }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(Config.deviceToken)", forHTTPHeaderField: "Authorization")
        return request
    }

    static func decode(_ data: Data) throws -> DeviceState {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(DeviceState.self, from: data)
    }

    /// Renvoie l'état du jour et le JSON brut (mis en cache pour l'affichage hors ligne).
    static func today() async throws -> (DeviceState, Data) {
        let (data, response) = try await session.data(for: request("/api/today"))
        try check(data, response)
        return (try decode(data), data)
    }

    static func submit(pages: [Data]) async throws {
        let boundary = "devoir-\(UUID().uuidString)"
        var request = try request("/api/today/submissions")
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        for (index, page) in pages.enumerated() {
            body.append(Data("--\(boundary)\r\n".utf8))
            body.append(Data("Content-Disposition: form-data; name=\"photos\"; filename=\"page\(index + 1).jpg\"\r\n".utf8))
            body.append(Data("Content-Type: image/jpeg\r\n\r\n".utf8))
            body.append(page)
            body.append(Data("\r\n".utf8))
        }
        body.append(Data("--\(boundary)--\r\n".utf8))
        let (data, response) = try await session.upload(for: request, from: body)
        try check(data, response)
    }

    private static func check(_ data: Data, _ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw ApiError(message: "Réponse invalide") }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw ApiError(message: detail ?? "Erreur serveur (\(http.statusCode))")
        }
    }
}
