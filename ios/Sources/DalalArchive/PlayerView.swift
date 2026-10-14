import SwiftUI
import AVFoundation

// Black stage player, mirrors the site: video on black, resume from the
// last watched position, progress saved while playing. AirPlay, PiP and
// background audio come from AVPlayerViewController for free.
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
            if let player {
                VideoPlayer(player: player)
                    .ignoresSafeArea()
            } else if let loadError {
                VStack(spacing: 14) {
                    Text(loadError).font(D.mono).foregroundStyle(D.card)
                    Button("RETRY") {
                        loadError = nil
                        Task { await start() }
                    }
                    .font(D.body)
                    .foregroundStyle(D.inverted)
                    .padding(.horizontal, 18).padding(.vertical, 10)
                    .background(D.card)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            } else {
                ProgressView().tint(.white)
            }
        }
        .task { await start() }
        .onDisappear { teardown() }
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
        if saved > 5 { p.seek(to: CMTime(seconds: saved, toleranceBefore: .zero, toleranceAfter: .zero)) }

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
        if let p = player, let t = p.currentTime().seconds, t.isFinite, t > 0 {
            Progress.set(video.slug, t)
        }
        player?.pause()
        player = nil
    }
}
