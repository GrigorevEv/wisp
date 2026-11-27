import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import wiki_html as wiki


class WikiCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.html = self.root / "html"
        self.html.mkdir()
        self.index = self.html / "index.html"
        shutil.copyfile(wiki.TEMPLATE.with_name("Индекс.html"), self.index)
        for source in (Path(wiki.__file__), wiki.TEMPLATE, wiki.TEMPLATE.with_name("Индекс.html")):
            target = self.root / source.relative_to(wiki.PROJECT)
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(self.root / "scripts/wiki_html.py"), *args],
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode == 0, ok, output)
        return output

    def create(self, name, *options, section="Входящие"):
        self.cli("create", name, "--section", section, "--summary", name, "--heading", "Пример", *options)
        return self.html / wiki.page_file(name)

    @contextmanager
    def server(self):
        with subprocess.Popen([sys.executable, "-u", str(self.root / "scripts/wiki_html.py"),
                               "serve", "--port", "0"], cwd=self.root, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True) as process:
            try:
                line = process.stdout.readline()
                self.assertTrue(line.startswith("Откройте "), line)
                yield line.split()[1].rstrip("/")
            finally:
                process.terminate()

    def request(self, base, method, filename, title=None, *, folder=None, origin=True):
        data = {"folder": folder} if folder is not None else {"title": title}
        payload = json.dumps(data).encode() if folder is not None or title is not None else None
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = base
        req = Request(base + "/notes/" + filename, data=payload, method=method, headers=headers)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as exc:
            exc.close()
            return exc.code, b""
