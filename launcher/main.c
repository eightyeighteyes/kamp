/*
 * kamp launcher
 *
 * A minimal C binary that embeds Python and runs `python -m kamp_daemon`.
 * Because this binary stays alive as the top-level process (no exec), macOS
 * sets p_comm to "kamp" at exec time and it never changes — so both
 * Activity Monitor and `ps` show the correct name without any userspace tricks.
 *
 * Build (handled by the Homebrew formula):
 *   cc launcher/main.c \
 *     -DVENV_PYTHON='"<venv>/bin/python3"' \
 *     $(python3-config --cflags) \
 *     $(python3-config --ldflags --embed) \
 *     -Wno-deprecated-declarations \
 *     -o kamp
 *
 * VENV_PYTHON tells Python which prefix/site-packages to use so the venv's
 * packages are importable even though the binary lives outside the venv.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>

/*
 * Embed an Info.plist so UNUserNotificationCenter can find CFBundleIdentifier.
 * Without this, currentNotificationCenter() crashes with
 * "bundleProxyForCurrentProcess is nil" on macOS 14+ because the binary has
 * no bundle identity.  The launcher stays alive as the top-level process (no
 * exec), so NSBundle.mainBundle() IS this binary's bundle — Python code
 * running inside Py_Main() sees the same bundle identifier.
 */
__attribute__((used, section("__TEXT,__info_plist")))
static const char _info_plist[] =
    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
    "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\""
    " \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">"
    "<plist version=\"1.0\"><dict>"
    "<key>CFBundleIdentifier</key><string>com.kamp</string>"
    "<key>CFBundleName</key><string>kamp</string>"
    "<key>CFBundleVersion</key><string>1</string>"
    "</dict></plist>";

#ifndef VENV_PYTHON
#error "compile with -DVENV_PYTHON='\"<path-to-venv-python3>\"'"
#endif

int main(int argc, char *argv[]) {
    /* Tell Python which interpreter prefix to use — picks up venv site-packages. */
    wchar_t *program = Py_DecodeLocale(VENV_PYTHON, NULL);
    if (program == NULL) {
        fprintf(stderr, "kamp: Py_DecodeLocale failed\n");
        return 1;
    }
    Py_SetProgramName(program);

    /*
     * Inject "-m kamp_daemon" after argv[0] so the effect is:
     *   kamp [user args]  →  python -m kamp_daemon [user args]
     *
     * Py_Main takes wchar_t **, so each char * argument must be decoded via
     * Py_DecodeLocale.  We free them with PyMem_RawFree after Py_Main returns.
     */
    int new_argc = argc + 2;
    const char *char_argv[new_argc + 1];
    char_argv[0] = argv[0];
    char_argv[1] = "-m";
    char_argv[2] = "kamp_daemon";
    for (int i = 1; i < argc; i++) {
        char_argv[i + 2] = argv[i];
    }
    char_argv[new_argc] = NULL;

    wchar_t **w_argv = malloc((size_t)(new_argc + 1) * sizeof(wchar_t *));
    if (w_argv == NULL) {
        PyMem_RawFree(program);
        return 1;
    }
    for (int i = 0; i < new_argc; i++) {
        w_argv[i] = Py_DecodeLocale(char_argv[i], NULL);
        if (w_argv[i] == NULL) {
            fprintf(stderr, "kamp: Py_DecodeLocale failed for arg %d\n", i);
            for (int j = 0; j < i; j++) PyMem_RawFree(w_argv[j]);
            free(w_argv);
            PyMem_RawFree(program);
            return 1;
        }
    }
    w_argv[new_argc] = NULL;

    int rc = Py_Main(new_argc, w_argv);

    for (int i = 0; i < new_argc; i++) PyMem_RawFree(w_argv[i]);
    free(w_argv);
    PyMem_RawFree(program);
    return rc;
}
