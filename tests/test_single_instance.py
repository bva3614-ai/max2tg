"""Tests for app/single_instance.py — the guard against a second running copy."""

import os
import subprocess
import sys
import textwrap

import pytest

from app import single_instance


def test_acquire_creates_the_lock_file(tmp_path):
    path = str(tmp_path / "max2tg.lock")

    handle = single_instance.acquire(path)
    try:
        assert os.path.exists(path)
    finally:
        handle.close()


def test_acquire_records_the_owning_pid(tmp_path):
    path = str(tmp_path / "max2tg.lock")

    handle = single_instance.acquire(path)
    handle.close()

    with open(path) as f:
        assert f.read() == str(os.getpid())


def test_second_acquire_is_refused(tmp_path):
    path = str(tmp_path / "max2tg.lock")

    handle = single_instance.acquire(path)
    try:
        with pytest.raises(single_instance.AlreadyRunning):
            single_instance.acquire(path)
    finally:
        handle.close()


def test_lock_is_reusable_once_released(tmp_path):
    """A leftover lock file must not wedge the next start."""
    path = str(tmp_path / "max2tg.lock")

    single_instance.acquire(path).close()

    handle = single_instance.acquire(path)
    handle.close()


def test_another_process_is_refused(tmp_path):
    """The whole point: the guard has to work across processes, not just threads."""
    path = str(tmp_path / "max2tg.lock")
    script = tmp_path / "contender.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r})
        from app import single_instance
        try:
            single_instance.acquire({path!r})
        except single_instance.AlreadyRunning:
            sys.exit(0)
        sys.exit(1)
    """))

    handle = single_instance.acquire(path)
    try:
        held = subprocess.run([sys.executable, str(script)])
        assert held.returncode == 0, "a second process managed to take the lock"
    finally:
        handle.close()

    freed = subprocess.run([sys.executable, str(script)])
    assert freed.returncode == 1, "the lock stayed held after it was released"
