"""Notebook hosts for the recorder's existing panels."""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from pathlib import Path
import os


class CopyTextRouter:
    """Offer copying for labels/status text as well as editable text widgets."""
    def __init__(self, root):
        self.root = root
        self.tag = f"RecorderCopy_{id(self)}"
        self.menu = tk.Menu(root, tearoff=False)
        root.bind_class(self.tag, "<Button-3>", self.popup)
        root.bind_all("<Map>", self.on_map, add="+")
        self.attach(root)

    def attach(self, widget):
        if self.tag not in widget.bindtags():
            widget.bindtags((self.tag,) + widget.bindtags())
        for child in widget.winfo_children():
            self.attach(child)

    def on_map(self, event):
        if isinstance(event.widget, tk.Misc):
            self.attach(event.widget)

    def text_values(self, widget):
        selected = ""
        if isinstance(widget, tk.Text):
            try:
                selected = widget.get("sel.first", "sel.last")
            except tk.TclError:
                pass
            return selected, widget.get("1.0", "end-1c")
        if isinstance(widget, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox)):
            if widget.selection_present():
                selected = widget.get()[int(widget.index("sel.first")):int(widget.index("sel.last"))]
            return selected, widget.get()
        keys = widget.keys()
        if "textvariable" in keys and widget.cget("textvariable"):
            return "", str(self.root.getvar(widget.cget("textvariable")))
        if "text" in keys:
            return "", str(widget.cget("text"))
        return "", ""

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def select_text(self, text):
        window = tk.Toplevel(self.root)
        window.title("Select and copy / 选择并复制")
        window.geometry("700x360")
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        field = tk.Text(body, wrap="word", exportselection=False)
        scrollbar = ttk.Scrollbar(body, command=field.yview)
        field.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        field.pack(fill="both", expand=True)
        field.insert("1.0", text)
        field.configure(state="disabled")
        ttk.Button(window, text="Copy all / 复制全部", command=lambda: self.copy(text)).pack(pady=(0, 10))

    def popup(self, event):
        if not isinstance(event.widget, tk.Misc):
            return
        selected, text = self.text_values(event.widget)
        if isinstance(event.widget, ttk.Notebook):
            try:
                text = event.widget.tab(event.widget.index(f"@{event.x},{event.y}"), "text")
            except tk.TclError:
                text = event.widget.tab(event.widget.select(), "text")
        if not text:
            return
        self.menu.delete(0, "end")
        if selected:
            self.menu.add_command(label="Copy selection / 复制选中内容", command=lambda: self.copy(selected))
        self.menu.add_command(label="Copy all / 复制全部", command=lambda: self.copy(text))
        if not isinstance(event.widget, (tk.Text, tk.Entry, ttk.Entry)):
            self.menu.add_command(label="Select text… / 选择部分文字…", command=lambda: self.select_text(text))
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"


class MouseWheelRouter:
    """Scroll the nearest scrollable ancestor under the pointer, once per event."""
    def __init__(self, root):
        self.root = root
        self.tag = f"RecorderWheel_{id(self)}"
        self.remainders = {}
        root.bind_class(self.tag, "<MouseWheel>", self.on_wheel)
        root.bind_all("<Map>", self.on_map, add="+")
        self.attach(root)

    def attach(self, widget):
        tags = widget.bindtags()
        if self.tag not in tags:
            widget.bindtags((self.tag,) + tags)
        for child in widget.winfo_children():
            self.attach(child)

    def on_map(self, event):
        if isinstance(event.widget, tk.Misc):
            self.attach(event.widget)

    def on_wheel(self, event):
        try:
            widget = self.root.winfo_containing(event.x_root, event.y_root) or event.widget
        except (KeyError, tk.TclError):
            return  # Native combobox popups keep their own scrolling behavior.
        if not isinstance(widget, tk.Misc):
            return
        candidates = []
        while widget is not None:
            if isinstance(widget, (tk.Text, tk.Canvas, tk.Listbox, ttk.Treeview)):
                candidates.append(widget)
            elif isinstance(widget, (ttk.Scrollbar, tk.Scrollbar)):
                command = self.root.tk.splitlist(widget.cget("command"))
                if len(command) >= 2 and command[1] == "yview":
                    try:
                        candidates.append(self.root.nametowidget(command[0]))
                    except KeyError:
                        pass
            widget = widget.master
        for target in candidates:
            first, last = target.yview()
            if (event.delta < 0 and last < 0.999999) or (event.delta > 0 and first > 0.000001):
                amount = self.remainders.get(str(target), 0) - event.delta / 120
                steps = int(amount)
                self.remainders[str(target)] = amount - steps
                if steps:
                    target.yview_scroll(steps * 3, "units")
                return "break"
        # Do not let a combobox change a model selection when scrolling a page.
        if candidates:
            return "break"


