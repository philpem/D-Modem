"""Curses presentation view. All modem traffic still comes from the live runner."""
from collections import deque
import curses
import re
import textwrap
import time

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


class Pane:
    def __init__(self, title):
        self.title = title
        self.entries = deque(maxlen=2000)
        self.scroll = None  # None follows new output; an integer holds a page.
        self.top, self.height, self.total = 0, 1, 0

    def append(self, text, colour=0):
        self.entries.append((text, colour))

    def rows(self, width):
        rows = []
        for text, colour in self.entries:
            rows.extend((s, colour) for s in textwrap.wrap(text.expandtabs(4), max(1, width),
                        replace_whitespace=False, drop_whitespace=False) or [''])
        return rows


class SplitConsole:
    def __init__(self, stream, screen, diagnostic_path, plain=False):
        self.stream, self.screen = stream, screen
        self.start = time.monotonic()
        self.diagnostic_path = diagnostic_path
        self.position, self.partial, self.debug_lines = 0, b'', 0
        self.last_draw = self.last_tail = 0
        self.stats_mode, self.finished, self.dismissed = False, False, False
        self.report = None
        self.active = 0
        self.status = 'Preparing the live demonstration'
        self.panes = {name: Pane(title) for name, title in (
            ('USR', 'USR'), ('slmodem', 'SLModem'),
            ('debug', 'slmodem diagnostics + DEMO'), ('ATI6', 'USR - ATI6'), ('ATI11', 'USR - ATI11'))}
        self.tx, self.rx = 0, 0
        self.screen.nodelay(True)
        self.screen.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if not plain and curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            # xterm-256 orange and teal, with readable eight-colour fallbacks.
            curses.init_pair(1, 208 if curses.COLORS >= 256 else curses.COLOR_YELLOW, background)
            curses.init_pair(2, 37 if curses.COLORS >= 256 else curses.COLOR_CYAN, background)
            self.tx, self.rx = curses.color_pair(1), curses.color_pair(2)
        self.draw(force=True)

    def visible(self):
        return ['ATI6', 'ATI11'] if self.stats_mode else ['USR', 'slmodem', 'debug']

    def show(self, name, direction, text):
        text = ''.join(c for c in ANSI.sub('', text) if c.isprintable() or c == '\t')
        stamp = time.monotonic()-self.start
        self.stream.write(f'{stamp:7.2f}  {name:8} {direction} {text}\n')
        self.stream.flush()
        colour = self.tx if direction == '>' else self.rx if direction == '<' else 0
        if name == 'DEMO':
            self.status = text
            self.panes['debug'].append(f'{stamp:6.1f} DEMO: {text}', curses.A_BOLD)
        elif self.stats_mode:
            if name == 'USR' and direction == '>':
                self.report = text if text in ('ATI6', 'ATI11') else None
            if name == 'USR' and self.report:
                self.panes[self.report].append(('TX > ' if direction == '>' else '') + text, colour)
        else:
            self.panes[name].append(f'{stamp:6.1f} {"TX >" if direction == ">" else "RX <"} {text}', colour)
        self.draw()

    def stage(self, text):
        self.show('DEMO', '*', text)
        self.draw(force=True)

    def statistics(self):
        self.tail()
        self.stats_mode, self.active = True, 0
        for name in ('ATI6', 'ATI11'):
            self.panes[name].scroll = 0
        self.stage('USR last-call statistics | ATI6 left, ATI11 right')

    def tail(self):
        if time.monotonic()-self.last_tail < .1:
            return
        self.last_tail = time.monotonic()
        try:
            with self.diagnostic_path.open('rb') as source:
                source.seek(self.position)
                data = source.read(65536)
                self.position = source.tell()
        except FileNotFoundError:
            return
        lines = (self.partial+data).split(b'\n')
        self.partial = lines.pop()
        for line in lines:
            text = ANSI.sub('', line.decode('utf-8', errors='replace'))
            text = ''.join(c for c in text if c.isprintable() or c == '\t')
            self.panes['debug'].append(text)
            self.debug_lines += 1

    def poll(self):
        self.tail()
        while True:
            key = self.screen.getch()
            if key == -1:
                break
            if key in (ord('q'), ord('Q'), 27):
                if not self.finished:
                    raise KeyboardInterrupt
                self.dismissed = True
            elif key in (10, 13, curses.KEY_ENTER) and self.finished:
                self.dismissed = True
            elif key == 9:
                self.active = (self.active+1) % len(self.visible())
            elif key == curses.KEY_RESIZE:
                curses.update_lines_cols()
            else:
                pane = self.panes[self.visible()[self.active]]
                delta = {curses.KEY_UP: -1, curses.KEY_DOWN: 1,
                         curses.KEY_PPAGE: -pane.height, curses.KEY_NPAGE: pane.height}.get(key)
                if delta is not None:
                    pane.scroll = max(0, pane.top+delta)
                elif key == curses.KEY_HOME:
                    pane.scroll = 0
                elif key == curses.KEY_END:
                    pane.scroll = None
        self.draw()

    def draw(self, force=False):
        if not force and time.monotonic()-self.last_draw < .1:
            return
        self.last_draw = time.monotonic()
        height, width = self.screen.getmaxyx()
        try:
            self.screen.erase()
            if height < 24 or width < 80:
                self.screen.addnstr(0, 0, 'Resize to at least 80 columns x 24 rows. The call continues; q quits.', max(0, width-1))
                self.screen.refresh()
                return
            self.screen.addnstr(0, 0, self.status, width-1, curses.A_BOLD)
            left_width = width//2
            if self.stats_mode:
                boxes = [('ATI6', height-2, left_width, 1, 0),
                         ('ATI11', height-2, width-left_width, 1, left_width)]
            else:
                top_height = (height-2)*2//3
                boxes = [('USR', top_height, left_width, 1, 0),
                         ('slmodem', top_height, width-left_width, 1, left_width),
                         ('debug', height-2-top_height, width, 1+top_height, 0)]
            self.screen.noutrefresh()
            for index, (name, rows, cols, y, x) in enumerate(boxes):
                pane = self.panes[name]
                win = curses.newwin(rows, cols, y, x)
                win.box()
                attr = curses.A_BOLD if index == self.active else 0
                win.addnstr(0, 2, ' '+pane.title+' ', cols-4, attr)
                content = pane.rows(cols-2)
                pane.height, pane.total = rows-2, len(content)
                last = max(0, len(content)-pane.height)
                pane.top = last if pane.scroll is None else min(pane.scroll, last)
                for row, (text, colour) in enumerate(content[pane.top:pane.top+pane.height], 1):
                    win.addnstr(row, 1, text, cols-2, colour)
                win.noutrefresh()
            footer = ('Enter/q close | ' if self.finished else 'q/Ctrl-C stop | ')
            footer += 'Tab pane | arrows/PgUp/PgDn scroll | Home/End | TX orange  RX teal'
            self.screen.addnstr(height-1, 0, footer, width-1)
            self.screen.noutrefresh()
            curses.doupdate()
        except curses.error:
            # A resize may invalidate the geometry between the size check and draw.
            pass

    def finish(self, result):
        self.finished = True
        self.stage(f"Demo {result['status']}. Reports saved. Press Enter to close.")
        if result['status'] != 'cancelled':
            while not self.dismissed:
                self.poll()
                time.sleep(.05)
