/*
 * tune-shifter launcher
 *
 * A minimal C binary that embeds Python and runs `python -m tune_shifter`.
 * Because this binary stays alive as the top-level process (no exec), macOS
 * sets p_comm to "tune-shifter" at exec time and it never changes — so both
 * Activity Monitor and `ps` show the correct name without any userspace tricks.
 *
 * Build (handled by the Homebrew formula):
 *   cc launcher/main.c \
 *     -DVENV_PYTHON='"<venv>/bin/python3"' \
 *     $(python3-config --cflags) \
 *     $(python3-config --ldflags --embed) \
 *     -Wno-deprecated-declarations \
 *     -o tune-shifter
 *
 * VENV_PYTHON tells Python which prefix/site-packages to use so the venv's
 * packages are importable even though the binary lives outside the venv.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>

#ifndef VENV_PYTHON
#error "compile with -DVENV_PYTHON='\"<path-to-venv-python3>\"'"
#endif

int main(int argc, char *argv[]) {
    /* Tell Python which interpreter prefix to use — picks up venv site-packages. */
    wchar_t *program = Py_DecodeLocale(VENV_PYTHON, NULL);
    if (program == NULL) {
        fprintf(stderr, "tune-shifter: Py_DecodeLocale failed\n");
        return 1;
    }
    Py_SetProgramName(program);

    /*
     * Inject "-m tune_shifter" after argv[0] so the effect is:
     *   tune-shifter [user args]  →  python -m tune_shifter [user args]
     *
     * Py_Main takes wchar_t **, so each char * argument must be decoded via
     * Py_DecodeLocale.  We free them with PyMem_RawFree after Py_Main returns.
     */
    int new_argc = argc + 2;
    const char *char_argv[new_argc + 1];
    char_argv[0] = argv[0];
    char_argv[1] = "-m";
    char_argv[2] = "tune_shifter";
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
            fprintf(stderr, "tune-shifter: Py_DecodeLocale failed for arg %d\n", i);
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