class EmbeddedPanel(ttk.Frame):
    """Provide window-level navigation for a panel hosted in the main notebook."""
    def __init__(self, parent, app, key):
        super().__init__(parent)
        self.app, self.key, self.caption = app, key, ""
        self.pack(fill=tk.BOTH, expand=True)

    def title(self, value=None):
        if value is not None:
            self.caption = value
        return self.caption

    def geometry(self, *args): pass
    def minsize(self, *args): pass
    def resizable(self, *args): pass
    def protocol(self, *args): pass
    def deiconify(self): self.app.show_main_tab(self.key)
    def lift(self, *args): self.app.root.lift()


class SelectableText(tk.Text):
    """Label-like read-only text with normal mouse selection and Ctrl+C."""
    def __init__(self, parent, text=""):
        background = ttk.Style(parent).lookup("TFrame", "background") or parent.winfo_toplevel().cget("background")
        super().__init__(parent, wrap="word", height=1, width=1, relief="flat", borderwidth=0,
                         highlightthickness=0, background=background, cursor="xterm",
                         font="TkDefaultFont", exportselection=False, takefocus=True)
        self.bind("<Configure>", self.fit_height)
        self.configure(state="disabled")
        self.set_text(text)

    def set_text(self, text):
        if self.get("1.0", "end-1c") == text:
            return  # Polling must not clear an in-progress mouse selection.
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("1.0", text)
        self.configure(state="disabled")
        self.after_idle(self.fit_height)

    def fit_height(self, event=None):
        count = self.count("1.0", "end", "displaylines")
        height = max(1, min(12, count[0] if count else 1))
        if int(self.cget("height")) != height:
            self.configure(height=height)


