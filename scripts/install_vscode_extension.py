"""Empaquette l'extension VS Code de Custos (integrations/vscode) et l'installe.

Un .vsix n'est qu'un zip avec deux fichiers de description : pas besoin de npm ni de vsce.

    python scripts/install_vscode_extension.py           # construit et installe
    python scripts/install_vscode_extension.py --build   # construit seulement (chemin affiché)

Pour la retirer : code --uninstall-extension touya0.custos
"""

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "integrations" / "vscode"

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\
<Default Extension=".json" ContentType="application/json"/>\
<Default Extension=".js" ContentType="application/javascript"/>\
<Default Extension=".vsixmanifest" ContentType="text/xml"/>\
</Types>"""


def manifest(package: dict) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{package['name']}" Version="{package['version']}" Publisher="{package['publisher']}"/>
    <DisplayName>{package['displayName']}</DisplayName>
    <Description xml:space="preserve">{package['description']}</Description>
    <Categories>Other</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{package['engines']['vscode']}"/>
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>"""


def build(folder: Path) -> Path:
    package = json.loads((SOURCE / "package.json").read_text())
    vsix = folder / f"{package['publisher']}.{package['name']}-{package['version']}.vsix"
    with zipfile.ZipFile(vsix, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("extension.vsixmanifest", manifest(package))
        for name in ("package.json", "extension.js"):
            archive.write(SOURCE / name, f"extension/{name}")
    return vsix


def main() -> int:
    if "--build" in sys.argv:
        print(build(Path.cwd()))
        return 0
    with tempfile.TemporaryDirectory() as folder:
        vsix = build(Path(folder))
        result = subprocess.run(["code", "--install-extension", str(vsix), "--force"], check=False)
    if result.returncode == 0:
        print("Extension installée : recharge les fenêtres VS Code (Ctrl+Maj+P, « Reload Window »).")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
