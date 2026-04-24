// kamp-keychain-helper — thin Security.framework wrapper for the kamp daemon.
//
// The daemon (PyInstaller binary) has no bound Info.plist, so macOS kills any
// process that claims keychain-access-groups at exec time. This helper has a
// bound Info.plist (CFBundleIdentifier = com.kamp.app, embedded via __TEXT,
// __info_plist) and the keychain-access-groups entitlement, giving it stable
// access to the Data Protection Keychain across app updates.
//
// Protocol (all via argv + stdin/stdout):
//   get_dpc     <service> <account>        → password on stdout; exit 0/1/2/3
//   set_dpc     <service> <account>        → password on stdin; exit 0/2/3
//   delete_dpc  <service> <account>        → exit 0/1/2/3
//   get_login   <service> <account>        → password on stdout; exit 0/1/2/3
//   delete_login <service> <account>       → exit 0/1/2/3
//
// Exit codes: 0 = ok, 1 = not found, 2 = keychain locked, 3 = error.
// The get_login / delete_login ops target the Login Keychain (no DPC flag, no
// access group) — used once during migration from Login → DPC on first launch.

import Foundation
import Security

private let kGroup = "X6K4L8ZMLS.com.kamp.app"

// MARK: - DPC

private func getDPC(service: String, account: String) -> Int32 {
    var q: [CFString: Any] = [
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
        kSecAttrAccessGroup: kGroup,
        kSecUseDataProtectionKeychain: true,
        kSecMatchLimit: kSecMatchLimitOne,
        kSecReturnData: true,
    ]
    var item: CFTypeRef?
    let st = SecItemCopyMatching(q as CFDictionary, &item)
    if st == errSecItemNotFound { return 1 }
    if st == errSecInteractionNotAllowed || st == errSecAuthFailed { return 2 }
    guard st == errSecSuccess,
          let data = item as? Data,
          let password = String(data: data, encoding: .utf8) else {
        fputs("kamp-keychain-helper: get_dpc OSStatus \(st)\n", stderr)
        return 3
    }
    print(password, terminator: "")
    return 0
}

private func setDPC(service: String, account: String, password: String) -> Int32 {
    guard let data = password.data(using: .utf8) else { return 3 }

    let findQ: [CFString: Any] = [
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
        kSecAttrAccessGroup: kGroup,
        kSecUseDataProtectionKeychain: true,
    ]
    var st = SecItemUpdate(findQ as CFDictionary, [kSecValueData: data] as CFDictionary)

    if st == errSecItemNotFound {
        let addQ: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecAttrAccessGroup: kGroup,
            kSecUseDataProtectionKeychain: true,
            kSecValueData: data,
            // AfterFirstUnlock: daemon can access credentials after login even
            // when the screen is locked (e.g. launchd-managed background process).
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlock,
        ]
        st = SecItemAdd(addQ as CFDictionary, nil)
    }

    if st == errSecSuccess { return 0 }
    if st == errSecInteractionNotAllowed || st == errSecAuthFailed { return 2 }
    fputs("kamp-keychain-helper: set_dpc OSStatus \(st)\n", stderr)
    return 3
}

private func deleteDPC(service: String, account: String) -> Int32 {
    let q: [CFString: Any] = [
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
        kSecAttrAccessGroup: kGroup,
        kSecUseDataProtectionKeychain: true,
    ]
    let st = SecItemDelete(q as CFDictionary)
    if st == errSecSuccess { return 0 }
    if st == errSecItemNotFound { return 1 }
    if st == errSecInteractionNotAllowed || st == errSecAuthFailed { return 2 }
    fputs("kamp-keychain-helper: delete_dpc OSStatus \(st)\n", stderr)
    return 3
}

// MARK: - Login Keychain (migration only)

private func getLogin(service: String, account: String) -> Int32 {
    let q: [CFString: Any] = [
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
        kSecMatchLimit: kSecMatchLimitOne,
        kSecReturnData: true,
    ]
    var item: CFTypeRef?
    let st = SecItemCopyMatching(q as CFDictionary, &item)
    if st == errSecItemNotFound { return 1 }
    if st == errSecInteractionNotAllowed || st == errSecAuthFailed { return 2 }
    guard st == errSecSuccess,
          let data = item as? Data,
          let password = String(data: data, encoding: .utf8) else {
        fputs("kamp-keychain-helper: get_login OSStatus \(st)\n", stderr)
        return 3
    }
    print(password, terminator: "")
    return 0
}

private func deleteLogin(service: String, account: String) -> Int32 {
    let q: [CFString: Any] = [
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
    ]
    let st = SecItemDelete(q as CFDictionary)
    if st == errSecSuccess { return 0 }
    if st == errSecItemNotFound { return 1 }
    if st == errSecInteractionNotAllowed || st == errSecAuthFailed { return 2 }
    fputs("kamp-keychain-helper: delete_login OSStatus \(st)\n", stderr)
    return 3
}

// MARK: - Entry point

let args = CommandLine.arguments
guard args.count >= 4 else {
    fputs("usage: kamp-keychain-helper <op> <service> <account>\n", stderr)
    fputs("  ops: get_dpc set_dpc delete_dpc get_login delete_login\n", stderr)
    exit(3)
}

let op = args[1]
let service = args[2]
let account = args[3]

switch op {
case "get_dpc":
    exit(getDPC(service: service, account: account))
case "set_dpc":
    guard let password = readLine(strippingNewline: true) else {
        fputs("kamp-keychain-helper: failed to read password from stdin\n", stderr)
        exit(3)
    }
    exit(setDPC(service: service, account: account, password: password))
case "delete_dpc":
    exit(deleteDPC(service: service, account: account))
case "get_login":
    exit(getLogin(service: service, account: account))
case "delete_login":
    exit(deleteLogin(service: service, account: account))
default:
    fputs("kamp-keychain-helper: unknown op '\(op)'\n", stderr)
    exit(3)
}
