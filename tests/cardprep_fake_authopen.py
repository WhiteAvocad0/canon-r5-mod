"""A stand-in for /usr/libexec/authopen used by the card-preparation tests.

Run as:  python cardprep_fake_authopen.py MODE -stdoutpipe -o FLAGS PATH

Like authopen with -stdoutpipe, it opens PATH with the numeric open(2) FLAGS and sends
the descriptor to its parent over stdout, which must be a UNIX socket, using SCM_RIGHTS.
MODE makes it misbehave on purpose:
  ok         send the descriptor
  cancel     exit 1 as if the password window was cancelled (sends nothing)
  error      exit 1 with an error message (sends nothing)
  nofd       exit 0 without sending anything
  two        send two descriptors
  rdonly     ignore FLAGS and open read-only
  other:FILE send a descriptor of FILE instead of PATH
"""
import os
import socket
import sys


def main() -> int:
    mode, args = sys.argv[1], sys.argv[2:]
    if len(args) != 4 or args[0] != "-stdoutpipe" or args[1] != "-o" or not args[2].isdigit():
        sys.stderr.write("usage: authopen [-stdoutpipe] -o flags filename\n")
        return 2
    flags, path = int(args[2]), args[3]
    if mode == "cancel":
        sys.stderr.write("authopen: AuthorizationCopyRights failed: -60006 (user canceled)\n")
        return 1
    if mode == "error":
        sys.stderr.write(f"authopen: open {path}: Operation not permitted\n")
        return 1
    if mode == "nofd":
        return 0
    if mode == "rdonly":
        flags = os.O_RDONLY
    if mode.startswith("other:"):
        path = mode[len("other:"):]
    fd = os.open(path, flags)
    fds = [fd, os.dup(fd)] if mode == "two" else [fd]
    socket.send_fds(socket.socket(fileno=1), [b"\0"], fds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
