"""Offline contextual learning. Importing this module does not import Qt or start IO."""
__version__ = "1.0.0"


def anchor(widget, help_id):
    """Attach a stable help identifier without changing widget actions or ownership."""
    widget.setProperty("learningHelpId", help_id)
    return widget
