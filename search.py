import gi
import os

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango


class SearchDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(title="Search", transient_for=parent, modal=True)
        self.add_buttons(
            Gtk.STOCK_FIND,
            Gtk.ResponseType.OK,
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
        )

        box = self.get_content_area()

        label = Gtk.Label(label="Insert text you want to search for:")
        box.add(label)

        self.entry = Gtk.Entry()
        box.add(self.entry)

        self.show_all()


class TextViewWindow(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title="File Search Example")

        self.set_default_size(-1, 350)

        self.grid = Gtk.Grid()
        self.add(self.grid)

        self.create_textview()
        self.create_toolbar()

    def create_toolbar(self):
        toolbar = Gtk.Toolbar()
        self.grid.attach(toolbar, 0, 0, 3, 1)

        button_search = Gtk.ToolButton()
        button_search.set_icon_name("system-search-symbolic")
        button_search.connect("clicked", self.on_search_clicked)
        toolbar.insert(button_search, 0)

    def create_textview(self):
        scrolledwindow = Gtk.ScrolledWindow()
        scrolledwindow.set_hexpand(True)
        scrolledwindow.set_vexpand(True)
        self.grid.attach(scrolledwindow, 0, 1, 3, 1)

        self.textview = Gtk.TextView()
        self.textbuffer = self.textview.get_buffer()
        self.textbuffer.set_text("Search results will appear here.")
        scrolledwindow.add(self.textview)

    def on_search_clicked(self, widget):
        dialog = SearchDialog(self)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            search_text = dialog.entry.get_text()
            self.search_files(search_text)
        dialog.destroy()

    def search_files(self, keyword):
        directory = "./virtual_env/"
        result_files = []

        # 디렉토리 내 파일 검색
        for filename in os.listdir(directory):
            if filename == ".DS_Store":
                continue 
            file_path = os.path.join(directory, filename)

            if os.path.isfile(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        if keyword in content and "f0b10bda-729c-4dc0-8844-c9c5a2af2872" in content:
                            result_files.append(filename)
                except Exception as e:
                    print(f"Error reading {filename}: {e}")

        # 결과 출력
        if result_files:
            result_text = "🔍 Search Results:\n\n" + "\n".join(result_files)
        else:
            result_text = "❌ No files contain the keyword."

        self.textbuffer.set_text(result_text)


win = TextViewWindow()
win.connect("destroy", Gtk.main_quit)
win.show_all()
Gtk.main()