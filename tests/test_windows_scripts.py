from pathlib import Path


def test_windows_powershell_scripts_are_ascii_for_powershell_51() -> None:
    scripts = Path(__file__).parents[1] / "scripts"

    for path in sorted(scripts.glob("*.ps1")):
        path.read_bytes().decode("ascii")
