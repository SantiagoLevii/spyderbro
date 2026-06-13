"""Animated ASCII spider intro for the ScrapBro TUI.

Renders phosphor-green frames directly with ANSI escape codes for maximum
terminal compatibility on Windows. Any keypress skips the animation, and the
whole thing is a no-op when stdout is not a TTY (tests / piped output).
"""
import sys
import time

from ui.theme import (
    ANSI_CLEAR,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_HIDE_CURSOR,
    ANSI_RESET,
    ANSI_SHOW_CURSOR,
)

SPIDER_FRAMES = [
    r"""
        /\   /\
       /  \ /  \
      | (o) (o) |
       \  \_/  /
    ___/`-----'\___
   /   |       |   \
  /    |  ___  |    \
 /  /\ | |   | | /\  \
/__/ /\|_|___|_|/\ \__\
   |/                \|
""",
    r"""
       /\    /\
      /  \  /  \
     | (o)(o)   |
      \  \_/   /
   ___/`------'\___
  /   |        |   \
 /    |  ____  |    \
/ /\  | |    | | /\  \
|/  \_|_|____|_|/  \|
   |/               \|
""",
    r"""
         /\  /\
        /  \/  \
       |(o)  (o)|
        \  /\/  /
    ____/`----'\____
   /    |      |    \
  /     | (__) |     \
 / /\/\ |      | /\/\ \
|/    \_|______|/    \|
    |/              \|
""",
    r"""
       /\    /\
      /  \  /  \
     | (o)(o)   |
      \  \_/   /
   ___/`------'\___
  /   |        |   \
 /    |  ____  |    \
/ /\  | |    | | /\  \
|/  \_|_|____|_|/  \|
   |/               \|
""",
]

WEB_ART = r"""
    *               *               *
        *       *       *       *
    *       \ | /   \ | /   *
           --\|/-----\|/--
        *   /|\       /|\   *
    *      / | \     / | \
        *               *       *
    *       *       *       *
"""

_FRAME_DELAY = 0.15


def _key_pressed() -> bool:
    """Return True if a key is waiting in the input buffer (Windows only)."""
    try:
        import msvcrt
        if msvcrt.kbhit():
            msvcrt.getch()
            return True
    except Exception:
        return False
    return False


def _render_frame(frame: str) -> None:
    """Draw a single spider frame with a subtle scanline effect."""
    out = [ANSI_CLEAR, ANSI_GREEN]
    for index, line in enumerate(WEB_ART.splitlines() + frame.splitlines()):
        if index % 2 == 1:
            out.append(f"{ANSI_DIM}{line}{ANSI_RESET}{ANSI_GREEN}\n")
        else:
            out.append(line + "\n")
    out.append(ANSI_RESET)
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def animate_spider(duration_seconds: float = 2.5) -> None:
    """Animate the spider in phosphor green over a black background.

    Cycles the frames for ``duration_seconds`` (~0.15s/frame). Any keypress skips
    it. No-op when stdout is not a TTY.

    Args:
        duration_seconds: Total animation time before returning.
    """
    if not sys.stdout.isatty():
        return

    sys.stdout.write(ANSI_HIDE_CURSOR)
    sys.stdout.flush()
    try:
        elapsed = 0.0
        index = 0
        while elapsed < duration_seconds:
            _render_frame(SPIDER_FRAMES[index % len(SPIDER_FRAMES)])
            index += 1
            time.sleep(_FRAME_DELAY)
            elapsed += _FRAME_DELAY
            if _key_pressed():
                break
    finally:
        sys.stdout.write(ANSI_CLEAR + ANSI_SHOW_CURSOR + ANSI_RESET)
        sys.stdout.flush()
