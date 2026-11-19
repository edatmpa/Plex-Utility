"""
Shared console-menu helpers: an explain-then-confirm pattern matching
DBRepair-Windows-Launcher.bat's style, used by the Python interactive
menus (plex_search.py, plex_db.py, vdo_util.py) so every option explains
itself before it runs.
"""


def explain(title: str, bullets: list[str], warning: str | None = None) -> None:
    """Print a boxed header, bullet list of what an option does, and an optional warning."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)
    for b in bullets:
        print(f"  - {b}")
    if warning:
        print(f"\n  WARNING: {warning}")
    print()


def confirm(prompt: str = "Continue?") -> bool:
    """Y/N prompt. Returns True only on an explicit 'y'."""
    return input(f"  {prompt} (y/N): ").strip().lower() == "y"
