"""Tryb terminala: program działa „na żywo” i czeka, aż wpiszesz dane (jak w konsoli), zamiast dostać całe wejście
na starcie. Na macOS/Linuksie program dostaje pseudoterminal — bufory zachowują się jak w prawdziwym terminalu
(np. `cin.tie(0)` opóźnia wypisywanie do końca programu albo `endl`/`flush`, tak samo jak w konsoli). Na Windows: potoki.

Trasy:
  POST /api/term/start {code, lang}       → {started, id, compile}
  POST /api/term/write {id, data, eof?}   → dopisuje do stdin (eof = Ctrl+D, koniec danych)
  GET  /api/term/read?id=…&since=n        → nowe fragmenty [{k: out|err|in, t}], done, exit_code, verdict, time (long-poll ≤ 0,8 s)
  POST /api/term/kill {id}
"""
from __future__ import annotations

import codecs
import os
import subprocess
import threading
import time
import uuid

from ..core import runner
from ..server_api import ApiError

MAX_SESSIONS = 4
MAX_OUT = 2_000_000          # znaków wyjścia na sesję — potem program jest zatrzymywany
IDLE_KILL = 15 * 60          # nieużywana sesja ginie po 15 minutach
POLL_WAIT = 0.8

_SESSIONS: dict[str, "Session"] = {}
_LOCK = threading.Lock()


class Session:
    def __init__(self, cmd: list[str], env: dict):
        self.id = uuid.uuid4().hex[:12]
        self.chunks: list[dict] = []
        self.size = 0
        self.rev = 0                 # rośnie przy każdej zmianie — long-poll czeka na nowszą wersję
        self.cond = threading.Condition()
        self.done = False
        self.exit_code: int | None = None
        self.verdict = None
        self.signal = None
        self.truncated = False
        self.t0 = time.perf_counter()
        self.wall = 0.0
        self.last = time.time()
        self.master = None
        self._readers: list[threading.Thread] = []
        if os.name == "posix":
            import pty
            import termios
            m, s = pty.openpty()
            try:
                attrs = termios.tcgetattr(s)
                attrs[1] &= ~termios.OPOST                      # bez zamiany \n → \r\n
                attrs[3] &= ~(termios.ECHO | termios.ECHONL)    # bez echa — wpisany tekst pokazuje interfejs
                termios.tcsetattr(s, termios.TCSANOW, attrs)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.proc = subprocess.Popen(cmd, stdin=s, stdout=s, stderr=subprocess.PIPE, env=env,
                                             start_new_session=True, close_fds=True)
            finally:
                os.close(s)
            self.master = m
            self._reader(m, "out")
        else:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         env=env, **runner._no_window())
            self._reader(self.proc.stdout.fileno(), "out")
        self._reader(self.proc.stderr.fileno(), "err")
        threading.Thread(target=self._wait, daemon=True).start()

    # ------------------------------------------------------------------ wyjście programu
    def _reader(self, fd: int, kind: str) -> None:
        t = threading.Thread(target=self._read_loop, args=(fd, kind), daemon=True)
        self._readers.append(t)
        t.start()

    def _read_loop(self, fd: int, kind: str) -> None:
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            try:
                b = os.read(fd, 65536)
            except OSError:          # Linux: EIO na pseudoterminalu po zakończeniu programu
                b = b""
            if not b:
                tail = dec.decode(b"", final=True)
                if tail:
                    self._push(kind, tail)
                break
            self._push(kind, dec.decode(b))

    def _push(self, kind: str, text: str) -> None:
        if not text:
            return
        with self.cond:
            if self.size >= MAX_OUT:
                if not self.truncated:
                    self.truncated = True
                    self.chunks.append({"k": "err", "t": "\n[OI Forge] Za dużo wyjścia (ponad 2 mln znaków) — zatrzymuję program.\n"})
                    self._kill()
            else:
                # sklejamy z ostatnim fragmentem tego samego rodzaju (klient odbiera go ponownie i podmienia)
                if self.chunks and self.chunks[-1]["k"] == kind and kind != "in" and len(self.chunks[-1]["t"]) < 8192:
                    self.chunks[-1] = {"k": kind, "t": self.chunks[-1]["t"] + text}
                else:
                    self.chunks.append({"k": kind, "t": text})
                self.size += len(text)
            self.rev += 1
            self.cond.notify_all()

    def _wait(self) -> None:
        code = self.proc.wait()
        for t in self._readers:
            t.join(1.0)
        for f in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if f:
                    f.close()
            except OSError:
                pass
        with self.cond:
            self.wall = time.perf_counter() - self.t0
            self.exit_code = code
            self.signal = runner.signal_name(code) if code else None
            self.verdict = "OK" if code == 0 else "RE"
            self.done = True
            self.rev += 1
            if self.master is not None:
                try:
                    os.close(self.master)
                except OSError:
                    pass
                self.master = None
            self.cond.notify_all()

    # ------------------------------------------------------------------ wejście
    def write(self, data: str, eof: bool = False) -> None:
        self.last = time.time()
        if self.done:
            return
        if data:
            with self.cond:
                self.chunks.append({"k": "in", "t": data})
                self.rev += 1
                self.cond.notify_all()
            raw = data.encode("utf-8", "replace")
            try:
                if self.master is not None:
                    # tryb kanoniczny terminala: linia trafia do programu po \n; długie dane piszemy kawałkami
                    for i in range(0, len(raw), 512):
                        os.write(self.master, raw[i:i + 512])
                else:
                    self.proc.stdin.write(raw)
                    self.proc.stdin.flush()
            except OSError:
                pass
        if eof:
            try:
                if self.master is not None:
                    os.write(self.master, b"\x04")       # Ctrl+D na początku linii = koniec danych
                else:
                    self.proc.stdin.close()
            except OSError:
                pass

    def _kill(self) -> None:
        try:
            if os.name == "posix":
                os.killpg(self.proc.pid, 9)
            else:
                self.proc.kill()
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def kill(self) -> None:
        if not self.done:
            with self.cond:
                self.chunks.append({"k": "err", "t": "\n[zatrzymano]\n"})
                self.rev += 1
                self.cond.notify_all()
            self._kill()

    def read(self, since: int, rev: int) -> dict:
        """Fragmenty od `since - 1` (ostatni mógł urosnąć — klient go podmienia). Czeka do 0,8 s na zmianę (rev)."""
        self.last = time.time()
        deadline = time.time() + POLL_WAIT
        with self.cond:
            while self.rev <= rev and not self.done:
                left = deadline - time.time()
                if left <= 0:
                    break
                self.cond.wait(left)
            start = max(0, min(since, len(self.chunks)) - 1)
            return {"start": start, "chunks": self.chunks[start:], "next": len(self.chunks), "rev": self.rev,
                    "done": self.done, "exit_code": self.exit_code, "verdict": self.verdict, "signal": self.signal,
                    "truncated": self.truncated,
                    "time": round(self.wall if self.done else time.perf_counter() - self.t0, 3)}


