import fcntl, os, pty, select, struct, subprocess, sys, termios, time

def _ctty():
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass

class Harness:
    def __init__(self, cmd, env=None, name="harness", cols=100, rows=30):
        self.name = name
        self.buf = b""
        self.done = False
        env2 = os.environ.copy()
        if env:
            env2.update(env)
        env2["TERM"] = "xterm-256color"
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave,
                                     env=env2, close_fds=True, start_new_session=True,
                                     preexec_fn=_ctty)
        os.close(slave)
        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass

    def read(self, timeout=0.2):
        t0 = time.time()
        while time.time() - t0 < timeout:
            r, _, _ = select.select([self.master], [], [], 0.05)
            if r:
                try:
                    data = os.read(self.master, 65536)
                except OSError:
                    self.done = True
                    break
                if not data:
                    self.done = True
                    break
                self.buf += data
                if b"\x1b[6n" in data:
                    try:
                        os.write(self.master, b"\x1b[1;1R")
                    except OSError:
                        pass
        return self.buf

    def send(self, text, delay=0.3):
        for ch in text:
            os.write(self.master, ch.encode())
            time.sleep(delay)
        time.sleep(0.2)

    def wait_for(self, text, timeout=30, silent=False):
        t0 = time.time()
        needle = text.encode()
        while time.time() - t0 < timeout:
            self.read(0.3)
            if needle in self.buf:
                if not silent:
                    print(f"  [PASS] saw '{text}' ({time.time()-t0:.1f}s)")
                return True
            if self.done:
                print(f"  [FAIL] process exited before '{text}'")
                self.tail()
                return False
        print(f"  [FAIL] timeout {timeout}s waiting for '{text}'")
        self.tail()
        return False

    def wait_new(self, text, timeout=30, silent=False):
        t0 = time.time()
        pos = len(self.buf)
        needle = text.encode()
        while time.time() - t0 < timeout:
            self.read(0.3)
            if needle in self.buf[pos:]:
                if not silent:
                    print(f"  [PASS] new '{text}' ({time.time()-t0:.1f}s)")
                return True
            if self.done:
                print(f"  [FAIL] process exited before new '{text}'")
                self.tail()
                return False
        print(f"  [FAIL] timeout {timeout}s waiting for new '{text}'")
        self.tail()
        return False

    def tail(self, n=15):
        lines = self.buf.decode(errors="replace").splitlines()
        print("  --- last lines ---")
        for ln in lines[-n:]:
            print("  |", repr(ln)[:110])

    def kill(self):
        try:
            os.killpg(os.getpgid(self.proc.pid), 9)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass