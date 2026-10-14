import Foundation

// Mirror of the website's API contract. All auth is cookie-based: URLSession
// stores the svg_session + CloudFront cookies automatically, and playback
// passes them to AVPlayer via AVURLAsset's cookie option.

struct Video: Codable, Identifiable, Hashable {
    let slug: String
    let title: String
    let folder: String
    let durationSeconds: Int
    let width: Int?
    let height: Int?
    let playlist: String
    let thumbnail: String

    var id: String { slug }
    var fullPlaylist: URL { URL(string: API.base + playlist)! }
    var fullThumbnail: URL? { URL(string: API.base + thumbnail) }

    var qualityLabel: String {
        guard let h = height, h > 0 else { return "HLS" }
        if h >= 2000 { return "4K" }
        if h >= 1000 { return "1080P" }
        return "\(h)P"
    }

    var durationLabel: String {
        let s = durationSeconds
        let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, sec)
                     : String(format: "%d:%02d", m, sec)
    }

    enum CodingKeys: String, CodingKey {
        case slug, title, folder, playlist, thumbnail, width, height
        case durationSeconds = "duration_seconds"
    }
}

enum API {
    static let base = "https://d31uqelgcxwhug.cloudfront.net"

    static let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.httpCookieStorage = HTTPCookieStorage.shared
        cfg.timeoutIntervalForRequest = 30
        cfg.waitsForConnectivity = true
        return URLSession(configuration: cfg)
    }()

    struct LoginResponse: Codable { let ok: Bool; let username: String }
    struct VideosResponse: Codable { let username: String; let videos: [Video] }
    struct APIError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    @discardableResult
    static func streamAuth() async throws -> Data {
        let (data, _) = try await session.data(
            for: URLRequest(url: URL(string: "\(base)/api/stream-auth")!))
        return data
    }

    static func login(username: String, password: String) async throws -> String {
        var req = URLRequest(url: URL(string: "\(base)/api/login")!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(
            ["username": username.lowercased().trimmingCharacters(in: .whitespaces),
             "password": password])
        let (data, resp) = try await session.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            let msg = (try? JSONDecoder().decode([String: String].self, from: data))?["error"]
            throw APIError(message: (msg ?? "login failed").uppercased())
        }
        _ = try? await streamAuth()   // pre-warm CloudFront signed cookies
        return try JSONDecoder().decode(LoginResponse.self, from: data).username
    }

    static func videos() async throws -> (String, [Video]) {
        let (data, resp) = try await session.data(
            for: URLRequest(url: URL(string: "\(base)/api/videos")!))
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            throw APIError(message: "SESSION EXPIRED")
        }
        let out = try JSONDecoder().decode(VideosResponse.self, from: data)
        return (out.username, out.videos)
    }

    static func logout() async {
        var req = URLRequest(url: URL(string: "\(base)/api/logout")!)
        req.httpMethod = "POST"
        _ = try? await session.data(for: req)
        // belt and braces: drop every auth cookie locally too
        for c in HTTPCookieStorage.shared.cookies ?? [] where c.name == "svg_session"
            || c.name.hasPrefix("CloudFront-") {
            HTTPCookieStorage.shared.deleteCookie(c)
        }
    }
}

// Watch positions, keyed per video (localStorage's iOS counterpart).
enum Progress {
    static func get(_ slug: String) -> Double {
        UserDefaults.standard.double(forKey: "pos:\(slug)")
    }
    static func set(_ slug: String, _ seconds: Double) {
        UserDefaults.standard.set(seconds, forKey: "pos:\(slug)")
    }
}
