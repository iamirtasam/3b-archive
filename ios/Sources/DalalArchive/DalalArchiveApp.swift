import SwiftUI
import AVFoundation

@main
struct DalalArchiveApp: App {
    init() {
        Fonts.register
        try? AVAudioSession.sharedInstance().setCategory(.playback)  // background audio
    }

    var body: some Scene {
        WindowGroup { RootView() }
    }
}

enum Route: Hashable {
    case archive(username: String, videos: [Video])
    case player(Video)
}

struct RootView: View {
    @State private var username: String? = nil
    @State private var videos: [Video] = []
    @State private var loading = false
    @State private var loginError: String?
    @State private var loginUsername = ""
    @State private var loginPassword = ""

    var body: some View {
        NavigationStack {
            if let username {
                ArchiveView(username: username, videos: videos,
                            onLogout: { Task { await logout() } })
                    .navigationDestination(for: Video.self) { v in
                        PlayerView(video: v)
                            .navigationBarHidden(true)
                    }
            } else {
                LoginView(username: $loginUsername, password: $loginPassword,
                          busy: loading, error: loginError) {
                    Task { await login() }
                }
            }
        }
        .task {
            // Returning app open: if the session cookie is still valid,
            // drop straight into the archive (like the site's boot check).
            if !loading, username == nil {
                loading = true
                defer { loading = false }
                if let (user, vids) = try? await API.videos() {
                    username = user
                    videos = vids
                }
            }
        }
    }

    private func login() async {
        loading = true
        loginError = nil
        defer { loading = false }
        do {
            username = try await API.login(username: loginUsername, password: loginPassword)
            loginPassword = ""
            (_, videos) = try await API.videos()
        } catch {
            loginError = (error as? LocalizedError)?.errorDescription ?? "NETWORK ERROR"
        }
    }

    private func logout() async {
        await API.logout()
        username = nil
        videos = []
        loginUsername = ""
    }
}
