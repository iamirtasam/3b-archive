import SwiftUI

// The archive: folder chips on canvas, flat white video cards with
// thumbnails, mono meta labels, watch-progress bars — the site's grid,
// thumb-sized.

struct ArchiveView: View {
    let username: String
    @Binding var videos: [Video]
    let onLogout: () -> Void
    @State private var folder: String? = nil   // nil = ALL
    @State private var manageTarget: Video? = nil
    @State private var renameText = ""
    @State private var moveText = ""
    @State private var folderRenameFrom: String? = nil
    @State private var folderRenameText = ""
    @State private var deleteConfirm: Video? = nil
    @State private var busy = false

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
        .confirmationDialog(
            "DELETE “\(deleteConfirm?.title ?? "")”?\nRemoved from S3 permanently.",
            isPresented: Binding(get: { deleteConfirm != nil },
                                 set: { if !$0 { deleteConfirm = nil } }),
            titleVisibility: .visible) {
            Button("DELETE", role: .destructive) {
                if let v = deleteConfirm { Task { await doDelete(v) } }
                deleteConfirm = nil
            }
            Button("CANCEL", role: .cancel) { deleteConfirm = nil }
        }
        .alert("RENAME VIDEO", isPresented: Binding(get: { manageTarget != nil && renameMode == .rename },
                                                     set: { if !$0 { manageTarget = nil } })) {
            TextField("TITLE", text: $renameText)
            Button("SAVE") { if let v = manageTarget { Task { await doRename(v, renameText) } } ; manageTarget = nil }
            Button("CANCEL", role: .cancel) { manageTarget = nil }
        }
        .alert("MOVE TO FOLDER", isPresented: Binding(get: { manageTarget != nil && renameMode == .move },
                                                      set: { if !$0 { manageTarget = nil } })) {
            TextField("FOLDER", text: $moveText)
            Button("MOVE") { if let v = manageTarget { Task { await doMove(v, moveText) } } ; manageTarget = nil }
            Button("CANCEL", role: .cancel) { manageTarget = nil }
        } message: {
            Text("EXISTING: \(folders.joined(separator: ", "))")
        }
        .alert("RENAME FOLDER", isPresented: Binding(get: { folderRenameFrom != nil },
                                                     set: { if !$0 { folderRenameFrom = nil } })) {
            TextField("NEW NAME", text: $folderRenameText)
            Button("RENAME") { if let f = folderRenameFrom { Task { await doFolderRename(f, folderRenameText) } } ; folderRenameFrom = nil }
            Button("CANCEL", role: .cancel) { folderRenameFrom = nil }
        }
    }

    private enum ManageMode { case rename, move }
    private var renameMode: ManageMode {
        renameText.isEmpty && !moveText.isEmpty ? .move : .rename
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 0) {
                Text("3B").font(D.display).tracking(-0.5)
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
        .contextMenu {
            Button("RENAME FOLDER") {
                folderRenameFrom = label == "ALL" ? nil : label
                folderRenameText = folderRenameFrom ?? ""
            }
        }
    }

    private var grid: some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 14),
                            GridItem(.flexible(), spacing: 14)], spacing: 14) {
            ForEach(shown) { v in
                NavigationLink(value: v) {
                    VideoCard(v: v)
                        .contextMenu {
                            Button("RENAME") {
                                manageTarget = v
                                renameText = v.title
                                moveText = ""
                            }
                            Button("MOVE TO FOLDER") {
                                manageTarget = v
                                moveText = v.folder
                                renameText = ""
                            }
                            Button("DELETE", role: .destructive) { deleteConfirm = v }
                        }
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: management actions

    private func doDelete(_ v: Video) async {
        busy = true
        defer { busy = false }
        try? await API.deleteVideo(v.slug)
        videos.removeAll { $0.slug == v.slug }
        Progress.reset(v.slug)
    }

    private func doRename(_ v: Video, _ title: String) async {
        let t = title.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        busy = true
        defer { busy = false }
        try? await API.renameVideo(v.slug, to: t)
        if let i = videos.firstIndex(where: { $0.slug == v.slug }) {
            videos[i].title = t
        }
    }

    private func doMove(_ v: Video, _ f: String) async {
        let folder = f.trimmingCharacters(in: .whitespaces)
        guard !folder.isEmpty else { return }
        busy = true
        defer { busy = false }
        try? await API.moveVideo(v.slug, to: folder)
        if let i = videos.firstIndex(where: { $0.slug == v.slug }) {
            videos[i].folder = folder
        }
    }

    private func doFolderRename(_ from: String, _ to: String) async {
        let new = to.trimmingCharacters(in: .whitespaces)
        guard !new.isEmpty, new != from else { return }
        busy = true
        defer { busy = false }
        try? await API.renameFolder(from, to: new)
        for i in videos.indices where videos[i].folder == from {
            videos[i].folder = new
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
