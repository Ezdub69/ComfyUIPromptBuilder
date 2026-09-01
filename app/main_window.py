from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QTabWidget, QToolButton

from app.gallery_tab import GalleryTab
from app.help_tab import HelpTab
from app.krea2_assistant_tab import Krea2AssistantTab
from app.krea2_tab import Krea2Tab
from app.library_tab import LibraryTab
from app.lm_studio_tab import LMStudioTab
from app.saved_tab import SavedTab


class MainWindow(QMainWindow):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.setWindowTitle("ComfyUI Prompt Builder")
        self.resize(1700, 1050)

        tabs = QTabWidget()
        self.library_tab = LibraryTab(conn)
        self.saved_tab = SavedTab(conn)
        self.lm_studio_tab = LMStudioTab(conn)
        self.krea2_tab = Krea2Tab(conn)
        self.krea2_assistant_tab = Krea2AssistantTab(conn)
        self.gallery_tab = GalleryTab(conn)
        self.help_tab = HelpTab()

        tabs.addTab(self.lm_studio_tab, "LM Studio")
        tabs.addTab(self.krea2_tab, "Krea 2 Prompt Builder")
        tabs.addTab(self.krea2_assistant_tab, "Krea 2 Assistant")
        tabs.addTab(self.gallery_tab, "Image Analyser")
        tabs.addTab(self.saved_tab, "Saved Prompts")
        tabs.addTab(self.help_tab, "Help")

        # Both Krea 2 tabs get their model list AND their model assignment
        # exclusively from here now - detecting once feeds both, instead of
        # each having its own (previously out-of-sync) server field and
        # picker(s). assignments_changed covers picking a different already-
        # known model without needing a fresh detect.
        self.lm_studio_tab.models_detected.connect(self.krea2_tab.set_available_models)
        self.lm_studio_tab.models_detected.connect(self.krea2_assistant_tab.set_available_models)
        self.lm_studio_tab.assignments_changed.connect(self.krea2_tab.refresh_model_label)
        self.lm_studio_tab.assignments_changed.connect(self.krea2_assistant_tab.refresh_model_label)

        # Library Settings is administrative, not part of the day-to-day
        # workflow - pinned to the tab bar's top-right corner instead of
        # just being the last entry in the row, so it stays anchored to the
        # actual edge of the window (visually separated from the 4 workflow
        # tabs) rather than merely trailing them with empty space beyond it
        # on a wide window. Still a real tab/page underneath (setTabVisible
        # only hides its row entry), so everything that references
        # self.library_tab keeps working unchanged.
        library_index = tabs.addTab(self.library_tab, "Library Settings")
        tabs.setTabVisible(library_index, False)
        self.library_btn = QToolButton()
        self.library_btn.setText("Library Settings")
        self.library_btn.setCheckable(True)
        self.library_btn.clicked.connect(lambda: tabs.setCurrentWidget(self.library_tab))
        tabs.setCornerWidget(self.library_btn, Qt.TopRightCorner)

        # Reloading a saved prompt, or coming back to the Krea 2 tab after
        # editing the Library, should pick up any tag changes made in the
        # meantime rather than showing stale picker options.
        tabs.currentChanged.connect(self._on_tab_changed)

        self.saved_tab.load_requested.connect(self._load_saved_state)
        self.saved_tab.load_chat_output_requested.connect(self._load_chat_output)

        self.setCentralWidget(tabs)
        self._tabs = tabs

    def _on_tab_changed(self, index):
        self.library_btn.setChecked(self._tabs.widget(index) is self.library_tab)
        if self._tabs.widget(index) is self.krea2_tab:
            self.krea2_tab.reload_pickers()

    def _load_saved_state(self, state):
        self.krea2_tab.set_state(state)
        self._tabs.setCurrentWidget(self.krea2_tab)

    def _load_chat_output(self, text):
        self.krea2_assistant_tab.load_output_text(text)
        self._tabs.setCurrentWidget(self.krea2_assistant_tab)
