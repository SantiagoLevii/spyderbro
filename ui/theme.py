"""ScrapBro visual theme — Matrix green on black.

Primary color: #048c04 (dark phosphor green). Exposes both a prompt_toolkit
``Style`` for the interactive dialogs and raw ANSI escape codes for the spider
animation and banner.
"""
from prompt_toolkit.styles import Style

# Hex palette
GREEN = "#048c04"
GREEN_BRIGHT = "#05b304"
GREEN_DIM = "#025c02"
GREEN_FAINT = "#013a01"
BLACK = "#000000"
WHITE = "#ffffff"
RED_ERROR = "#cc0000"
YELLOW_WARN = "#888800"

# Raw ANSI (truecolor) for the animated intro, where prompt_toolkit is not used.
ANSI_GREEN = "\033[38;2;4;140;4m"
ANSI_GREEN_BRIGHT = "\033[38;2;5;179;4m"
ANSI_RED = "\033[38;2;204;0;0m"
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"
ANSI_CLEAR = "\033[2J\033[H"
ANSI_HIDE_CURSOR = "\033[?25l"
ANSI_SHOW_CURSOR = "\033[?25h"

SCRAPBRO_STYLE = Style.from_dict({
    "": f"fg:{GREEN} bg:{BLACK}",
    "menu-item": f"fg:{GREEN} bg:{BLACK}",
    "menu-selected": f"fg:{BLACK} bg:{GREEN} bold",
    "checkbox-checked": f"fg:{GREEN_BRIGHT} bold",
    "checkbox-unchecked": f"fg:{GREEN_DIM}",
    "title": f"fg:{GREEN_BRIGHT} bold",
    "separator": f"fg:{GREEN_FAINT}",
    "prompt": f"fg:{GREEN} bold",
    "help": f"fg:{GREEN_DIM} italic",
    "error": f"fg:{RED_ERROR} bold",
    "warning": f"fg:{YELLOW_WARN}",
    "success": f"fg:{GREEN_BRIGHT} bold",
    "cursor": f"fg:{GREEN_BRIGHT} bg:{GREEN_FAINT}",
    # prompt_toolkit dialog widget classes mapped to the green palette.
    "dialog": f"bg:{BLACK}",
    "dialog frame.label": f"fg:{GREEN_BRIGHT} bold",
    "dialog.body": f"bg:{BLACK} fg:{GREEN}",
    "dialog shadow": f"bg:{GREEN_FAINT}",
    "button": f"fg:{GREEN} bg:{BLACK}",
    "button.focused": f"fg:{BLACK} bg:{GREEN} bold",
    "checkbox-list": f"fg:{GREEN} bg:{BLACK}",
    "checkbox": f"fg:{GREEN}",
    "checkbox-selected": f"fg:{BLACK} bg:{GREEN}",
    "radio": f"fg:{GREEN}",
    "radio-selected": f"fg:{BLACK} bg:{GREEN}",
    "frame.border": f"fg:{GREEN_DIM}",
})
