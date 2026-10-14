import SwiftUI

@main
struct DalalArchiveApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
                .preferredColorScheme(.light)   // the design system is light-only
        }
    }
}

// Temporary root until the API layer lands: shows the login view wired to
// local state so the design can be reviewed on-device immediately.
struct RootView: View {
    @State private var username = ""
    @State private var password = ""
    @State private var busy = false
    @State private var error: String? = nil

    var body: some View {
        LoginView(username: $username, password: $password,
                  busy: busy, error: error) {
            Task { await fakeLogin() }
        }
    }

    // Placeholder — replaced by the real API client next step.
    private func fakeLogin() async {
        busy = true
        error = nil
        try? await Task.sleep(for: .seconds(1))
        busy = false
        error = "API client lands in the next commit"
    }
}
