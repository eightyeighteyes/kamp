# macOS Code Signing & Notarization

This guide walks through getting the five values needed to sign and notarize
the Kamp `.app` so macOS accepts it on any machine without a security warning.

**Prerequisites:** An [Apple Developer Program](https://developer.apple.com/programs/) membership ($99/year).
You do not need to publish to the App Store — Developer ID is the outside-the-Store signing track.

---

## What you're collecting

| GitHub Secret | What it is |
|---|---|
| `CSC_LINK` | Your Developer ID certificate, base64-encoded |
| `CSC_KEY_PASSWORD` | The password you choose when exporting that certificate |
| `APPLE_TEAM_ID` | Your 10-character team identifier |
| `APPLE_ID` | The Apple ID email on your developer account |
| `APPLE_APP_SPECIFIC_PASSWORD` | A one-time password for notarization (not your Apple ID password) |

---

## Step 1 — Create a Developer ID Application certificate

This is the certificate that signs the `.app`. You create a signing request on
your Mac, upload it to Apple, and download the resulting certificate.

**1a. Generate a Certificate Signing Request (CSR) on your Mac**

1. Open **Keychain Access** (Spotlight → "Keychain Access")
2. Menu bar: **Keychain Access → Certificate Assistant → Request a Certificate From a Certificate Authority…**
3. Fill in:
   - **User Email Address:** your Apple ID email
   - **Common Name:** anything (e.g. "Kamp Signing")
   - **CA Email Address:** leave blank
   - Select **Saved to disk**
4. Click **Continue** and save the `.certSigningRequest` file somewhere handy

**1b. Create the certificate in the Developer Portal**

1. Go to [developer.apple.com/account](https://developer.apple.com/account) and sign in
2. Click **Certificates, IDs & Profiles** in the left sidebar (or the top card)
3. Click the **+** button next to "Certificates"
4. Under **Software**, select **Developer ID Application** — this is the one for distributing outside the App Store
   - (Ignore App Store Distribution, Development, etc.)
5. Click **Continue**
6. Upload the `.certSigningRequest` file you saved in step 1a
7. Click **Continue**, then **Download**

**1c. Install and export the certificate**

1. Double-click the downloaded `.cer` file — it installs into Keychain Access automatically
2. Open **Keychain Access**, select the **login** keychain, and find the certificate
   - It will be named **"Developer ID Application: Your Name (XXXXXXXXXX)"**
3. Right-click it → **Export "Developer ID Application: …"**
4. Save as a `.p12` file (the format defaults to `.p12` — keep it)
5. Choose a strong password when prompted — this is your `CSC_KEY_PASSWORD`

**1d. Base64-encode the .p12 for GitHub**

```bash
base64 -i /path/to/your-certificate.p12 | pbcopy
```

This copies the base64 string to your clipboard. That's your `CSC_LINK`.

---

## Step 2 — Find your Team ID

1. In the Developer Portal, click your name / account icon in the top-right
2. Select **Membership details**
3. Copy the **Team ID** — it's a 10-character alphanumeric string like `AB12CD34EF`

That's your `APPLE_TEAM_ID`.

---

## Step 3 — Create an app-specific password for notarization

Notarization requires submitting the `.app` to Apple's servers. Apple won't
accept your actual Apple ID password for this — you create a dedicated one-time
app password instead.

1. Go to [appleid.apple.com](https://appleid.apple.com) and sign in
2. Under **Sign-In and Security**, click **App-Specific Passwords**
3. Click **Generate an app-specific password**
4. Name it something like "Kamp Notarization"
5. Copy the generated password — it looks like `xxxx-xxxx-xxxx-xxxx`

That's your `APPLE_APP_SPECIFIC_PASSWORD`. Your `APPLE_ID` is the email you used to sign in.

---

## Step 4 — Add the secrets to GitHub

1. Go to your repository on GitHub
2. **Settings → Secrets and variables → Actions**
3. Click **New repository secret** for each of the five values:

| Name | Value |
|---|---|
| `CSC_LINK` | The base64 string from step 1d |
| `CSC_KEY_PASSWORD` | The .p12 export password from step 1c |
| `APPLE_TEAM_ID` | The 10-character ID from step 2 |
| `APPLE_ID` | Your Apple ID email |
| `APPLE_APP_SPECIFIC_PASSWORD` | The app-specific password from step 3 |

---

## Step 5 — Enable signing in the CI workflow

In `.github/workflows/build-app.yml`, uncomment the signing and notarization
environment variables in the **Build DMG** step:

```yaml
      - name: Build DMG
        env:
          CSC_LINK: ${{ secrets.CSC_LINK }}
          CSC_KEY_PASSWORD: ${{ secrets.CSC_KEY_PASSWORD }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_APP_SPECIFIC_PASSWORD: ${{ secrets.APPLE_APP_SPECIFIC_PASSWORD }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
```

And in `kamp_ui/electron-builder.yml`, enable hardened runtime and notarization
under the `mac:` section:

```yaml
mac:
  hardenedRuntime: true
  notarize: true
```

---

## Verification

After a successful signed+notarized build:

- Opening the `.dmg` on any Mac should work without any security dialog
- To confirm: `codesign -dv --verbose=4 Kamp.app` should show your Developer ID
- `spctl -a -v Kamp.app` should print `accepted` (source: Developer ID)