class SelectableLabel(SelectableText):
    """Selectable static or variable-backed label with a natural layout width."""
    def __init__(self, parent, text="", textvariable=None, wraplength=600, justify="left", **options):
        self.wrap_pixels = int(wraplength) or 600
        self.variable = textvariable
        self.variable_trace = None
        super().__init__(parent)
        self.tag_configure("content", justify=justify)
        if options:
            self.configure(**options)
        if textvariable is not None:
            if not isinstance(textvariable, tk.Variable):
                self.variable = tk.StringVar(parent, name=str(textvariable))
            self.variable_trace = self.variable.trace_add("write", self.variable_changed)
            text = self.variable.get()
        self.set_text(str(text))

    def variable_changed(self, *args):
        self.set_text(str(self.variable.get()))

    def set_text(self, text):
        if self.get("1.0", "end-1c") == text:
            return
        super().set_text(text)
        font = tkfont.Font(font=self.cget("font"))
        pixels = min(self.wrap_pixels, max((font.measure(line) for line in text.splitlines()), default=1))
        width = max(1, (pixels + max(1, font.measure("0")) - 1) // max(1, font.measure("0")))
        if int(self.cget("width")) != width:
            super().configure(width=width)
        self.tag_add("content", "1.0", "end")

    def configure(self, cnf=None, **options):
        text = options.pop("text", None)
        result = super().configure(cnf, **options)
        if text is not None:
            self.set_text(str(text))
        return result

    config = configure

    def destroy(self):
        if self.variable_trace is not None:
            self.variable.trace_remove("write", self.variable_trace)
            self.variable_trace = None
        super().destroy()


class SelectableCheckbutton(ttk.Frame):
    control_class = ttk.Checkbutton

    def __init__(self, parent, text="", **options):
        super().__init__(parent)
        self.control = self.control_class(self, text="", **options)
        self.control.pack(side="left", anchor="n")
        self.caption = SelectableLabel(self, text=text, wraplength=420)
        self.caption.pack(side="left", fill="x", expand=True)

    def configure(self, cnf=None, **options):
        if "text" in options:
            self.caption.set_text(str(options.pop("text")))
        if "state" in options:
            self.control.configure(state=options.pop("state"))
        return super().configure(cnf, **options)

    config = configure

    def invoke(self):
        return self.control.invoke()


class SelectableRadiobutton(SelectableCheckbutton):
    control_class = ttk.Radiobutton


def selectable_label_frame(parent, text="", **options):
    frame = ttk.LabelFrame(parent, **options)
    caption = SelectableLabel(frame, text=text)
    frame.configure(labelwidget=caption)
    return frame


class SummaryQueueView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app, self.rows = app, {}
        self.pack(fill=tk.BOTH, expand=True)
        self.count = SelectableText(self)
        self.count.pack(fill="x", pady=(0, 8))
        SelectableText(self, text="Progress = completed sections + final merge. Paused work keeps its checkpoints.").pack(fill="x")
        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas = canvas
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(canvas)
        item = canvas.create_window((0, 0), window=self.content, anchor="nw")
        def resize_content(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            self.measure_page_capacity()
        def resize_canvas(event):
            canvas.itemconfigure(item, width=event.width)
            self.measure_page_capacity()
        self.content.bind("<Configure>", resize_content)
        canvas.bind("<Configure>", resize_canvas)
        self.refresh()

    def measure_page_capacity(self):
        if self.canvas.winfo_height() <= 1:
            return
        heights = [widgets[0].master.winfo_reqheight() + 8 for widgets in self.rows.values()]
        row_height = max(heights, default=130)
        self.app.summary_store.set_page_capacity(self.canvas.winfo_height() // max(1, row_height))

    def refresh(self):
        if self.app.shutting_down or not self.winfo_exists():
            return
        records = self.app.summary_store.snapshot()
        active_ids = {r["id"] for r in records}
        for identity in list(self.rows):
            if identity not in active_ids:
                self.rows.pop(identity)[0].master.destroy()
        self.count.set_text(f"Pending: {self.app.summary_store.pending_count}   |   All meetings: {len(records)}")
        for record in records:
            identity = record["id"]
            if identity not in self.rows:
                row = ttk.Frame(self.content, padding=10, relief="groove", borderwidth=1)
                existing = self.content.pack_slaves()
                row.pack(fill="x", pady=(0, 8), **({"before": existing[0]} if existing else {}))
                SelectableText(row, text=record["file_stem"]).pack(fill="x", pady=(0, 5))
                label = SelectableText(row)
                label.pack(fill="x")
                bar = ttk.Progressbar(row, maximum=100)
                bar.pack(fill="x", pady=6)
                buttons = ttk.Frame(row)
                buttons.pack(fill="x")
                ttk.Button(buttons, text="Open transcript", command=lambda p=record["text_path"]: os.startfile(str(Path(p).resolve()))).pack(side="left")
                retry = ttk.Button(buttons, text="Retry", command=lambda i=identity: self.app.retry_summary(i))
                retry.pack(side="right")
                self.rows[identity] = label, bar, retry
            label, bar, retry = self.rows[identity]
            label.set_text(f"{record['status']} — {record['progress']:.0f}% — {record['detail']}")
            bar.configure(value=record["progress"])
            retry.configure(state="normal" if record["status"] == "failed" and not record.get("awaiting_transcript") else "disabled")
        self.after(750, self.refresh)
