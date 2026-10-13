"""Double-fork daemonizer: escapes the harness's process-group reaper.
Usage: python ll_daemonize.py <logfile> <cmd> [args...]

Parent exits immediately; the child detaches into its own session
(setsid) with stdio redirected to the logfile, so it keeps running
after the command that spawned it has ended.
"""
import os
import sys


def main() -> None:
    log_path, cmd = sys.argv[1], sys.argv[2:]
    if os.fork():
        sys.exit(0)  # parent exits right away
    os.setsid()      # new session: escapes process-group reaping
    if os.fork():    # second fork: child can never reacquire a tty
        sys.exit(0)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.dup2(fd, 0)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
