# Code Signing

How kamp's installers are signed (or not) on each platform.

- [macOS](#macos) — signed and notarized.
- [Windows](#windows-deferred) — currently unsigned; SmartScreen warning expected on first install. Deferred until a revisit trigger fires.

---

## macOS

This section walks through getting the five values needed to sign and notarize
the Kamp `.app` so macOS accepts it on any machine without a security warning.

**Prerequisites:** An [Apple Developer Program](https://developer.apple.com/programs/) membership ($99/year).
You do not need to publish to the App Store — Developer ID is the outside-the-Store signing track.

---

### What you're collecting

| GitHub Secret | What it is |
|---|---|
| `CSC_LINK` | Your Developer ID certificate, base64-encoded |
| `CSC_KEY_PASSWORD` | The password you choose when exporting that certificate |
| `APPLE_TEAM_ID` | Your 10-character team identifier |
| `APPLE_ID` | The Apple ID email on your developer account |
| `APPLE_APP_SPECIFIC_PASSWORD` | A one-time password for notarization (not your Apple ID password) |

---

### Step 1 — Create a Developer ID Application certificate

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

### Step 2 — Find your Team ID

1. In the Developer Portal, click your name / account icon in the top-right
2. Select **Membership details**
3. Copy the **Team ID** — it's a 10-character alphanumeric string like `AB12CD34EF`

That's your `APPLE_TEAM_ID`.

---

### Step 3 — Create an app-specific password for notarization

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

### Step 4 — Add the secrets to GitHub

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

### Step 5 — Enable signing in the CI workflow

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

### Verification

After a successful signed+notarized build:

- Opening the `.dmg` on any Mac should work without any security dialog
- To confirm: `codesign -dv --verbose=4 Kamp.app` should show your Developer ID
- `spctl -a -v Kamp.app` should print `accepted` (source: Developer ID)

---

## Windows

kamp's Windows installer is signed via **Azure Trusted Signing** — Microsoft's cloud HSM-backed
code-signing service. The cert lives in Azure's HSM; CI authenticates as a service principal and
calls the signing API during each build. This gives immediate SmartScreen reputation because
Microsoft is the CA.

**Why not the same `.p12`-in-Secrets approach as macOS?** As of June 2023 (CA/B Forum baseline),
all new Windows code-signing certificates must have private keys in FIPS 140-2 Level 2 hardware
(token or cloud HSM). The base64-encoded `.p12` pattern cannot be used for new Windows certs.

**Prerequisites:** An Azure subscription with billing configured, and a US or Canadian identity
(required for the individual-developer track). Non-US/CA developers must use the Organization
track, which requires a registered business.

---

### What you're collecting

| GitHub Secret | What it is |
|---|---|
| `AZURE_TENANT_ID` | Microsoft Entra ID (Azure AD) tenant / directory ID |
| `AZURE_CLIENT_ID` | Service principal (App Registration) client ID |
| `AZURE_CLIENT_SECRET` | Service principal client secret |
| `AZURE_CODE_SIGNING_ENDPOINT` | Regional endpoint URL for your Trusted Signing account |
| `AZURE_CODE_SIGNING_ACCOUNT_NAME` | Name of your Trusted Signing account resource |
| `AZURE_CERT_PROFILE_NAME` | Name of your certificate profile |
| `AZURE_PUBLISHER_NAME` | Your verified publisher name — the CN that appears in signed binaries; available after identity verification completes |

---

### Step 1 — Create an Azure subscription

If you don't already have one, go to [portal.azure.com](https://portal.azure.com) and sign in with
a Microsoft account (personal or work/school). Set up a subscription with a billing method — the
free tier does not cover Trusted Signing.

---

### Step 2 — Register the Microsoft.CodeSigning resource provider

This is a one-time step per subscription before Trusted Signing resources can be created.

1. In the Azure portal, navigate to **Subscriptions** and select your subscription
2. Click **Resource providers** in the left sidebar
3. Search for `Microsoft.CodeSigning`
4. Click **Register** — takes about a minute

---

### Step 3 — Create a Trusted Signing account

1. In the Azure portal, click **+ Create a resource** and search for **Trusted Signing**
2. Select **Trusted Signing** and click **Create**
3. Fill in:
   - **Resource group:** create a new one (e.g. `kamp-signing`) or use an existing group
   - **Account name:** unique within your region (e.g. `kamp-signing`) — this is your `AZURE_CODE_SIGNING_ACCOUNT_NAME`
   - **Region:** choose a supported region; note the endpoint for your choice:

     | Region | Endpoint |
     |---|---|
     | East US | `https://eus.codesigning.azure.net/` |
     | West US 2 | `https://wus2.codesigning.azure.net/` |
     | West Central US | `https://wcus.codesigning.azure.net/` |
     | North Europe | `https://neu.codesigning.azure.net/` |
     | West Europe | `https://weu.codesigning.azure.net/` |
     | East Asia | `https://ea.codesigning.azure.net/` |

   - **SKU:** Basic (~$10/month)
4. Click **Review + create**, then **Create**

The endpoint URL you chose is your `AZURE_CODE_SIGNING_ENDPOINT`.

---

### Step 4 — Complete identity verification

Microsoft verifies your identity before issuing a certificate. This is what grants SmartScreen
reputation — the cert CN comes from the verified identity, not from a self-declared string.

**4a. Grant yourself the Identity Verifier role**

Before you can submit an identity validation request, your own Azure user account needs the
**Trusted Signing Identity Verifier** role on the account. (This is separate from the service
principal role set up in step 7.)

1. In your Trusted Signing account, click **Access control (IAM)**
2. Click **+ Add → Add role assignment**
3. Search for and select **Trusted Signing Identity Verifier**
4. Click **Next**, then **+ Select members** — select your own Azure user account (not the service principal you'll create later)
5. Click **Review + assign** twice
6. Wait ~2 minutes, then refresh before proceeding — the portal warning will clear once the role propagates

**4b. Submit the identity validation request**

1. Navigate to your Trusted Signing account in the Azure portal
2. Under **Settings**, click **Identity validation**
3. Click **+ Add** and choose:
   - **Individual publisher** — for individuals signing under their own name; requires government-issued photo ID; US/Canada only
   - **Organization** — for signing under a business name; requires business registration documents
4. Fill in your legal name, address, country, and email exactly as on your government ID
5. Upload the requested ID document when prompted
6. Submit — verification typically takes **1–5 business days**

**After verification,** return to the Identity validation page. The verified publisher name shown
there (the CN that will appear in signed binaries and in the SmartScreen prompt) is your
`AZURE_PUBLISHER_NAME`.

---

### Step 5 — Create a certificate profile

1. In your Trusted Signing account, click **Certificate profiles** under **Objects**
2. Click **+ Add**
3. Fill in:
   - **Profile name:** e.g. `kamp-public` — this is your `AZURE_CERT_PROFILE_NAME`
   - **Profile type:** `PublicTrust` — required for external distribution; gives immediate SmartScreen reputation because Microsoft is the CA
   - **Identity validation:** select the identity you verified in step 4
4. Click **Create**

---

### Step 6 — Create a service principal for CI

CI authenticates to Azure using a service principal (an App Registration). This is the machine
identity that GitHub Actions uses when calling the signing API.

1. In the Azure portal, navigate to **Microsoft Entra ID** (search in the top bar)
2. Click **App registrations** → **+ New registration**
3. Fill in:
   - **Name:** e.g. `kamp-ci-signer`
   - **Supported account types:** Single tenant
4. Click **Register**
5. On the overview page, note:
   - **Application (client) ID** → `AZURE_CLIENT_ID`
   - **Directory (tenant) ID** → `AZURE_TENANT_ID`

**Create a client secret:**

1. In the app registration, click **Certificates & secrets** → **New client secret**
2. Set a description (e.g. "GitHub Actions") and expiry (24 months max)
3. Click **Add** — **copy the secret Value immediately**, it is shown only once
4. This is your `AZURE_CLIENT_SECRET`

---

### Step 7 — Grant the service principal signing permission

1. Navigate back to your **Trusted Signing account** in the Azure portal
2. Click **Access control (IAM)** in the left sidebar
3. Click **+ Add** → **Add role assignment**
4. On the **Role** tab, search for and select **Trusted Signing Certificate Profile Signer**
5. Click **Next**, then under **Members** click **+ Select members**
6. Search for your app registration (e.g. `kamp-ci-signer`) and select it
7. Click **Review + assign** twice to confirm

---

### Step 8 — Add the secrets to GitHub

1. Go to your repository on GitHub
2. **Settings → Secrets and variables → Actions**
3. Click **New repository secret** for each of the seven values:

| Name | Value |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID from step 6 |
| `AZURE_CLIENT_ID` | Application (client) ID from step 6 |
| `AZURE_CLIENT_SECRET` | Client secret value from step 6 |
| `AZURE_CODE_SIGNING_ENDPOINT` | Regional endpoint URL from step 3 |
| `AZURE_CODE_SIGNING_ACCOUNT_NAME` | Account name from step 3 |
| `AZURE_CERT_PROFILE_NAME` | Profile name from step 5 |
| `AZURE_PUBLISHER_NAME` | Verified publisher name from step 4 |

---

### Step 9 — CI wiring (already in the repo)

`kamp_ui/electron-builder.yml` already has the `azureSignOptions` block in the `win:` section,
and `.github/workflows/build-app.yml` already passes all seven secrets as env vars to the
**Build NSIS installer** step. Signing is active in CI as soon as the secrets are populated.

---

### Verification

After a successful signed build:

- Install on any Windows machine — SmartScreen should either not appear or show your publisher
  name (not "unknown publisher")
- Verify from PowerShell:

  ```powershell
  Get-AuthenticodeSignature .\kamp-<version>-setup.exe
  ```

  `Status` should be `Valid` and `SignerCertificate` should show your publisher name

- The installed executables under `%LOCALAPPDATA%\Programs\Kamp\` will also be individually signed
