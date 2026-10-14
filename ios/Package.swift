// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "DalalArchive",
    platforms: [.iOS(.v17)],
    targets: [
        .executableTarget(
            name: "DalalArchive",
            path: "Sources/DalalArchive"
        )
    ]
)
