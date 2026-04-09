# Makefile for kamp build tasks.
# These targets prepare binary assets for the Electron .app bundle.
# The full CI build is handled by .github/workflows/build-app.yml.

.PHONY: fetch-mpv fetch-node build-kamp generate-icon build-app

# ── mpv binary ──────────────────────────────────────────────────────────────
# Copies the Homebrew mpv binary to kamp_ui/resources/mpv so electron-builder
# can include it in the .app bundle via extraResources.
fetch-mpv:
	@echo "→ Installing mpv via Homebrew..."
	brew install mpv
	@mkdir -p kamp_ui/resources
	cp "$$(brew --prefix)/bin/mpv" kamp_ui/resources/mpv
	chmod +x kamp_ui/resources/mpv
	@echo "→ Fetched $$(file kamp_ui/resources/mpv)"

# ── Node.js binary + npm ────────────────────────────────────────────────────
# Downloads the official Node.js LTS binary from nodejs.org (system-dylib-only,
# safe to bundle). The Homebrew node links against Homebrew-specific dylibs and
# must NOT be used — it fails at runtime on machines without Homebrew.
fetch-node:
	bash scripts/fetch_node.sh

# ── PyInstaller bundle ───────────────────────────────────────────────────────
# Freezes kamp_daemon into kamp_ui/resources/kamp/ (onedir bundle).
build-kamp:
	@echo "→ Building PyInstaller bundle..."
	poetry run pyinstaller \
		--distpath kamp_ui/resources \
		--workpath /tmp/pyinstaller-work \
		--clean -y \
		kamp.spec
	@echo "→ Bundle: $$(ls kamp_ui/resources/kamp/kamp)"

# ── App icon ─────────────────────────────────────────────────────────────────
# Renders icon_source.svg → 1024px PNG → .icns via rsvg-convert + iconutil.
# Requires: brew install librsvg
generate-icon:
	@echo "→ Generating icon..."
	brew install librsvg 2>/dev/null || true
	rsvg-convert -w 1024 -h 1024 kamp_ui/resources/icon_source.svg \
		-o kamp_ui/resources/icon_1024.png
	bash scripts/make_icns.sh kamp_ui/resources/icon_1024.png
	@echo "→ Icon: kamp_ui/build/icon.icns"

# ── Full local app build ─────────────────────────────────────────────────────
# Runs all pre-build steps then packages the Electron app.
# Output: kamp_ui/dist/*.dmg
build-app: fetch-mpv fetch-node build-kamp generate-icon
	@echo "→ Building Electron app..."
	cd kamp_ui && npm run build:mac
	@echo "→ Done: $$(ls kamp_ui/dist/*.dmg 2>/dev/null)"
