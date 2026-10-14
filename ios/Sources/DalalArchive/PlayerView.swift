import SwiftUI
import AVFoundation
import AVKit

// Black stage player, mirrors the site: video on black, resume from the
// last watched position, progress saved while playing. Wrapped
// AVPlayerViewController gives the native transport bar with AirPlay and
// Picture-in-Picture for free.
//
// Playback auth: the website uses CloudFront signed cookies, and AVPlayer
// honours cookies via AVURLAsset's AVURLAssetHTTPCookiesKey option — we
// hand it the same shared cookie storage URLSession logged in with.

struct PlayerView: View {
    let video: Video
    @Environment(\.dismiss) private var dismiss
    @State private var player: AVPlayer?
    @State private var loadError: String?
    @State private var timeObserver: Any?

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            stage.ignoresSafeArea()
        }
        .task { await start() }
        .onDisappear { teardown() }
    }

    // Kept as separate subviews with explicit bindings — the `if let`
    // shorthand inside a big ViewBuilder crashes the 6.4 compiler.
    @ViewBuilder
    private var stage: some View {
        if player != nil {
            PlayerController(player: player!)
        } else if loadError != nil {
            ErrorStage(message: loadError!) {
                loadError = nil
                Task { await start() }
            }
        } else {
            ProgressView().tint(.white)
        }
    }

    private func start() async {
        // Stream cookies may have expired since login (12 h TTL); refresh.
        _ = try? await API.streamAuth()

        let asset = AVURLAsset(
            url: video.fullPlaylist,
            options: [AVURLAssetHTTPCookiesKey: HTTPCookieStorage.shared.cookies ?? []])
        let item = AVPlayerItem(asset: asset)
        let p = AVPlayer(playerItem: item)

        // Resume where the site left off (and vice versa).
        let saved = Progress.get(video.slug)
        if saved > 5 {
            await p.seek(to: CMTime(seconds: saved, preferredTimescale: 600),
                         toleranceBefore: .zero, toleranceAfter: .zero)
        }

        p.allowsExternalPlayback = true          // AirPlay
        player = p
        p.play()

        // Save watch position every 5 s, like the site's localStorage.
        timeObserver = p.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 5, preferredTimescale: 1),
            queue: .main
        ) { time in
            guard let dur = p.currentItem?.duration.seconds, dur.isFinite, dur > 0,
                  time.seconds < dur - 10 else { return }   // don't log the credits
            Progress.set(video.slug, time.seconds)
        }
    }

    private func teardown() {
        if let observer = timeObserver {
            player?.removeTimeObserver(observer)
            timeObserver = nil
        }
        let t = player?.currentTime().seconds ?? 0
        if t.isFinite && t > 0 {
            Progress.set(video.slug, t)
        }
        player?.pause()
        player = nil
    }
}

private struct ErrorStage: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            Text(message).font(D.mono).foregroundStyle(D.card)
            Button("RETRY", action: retry)
                .font(D.body)
                .foregroundStyle(D.inverted)
                .padding(.horizontal, 18).padding(.vertical, 10)
                .background(D.card)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}

// AVKit's VideoPlayer SwiftUI view is not exposed in the Linux-built SDK;
// the view controller always is, and it brings the full native controls.
struct PlayerController: UIViewControllerRepresentable {
    let player: AVPlayer

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let vc = AVPlayerViewController()
        vc.player = player
        vc.allowsPictureInPicturePlayback = true
        vc.updatesNowPlayingInfoCenter = true
        return vc
    }

    func updateUIViewController(_ vc: AVPlayerViewController, context: Context) {}
}
