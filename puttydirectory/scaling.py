"""UI scale: one factor that resizes the whole window.

Tk has no notion of desktop scaling. It asks X for a DPI, gets 96 whatever the
panel is set to, and sizes everything from there - which is why the window can
look small next to native apps on the same screen. There is no system setting
to read that would fix this reliably, so the size is ours to choose and to
remember.

Scaling is done in two places at once, because neither alone is enough:

* the **named fonts** (``TkDefaultFont`` and friends), which every Tk and ttk
  widget derives its text from - this is what actually makes things bigger;
* ``tk scaling``, the pixels-per-point ratio, which governs the handful of
  measurements Tk takes in points rather than pixels.

Anything sized in raw pixels - Treeview row height, column widths, the window
itself - is not covered by either and has to be scaled by the caller, which is
what :meth:`UiScale.px` and :meth:`UiScale.dims` are for.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

#: Offered in the View menu. 1.0 is "whatever Tk would have done by itself".
PRESETS = (1.0, 1.25, 1.5, 1.75, 2.0)

MIN_SCALE = 0.8
MAX_SCALE = 3.0

#: One press of Larger/Smaller.
STEP = 0.1


def clamp(factor: float) -> float:
    """Keep a factor inside the supported range, rounded to a sane step."""
    try:
        value = float(factor)
    except (TypeError, ValueError):
        return 1.0
    return round(min(max(value, MIN_SCALE), MAX_SCALE), 2)


class UiScale:
    """Applies a scale factor to a Tk root, and remembers the 1.0 baseline.

    The baseline is captured once, at construction, before anything has been
    scaled. Every later ``apply`` is computed from that baseline rather than
    from the current state, so repeated changes cannot drift or compound.
    """

    def __init__(self, root: tk.Misc):
        self.root = root
        self.factor = 1.0

        self._base_fonts: dict[str, int] = {}
        self._derived: dict[str, tkfont.Font] = {}
        for name in tkfont.names(root):
            # Tk's own named fonts all start with "Tk"; those are the ones every
            # widget inherits from. Anything else belongs to somebody else.
            if not name.startswith("Tk"):
                continue
            try:
                self._base_fonts[name] = tkfont.nametofont(name, root).cget("size")
            except tk.TclError:
                continue

        try:
            self._base_tk_scaling = float(root.tk.call("tk", "scaling"))
        except (tk.TclError, ValueError):
            self._base_tk_scaling = 1.3333333333333333

    # -- derived fonts -----------------------------------------------------

    def derive(self, name: str, source: str = "TkDefaultFont", **overrides) -> str:
        """Create a managed named font from another one, e.g. a bold variant.

        The new font is built from ``source``'s **unscaled** size, not its
        current one. Deriving from the live size would scale an already-scaled
        number, so a font made while the UI sits at 175% would come out at
        175% of 175%.

        The Font object is kept on this instance on purpose: tkinter's wrapper
        deletes the underlying named font in ``__del__``, so letting it fall out
        of scope would take the font with it.
        """
        if name in self._derived:
            return name
        try:
            source_font = tkfont.nametofont(source, self.root)
        except tk.TclError:
            return source

        base = self._base_fonts.get(source, source_font.cget("size"))
        if name in tkfont.names(self.root):
            self._derived[name] = tkfont.nametofont(name, self.root)
        else:
            spec = {**source_font.actual(), **overrides, "size": base}
            try:
                self._derived[name] = tkfont.Font(root=self.root, name=name, **spec)
            except tk.TclError:
                return source
        self._base_fonts[name] = base
        self._apply_fonts()
        return name

    # -- applying ----------------------------------------------------------

    def apply(self, factor: float) -> float:
        """Scale to ``factor``. Returns the value actually used, after clamping."""
        self.factor = clamp(factor)
        self._apply_fonts()
        try:
            self.root.tk.call("tk", "scaling", self._base_tk_scaling * self.factor)
        except tk.TclError:
            pass
        return self.factor

    def _apply_fonts(self) -> None:
        for name, base in self._base_fonts.items():
            try:
                widget_font = tkfont.nametofont(name, self.root)
            except tk.TclError:
                continue
            # A negative size means pixels rather than points; Tk keeps the sign
            # as the unit, so scale the magnitude and put the sign back.
            size = max(1, round(abs(base) * self.factor))
            try:
                widget_font.configure(size=-size if base < 0 else size)
            except tk.TclError:
                pass

    # -- measurements the caller has to scale itself -----------------------

    def px(self, pixels: float) -> int:
        """Scale a pixel measurement taken at 1.0."""
        return max(1, round(pixels * self.factor))

    def dims(self, width: float, height: float) -> tuple[int, int]:
        return self.px(width), self.px(height)

    def geometry(self, width: float, height: float) -> str:
        return "{}x{}".format(*self.dims(width, height))

    def row_height(self) -> int:
        """A Treeview row tall enough for the current font, plus breathing room.

        Measured from the font rather than derived from the factor: families
        differ in how tall they draw at a given point size, and a row shorter
        than its text clips the descenders.
        """
        try:
            linespace = tkfont.nametofont("TkDefaultFont", self.root).metrics("linespace")
        except tk.TclError:
            linespace = self.px(16)
        return int(linespace + self.px(6))
