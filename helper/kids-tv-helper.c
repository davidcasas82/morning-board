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
 * Usage: kids-tv-helper KIDS_TV_SCRIPT [kids-tv.py args...]
 * KIDS_TV_SCRIPT is fixed when the app is built (clang -DKIDS_TV_SCRIPT=...,
 * see scripts/build-kids-tv-helper.sh): the absolute path of the installed
 * kids-tv.py. Any other first argument is refused, so the app's Local Network
 * permission covers that one script and nothing else.
 * Every PYTHON* environment variable is removed before Python starts, so
 * PYTHONPATH, PYTHONSTARTUP and the like cannot load other code into it.
 * Exit status: the child's status; 128+N if the child died of signal N;
 * 64 for bad usage; 71 if Python could not be started.
 * SIGTERM, SIGINT and SIGHUP are passed on to the child. They are blocked
 * before their handlers are installed and stay blocked until the child's pid
 * is known, so one that arrives any time before then is held and passed on,
 * rather than killing this launcher with no child started or being dropped by
 * a handler with no child to send it to. The child starts with an empty
 * signal mask and their default actions.
 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef KIDS_TV_SCRIPT
#error "build with -DKIDS_TV_SCRIPT='\"/absolute/path/to/kids-tv.py\"' (scripts/build-kids-tv-helper.sh)"
#endif

extern char **environ;

static const char PYTHON[] = "/usr/bin/python3";
static const char SCRIPT[] = KIDS_TV_SCRIPT;
static volatile sig_atomic_t child = 0;

static void forward(int sig) {
    if (child > 0) kill((pid_t)child, sig);
}

static int usage(const char *why) {
    fprintf(stderr, "kids-tv-helper: %s\nusage: kids-tv-helper %s [args...]\n", why, SCRIPT);
    return 64;
}

/* environ without the PYTHON* variables. */
static char **clean_environ(void) {
    size_t n = 0;
    while (environ[n] != NULL) n++;
    char **env = calloc(n + 1, sizeof *env);
    if (env == NULL) return NULL;
    size_t k = 0;
    for (size_t i = 0; i < n; i++)
        if (strncmp(environ[i], "PYTHON", 6) != 0) env[k++] = environ[i];
    env[k] = NULL;
    return env;
}

int main(int argc, char **argv) {
    if (argc < 2) return usage("no script given");
    if (strcmp(argv[1], SCRIPT) != 0) return usage("this app runs only the kids-tv.py it was built for");

    char **args = calloc((size_t)argc + 1, sizeof *args);
    char **env = clean_environ();
    if (args == NULL || env == NULL) return 71;
    args[0] = (char *)PYTHON;
    args[1] = (char *)SCRIPT;
    for (int i = 2; i < argc; i++) args[i] = argv[i];
    args[argc] = NULL;

    sigset_t forwarded, previous, empty;
    sigemptyset(&forwarded);
    sigaddset(&forwarded, SIGTERM);
    sigaddset(&forwarded, SIGINT);
    sigaddset(&forwarded, SIGHUP);
    sigemptyset(&empty);

    /* Held from here until child is set, then passed on by forward(). */
    sigprocmask(SIG_BLOCK, &forwarded, &previous);
#ifdef KIDS_TV_TEST_SIGNAL_BEFORE_HANDLERS
    raise(KIDS_TV_TEST_SIGNAL_BEFORE_HANDLERS); /* test builds only: a signal before the handlers exist */
#endif
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    posix_spawnattr_t attr;
    if (posix_spawnattr_init(&attr) != 0) return 71;
    posix_spawnattr_setsigmask(&attr, &empty);
    posix_spawnattr_setsigdefault(&attr, &forwarded);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF);

#ifdef KIDS_TV_TEST_SIGNAL_IN_SPAWN_WINDOW
    raise(KIDS_TV_TEST_SIGNAL_IN_SPAWN_WINDOW); /* test builds only: a signal before the child's pid is known */
#endif
    pid_t pid;
    int rc = posix_spawn(&pid, PYTHON, NULL, &attr, args, env);
    posix_spawnattr_destroy(&attr);
    if (rc != 0) {
        sigprocmask(SIG_SETMASK, &previous, NULL);
        fprintf(stderr, "kids-tv-helper: cannot start %s: %s\n", PYTHON, strerror(rc));
        return 71;
    }
    child = pid;
    sigdelset(&previous, SIGTERM);
    sigdelset(&previous, SIGINT);
    sigdelset(&previous, SIGHUP);
    sigprocmask(SIG_SETMASK, &previous, NULL);

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
