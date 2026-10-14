import SwiftUI

// The archive: folder chips on canvas, flat white video cards with
// thumbnails, mono meta labels, watch-progress bars — the site's grid,
// thumb-sized.

struct ArchiveView: View {
    let username: String
    let videos: [Video]
    let onLogout: () -> Void
    @State private var folder: String? = nil   // nil = ALL

    private var folders: [String] {
        Array(Set(videos.map(\.folder))).sorted()
    }
    private var shown: [Video] {
        videos.filter { folder == nil || $0.folder == folder }
              .sorted { ($0.folder, $0.title) < ($1.folder, $1.title) }
    }

    var body: some View {
        ZStack {
            D.canvas.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header
                    if !folders.isEmpty { folderChips }
                    grid
                    if shown.isEmpty {
                        Text("NOTHING HERE YET — UPLOAD FROM YOUR PC")
                            .font(D.mono).foregroundStyle(D.smoke)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 60)
                    }
                }
                .padding(20)
            }
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 0) {
                Text("DALAL").font(D.display).tracking(-0.5)
                Text("ARCHIVE").font(D.display).tracking(-0.5)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 6) {
                Text("@\(username.uppercased())").font(D.mono).foregroundStyle(D.slate)
                Button("LOG OUT", action: onLogout)
                    .font(D.mono)
                    .foregroundStyle(D.inverted)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .overlay(RoundedRectangle(cornerRadius: 4).stroke(D.inverted, lineWidth: 1.2))
            }
        }
    }

    private var folderChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                chip("ALL", active: folder == nil) { folder = nil }
                ForEach(folders, id: \.self) { f in
                    chip(f.uppercased(), active: folder == f) {
                        folder = (folder == f) ? nil : f
                    }
                }
            }
        }
    }

    private func chip(_ label: String, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(D.mono)
                .foregroundStyle(active ? D.inverted : D.slate)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(active ? D.mint : D.card)
                .clipShape(Capsule())
                .overlay(Capsule().stroke(active ? D.mint : D.ash, lineWidth: 1))
        }
    }

    private var grid: some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 14),
                            GridItem(.flexible(), spacing: 14)], spacing: 14) {
            ForEach(shown) { v in
                NavigationLink(value: v) {
                    VideoCard(v: v)
                }
                .buttonStyle(.plain)
            }
        }
    }
}

struct VideoCard: View {
    let v: Video
    @State private var aspect: CGFloat = 16 / 9

    private var watched: Double {
        let d = Double(v.durationSeconds)
        guard d > 0 else { return 0 }
        return min(Progress.get(v.slug) / d, 1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack(alignment: .bottomLeading) {
                AsyncImage(url: v.fullThumbnail) { img in
                    img.resizable().aspectRatio(contentMode: .fill)
                } placeholder: {
                    D.mist
                }
                .aspectRatio(aspect, contentMode: .fit)
                .clipped()

                Text(v.durationLabel)
                    .font(D.mono)
                    .foregroundStyle(D.card)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(D.inverted.opacity(0.85))
                    .padding(6)
            }
            .background(D.mist)
            .overlay(alignment: .bottom) {
                if watched > 0.01 {
                    GeometryReader { geo in
                        HStack { Rectangle().fill(D.voltage).frame(width: geo.size.width * watched) }
                    }
                    .frame(height: 4)
                }
            }

            VStack(alignment: .leading, spacing: 5) {
                Text(v.title.uppercased())
                    .font(D.body)
                    .foregroundStyle(D.inverted)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                Text("\(v.folder.uppercased()) · \(v.qualityLabel)\(watched > 0.01 ? " · \(Int(watched * 100))% WATCHED" : "")")
                    .font(D.mono)
                    .foregroundStyle(D.smoke)
            }
            .padding(12)
        }
        .background(D.card)
        .clipShape(RoundedRectangle(cornerRadius: D.rCard))
        .onAppear {
            if let h = v.height, let w = v.width, w > 0 { aspect = CGFloat(w) / CGFloat(h) }
        }
    }
}