def _cleanup() -> None:
    now = time.time()
    with _LOCK:
        for sid, s in list(_SESSIONS.items()):
            if now - s.last > IDLE_KILL or (s.done and now - s.last > 120):
                s.kill()
                _SESSIONS.pop(sid, None)
        while len(_SESSIONS) >= MAX_SESSIONS:
            sid = min(_SESSIONS, key=lambda k: _SESSIONS[k].last)
            _SESSIONS.pop(sid).kill()


def _get(sid: str) -> Session:
    s = _SESSIONS.get(str(sid or ""))
    if not s:
        raise ApiError("Ta sesja terminala już nie istnieje — uruchom program ponownie.", status=404)
    return s


def handle_start(body: dict) -> dict:
    code = body.get("code") or ""
    if not code.strip():
        raise ApiError("Brak kodu do uruchomienia.")
    prev = body.get("replace")
    if prev and prev in _SESSIONS:
        with _LOCK:
            _SESSIONS.pop(prev).kill()
    prog = runner.prepare(code, lang=body.get("lang"), mode="release", std=body.get("std") or "c++20")
    comp = prog.compile.to_dict() if prog.compile else None
    if not prog.ok:
        return {"started": False, "compile": comp, "error": prog.error}
    _cleanup()
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        s = Session([str(c) for c in prog.cmd], env)
    except OSError as e:
        raise ApiError(f"Nie można uruchomić programu: {e}") from e
    with _LOCK:
        _SESSIONS[s.id] = s
    return {"started": True, "id": s.id, "compile": comp, "lang": prog.lang}


def handle_write(body: dict) -> dict:
    s = _get(body.get("id"))
    s.write(str(body.get("data") or ""), eof=bool(body.get("eof")))
    return {"done": s.done}


def handle_read(q: dict) -> dict:
    s = _get(q.get("id"))
    try:
        since = max(0, int(q.get("since") or 0))
        rev = int(q.get("rev") if q.get("rev") not in (None, "") else -1)
    except ValueError:
        since, rev = 0, -1
    return s.read(since, rev)


def handle_kill(body: dict) -> dict:
    sid = str(body.get("id") or "")
    with _LOCK:
        s = _SESSIONS.pop(sid, None)
    if s:
        s.kill()
    return {"killed": bool(s)}


ROUTES = {
    "POST /api/term/start": handle_start,
    "POST /api/term/write": handle_write,
    "GET /api/term/read": handle_read,
    "POST /api/term/kill": handle_kill,
}
