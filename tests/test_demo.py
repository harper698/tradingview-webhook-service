import subprocess
import sys
from pathlib import Path


def test_offline_demo_completes_including_temporary_database_cleanup():
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "examples" / "demo.py")],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "persisted    1 alert, 1 simulated action" in result.stdout
    assert "PASS:" in result.stdout
