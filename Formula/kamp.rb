# This formula lives in the homebrew-kamp tap repo (Formula/kamp.rb).
# A copy is kept here as source of truth. The release workflow patches `url` and
# `sha256` in the tap repo automatically on each tagged release.
#
# One-time tap setup:
#   brew tap GITHUB_USERNAME/kamp
#   brew install kamp
#
# (Requires a GitHub repo named `homebrew-kamp` containing this file.)

class Kamp < Formula
  desc "Automated audio library ingest daemon for Bandcamp downloads"
  homepage "https://github.com/eightyeighteyes/kamp"
  url "https://github.com/eightyeighteyes/kamp/releases/download/v0.1.0/kamp_daemon-0.1.0.tar.gz"
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

    # Compile a native launcher binary so macOS sets p_comm to "kamp"
    # at exec time.  A symlink to the Python shebang script would leave p_comm
    # as "python3.11", which shows up in Activity Monitor.  The compiled binary
    # embeds Python (Py_SetProgramName + Py_Main) and stays alive as the
    # top-level process, so the kernel-level name is always "kamp".
    venv_python = venv/"bin/python3"
    cflags  = Utils.safe_popen_read(venv_python, "-c",
                "import sysconfig; print(sysconfig.get_config_var('CFLAGS') or '')").chomp
    ldflags = Utils.safe_popen_read(venv_python, "-c",
                "import sysconfig; print(sysconfig.get_config_var('LDFLAGS') or '')").chomp
    include_dir = Utils.safe_popen_read(venv_python, "-c",
                "import sysconfig; print(sysconfig.get_path('include'))").chomp
    lib_dir = Utils.safe_popen_read(venv_python, "-c",
                "import sysconfig; print(sysconfig.get_config_var('LIBDIR') or '')").chomp
    py_ver = Utils.safe_popen_read(venv_python, "-c",
                "import sysconfig; print(sysconfig.get_config_var('LDVERSION') or '')").chomp
    # Compile into buildpath first; bin/ doesn't exist until bin.install creates it.
    system ENV.cc,
           buildpath/"launcher/main.c",
           "-DVENV_PYTHON=\"#{venv_python}\"",
           "-I#{include_dir}",
           *cflags.split,
           "-L#{lib_dir}", "-lpython#{py_ver}",
           *ldflags.split,
           "-Wno-deprecated-declarations",
           "-o", buildpath/"kamp"
    bin.install buildpath/"kamp"

    zsh_completion.install "completions/_kamp"
    (share/"kamp").install "USAGE.md"
  end

  def caveats
    usage = (share/"kamp"/"USAGE.md").read
    <<~EOS
      #{usage}
      Bandcamp auto-download requires Playwright browser binaries.
      Run this once after install:

        #{prefix}/venv/bin/playwright install chromium

    EOS
  end

  test do
    system bin/"kamp", "--help"
  end
end
