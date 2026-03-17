# This formula lives in the homebrew-tune-shifter tap repo (Formula/tune-shifter.rb).
# A copy is kept here as source of truth. The release workflow patches `url` and
# `sha256` in the tap repo automatically on each tagged release.
#
# One-time tap setup:
#   brew tap GITHUB_USERNAME/tune-shifter
#   brew install tune-shifter
#
# (Requires a GitHub repo named `homebrew-tune-shifter` containing this file.)

class TuneShifter < Formula
  desc "Automated audio library ingest daemon for Bandcamp downloads"
  homepage "https://github.com/eightyeighteyes/tune-shifter"
  url "https://github.com/eightyeighteyes/tune-shifter/releases/download/v0.1.0/tune_shifter-0.1.0.tar.gz"
  sha256 "PLACEHOLDER"
  license "GPL-3.0-only"

  depends_on "python@3.11"

  def install
    # Create a virtualenv and pip-install the package with all its dependencies.
    # pip resolves the full dependency tree from PyPI, which is simpler and more
    # reliable than pre-declaring every transitive resource with pinned SHA256s.
    venv = libexec/"venv"
    system Formula["python@3.11"].opt_bin/"python3.11", "-m", "venv", venv
    system venv/"bin/pip", "install", "--upgrade", "pip"
    system venv/"bin/pip", "install", buildpath
    bin.install_symlink venv/"bin/tune-shifter"
    zsh_completion.install "completions/_tune-shifter"
    (share/"tune-shifter").install "USAGE.md"
  end

  def caveats
    usage = (share/"tune-shifter"/"USAGE.md").read
    <<~EOS
      #{usage}
      Bandcamp auto-download requires Playwright browser binaries.
      Run this once after install:

        #{prefix}/venv/bin/playwright install chromium

    EOS
  end

  test do
    system bin/"tune-shifter", "--help"
  end
end
