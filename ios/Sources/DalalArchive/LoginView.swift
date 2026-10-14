import SwiftUI

// Design tokens ported 1:1 from site-design/DESIGN.md — the brutalist
// editorial system the website uses. Flat surfaces, zero shadows, big
// radii, mono labels, uppercase display type. Fonts are the same ones
// the site uses: Anton (display) + JetBrains Mono (labels), bundled.
enum D {
    // Colors
    static let canvas   = Color(hex: 0xE5E5E5)  // warm canvas
    static let card     = Color(hex: 0xFFFFFF)  // paper white
    static let inverted = Color(hex: 0x000000)  // carbon black
    static let mist     = Color(hex: 0xF3F3F3)  // mist gray
    static let ash      = Color(hex: 0xC6C6C6)  // hairlines
    static let smoke    = Color(hex: 0x979797)  // secondary text
    static let slate    = Color(hex: 0x444444)  // body text
    static let mint     = Color(hex: 0xD1FFCA)  // accent tags
    static let voltage  = Color(hex: 0xFFF100)  // micro highlights

    // Radii
    static let rCard: CGFloat = 24
    static let rPill: CGFloat = 48

    // Fonts — bundled to match the site (Anton display / Inter body /
    // JetBrains Mono labels; Inter falls back to the system grotesque)
    static let anton = Font.custom("Anton", size: 20, relativeTo: .title)
    static let mono  = Font.custom("JetBrainsMono-Regular", size: 11, relativeTo: .caption2)

    // Type roles (site scale, tuned for phone widths)
    static let display   = anton.size(44)
    static let displayLG = anton.size(64)
    static let heading   = anton.size(24)
    static let body: Font = .system(size: 15, weight: .medium)
    static let bodySm: Font = .system(size: 13, weight: .regular)
}

extension Font {
    // Font.custom doesn't chain sizes; this keeps call sites terse.
    func size(_ s: CGFloat) -> Font {
        switch self {
        case D.anton: return .custom("Anton", size: s, relativeTo: .largeTitle)
        default: return self
        }
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}

// Registers the bundled fonts exactly once, before any view renders.
enum Fonts {
    static let register: Void = {
        guard let urls = Bundle.module.urls(forResourcesWithExtension: "ttf",
                                            subdirectory: nil) else { return }
        for url in urls { CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil) }
    }()
}

// MARK: - Login view (mirrors site: hero + access card)

struct LoginView: View {
    @Binding var username: String
    @Binding var password: String
    let busy: Bool
    let error: String?
    let onLogin: () -> Void

    var body: some View {
        let _ = Fonts.register
        ZStack {
            D.canvas.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("3B")
                            .font(D.displayLG)
                            .tracking(-1)
                        Text("ARCHIVE")
                            .font(D.displayLG)
                            .tracking(-1)
                            .foregroundStyle(D.canvas)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(D.inverted)
                    }
                    Text("PRIVATE STREAMING — EVERY RECORDING,\nANYWHERE, FOR INVITED PEOPLE ONLY")
                        .font(D.bodySm)
                        .foregroundStyle(D.slate)
                        .lineSpacing(3)

                    // Access card
                    VStack(alignment: .leading, spacing: 14) {
                        HStack {
                            Text("ACCESS")
                                .font(D.mono)
                                .tracking(1)
                            Spacer()
                            Circle().fill(D.voltage).frame(width: 8, height: 8)
                        }
                        CustomField(placeholder: "USERNAME", text: $username)
                        CustomField(placeholder: "PASSWORD", text: $password, secure: true)
                        if let error {
                            Text(error.uppercased())
                                .font(D.mono)
                                .foregroundStyle(D.inverted)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 4)
                                .background(D.voltage)
                        }
                        Button {
                            onLogin()
                        } label: {
                            HStack {
                                Text(busy ? "CHECKING…" : "ENTER THE ARCHIVE")
                                Spacer()
                                Image(systemName: "arrow.right")
                            }
                            .font(D.body)
                            .foregroundStyle(D.card)
                            .padding(.horizontal, 20)
                            .padding(.vertical, 16)
                            .background(D.inverted)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .disabled(busy || username.isEmpty || password.isEmpty)
                        .opacity(busy ? 0.7 : 1)
                    }
                    .padding(24)
                    .background(D.card)
                    .clipShape(RoundedRectangle(cornerRadius: D.rCard))

                    HStack(spacing: 8) {
                        Text("INVITE-ONLY").font(D.mono)
                            .padding(.horizontal, 12).padding(.vertical, 6)
                            .background(D.mint).clipShape(Capsule())
                        Text("HLS · SIGNED COOKIES").font(D.mono)
                            .foregroundStyle(D.slate)
                    }
                }
                .padding(24)
            }
        }
    }
}

// MARK: - Flat text field (no shadows, thin ash border)

struct CustomField: View {
    var placeholder: String
    @Binding var text: String
    var secure = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(placeholder).font(D.mono).foregroundStyle(D.smoke)
            Group {
                if secure {
                    SecureField("", text: $text)
                } else {
                    TextField("", text: $text)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
            }
            .font(D.body)
            .padding(14)
            .background(D.mist)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(D.ash, lineWidth: 1)
            )
        }
    }
}

#Preview {
    LoginView(username: .constant(""),
              password: .constant(""),
              busy: false, error: nil, onLogin: {})
}
