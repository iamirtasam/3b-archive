// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "DalalArchive",
    platforms: [.iOS(.v17)],
    products: [
        // xtool bundles this library into the iOS app described by xtool.yml
        .library(name: "DalalArchive", targets: ["DalalArchive"])
    ],
    targets: [
        .target(
            name: "DalalArchive",
            path: "Sources/DalalArchive",
            resources: [
                .process("Fonts")
            ]
        )
    ]
)
