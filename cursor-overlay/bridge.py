"""
Qt signal bridge — lets background threads safely talk to the main Qt thread.
"""
from PyQt6.QtCore import QObject, pyqtSignal


class Bridge(QObject):
    toggle_circle    = pyqtSignal()
    toggle_voice     = pyqtSignal()
    wake_detected    = pyqtSignal()
    set_levels       = pyqtSignal(list)
    show_response    = pyqtSignal(str)
    show_error       = pyqtSignal(str)
    silence_detected = pyqtSignal()
    begin_processing = pyqtSignal()
    start_speaking   = pyqtSignal()
    stop_speaking    = pyqtSignal()
    mesh_pairing_result = pyqtSignal(bool, str)   # (ok, message) from lan_mesh background thread
