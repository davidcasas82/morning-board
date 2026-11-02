/*
 * kids-tv-helper: main executable of MorningBoardTVHelper.app.
 *
 * launchd starts this program; it starts /usr/bin/python3 on kids-tv.py as a
 * CHILD process (posix_spawn, never exec) and waits for it. A child keeps its
 * parent's responsible process, so macOS Local Network privacy attributes the
 * Python process, and anything it runs, to this app bundle instead of to
 * com.apple.python3. An exec would replace this image with Python and lose
 * that identity.
 *
 * Usage: kids-tv-helper /absolute/path/to/kids-tv.py [kids-tv.py args...]
 * It runs nothing but a script named kids-tv.py, given by absolute path.
 * Exit status: the child's status; 128+N if the child died of signal N;
 * 64 for bad usage; 71 if Python could not be started.
 * SIGTERM, SIGINT and SIGHUP are passed on to the child.
 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static const char PYTHON[] = "/usr/bin/python3";
static const char SCRIPT_NAME[] = "kids-tv.py";
static volatile sig_atomic_t child = 0;

static void forward(int sig) {
    if (child > 0) kill((pid_t)child, sig);
}

static int usage(const char *why) {
    fprintf(stderr, "kids-tv-helper: %s\nusage: kids-tv-helper /absolute/path/to/%s [args...]\n", why, SCRIPT_NAME);
    return 64;
}

int main(int argc, char **argv) {
    if (argc < 2) return usage("no script given");
    const char *script = argv[1];
    if (script[0] != '/') return usage("the script path must be absolute");
    const char *base = strrchr(script, '/') + 1;
    if (strcmp(base, SCRIPT_NAME) != 0) return usage("only kids-tv.py may be run");

    char **args = calloc((size_t)argc + 1, sizeof *args);
    if (args == NULL) return 71;
    args[0] = (char *)PYTHON;
    for (int i = 1; i < argc; i++) args[i] = argv[i];
    args[argc] = NULL;

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    pid_t pid;
    int rc = posix_spawn(&pid, PYTHON, NULL, NULL, args, environ);
    if (rc != 0) {
        fprintf(stderr, "kids-tv-helper: cannot start %s: %s\n", PYTHON, strerror(rc));
        return 71;
    }
    child = pid;

    int status;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) {
            perror("kids-tv-helper: waitpid");
            return 71;
        }
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 1;
}
