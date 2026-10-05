"""Versions listed in Dahua release notes (PDF), for finding earlier builds of a firmware.

Dahua's release notes have a version history: one section per release, titled with the full version and build date,
e.g. "1.3 V3.140.0000000.71.R.260210". Reading the text out of the PDF and matching that pattern gives every release
the notes cover.
"""
import re
import shutil
import subprocess
import tempfile
import zlib

# V3.140.0000000.71.R.260210 / V2.622.0000000.31.R.20171229 (version, then the build date)
DAHUA_VERSION_DATE = re.compile(r"(?<![\w.])(V\d+\.\d+\.[0-9A-Za-z]{4,8}\.\d+\.[A-Z])\.(\d{6}|\d{8})(?!\d)")


def pdf_text(data):
    """The text of a PDF: from pdftotext when it's installed, else a rough extraction from its compressed streams
    (enough to find version strings, which Dahua's PDFs store as plain text runs)."""
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(data)
            pdf.flush()
            result = subprocess.run(["pdftotext", "-layout", pdf.name, "-"], capture_output=True, timeout=120)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.decode("utf-8", "replace")
    return fallback_pdf_text(data)


def fallback_pdf_text(data):
    texts = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
        stream = match.group(1)
        try:
            stream = zlib.decompress(stream)
        except zlib.error:
            pass
        # Text drawing operators: (text) Tj and [(te) 12 (xt)] TJ
        for run in re.findall(rb"\(((?:\\.|[^\\)])*)\)", stream):
            texts.append(run.decode("latin-1"))
    return "".join(texts) + "\n" + data.decode("latin-1", "replace")


def versions_in_text(text):
    """[(version, build date)] in the order they appear, without repeats."""
    seen, versions = set(), []
    for version, build_date in DAHUA_VERSION_DATE.findall(text):
        if (version, build_date) not in seen:
            seen.add((version, build_date))
            versions.append((version, build_date))
    return versions
