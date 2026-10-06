import subprocess
import sys
from pathlib import Path


def test_native_traceback_is_written_to_persistent_log(tmp_path):
    script = '''
import faulthandler, sys
from pathlib import Path
from local_meeting_ai.config import AppSettings
from local_meeting_ai.paths import AppPaths
from local_meeting_ai.logging_config import native_fault_logging
paths = AppPaths.from_settings(AppSettings(data_dir=Path(sys.argv[1]), testing=True))
before = faulthandler.is_enabled()
original_enable = faulthandler.enable
target = None
def enable(**kwargs):
    global target
    target = kwargs.get('file')
    original_enable(**kwargs)
faulthandler.enable = enable
with native_fault_logging(paths, enabled=True):
    # Explicit dump exercises the same descriptor without crashing the test process.
    output = target
    faulthandler.dump_traceback(file=output, all_threads=True)
assert output.closed
assert faulthandler.is_enabled() == before
print(paths.logs / 'native-fault.log')
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True, text=True, check=True,
    )
    content = Path(result.stdout.strip()).read_text()
    assert "Native fault logging started" in content
    assert 'File "<string>"' in content


