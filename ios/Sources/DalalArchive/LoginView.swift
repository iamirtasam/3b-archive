import SwiftUI

// Design tokens ported 1:1 from site-design/DESIGN.md — the brutalist
// editorial system the website uses. Flat surfaces, zero shadows, big
// radii, mono labels, uppercase condensed display type.
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

    // Type roles (site scale, tuned for phone widths)
    static let display: Font = .system(size: 44, weight: .heavy, design: .condensed)
    static let displayLG: Font = .system(size: 64, weight: .heavy, design: .condensed)
    static let heading: Font = .system(size: 24, weight: .semibold, design: .condensed)
    static let body: Font = .system(size: 15, weight: .medium)
    static let bodySm: Font = .system(size: 13, weight: .regular)
    static let mono: Font = .system(size: 11, weight: .regular, design: .monospaced)
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

// MARK: - Login view (mirrors site: hero + access card)

struct LoginView: View {
    @Binding var username: String
    @Binding var password: String
    let busy: Bool
    let error: String?
    let onLogin: () -> Void

    var body: some View {
        ZStack {
            D.canvas.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("DALAL")
                            .font(D.displayLG)
                            .tracking(-1)
                        Text("ARCHIVE")
                            .font(D.displayLG)
                            .tracking(-1)
                            .foregroundStyle(.white)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 2)
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
