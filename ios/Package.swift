// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "DalalArchive",
    platforms: [.iOS(name: .iOS, version: "17.0")],
    targets: [
        .executableTarget(
            name: "DalalArchive",
            path: "Sources/DalalArchive"
        )
    ]
)
