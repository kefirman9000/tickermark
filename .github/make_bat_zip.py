"""Build dist/StockLogoDownloader-bat.zip: the source + Install.bat / Run.bat, for
people whose antivirus or firewall blocks the .exe. Batch/PowerShell files are
forced to Windows (CRLF) line endings so cmd.exe always runs them correctly."""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["Install.bat", "Run.bat", "make_shortcut.ps1", "app.py", "logo_core.py",
         "requirements.txt", "README.txt"]
OUT = os.path.join(ROOT, "dist", "StockLogoDownloader-bat.zip")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    version = re.search(r'VERSION = "([^"]+)"',
                        open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()).group(1)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            data = open(os.path.join(ROOT, name), "rb").read()
            if name.endswith((".bat", ".ps1")):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            z.writestr(f"StockLogoDownloader/{name}", data)
        z.writestr("StockLogoDownloader/START HERE.txt", (
            f"Stock Logo Downloader {version} - batch-file version (no .exe)\r\n\r\n"
            "1. Unzip this folder anywhere (do not run it from inside the zip).\r\n"
            "2. Double-click Install.bat (one time: sets up Python + packages and makes a\r\n"
            "   desktop shortcut). It then opens the program.\r\n"
            "3. Next time use the desktop shortcut or Run.bat.\r\n\r\n"
            "Use this version if Windows or your antivirus blocks StockLogoDownloader.exe.\r\n"
            "More detail: README.txt\r\n"))
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes), version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
