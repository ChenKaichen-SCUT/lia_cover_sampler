"""Synchronize signal tests with actual delivery, not dynamic-loader latency."""
import time

def wait_for_complete_line(process, path, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            with path.open('rb') as stream:
                if stream.readline().endswith(b'\n'):return
        except FileNotFoundError:
            pass
        if process.poll() is not None:
            raise AssertionError(f'process exited ({process.returncode}) before delivering {path.name}')
        time.sleep(.01)
    raise AssertionError(f'no complete {path.name} within {timeout} seconds')

def cleanup_process(process):
    if process.poll() is None:process.kill()
    process.communicate(timeout=10)
