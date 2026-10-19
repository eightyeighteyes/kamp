// Replaces a DMG's /Applications symlink with a Finder alias carrying a custom
// icon, so the install target can look like a record player instead of a blue
// folder (KAMP-675).
//
// Build:  swiftc -O -o dmg-target-icon kamp_ui/native/DmgTargetIcon.swift
// Run:    dmg-target-icon <target-dir> <alias-path> <icns-path>
//
// == Why an alias, and why this is the only route ==
//
// electron-builder's DmgContent exposes no per-item icon, so the icon has to be
// applied after the image is built. Four things were measured before settling
// on this:
//
//   * NSWorkspace.setIcon on the Applications SYMLINK resolves the link and
//     decorates the real /Applications instead — it returns false and leaves an
//     Icon\r file in the user's own folder.
//   * macOS refuses com.apple.ResourceFork on a symlink outright (errno 1), on
//     HFS+ as well as APFS, so a symlink cannot carry a custom icon by any
//     route. Separately, Finder draws a symlink using its TARGET's icon, so the
//     blue folder is /Applications' own icon rather than a property of the link.
//   * An alias file can hold one, and URL.bookmarkData(.suitableForBookmarkFile)
//     mints one with no Finder and no AppleEvents — so this is CI-safe, unlike
//     the osascript route, which needs a live Finder and times out headlessly.
//   * An alias with a FULLY TRANSPARENT icon is not a drop target: Finder has no
//     painted pixels to hit-test, and dragging onto it silently does nothing.
//     A visible icon drops correctly. So the icon passed here must be opaque —
//     do not "hide" the target by making it clear.
//
// The alias records the target's volume identity as well as its path, but
// resolution falls back to the path when that identity does not match, which is
// what makes an alias minted on a build machine work on someone else's Mac.
import AppKit

func fail(_ message: String) -> Never {
    FileHandle.standardError.write("dmg-target-icon: \(message)\n".data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 4 else {
    fail("usage: dmg-target-icon <target-dir> <alias-path> <icns-path>")
}
let target = URL(fileURLWithPath: args[1])
let aliasPath = URL(fileURLWithPath: args[2])
let icnsPath = args[3]

var isDir: ObjCBool = false
guard FileManager.default.fileExists(atPath: target.path, isDirectory: &isDir), isDir.boolValue else {
    fail("alias target \(target.path) does not exist or is not a directory")
}
guard let image = NSImage(contentsOfFile: icnsPath) else {
    fail("could not load icon \(icnsPath)")
}

do {
    let data = try target.bookmarkData(
        options: .suitableForBookmarkFile,
        includingResourceValuesForKeys: nil,
        relativeTo: nil
    )
    try URL.writeBookmarkData(data, to: aliasPath)
} catch {
    fail("could not write alias at \(aliasPath.path): \(error)")
}

// Must not be a symlink, or setIcon would follow it to the real target.
if (try? FileManager.default.destinationOfSymbolicLink(atPath: aliasPath.path)) != nil {
    fail("\(aliasPath.path) came out as a symlink, not an alias")
}

guard NSWorkspace.shared.setIcon(image, forFile: aliasPath.path, options: []) else {
    fail("setIcon refused \(aliasPath.path)")
}

// Prove the alias still points where it should before declaring success: a
// silently broken alias is an installer whose drop target does nothing.
var stale = false
guard
    let written = try? URL.bookmarkData(withContentsOf: aliasPath),
    let resolved = try? URL(
        resolvingBookmarkData: written,
        options: [.withoutUI, .withoutMounting],
        relativeTo: nil,
        bookmarkDataIsStale: &stale
    )
else {
    fail("alias at \(aliasPath.path) does not resolve after writing")
}
guard resolved.path == target.path else {
    fail("alias resolves to \(resolved.path), expected \(target.path)")
}

print("dmg-target-icon: \(aliasPath.lastPathComponent) -> \(resolved.path) with custom icon")
