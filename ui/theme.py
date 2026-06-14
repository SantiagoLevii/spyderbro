"""ScrapBro visual theme — Matrix green on black.

Primary color: #048c04 (dark phosphor green). Exposes the exact phosphor palette,
a prompt_toolkit ``Style`` for the manually-built interactive applications, and
raw ANSI escape codes for the spider animation, banner, progress box and the
results table (where prompt_toolkit is not used).
"""
from prompt_toolkit.styles import Style

# Exact phosphor palette (Sprint K).
GREEN_PRIMARY = "#048c04"   # main text, borders, checkboxes
GREEN_BRIGHT = "#05b304"    # selected items, titles, success
GREEN_DIM = "#025c02"       # secondary text, help
GREEN_FAINT = "#013a01"     # scanlines, very soft separators
BLACK = "#000000"           # background of everything
YELLOW_WARN = "#886600"     # only for cookie ⚠ icons
RED_ERROR = "#880000"       # only for critical errors

# Back-compat aliases (referenced by tests and existing modules).
GREEN = GREEN_PRIMARY
WHITE = "#ffffff"

# Raw ANSI (truecolor) for components rendered without prompt_toolkit.
ANSI_GREEN = "\033[38;2;4;140;4m"          # #048c04
ANSI_GREEN_BRIGHT = "\033[38;2;5;179;4m"   # #05b304
ANSI_GREEN_DIM = "\033[38;2;2;92;2m"       # #025c02
ANSI_GREEN_FAINT = "\033[38;2;1;58;1m"     # #013a01
ANSI_RED = "\033[38;2;136;0;0m"            # #880000
ANSI_YELLOW = "\033[38;2;136;102;0m"       # #886600
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"
ANSI_CLEAR = "\033[2J\033[H"
ANSI_HIDE_CURSOR = "\033[?25l"
ANSI_SHOW_CURSOR = "\033[?25h"

# prompt_toolkit Style for the manually-built Applications. Every widget class
# is pinned to the green-on-black palette so no default white/grey leaks through.
SCRAPBRO_STYLE = Style.from_dict({
    "": f"fg:{GREEN_PRIMARY} bg:{BLACK}",
    "title": f"fg:{GREEN_BRIGHT} bold",
    "intro": f"fg:{GREEN_DIM}",
    "help": f"fg:{GREEN_DIM} italic",
    "separator": f"fg:{GREEN_FAINT}",
    "item": f"fg:{GREEN_PRIMARY}",
    "item.selected": f"fg:{BLACK} bg:{GREEN_PRIMARY} bold",
    "checkbox.checked": f"fg:{GREEN_BRIGHT} bold",
    "checkbox.unchecked": f"fg:{GREEN_DIM}",
    "radio.on": f"fg:{GREEN_BRIGHT} bold",
    "radio.off": f"fg:{GREEN_DIM}",
    "error": f"fg:{RED_ERROR} bold",
    "warning": f"fg:{YELLOW_WARN}",
    "success": f"fg:{GREEN_BRIGHT} bold",
    "detected": f"fg:{GREEN_BRIGHT}",
    # Frame / box borders.
    "frame.border": f"fg:{GREEN_PRIMARY}",
    "frame.label": f"fg:{GREEN_BRIGHT} bold",
    # TextArea (cookie paste / query input).
    "text-area": f"fg:{GREEN_BRIGHT} bg:{BLACK}",
    "text-area.prompt": f"fg:{GREEN_DIM}",
})
